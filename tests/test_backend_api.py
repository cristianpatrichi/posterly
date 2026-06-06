"""API tests for the collage backend.

Run as part of the standard suite::

    python -m unittest discover -s tests

These tests point ``COLLAGE_DATA_DIR`` at a per-class temp dir *before* importing
the app, so nothing is ever written to ``/data``.
"""

import io
import json
import os
import tempfile
import unittest

from PIL import Image

# Point storage + auth config at a temp dir / test secrets BEFORE importing the
# app, so nothing is written to /data and the routes (now auth-gated) are testable.
_TMP = tempfile.TemporaryDirectory()
os.environ["COLLAGE_DATA_DIR"] = _TMP.name
os.environ["SESSION_SECRET"] = "test-session-secret-please-change-32bytes"
os.environ["GOOGLE_CLIENT_ID"] = "test.apps.googleusercontent.com"
os.environ["APP_ORIGIN"] = "http://localhost:5173"
os.environ["COOKIE_SECURE"] = "0"
os.environ["OTP_DEV_EXPOSE"] = "1"
# Starlette's TestClient sends "Host: testserver"; allow it so the new
# TrustedHostMiddleware (S-004) doesn't 400 every request in the suite.
os.environ["EXTRA_TRUSTED_HOSTS"] = "testserver"

from fastapi.testclient import TestClient  # noqa: E402

from backend.app import auth as _auth  # noqa: E402
from backend.app import otp as _otp  # noqa: E402
from backend.app import storage as _storage  # noqa: E402
from backend.app.main import app, limiter  # noqa: E402
from collage_a4 import A4_LANDSCAPE, A4_PORTRAIT  # noqa: E402

# Disable IP rate limiting so a fast test run doesn't trip 429s; one dedicated
# test re-enables it to verify the limiter actually fires.
limiter.enabled = False

TEST_EMAIL = "tester@example.com"


def _allow(email: str) -> None:
    _storage.data_root().mkdir(parents=True, exist_ok=True)
    path = _auth.allowed_emails_path()
    existing = path.read_text() if path.exists() else ""
    if email not in existing:
        path.write_text(existing + email + "\n")


def _authed_client(email: str = TEST_EMAIL) -> TestClient:
    """A TestClient logged in as ``email`` (via the OTP dev flow), so its cookie
    jar carries a real session. Use distinct emails to avoid the send-cooldown."""
    _allow(email)
    client = TestClient(app)
    code = client.post("/api/auth/otp/request", json={"email": email}).json()["dev_code"]
    resp = client.post("/api/auth/otp/verify", json={"email": email, "code": code})
    assert resp.status_code == 200, resp.text
    return client


def _png_bytes(width=1200, height=800, color="red"):
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _jpg_bytes(width=900, height=700, color="green"):
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


class BackendApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = _authed_client()

    @classmethod
    def tearDownClass(cls):
        _TMP.cleanup()

    # -- helpers ---------------------------------------------------------- #
    def _create_project(self):
        response = self.client.post("/api/projects")
        self.assertEqual(response.status_code, 201)
        return response.json()

    def _upload(self, project_id, files):
        return self.client.post(f"/api/projects/{project_id}/images", files=files)

    # -- tests ------------------------------------------------------------ #
    def test_put_image_order_reorders_images(self):
        pid = self._create_project()["id"]
        self._upload(
            pid,
            [
                ("files", ("a.png", _png_bytes(800, 600, "red"), "image/png")),
                ("files", ("b.png", _png_bytes(800, 600, "green"), "image/png")),
                ("files", ("c.png", _png_bytes(800, 600, "blue"), "image/png")),
            ],
        )
        proj = self.client.get(f"/api/projects/{pid}").json()
        ids = [img["id"] for img in proj["images"]]
        self.assertEqual(len(ids), 3)

        new_order = [ids[2], ids[0], ids[1]]
        put = self.client.put(
            f"/api/projects/{pid}", json={"image_order": new_order}
        )
        self.assertEqual(put.status_code, 200)
        self.assertEqual([img["id"] for img in put.json()["images"]], new_order)

        # Persisted across a fresh GET.
        again = self.client.get(f"/api/projects/{pid}").json()
        self.assertEqual([img["id"] for img in again["images"]], new_order)

        # Unknown ids are ignored; omitted ids are appended (never dropped).
        partial = self.client.put(
            f"/api/projects/{pid}", json={"image_order": ["nope", ids[1]]}
        )
        self.assertEqual(partial.status_code, 200)
        result_ids = [img["id"] for img in partial.json()["images"]]
        self.assertEqual(set(result_ids), set(ids))
        self.assertEqual(result_ids[0], ids[1])

    def test_export_respects_paper_size_at_300dpi(self):
        from collage_a4 import canvas_size_for

        for paper, orientation in [
            ("A3", "portrait"),
            ("letter", "landscape"),
            ("A5", "portrait"),
        ]:
            pid = self._create_project()["id"]
            self._upload(
                pid,
                [
                    ("files", ("a.png", _png_bytes(800, 600), "image/png")),
                    ("files", ("b.png", _png_bytes(600, 800), "image/png")),
                ],
            )
            al = self.client.post(
                f"/api/projects/{pid}/auto-layout",
                json={"settings": {"paper_size": paper, "orientation": orientation}},
            )
            self.assertEqual(al.status_code, 200, f"{paper}/{orientation}")
            ex = self.client.post(
                f"/api/projects/{pid}/export", json={"format": "png"}
            )
            self.assertEqual(ex.status_code, 200)
            dl = self.client.get(f"/api/projects/{pid}/download/png")
            self.assertEqual(dl.status_code, 200)
            with Image.open(io.BytesIO(dl.content)) as result:
                self.assertEqual(
                    result.size,
                    canvas_size_for(orientation, paper),
                    f"{paper}/{orientation} pixel size",
                )
                dpi_x, dpi_y = result.info.get("dpi")
                self.assertAlmostEqual(dpi_x, 300.0, places=2)
                self.assertAlmostEqual(dpi_y, 300.0, places=2)

    def test_large_paper_sizes_render_at_300dpi(self):
        from collage_a4 import canvas_size_for

        # The exported PNGs are huge (a 1 m square is ~139 MP); raise Pillow's
        # decompression-bomb limit just so the TEST can open them to verify. The
        # app never re-opens exports, so its protection is unaffected.
        prev_limit = Image.MAX_IMAGE_PIXELS
        Image.MAX_IMAGE_PIXELS = None
        try:
            for paper, orientation in [("A2", "portrait"), ("100x100cm", "portrait")]:
                pid = self._create_project()["id"]
                self._upload(
                    pid,
                    [("files", ("a.png", _png_bytes(800, 600), "image/png"))],
                )
                al = self.client.post(
                    f"/api/projects/{pid}/auto-layout",
                    json={"settings": {"paper_size": paper, "orientation": orientation}},
                )
                self.assertEqual(al.status_code, 200, f"{paper}/{orientation}")
                ex = self.client.post(
                    f"/api/projects/{pid}/export", json={"format": "png"}
                )
                self.assertEqual(ex.status_code, 200)
                dl = self.client.get(f"/api/projects/{pid}/download/png")
                self.assertEqual(dl.status_code, 200)
                with Image.open(io.BytesIO(dl.content)) as result:
                    self.assertEqual(
                        result.size, canvas_size_for(orientation, paper), f"{paper}"
                    )
                    dpi_x, dpi_y = result.info.get("dpi")
                    self.assertAlmostEqual(dpi_x, 300.0, places=2)
        finally:
            Image.MAX_IMAGE_PIXELS = prev_limit

    def test_delete_images_removes_from_images_layout_and_disk(self):
        import os

        pid = self._create_project()["id"]
        self._upload(
            pid,
            [
                ("files", ("a.png", _png_bytes(800, 600, "red"), "image/png")),
                ("files", ("b.png", _png_bytes(800, 600, "green"), "image/png")),
                ("files", ("c.png", _png_bytes(800, 600, "blue"), "image/png")),
            ],
        )
        self.client.post(f"/api/projects/{pid}/auto-layout", json={})
        proj = self.client.get(f"/api/projects/{pid}").json()
        ids = [img["id"] for img in proj["images"]]
        files = {img["id"]: img["filename"] for img in proj["images"]}
        uploads = os.environ["COLLAGE_DATA_DIR"] + f"/projects/{pid}/uploads"

        # Delete two of the three.
        resp = self.client.post(
            f"/api/projects/{pid}/images/delete",
            json={"image_ids": [ids[0], ids[2]]},
        )
        self.assertEqual(resp.status_code, 200)
        out = resp.json()
        self.assertEqual([img["id"] for img in out["images"]], [ids[1]])
        # layout entries for deleted images are gone too.
        self.assertTrue(all(l["image_id"] == ids[1] for l in out["layout"]))
        # files removed from disk; the survivor remains.
        self.assertFalse(os.path.exists(os.path.join(uploads, files[ids[0]])))
        self.assertFalse(os.path.exists(os.path.join(uploads, files[ids[2]])))
        self.assertTrue(os.path.exists(os.path.join(uploads, files[ids[1]])))

        # Unknown ids are ignored (no error, no change).
        resp2 = self.client.post(
            f"/api/projects/{pid}/images/delete", json={"image_ids": ["nope"]}
        )
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(len(resp2.json()["images"]), 1)

    def test_health(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_create_project_returns_id_and_defaults(self):
        project = self._create_project()
        self.assertIn("id", project)
        self.assertTrue(project["id"])
        self.assertEqual(project["images"], [])
        self.assertEqual(project["layout"], [])
        settings = project["settings"]
        self.assertEqual(settings["orientation"], "landscape")
        self.assertEqual(settings["look"], "soft-oval")
        self.assertEqual(settings["order_mode"], "random")
        self.assertAlmostEqual(settings["spacing"], 0.5)
        self.assertAlmostEqual(settings["border"], 0.5)
        self.assertAlmostEqual(settings["feather"], 0.5)
        self.assertEqual(settings["seed"], 7)

    def test_upload_single_and_multiple_images_append(self):
        project = self._create_project()
        pid = project["id"]

        # One image.
        response = self._upload(
            pid, [("files", ("first.png", _png_bytes(1200, 800), "image/png"))]
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(len(body["images"]), 1)
        first = body["images"][0]
        self.assertEqual(first["name"], "first.png")
        self.assertEqual(first["width"], 1200)
        self.assertEqual(first["height"], 800)
        self.assertTrue(first["url"].endswith(first["filename"]))

        # Two more in one request -> appended (total 3).
        response = self._upload(
            pid,
            [
                ("files", ("a.jpg", _jpg_bytes(900, 700, "green"), "image/jpeg")),
                ("files", ("b.jpg", _jpg_bytes(640, 480, "blue"), "image/jpeg")),
            ],
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(len(body["images"]), 3)
        names = [img["name"] for img in body["images"]]
        self.assertEqual(names, ["first.png", "a.jpg", "b.jpg"])

    def test_upload_rejects_non_image(self):
        project = self._create_project()
        pid = project["id"]
        response = self._upload(
            pid, [("files", ("notes.txt", b"this is not an image", "text/plain"))]
        )
        self.assertEqual(response.status_code, 400)

    def test_get_project_returns_images_and_urls(self):
        project = self._create_project()
        pid = project["id"]
        self._upload(pid, [("files", ("x.png", _png_bytes(800, 600), "image/png"))])

        response = self.client.get(f"/api/projects/{pid}")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body["images"]), 1)
        image = body["images"][0]
        self.assertEqual(image["url"], f"/api/projects/{pid}/images/{image['filename']}")

        # The url actually serves the file.
        served = self.client.get(image["url"])
        self.assertEqual(served.status_code, 200)
        self.assertEqual(served.headers["content-type"], "image/png")

    def test_put_saves_settings_and_layout_and_get_reflects(self):
        project = self._create_project()
        pid = project["id"]
        upload = self._upload(
            pid, [("files", ("p.png", _png_bytes(1000, 1000), "image/png"))]
        ).json()
        image_id = upload["images"][0]["id"]

        new_settings = {
            "orientation": "portrait",
            "look": "paper",
            "order_mode": "manual",
            "spacing": 0.2,
            "rotation_intensity": 0.9,
            "border": 0.8,
            "feather": 0.1,
            "background": "#ffffff",
            "seed": 42,
        }
        manual_layout = [
            {
                "image_id": image_id,
                "x": 0.3,
                "y": 0.4,
                "width": 0.2,
                "height": 0.15,
                "rotation": 3.0,
                "z_index": 0,
                "look": "paper",
            }
        ]

        response = self.client.put(
            f"/api/projects/{pid}",
            json={"settings": new_settings, "layout": manual_layout},
        )
        self.assertEqual(response.status_code, 200, response.text)

        fetched = self.client.get(f"/api/projects/{pid}").json()
        self.assertEqual(fetched["settings"]["orientation"], "portrait")
        self.assertEqual(fetched["settings"]["seed"], 42)
        self.assertEqual(len(fetched["layout"]), 1)
        self.assertEqual(fetched["layout"][0]["image_id"], image_id)
        self.assertAlmostEqual(fetched["layout"][0]["x"], 0.3)

    def test_put_merges_partial_update(self):
        project = self._create_project()
        pid = project["id"]
        upload = self._upload(
            pid, [("files", ("p.png", _png_bytes(1000, 1000), "image/png"))]
        ).json()
        image_id = upload["images"][0]["id"]

        # Save a layout first.
        layout = [
            {
                "image_id": image_id,
                "x": 0.5,
                "y": 0.5,
                "width": 0.3,
                "height": 0.3,
            }
        ]
        self.client.put(f"/api/projects/{pid}", json={"layout": layout})

        # Now update only settings -- layout must survive.
        self.client.put(
            f"/api/projects/{pid}", json={"settings": {"background": "#000000"}}
        )
        fetched = self.client.get(f"/api/projects/{pid}").json()
        self.assertEqual(fetched["settings"]["background"], "#000000")
        self.assertEqual(len(fetched["layout"]), 1)

    def test_auto_layout_one_item_per_image_normalized(self):
        project = self._create_project()
        pid = project["id"]
        self._upload(
            pid,
            [
                ("files", ("a.png", _png_bytes(1200, 800, "red"), "image/png")),
                ("files", ("b.png", _png_bytes(800, 1200, "green"), "image/png")),
                ("files", ("c.png", _png_bytes(1000, 1000, "blue"), "image/png")),
            ],
        )

        response = self.client.post(f"/api/projects/{pid}/auto-layout")
        self.assertEqual(response.status_code, 200, response.text)
        layout = response.json()["layout"]
        self.assertEqual(len(layout), 3)

        image_ids = {img["id"] for img in self.client.get(f"/api/projects/{pid}").json()["images"]}
        for item in layout:
            self.assertIn(item["image_id"], image_ids)
            self.assertGreaterEqual(item["x"], 0.0)
            self.assertLessEqual(item["x"], 1.0)
            self.assertGreaterEqual(item["y"], 0.0)
            self.assertLessEqual(item["y"], 1.0)
            self.assertGreater(item["width"], 0.0)
            self.assertLessEqual(item["width"], 1.0)
            self.assertGreater(item["height"], 0.0)
            self.assertLessEqual(item["height"], 1.0)

    def test_export_produces_a4_png_and_pdf_and_downloads(self):
        project = self._create_project()
        pid = project["id"]
        self._upload(
            pid,
            [
                ("files", ("a.jpg", _jpg_bytes(1200, 800, "red"), "image/jpeg")),
                ("files", ("b.jpg", _jpg_bytes(900, 700, "green"), "image/jpeg")),
            ],
        )
        self.client.post(f"/api/projects/{pid}/auto-layout")

        response = self.client.post(f"/api/projects/{pid}/export", json={"format": "both"})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["png_ready"])
        self.assertTrue(body["pdf_ready"])
        self.assertEqual(body["png_url"], f"/api/projects/{pid}/download/png")
        self.assertEqual(body["pdf_url"], f"/api/projects/{pid}/download/pdf")

        png_resp = self.client.get(body["png_url"])
        self.assertEqual(png_resp.status_code, 200)
        self.assertEqual(png_resp.headers["content-type"], "image/png")
        self.assertIn("attachment", png_resp.headers["content-disposition"])
        self.assertRegex(
            png_resp.headers["content-disposition"],
            r"posterly-a4-landscape-300dpi-\d{8}-\d{6}\.png",
        )

        with Image.open(io.BytesIO(png_resp.content)) as result:
            self.assertEqual(result.size, A4_LANDSCAPE)
            dpi_x, dpi_y = result.info.get("dpi")
            self.assertAlmostEqual(dpi_x, 300.0, places=2)
            self.assertAlmostEqual(dpi_y, 300.0, places=2)

        pdf_resp = self.client.get(body["pdf_url"])
        self.assertEqual(pdf_resp.status_code, 200)
        self.assertEqual(pdf_resp.headers["content-type"], "application/pdf")
        self.assertIn("attachment", pdf_resp.headers["content-disposition"])
        self.assertRegex(
            pdf_resp.headers["content-disposition"],
            r"posterly-a4-landscape-300dpi-\d{8}-\d{6}\.pdf",
        )
        self.assertTrue(pdf_resp.content.startswith(b"%PDF"))

    def test_export_portrait_size(self):
        project = self._create_project()
        pid = project["id"]
        upload = self._upload(
            pid, [("files", ("p.jpg", _jpg_bytes(1000, 1400, "red"), "image/jpeg"))]
        ).json()
        image_id = upload["images"][0]["id"]
        self.client.put(
            f"/api/projects/{pid}",
            json={
                "settings": {"orientation": "portrait", "background": "#ffffff"},
                "layout": [
                    {
                        "image_id": image_id,
                        "x": 0.5,
                        "y": 0.5,
                        "width": 0.4,
                        "height": 0.4,
                    }
                ],
            },
        )
        self.client.post(f"/api/projects/{pid}/export", json={"format": "png"})
        png_resp = self.client.get(f"/api/projects/{pid}/download/png")
        self.assertEqual(png_resp.status_code, 200)
        with Image.open(io.BytesIO(png_resp.content)) as result:
            self.assertEqual(result.size, A4_PORTRAIT)

    def test_manual_layout_zorder_reaches_renderer(self):
        """Two overlapping items, different z_index: higher-z color must win."""
        project = self._create_project()
        pid = project["id"]
        upload = self._upload(
            pid,
            [
                ("files", ("red.png", _png_bytes(600, 600, "red"), "image/png")),
                ("files", ("blue.png", _png_bytes(600, 600, "blue"), "image/png")),
            ],
        ).json()
        red_id = upload["images"][0]["id"]
        blue_id = upload["images"][1]["id"]

        layout = [
            {
                "image_id": red_id,
                "x": 0.5,
                "y": 0.5,
                "width": 0.25,
                "height": 0.25,
                "rotation": 0.0,
                "z_index": 0,
                "look": "paper",
            },
            {
                "image_id": blue_id,
                "x": 0.5,
                "y": 0.5,
                "width": 0.25,
                "height": 0.25,
                "rotation": 0.0,
                "z_index": 5,
                "look": "paper",
            },
        ]
        self.client.put(
            f"/api/projects/{pid}",
            json={"settings": {"background": "#ffffff"}, "layout": layout},
        )
        self.client.post(f"/api/projects/{pid}/export", json={"format": "png"})

        png_resp = self.client.get(f"/api/projects/{pid}/download/png")
        with Image.open(io.BytesIO(png_resp.content)) as result:
            width, height = A4_LANDSCAPE
            center = result.getpixel((width // 2, height // 2))
            # Blue (higher z) on top: blue channel high, red/green low.
            self.assertLess(center[0], 80)
            self.assertLess(center[1], 80)
            self.assertGreater(center[2], 200)

    def test_export_without_layout_is_400(self):
        project = self._create_project()
        pid = project["id"]
        self._upload(pid, [("files", ("x.png", _png_bytes(800, 600), "image/png"))])
        # No layout saved.
        response = self.client.post(f"/api/projects/{pid}/export")
        self.assertEqual(response.status_code, 400)

    def test_404_for_missing_project(self):
        self.assertEqual(self.client.get("/api/projects/deadbeef").status_code, 404)
        self.assertEqual(
            self.client.put("/api/projects/deadbeef", json={"settings": {}}).status_code,
            404,
        )
        self.assertEqual(
            self.client.post("/api/projects/deadbeef/auto-layout").status_code, 404
        )
        self.assertEqual(
            self.client.post("/api/projects/deadbeef/export").status_code, 404
        )
        self.assertEqual(
            self.client.get("/api/projects/deadbeef/download/png").status_code, 404
        )

    def test_download_before_export_is_404(self):
        project = self._create_project()
        pid = project["id"]
        self.assertEqual(
            self.client.get(f"/api/projects/{pid}/download/png").status_code, 404
        )
        self.assertEqual(
            self.client.get(f"/api/projects/{pid}/download/pdf").status_code, 404
        )

    def test_path_traversal_on_image_route_is_rejected(self):
        project = self._create_project()
        pid = project["id"]
        self._upload(pid, [("files", ("x.png", _png_bytes(800, 600), "image/png"))])

        # Encoded traversal attempt -- must not escape the uploads dir.
        response = self.client.get(
            f"/api/projects/{pid}/images/..%2F..%2Fproject.json"
        )
        self.assertIn(response.status_code, (404, 400))
        self.assertNotEqual(response.status_code, 200)

    # -- regression tests (code-review hardening) ------------------------- #
    def test_auto_layout_single_square_image_stays_normalized(self):
        """CRITICAL 1: one ~1000x1000 image must not emit width/height > 1.0."""
        project = self._create_project()
        pid = project["id"]
        self._upload(
            pid, [("files", ("sq.png", _png_bytes(1000, 1000, "red"), "image/png"))]
        )

        response = self.client.post(f"/api/projects/{pid}/auto-layout")
        self.assertEqual(response.status_code, 200, response.text)
        layout = response.json()["layout"]
        self.assertEqual(len(layout), 1)
        for item in layout:
            self.assertGreater(item["width"], 0.0)
            self.assertLessEqual(item["width"], 1.0)
            self.assertGreater(item["height"], 0.0)
            self.assertLessEqual(item["height"], 1.0)

    def test_invalid_background_color_is_rejected_and_valid_works(self):
        """CRITICAL 2: junk background -> 4xx (422), valid background still OK."""
        project = self._create_project()
        pid = project["id"]

        bad = self.client.put(
            f"/api/projects/{pid}", json={"settings": {"background": "not-a-color"}}
        )
        self.assertGreaterEqual(bad.status_code, 400)
        self.assertLess(bad.status_code, 500)
        self.assertEqual(bad.status_code, 422)

        good = self.client.put(
            f"/api/projects/{pid}", json={"settings": {"background": "#f4efe6"}}
        )
        self.assertEqual(good.status_code, 200, good.text)
        self.assertEqual(good.json()["settings"]["background"], "#f4efe6")

        # CSS color names are accepted too.
        named = self.client.put(
            f"/api/projects/{pid}", json={"settings": {"background": "white"}}
        )
        self.assertEqual(named.status_code, 200, named.text)

    def test_malformed_project_id_is_404_not_500(self):
        """IMPORTANT 3: non-uuid / traversal ids 404 across all routes."""
        self.assertEqual(self.client.get("/api/projects/zzz").status_code, 404)
        self.assertEqual(
            self.client.get(
                "/api/projects/..%2f..%2fproject.json"
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.put("/api/projects/zzz", json={"settings": {}}).status_code,
            404,
        )
        self.assertEqual(
            self.client.post("/api/projects/zzz/auto-layout").status_code, 404
        )
        self.assertEqual(
            self.client.post("/api/projects/zzz/export").status_code, 404
        )
        self.assertEqual(
            self.client.get("/api/projects/zzz/download/png").status_code, 404
        )
        self.assertEqual(
            self.client.get("/api/projects/zzz/download/pdf").status_code, 404
        )

    def test_oversize_upload_is_rejected_and_not_appended(self):
        """IMPORTANT 4: a file over the byte cap is rejected and not stored."""
        from backend.app import config as cfg

        project = self._create_project()
        pid = project["id"]

        # Use a tiny cap so the oversize payload stays small (and fast).
        os.environ["MAX_UPLOAD_BYTES"] = "2048"
        try:
            oversize = b"\x00" * (cfg.max_upload_bytes() + 1)
            response = self._upload(
                pid, [("files", ("huge.png", oversize, "image/png"))]
            )
        finally:
            os.environ.pop("MAX_UPLOAD_BYTES", None)
        self.assertGreaterEqual(response.status_code, 400)
        self.assertLess(response.status_code, 500)

        fetched = self.client.get(f"/api/projects/{pid}").json()
        self.assertEqual(fetched["images"], [])

    def test_pdf_only_export(self):
        """IMPORTANT 5: format=pdf yields a pdf, deletes the png."""
        project = self._create_project()
        pid = project["id"]
        self._upload(
            pid,
            [("files", ("a.jpg", _jpg_bytes(1200, 800, "red"), "image/jpeg"))],
        )
        self.client.post(f"/api/projects/{pid}/auto-layout")

        response = self.client.post(
            f"/api/projects/{pid}/export", json={"format": "pdf"}
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["pdf_ready"])
        self.assertFalse(body["png_ready"])

        pdf_resp = self.client.get(f"/api/projects/{pid}/download/pdf")
        self.assertEqual(pdf_resp.status_code, 200)
        self.assertTrue(pdf_resp.content.startswith(b"%PDF"))

        png_resp = self.client.get(f"/api/projects/{pid}/download/png")
        self.assertEqual(png_resp.status_code, 404)

    def _project_with_image(self, paper, orientation):
        pid = self._create_project()["id"]
        self._upload(pid, [("files", ("a.png", _png_bytes(900, 700), "image/png"))])
        self.client.post(
            f"/api/projects/{pid}/auto-layout",
            json={"settings": {"paper_size": paper, "orientation": orientation}},
        )
        return pid

    def test_download_filename_reflects_paper_size(self):
        pid = self._project_with_image("A3", "portrait")
        self.client.post(f"/api/projects/{pid}/export", json={"format": "both"})
        pdf = self.client.get(f"/api/projects/{pid}/download/pdf")
        png = self.client.get(f"/api/projects/{pid}/download/png")
        self.assertRegex(
            pdf.headers.get("content-disposition", ""),
            r"posterly-a3-portrait-300dpi-\d{8}-\d{6}\.pdf",
        )
        self.assertRegex(
            png.headers.get("content-disposition", ""),
            r"posterly-a3-portrait-300dpi-\d{8}-\d{6}\.png",
        )

    def test_pdf_page_size_matches_paper_at_300dpi(self):
        import re

        pid = self._project_with_image("A3", "portrait")
        self.client.post(f"/api/projects/{pid}/export", json={"format": "pdf"})
        data = self.client.get(f"/api/projects/{pid}/download/pdf").content
        m = re.search(rb"/MediaBox\s*\[([^\]]+)\]", data)
        self.assertIsNotNone(m)
        nums = [float(x) for x in m.group(1).split()]
        # A3 portrait at 300 DPI = 3508x4961 px = 841.9 x 1190.6 pt.
        self.assertAlmostEqual(nums[2], 3508 / 300 * 72, delta=2)
        self.assertAlmostEqual(nums[3], 4961 / 300 * 72, delta=2)

    def test_preview_image_is_served_and_smaller(self):
        pid = self._create_project()["id"]
        # A big image so the proxy is meaningfully downscaled.
        self._upload(pid, [("files", ("big.png", _png_bytes(4000, 3000), "image/png"))])
        proj = self.client.get(f"/api/projects/{pid}").json()
        img = proj["images"][0]
        self.assertTrue(img["preview_url"].endswith("?preview=1"))
        full = self.client.get(img["url"])
        prev = self.client.get(img["preview_url"])
        self.assertEqual(full.status_code, 200)
        self.assertEqual(prev.status_code, 200)
        # Correct content-type so the browser renders it under nosniff.
        self.assertEqual(prev.headers["content-type"], "image/webp")
        with Image.open(io.BytesIO(prev.content)) as p:
            self.assertLessEqual(max(p.size), 1600)  # downscaled
        self.assertLess(len(prev.content), len(full.content))  # smaller payload

    def test_export_uses_full_resolution_not_preview(self):
        # The export must render at the paper's full pixel size regardless of the
        # small on-screen proxy.
        pid = self._project_with_image("A4", "landscape")
        self.client.post(f"/api/projects/{pid}/export", json={"format": "png"})
        data = self.client.get(f"/api/projects/{pid}/download/png").content
        with Image.open(io.BytesIO(data)) as im:
            self.assertEqual(im.size, A4_LANDSCAPE)


class AuthTest(unittest.TestCase):
    """Authentication, authorization and brute-force defenses."""

    def test_health_is_public(self):
        self.assertEqual(TestClient(app).get("/api/health").status_code, 200)

    def test_all_data_routes_require_auth(self):
        anon = TestClient(app)
        pid = "0" * 32
        cases = [
            ("get", "/api/projects"),
            ("post", "/api/projects"),
            ("get", f"/api/projects/{pid}"),
            ("put", f"/api/projects/{pid}"),
            ("delete", f"/api/projects/{pid}"),
            ("post", f"/api/projects/{pid}/auto-layout"),
            ("post", f"/api/projects/{pid}/export"),
            ("post", f"/api/projects/{pid}/images/delete"),
            ("get", f"/api/projects/{pid}/images/x.png"),
            ("get", f"/api/projects/{pid}/download/png"),
            ("get", f"/api/projects/{pid}/download/pdf"),
        ]
        for method, path in cases:
            fn = getattr(anon, method)
            resp = fn(path, json={}) if method in ("post", "put") else fn(path)
            self.assertEqual(resp.status_code, 401, f"{method} {path} -> {resp.status_code}")

    def test_allow_list_matching(self):
        path = _auth.allowed_emails_path()
        saved = path.read_text() if path.exists() else None
        try:
            path.write_text("# comment\nexact@example.com\n*@team.com\n")
            self.assertTrue(_auth.is_email_allowed("exact@example.com"))
            self.assertTrue(_auth.is_email_allowed("anyone@team.com"))
            self.assertFalse(_auth.is_email_allowed("other@example.com"))
            self.assertFalse(_auth.is_email_allowed("notanemail"))
            path.write_text("*\n")
            self.assertTrue(_auth.is_email_allowed("whoever@anywhere.com"))
        finally:
            if saved is not None:
                path.write_text(saved)
            else:
                path.unlink(missing_ok=True)

    def test_session_token_roundtrip_and_expiry(self):
        token = _auth.create_session_token("Person@Example.com")
        self.assertEqual(_auth.verify_session_token(token), "person@example.com")
        self.assertIsNone(_auth.verify_session_token("garbage.token.here"))
        os.environ["SESSION_TTL_SECONDS"] = "-5"
        try:
            expired = _auth.create_session_token("a@b.com")
        finally:
            os.environ.pop("SESSION_TTL_SECONDS", None)
        self.assertIsNone(_auth.verify_session_token(expired))

    def test_otp_single_use_wrong_code_and_attempt_cap(self):
        email = "otpuser@example.com"
        _allow(email)
        client = TestClient(app)
        code = client.post("/api/auth/otp/request", json={"email": email}).json()["dev_code"]
        # wrong code 5x burns the code on the 6th attempt
        for _ in range(6):
            self.assertEqual(
                client.post(
                    "/api/auth/otp/verify", json={"email": email, "code": "000000"}
                ).status_code,
                401,
            )
        # even the correct code now fails (burned by attempt cap)
        self.assertEqual(
            client.post(
                "/api/auth/otp/verify", json={"email": email, "code": code}
            ).status_code,
            401,
        )

    def test_otp_correct_code_is_single_use(self):
        email = "otponce@example.com"
        _allow(email)
        client = TestClient(app)
        code = client.post("/api/auth/otp/request", json={"email": email}).json()["dev_code"]
        self.assertEqual(
            client.post("/api/auth/otp/verify", json={"email": email, "code": code}).status_code,
            200,
        )
        client2 = TestClient(app)
        self.assertEqual(
            client2.post("/api/auth/otp/verify", json={"email": email, "code": code}).status_code,
            401,
        )

    def test_otp_expiry(self):
        email = "otpexpire@example.com"
        _allow(email)
        client = TestClient(app)
        code = client.post("/api/auth/otp/request", json={"email": email}).json()["dev_code"]
        record = json.loads(_otp._otp_path(email).read_text())
        record["expires_at"] = 0
        _otp._otp_path(email).write_text(json.dumps(record))
        self.assertEqual(
            client.post("/api/auth/otp/verify", json={"email": email, "code": code}).status_code,
            401,
        )

    def test_otp_request_hides_allow_list(self):
        client = TestClient(app)
        not_allowed = client.post("/api/auth/otp/request", json={"email": "ghost@nope.com"})
        self.assertEqual(not_allowed.status_code, 200)
        self.assertIsNone(not_allowed.json().get("dev_code"))

    def test_cross_user_isolation_all_routes(self):
        a = _authed_client("alice@example.com")
        b = _authed_client("bob@example.com")
        pid = a.post("/api/projects").json()["id"]
        a.post(
            f"/api/projects/{pid}/images",
            files=[("files", ("a.png", _png_bytes(800, 600), "image/png"))],
        )
        a.post(f"/api/projects/{pid}/auto-layout", json={})
        a.post(f"/api/projects/{pid}/export", json={"format": "png"})
        # Bob cannot touch Alice's project through any route.
        self.assertEqual(b.get(f"/api/projects/{pid}").status_code, 404)
        self.assertEqual(b.put(f"/api/projects/{pid}", json={}).status_code, 404)
        self.assertEqual(b.delete(f"/api/projects/{pid}").status_code, 404)
        self.assertEqual(b.post(f"/api/projects/{pid}/auto-layout", json={}).status_code, 404)
        self.assertEqual(b.post(f"/api/projects/{pid}/export", json={}).status_code, 404)
        self.assertEqual(
            b.post(f"/api/projects/{pid}/images/delete", json={"image_ids": []}).status_code,
            404,
        )
        self.assertEqual(b.get(f"/api/projects/{pid}/download/png").status_code, 404)
        # And Bob's history doesn't include it; Alice's does.
        self.assertNotIn(pid, [p["id"] for p in b.get("/api/projects").json()])
        self.assertIn(pid, [p["id"] for p in a.get("/api/projects").json()])

    def test_owner_can_delete_own_project(self):
        c = _authed_client("deleter@example.com")
        pid = c.post("/api/projects").json()["id"]
        self.assertEqual(c.delete(f"/api/projects/{pid}").status_code, 200)
        self.assertEqual(c.get(f"/api/projects/{pid}").status_code, 404)

    def test_origin_check_blocks_cross_origin_mutation(self):
        c = _authed_client("origin@example.com")
        ok = c.post("/api/projects")
        self.assertEqual(ok.status_code, 201)
        blocked = c.post("/api/projects", headers={"origin": "https://evil.example"})
        self.assertEqual(blocked.status_code, 403)

    def test_google_login_stub(self):
        from unittest import mock

        _allow("googler@example.com")
        client = TestClient(app)
        with mock.patch.object(
            _auth, "verify_google_credential", return_value="googler@example.com"
        ):
            ok = client.post("/api/auth/google", json={"credential": "x"})
            self.assertEqual(ok.status_code, 200)
            self.assertEqual(ok.json()["email"], "googler@example.com")
            self.assertEqual(client.get("/api/auth/me").json()["email"], "googler@example.com")
        with mock.patch.object(
            _auth, "verify_google_credential", return_value="stranger@nope.com"
        ):
            self.assertEqual(
                TestClient(app).post("/api/auth/google", json={"credential": "x"}).status_code,
                403,
            )
        with mock.patch.object(_auth, "verify_google_credential", return_value=None):
            self.assertEqual(
                TestClient(app).post("/api/auth/google", json={"credential": "x"}).status_code,
                401,
            )

    def test_rate_limiter_fires_when_enabled(self):
        _allow("rl@example.com")
        client = TestClient(app)
        limiter.enabled = True
        try:
            statuses = [
                client.post("/api/auth/otp/request", json={"email": "rl@example.com"}).status_code
                for _ in range(14)
            ]
        finally:
            limiter.enabled = False
        self.assertIn(429, statuses)

    def test_session_ttl_default_is_7_days(self):
        from backend.app import config as cfg

        saved = os.environ.pop("SESSION_TTL_SECONDS", None)
        try:
            self.assertEqual(cfg.session_ttl_seconds(), 7 * 24 * 3600)
        finally:
            if saved is not None:
                os.environ["SESSION_TTL_SECONDS"] = saved

    def test_empty_int_env_falls_back_to_default(self):
        # Compose forwards unset vars as "" (${VAR:-}); must not crash on int("").
        from backend.app import config as cfg

        for name, getter, default in [
            ("SESSION_TTL_SECONDS", cfg.session_ttl_seconds, 7 * 24 * 3600),
            ("SESSION_REFRESH_THRESHOLD_SECONDS", cfg.session_refresh_threshold_seconds, 86400),
            ("RETENTION_DAYS", cfg.retention_days, 60),
            ("CLEANUP_INTERVAL_SECONDS", cfg.cleanup_interval_seconds, 86400),
        ]:
            saved = os.environ.get(name)
            os.environ[name] = ""  # empty + non-numeric
            try:
                self.assertEqual(getter(), default, name)
                os.environ[name] = "not-a-number"
                self.assertEqual(getter(), default, name)
            finally:
                if saved is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = saved

    def test_cookie_secure_empty_uses_https_autodefault(self):
        from backend.app import config as cfg

        saved_cs = os.environ.get("COOKIE_SECURE")
        saved_origin = os.environ.get("APP_ORIGIN")
        os.environ["COOKIE_SECURE"] = ""  # as compose passes when unset
        try:
            os.environ["APP_ORIGIN"] = "https://collage.example.com"
            self.assertTrue(cfg.cookie_secure())  # https -> Secure even if blank
            os.environ["APP_ORIGIN"] = "http://localhost:5173"
            self.assertFalse(cfg.cookie_secure())
        finally:
            if saved_cs is None:
                os.environ.pop("COOKIE_SECURE", None)
            else:
                os.environ["COOKIE_SECURE"] = saved_cs
            if saved_origin is not None:
                os.environ["APP_ORIGIN"] = saved_origin

    def test_public_origin_requires_configured_session_secret(self):
        from backend.app import config as cfg

        saved_origin = os.environ.get("APP_ORIGIN")
        saved_secret = os.environ.get("SESSION_SECRET")
        try:
            os.environ["APP_ORIGIN"] = "https://collage.example.com"
            os.environ.pop("SESSION_SECRET", None)
            with self.assertRaises(RuntimeError):
                cfg.session_secret()

            os.environ["SESSION_SECRET"] = "too-short"
            with self.assertRaises(RuntimeError):
                cfg.session_secret()

            os.environ["APP_ORIGIN"] = "http://localhost:5173"
            os.environ.pop("SESSION_SECRET", None)
            self.assertGreaterEqual(len(cfg.session_secret()), 32)
        finally:
            if saved_origin is None:
                os.environ.pop("APP_ORIGIN", None)
            else:
                os.environ["APP_ORIGIN"] = saved_origin
            if saved_secret is None:
                os.environ.pop("SESSION_SECRET", None)
            else:
                os.environ["SESSION_SECRET"] = saved_secret

    def test_email_brand_is_html_escaped(self):
        from backend.app import email_service

        saved_brand = os.environ.get("BRAND_NAME")
        try:
            os.environ["BRAND_NAME"] = '<img src=x onerror="alert(1)">'
            markup = email_service._html("12345678")
            self.assertNotIn("<img", markup)
            self.assertIn("&lt;img", markup)
        finally:
            if saved_brand is None:
                os.environ.pop("BRAND_NAME", None)
            else:
                os.environ["BRAND_NAME"] = saved_brand

    def test_empty_backend_brand_uses_public_default(self):
        from backend.app import config as cfg

        saved_brand = os.environ.get("BRAND_NAME")
        try:
            os.environ["BRAND_NAME"] = ""
            self.assertEqual(cfg.brand_name(), "Posterly")
        finally:
            if saved_brand is None:
                os.environ.pop("BRAND_NAME", None)
            else:
                os.environ["BRAND_NAME"] = saved_brand

    def test_missing_email_provider_does_not_log_recipient(self):
        from unittest import mock

        from backend.app import email_service

        saved_key = os.environ.get("RESEND_API_KEY")
        os.environ.pop("RESEND_API_KEY", None)
        try:
            with mock.patch.object(email_service.log, "warning") as warning:
                self.assertFalse(
                    email_service.send_otp_email("private-person@example.com", "12345678")
                )
            self.assertNotIn("private-person@example.com", str(warning.call_args))
        finally:
            if saved_key is not None:
                os.environ["RESEND_API_KEY"] = saved_key

    def test_session_slides_when_stale_not_when_fresh(self):
        import time

        import jwt

        from backend.app import config as cfg

        _allow("slide@example.com")
        c = TestClient(app)
        now = int(time.time())

        def tok(iat):
            return jwt.encode(
                {"sub": "slide@example.com", "iat": iat, "exp": iat + cfg.session_ttl_seconds()},
                cfg.session_secret(),
                algorithm="HS256",
            )

        # Stale (older than the 1-day refresh threshold) -> cookie rolled forward.
        stale = c.get("/api/auth/me", cookies={"session": tok(now - 2 * 24 * 3600)})
        self.assertEqual(stale.status_code, 200)
        self.assertIn("session=", stale.headers.get("set-cookie", ""))

        # Fresh -> no refresh.
        fresh = c.get("/api/auth/me", cookies={"session": tok(now - 60)})
        self.assertEqual(fresh.status_code, 200)
        self.assertNotIn("session=", fresh.headers.get("set-cookie", ""))

    def test_expired_session_is_401_and_not_refreshed(self):
        import time

        import jwt

        from backend.app import config as cfg

        now = int(time.time())
        expired = jwt.encode(
            {"sub": "x@y.com", "iat": now - 10 * 86400, "exp": now - 3 * 86400},
            cfg.session_secret(),
            algorithm="HS256",
        )
        r = TestClient(app).get("/api/auth/me", cookies={"session": expired})
        self.assertEqual(r.status_code, 401)
        self.assertNotIn("session=", r.headers.get("set-cookie", ""))

    def test_trusted_host_blocks_unknown_host(self):
        client = TestClient(app)
        blocked = client.get("/api/health", headers={"host": "evil.example"})
        self.assertEqual(blocked.status_code, 400)

    def test_trusted_host_allows_app_origin_host(self):
        client = TestClient(app)
        ok = client.get("/api/health", headers={"host": "localhost"})
        self.assertEqual(ok.status_code, 200)

    def test_allowed_origins_excludes_localhost_when_public_https(self):
        from backend.app import config as cfg

        saved_origin = os.environ.get("APP_ORIGIN")
        saved_dev = os.environ.get("INCLUDE_DEV_ORIGINS")
        try:
            os.environ["APP_ORIGIN"] = "https://collage.example.com"
            os.environ.pop("INCLUDE_DEV_ORIGINS", None)
            origins = cfg.allowed_origins()
            self.assertIn("https://collage.example.com", origins)
            self.assertNotIn("http://localhost:5173", origins)
        finally:
            if saved_origin is not None:
                os.environ["APP_ORIGIN"] = saved_origin
            if saved_dev is None:
                os.environ.pop("INCLUDE_DEV_ORIGINS", None)
            else:
                os.environ["INCLUDE_DEV_ORIGINS"] = saved_dev

    def test_allowed_origins_can_include_localhost_for_dev(self):
        from backend.app import config as cfg

        saved_origin = os.environ.get("APP_ORIGIN")
        saved_dev = os.environ.get("INCLUDE_DEV_ORIGINS")
        try:
            os.environ["APP_ORIGIN"] = "https://collage.example.com"
            os.environ["INCLUDE_DEV_ORIGINS"] = "1"
            origins = cfg.allowed_origins()
            self.assertIn("http://localhost:5173", origins)
            self.assertIn("http://127.0.0.1:8787", origins)
        finally:
            if saved_origin is not None:
                os.environ["APP_ORIGIN"] = saved_origin
            if saved_dev is None:
                os.environ.pop("INCLUDE_DEV_ORIGINS", None)
            else:
                os.environ["INCLUDE_DEV_ORIGINS"] = saved_dev


class CleanupTest(unittest.TestCase):
    def _backdated_project(self, email, age_days):
        client = _authed_client(email)
        pid = client.post("/api/projects").json()["id"]
        path = _storage.project_json_path(pid)
        doc = json.loads(path.read_text())
        doc["updated_at"] = int(__import__("time").time()) - age_days * 86400
        path.write_text(json.dumps(doc))
        return pid

    def test_sweep_deletes_old_keeps_recent(self):
        from backend.app import cleanup

        old = self._backdated_project("sweepold@example.com", 90)
        new = self._backdated_project("sweepnew@example.com", 1)
        stats = cleanup.sweep(retention_days=60)
        self.assertGreaterEqual(stats["projects_deleted"], 1)
        self.assertFalse(_storage.project_exists(old))
        self.assertTrue(_storage.project_exists(new))

    def test_retention_zero_keeps_everything(self):
        from backend.app import cleanup

        pid = self._backdated_project("sweepkeep@example.com", 999)
        cleanup.sweep(retention_days=0)
        self.assertTrue(_storage.project_exists(pid))

    def test_sweep_purges_expired_otp_only(self):
        import time

        from backend.app import cleanup

        now = int(time.time())
        _otp._save("expired@qa.local", {"code_hash": "x", "expires_at": now - 10, "attempts": 0, "sends": []})
        _otp._save("fresh@qa.local", {"code_hash": "x", "expires_at": now + 600, "attempts": 0, "sends": []})
        cleanup.sweep(retention_days=0)
        self.assertFalse(_otp._otp_path("expired@qa.local").exists())
        self.assertTrue(_otp._otp_path("fresh@qa.local").exists())


class SecurityHardeningTest(unittest.TestCase):
    def _client_and_project(self, email):
        client = _authed_client(email)
        pid = client.post("/api/projects").json()["id"]
        return client, pid

    def test_too_many_files_rejected(self):
        from backend.app import config as cfg

        client, pid = self._client_and_project("upmany@example.com")
        n = cfg.max_files_per_upload() + 1
        files = [("files", (f"f{i}.png", _png_bytes(50, 50), "image/png")) for i in range(n)]
        self.assertEqual(client.post(f"/api/projects/{pid}/images", files=files).status_code, 413)

    def test_oversize_megapixel_rejected(self):
        client, pid = self._client_and_project("upbig@example.com")
        os.environ["MAX_IMAGE_MEGAPIXELS"] = "1"  # 1500x1500 = 2.25 MP > 1
        try:
            r = client.post(
                f"/api/projects/{pid}/images",
                files=[("files", ("big.png", _png_bytes(1500, 1500), "image/png"))],
            )
            self.assertEqual(r.status_code, 400)
        finally:
            os.environ.pop("MAX_IMAGE_MEGAPIXELS", None)
        ok = client.post(
            f"/api/projects/{pid}/images",
            files=[("files", ("ok.png", _png_bytes(800, 600), "image/png"))],
        )
        self.assertEqual(ok.status_code, 200)

    def test_aggregate_upload_request_size_is_bounded(self):
        client, pid = self._client_and_project("uptotal@example.com")
        first = _png_bytes(100, 100, "red")
        second = _png_bytes(100, 100, "blue")
        self.assertGreater(len(first) + len(second), 400)

        saved = os.environ.get("MAX_UPLOAD_REQUEST_BYTES")
        os.environ["MAX_UPLOAD_REQUEST_BYTES"] = "400"
        try:
            response = client.post(
                f"/api/projects/{pid}/images",
                files=[
                    ("files", ("first.png", first, "image/png")),
                    ("files", ("second.png", second, "image/png")),
                ],
            )
            self.assertEqual(response.status_code, 413)
            self.assertIn("upload request too large", response.json()["detail"])
        finally:
            if saved is None:
                os.environ.pop("MAX_UPLOAD_REQUEST_BYTES", None)
            else:
                os.environ["MAX_UPLOAD_REQUEST_BYTES"] = saved

    def test_uploaded_name_is_sanitized(self):
        client, pid = self._client_and_project("upname@example.com")
        r = client.post(
            f"/api/projects/{pid}/images",
            files=[("files", ("<img src=x onerror=alert(1)>.png", _png_bytes(80, 60), "image/png"))],
        )
        self.assertEqual(r.status_code, 200)
        name = r.json()["images"][-1]["name"]
        self.assertNotIn("<", name)
        self.assertNotIn(">", name)

    def test_logout_revokes_sessions(self):
        import time

        import jwt

        from backend.app import config as cfg
        from backend.app import storage as st

        email = "revoke@example.com"
        _allow(email)
        now = int(time.time())
        token = jwt.encode(
            {"sub": email, "iat": now - 100, "exp": now + 100000},
            cfg.session_secret(),
            algorithm="HS256",
        )
        c = TestClient(app)
        self.assertEqual(c.get("/api/auth/me", cookies={"session": token}).status_code, 200)
        st.revoke_user_sessions(email, now=now - 50)  # epoch after the token's iat
        self.assertEqual(c.get("/api/auth/me", cookies={"session": token}).status_code, 401)

    def test_logout_endpoint_bumps_epoch(self):
        from backend.app import storage as st

        email = "revoke2@example.com"
        c = _authed_client(email)
        c.post("/api/auth/logout")
        self.assertGreater(st.user_session_epoch(email), 0)

    def test_export_rejects_outputs_above_pixel_budget(self):
        client, pid = self._client_and_project("exportcap@example.com")
        client.post(
            f"/api/projects/{pid}/images",
            files=[("files", ("a.png", _png_bytes(800, 600), "image/png"))],
        )
        os.environ["MAX_EXPORT_MEGAPIXELS"] = "40"
        try:
            al = client.post(
                f"/api/projects/{pid}/auto-layout",
                json={"settings": {"paper_size": "100x100cm", "orientation": "portrait"}},
            )
            self.assertEqual(al.status_code, 200)
            ex = client.post(f"/api/projects/{pid}/export", json={"format": "png"})
            self.assertEqual(ex.status_code, 413)
            self.assertIn("export too large", ex.json()["detail"])
        finally:
            os.environ.pop("MAX_EXPORT_MEGAPIXELS", None)

    def test_export_allows_a4_under_default_pixel_budget(self):
        client, pid = self._client_and_project("exportok@example.com")
        client.post(
            f"/api/projects/{pid}/images",
            files=[("files", ("a.png", _png_bytes(800, 600), "image/png"))],
        )
        al = client.post(
            f"/api/projects/{pid}/auto-layout",
            json={"settings": {"paper_size": "A4", "orientation": "landscape"}},
        )
        self.assertEqual(al.status_code, 200)
        ex = client.post(f"/api/projects/{pid}/export", json={"format": "png"})
        self.assertEqual(ex.status_code, 200)

    def test_default_export_budget_allows_largest_paper(self):
        # The user-chosen 200 MP default must cover every supported paper size
        # (largest is 100x140cm = 195 MP) so the big-size feature never 413s by
        # default. Checked at the budget-math level to avoid a 139 MP render.
        from backend.app import config as cfg
        from backend.app import main as m

        budget = cfg.max_export_megapixels() * 1_000_000
        for paper in ("100x100cm", "100x140cm", "A0", "A1", "70x100cm"):
            doc = {"settings": {"paper_size": paper, "orientation": "portrait"}}
            self.assertLessEqual(m._export_pixel_count(doc), budget, paper)

    def test_default_resource_caps_match_selected_policy(self):
        from backend.app import config as cfg

        self.assertEqual(cfg.max_files_per_upload(), 60)
        self.assertEqual(cfg.max_upload_bytes(), 40 * 1024 * 1024)
        self.assertEqual(cfg.max_upload_request_bytes(), 50 * 1024 * 1024)
        self.assertEqual(cfg.max_export_megapixels(), 200)

    def test_max_upload_bytes_is_configurable(self):
        from backend.app import config as cfg

        saved = os.environ.get("MAX_UPLOAD_BYTES")
        try:
            os.environ["MAX_UPLOAD_BYTES"] = "1024"
            self.assertEqual(cfg.max_upload_bytes(), 1024)
        finally:
            if saved is None:
                os.environ.pop("MAX_UPLOAD_BYTES", None)
            else:
                os.environ["MAX_UPLOAD_BYTES"] = saved

    def test_export_route_is_rate_limited(self):
        from unittest import mock

        client, pid = self._client_and_project("exportlimit@example.com")
        client.post(
            f"/api/projects/{pid}/images",
            files=[("files", ("a.png", _png_bytes(800, 600), "image/png"))],
        )
        client.post(f"/api/projects/{pid}/auto-layout", json={})
        limiter.enabled = True
        try:
            with mock.patch("backend.app.main.render_service.export_project"):
                statuses = [
                    client.post(f"/api/projects/{pid}/export", json={"format": "png"}).status_code
                    for _ in range(7)
                ]
        finally:
            limiter.enabled = False
        self.assertEqual(statuses[:6], [200] * 6)
        self.assertEqual(statuses[6], 429)


class PersistenceConcurrencyTest(unittest.TestCase):
    """Regression for the project.json corruption that made collages vanish:
    concurrent save_project calls must never produce an unparseable file."""

    def _item(self, i):
        return {
            "image_id": f"img{i}",
            "x": 0.5,
            "y": 0.5,
            "width": 0.2,
            "height": 0.2,
            "rotation": 0.0,
            "z_index": i,
            "look": "paper",
        }

    def test_load_recovers_from_trailing_garbage(self):
        doc = _storage.create_project(owner="torn@example.com")
        doc["layout"] = [self._item(i) for i in range(3)]
        _storage.save_project(doc)
        path = _storage.project_json_path(doc["id"])
        good = path.read_text(encoding="utf-8")
        # Simulate a torn write: a valid document + leftover tail from an older,
        # longer write (the exact "Extra data" corruption seen in production).
        path.write_text(good + "\n}\n  EXTRA OLD TAIL\n", encoding="utf-8")
        loaded = _storage.load_project(doc["id"])
        self.assertEqual(loaded["id"], doc["id"])
        self.assertEqual(len(loaded["layout"]), 3)

    def test_concurrent_saves_never_corrupt(self):
        import threading

        doc = _storage.create_project(owner="race@example.com")
        pid = doc["id"]
        errors: list = []

        def writer(k):
            try:
                for _ in range(25):
                    d = dict(doc)
                    # Vary payload size so a shared temp file would leave a
                    # longer write's tail behind a shorter one -> "Extra data".
                    d["layout"] = [self._item(i) for i in range(k * 6)]
                    _storage.save_project(d)
                    got = _storage.load_project(pid)  # must always parse cleanly
                    if not isinstance(got, dict) or got.get("id") != pid:
                        raise AssertionError("loaded a non-dict / wrong project")
            except Exception as exc:  # noqa: BLE001
                errors.append(repr(exc))

        threads = [threading.Thread(target=writer, args=(k,)) for k in range(1, 9)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"concurrent save/load corrupted state: {errors}")
        final = _storage.load_project(pid)
        self.assertEqual(final["id"], pid)
        # No stray temp files left behind.
        leftovers = list(_storage.project_dir(pid).glob("*.tmp"))
        self.assertEqual(leftovers, [], f"temp files leaked: {leftovers}")

    def test_project_lock_prevents_lost_append(self):
        """Concurrent read-modify-write under project_lock must not lose updates
        (the 'uploaded photo vanishes behind a stale layout PUT' class)."""
        import threading

        doc = _storage.create_project(owner="lock@example.com")
        pid = doc["id"]
        n = 60

        def appender(k):
            with _storage.project_lock(pid):
                d = _storage.load_project(pid)
                d.setdefault("images", []).append(
                    {"id": f"img{k}", "filename": f"{k}.png", "name": "x", "width": 1, "height": 1}
                )
                _storage.save_project(d)

        threads = [threading.Thread(target=appender, args=(k,)) for k in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        final = _storage.load_project(pid)
        self.assertEqual(len(final.get("images", [])), n, "a concurrent append was lost")

    def test_otp_attempt_cap_holds_under_concurrency(self):
        """Concurrent wrong guesses must not bypass MAX_ATTEMPTS: after a storm of
        wrong attempts the code is burned, so the correct code no longer works."""
        import threading

        from backend.app import otp as _o

        email = "brute@example.com"
        code, reason = _o.create_otp(email)
        self.assertIsNotNone(code, f"create_otp refused: {reason}")
        wrong = f"{(int(code) + 1) % 1_000_000:06d}"

        def guess():
            _o.verify_otp(email, wrong)

        threads = [threading.Thread(target=guess) for _ in range(30)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # >5 wrong attempts -> code burned -> even the correct code must fail.
        self.assertFalse(_o.verify_otp(email, code), "attempt cap was bypassed")


class MarginGuideTest(unittest.TestCase):
    def test_setting_round_trips_and_defaults(self):
        c = _authed_client("guide@example.com")
        pid = c.post("/api/projects").json()["id"]
        s0 = c.get(f"/api/projects/{pid}").json()["settings"]
        self.assertEqual(s0["margin_guide"], False)
        self.assertEqual(s0["margin_guide_mm"], 10.0)

        r = c.put(
            f"/api/projects/{pid}",
            json={"settings": {"margin_guide": True, "margin_guide_mm": 12.5}},
        )
        self.assertEqual(r.status_code, 200)
        s1 = c.get(f"/api/projects/{pid}").json()["settings"]
        self.assertTrue(s1["margin_guide"])
        self.assertEqual(s1["margin_guide_mm"], 12.5)

    def test_export_layout_excludes_margin_guide(self):
        # The renderer must never receive the margin-guide settings, so it can't
        # be drawn into the export.
        from backend.app import render_service

        doc = {
            "id": "a" * 32,
            "settings": {"margin_guide": True, "margin_guide_mm": 20.0},
            "images": [],
            "layout": [],
        }
        layout = render_service.build_collage_layout(doc)
        self.assertNotIn("margin_guide", layout)
        self.assertNotIn("margin_guide", str(layout))


if __name__ == "__main__":
    unittest.main()
