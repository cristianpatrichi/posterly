import tempfile
import unittest
from pathlib import Path

from PIL import Image

from collage_a4 import (
    A4_LANDSCAPE,
    A4_PORTRAIT,
    CollageLayout,
    PhotoLayout,
    balanced_slots,
    create_collage,
    create_collage_from_layout,
    soft_oval_photo,
)


class CollageA4Test(unittest.TestCase):
    def test_creates_a4_landscape_png_and_pdf_at_300_dpi(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = tmp_path / "photos"
            input_dir.mkdir()

            for index, color in enumerate(["red", "green", "blue", "yellow", "purple"]):
                image = Image.new("RGB", (900 + index * 40, 700), color)
                image.save(input_dir / f"photo-{index}.jpg", quality=95)

            png_path = tmp_path / "collage-a4-print.png"
            pdf_path = tmp_path / "collage-a4-print.pdf"

            create_collage(input_dir, png_path, pdf_path, seed=7)

            self.assertTrue(png_path.exists())
            self.assertTrue(pdf_path.exists())

            with Image.open(png_path) as result:
                self.assertEqual(result.size, A4_LANDSCAPE)
                dpi_x, dpi_y = result.info.get("dpi")
                self.assertAlmostEqual(dpi_x, 300.0, places=2)
                self.assertAlmostEqual(dpi_y, 300.0, places=2)

    def test_places_optional_center_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = tmp_path / "photos"
            input_dir.mkdir()

            Image.new("RGB", (700, 900), "red").save(input_dir / "center.jpg", quality=95)
            Image.new("RGB", (900, 700), "green").save(input_dir / "photo-1.jpg", quality=95)
            Image.new("RGB", (900, 700), "blue").save(input_dir / "photo-2.jpg", quality=95)

            png_path = tmp_path / "collage-a4-print.png"
            create_collage(
                input_dir,
                png_path,
                center_image=input_dir / "center.jpg",
                seed=2,
            )

            with Image.open(png_path) as result:
                center_pixel = result.getpixel((A4_LANDSCAPE[0] // 2, A4_LANDSCAPE[1] // 2))
                self.assertGreater(center_pixel[0], 200)
                self.assertLess(center_pixel[1], 80)
                self.assertLess(center_pixel[2], 80)

    def test_balanced_slots_spread_many_photos_across_page(self):
        slots = balanced_slots(21, A4_LANDSCAPE, 145, seed=12)
        centers = [(slot[0], slot[1]) for slot in slots]

        self.assertEqual(len(slots), 21)
        self.assertGreaterEqual(sum(1 for x, _ in centers if x > A4_LANDSCAPE[0] * 0.72), 3)
        self.assertGreaterEqual(sum(1 for _, y in centers if y > A4_LANDSCAPE[1] * 0.70), 4)
        self.assertGreaterEqual(sum(1 for x, _ in centers if x < A4_LANDSCAPE[0] * 0.28), 3)

    def test_soft_oval_photo_fades_image_into_white_frame(self):
        image = Image.new("RGB", (600, 400), "red")
        rendered = soft_oval_photo(image, (400, 280), border=40, feather=28, shadow=18)

        self.assertEqual(rendered.mode, "RGBA")
        center = rendered.getpixel((rendered.width // 2, rendered.height // 2))
        inner_corner = rendered.getpixel((60, 60))
        outer_corner = rendered.getpixel((2, 2))

        self.assertGreater(center[0], 220)
        self.assertLess(center[1], 60)
        self.assertGreater(inner_corner[0], 235)
        self.assertGreater(inner_corner[1], 235)
        self.assertGreater(inner_corner[2], 235)
        self.assertLess(outer_corner[3], 25)


    def test_manual_layout_controls_position_rotation_and_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            red_path = tmp_path / "red.jpg"
            blue_path = tmp_path / "blue.jpg"
            green_path = tmp_path / "green.jpg"
            Image.new("RGB", (600, 600), "red").save(red_path, quality=95)
            Image.new("RGB", (600, 600), "blue").save(blue_path, quality=95)
            Image.new("RGB", (600, 600), "green").save(green_path, quality=95)

            layout = CollageLayout(
                orientation="landscape",
                background="white",
                items=[
                    # Overlap at center: blue has higher z, must win.
                    PhotoLayout(
                        image_path=str(red_path),
                        x=0.5,
                        y=0.5,
                        width=0.25,
                        height=0.25,
                        rotation=0.0,
                        z_index=0,
                        look="paper",
                    ),
                    PhotoLayout(
                        image_path=str(blue_path),
                        x=0.5,
                        y=0.5,
                        width=0.25,
                        height=0.25,
                        rotation=0.0,
                        z_index=5,
                        look="paper",
                    ),
                    # Distinct position to prove x/y placement, not full-canvas fill.
                    PhotoLayout(
                        image_path=str(green_path),
                        x=0.2,
                        y=0.2,
                        width=0.18,
                        height=0.18,
                        rotation=0.0,
                        z_index=1,
                        look="paper",
                    ),
                ],
            )

            png_path = tmp_path / "manual.png"
            create_collage_from_layout(layout, png_path)
            self.assertTrue(png_path.exists())

            width, height = A4_LANDSCAPE
            with Image.open(png_path) as result:
                self.assertEqual(result.size, A4_LANDSCAPE)

                # z-order: higher z_index (blue) drawn on top at the shared center.
                center_pixel = result.getpixel((width // 2, height // 2))
                self.assertLess(center_pixel[0], 80)
                self.assertLess(center_pixel[1], 80)
                self.assertGreater(center_pixel[2], 200)

                # position: green item sits at its own normalized center.
                # PIL "green" == (0, 128, 0), so the green channel maxes at 128.
                green_pixel = result.getpixel((int(0.2 * width), int(0.2 * height)))
                self.assertLess(green_pixel[0], 80)
                self.assertGreater(green_pixel[1], 100)
                self.assertLess(green_pixel[2], 80)

                # background shows in a far empty corner (not a full-canvas paste).
                corner_pixel = result.getpixel((width - 5, height - 5))
                self.assertGreater(corner_pixel[0], 230)
                self.assertGreater(corner_pixel[1], 230)
                self.assertGreater(corner_pixel[2], 230)

            # rotation: a 45deg item exposes its corner area to the background where
            # an unrotated item would have covered it.
            single_axis_red = CollageLayout(
                orientation="landscape",
                background="white",
                items=[
                    PhotoLayout(
                        image_path=str(red_path),
                        x=0.5,
                        y=0.5,
                        width=0.3,
                        height=0.3,
                        rotation=0.0,
                        look="paper",
                    )
                ],
            )
            rotated_red = CollageLayout(
                orientation="landscape",
                background="white",
                items=[
                    PhotoLayout(
                        image_path=str(red_path),
                        x=0.5,
                        y=0.5,
                        width=0.3,
                        height=0.3,
                        rotation=45.0,
                        look="paper",
                    )
                ],
            )

            flat_png = tmp_path / "flat.png"
            spun_png = tmp_path / "spun.png"
            create_collage_from_layout(single_axis_red, flat_png)
            create_collage_from_layout(rotated_red, spun_png)

            # Sample the corner region of the unrotated square. The "paper" look is a
            # solid red rectangle (plus white frame/shadow) centred on the page; its
            # half-extent along each axis comfortably covers center +- 0.14 in BOTH
            # axes, so that bounding-box corner is solidly red when flat.
            #
            # After a 45deg rotation the rectangle keeps its area but its silhouette
            # becomes a diamond. The reach along a pure diagonal grows, but the reach
            # into a pure-diagonal *corner* of the old bounding box collapses: the
            # diamond's edge is pulled back toward the square's half-side. So the
            # (+0.14, -0.14) corner, at diagonal offset ~0.20 from center, falls
            # clearly OUTSIDE the diamond and reverts to the white background. This is
            # a categorical flip (solid red vs solid white), not a marginal delta.
            sample_x = int((0.5 + 0.14) * width)
            sample_y = int((0.5 - 0.14) * height)

            def region_avg(image, cx, cy, radius=20):
                reds = greens = blues = samples = 0
                for dx in range(-radius, radius + 1, 5):
                    for dy in range(-radius, radius + 1, 5):
                        px = image.getpixel((cx + dx, cy + dy))
                        reds += px[0]
                        greens += px[1]
                        blues += px[2]
                        samples += 1
                return (reds / samples, greens / samples, blues / samples)

            with Image.open(flat_png) as flat_result:
                flat_avg = region_avg(flat_result, sample_x, sample_y)
            with Image.open(spun_png) as spun_result:
                spun_avg = region_avg(spun_result, sample_x, sample_y)

            # Flat: that corner is solidly the photo color (red ~ (255, 0, 0)).
            self.assertGreater(flat_avg[0], 230)
            self.assertLess(flat_avg[1], 40)
            self.assertLess(flat_avg[2], 40)
            # Rotated: the same corner is now the white background (all channels high).
            self.assertGreater(spun_avg[0], 230)
            self.assertGreater(spun_avg[1], 230)
            self.assertGreater(spun_avg[2], 230)

    def test_layout_accepts_plain_dict_input_portrait(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            red_path = tmp_path / "red.jpg"
            Image.new("RGB", (600, 600), "red").save(red_path, quality=95)

            # Layout-as-dict with item-as-dict, the shape the backend will pass.
            layout = {
                "orientation": "portrait",
                "background": "white",
                "items": [
                    {
                        "image_path": str(red_path),
                        "x": 0.5,
                        "y": 0.5,
                        "width": 0.3,
                        "height": 0.3,
                        "look": "paper",
                    }
                ],
            }

            png_path = tmp_path / "dict.png"
            create_collage_from_layout(layout, png_path)

            self.assertTrue(png_path.exists())
            with Image.open(png_path) as result:
                self.assertEqual(result.size, A4_PORTRAIT)

    def test_tiny_soft_oval_item_does_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            red_path = tmp_path / "red.jpg"
            Image.new("RGB", (600, 600), "red").save(red_path, quality=95)

            # A near-zero soft-oval box used to invert the ellipse box and raise
            # ValueError from draw.ellipse; it must now render successfully.
            layout = CollageLayout(
                orientation="landscape",
                background="white",
                items=[
                    PhotoLayout(
                        image_path=str(red_path),
                        x=0.5,
                        y=0.5,
                        width=0.001,
                        height=0.001,
                        look="soft-oval",
                    )
                ],
            )

            png_path = tmp_path / "tiny.png"
            create_collage_from_layout(layout, png_path)

            self.assertTrue(png_path.exists())
            with Image.open(png_path) as result:
                self.assertEqual(result.size, A4_LANDSCAPE)

    def test_zero_or_negative_size_item_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            red_path = tmp_path / "red.jpg"
            Image.new("RGB", (600, 600), "red").save(red_path, quality=95)

            layout = CollageLayout(
                orientation="landscape",
                background="white",
                items=[
                    PhotoLayout(
                        image_path=str(red_path),
                        x=0.5,
                        y=0.5,
                        width=0.0,
                        height=0.25,
                        look="soft-oval",
                    ),
                    PhotoLayout(
                        image_path=str(red_path),
                        x=0.5,
                        y=0.5,
                        width=0.25,
                        height=-0.1,
                        look="paper",
                    ),
                ],
            )

            png_path = tmp_path / "skipped.png"
            create_collage_from_layout(layout, png_path)

            self.assertTrue(png_path.exists())
            with Image.open(png_path) as result:
                self.assertEqual(result.size, A4_LANDSCAPE)
                # Both items were skipped, so the center stays the white background.
                center_pixel = result.getpixel((A4_LANDSCAPE[0] // 2, A4_LANDSCAPE[1] // 2))
                self.assertGreater(center_pixel[0], 230)
                self.assertGreater(center_pixel[1], 230)
                self.assertGreater(center_pixel[2], 230)


if __name__ == "__main__":
    unittest.main()
