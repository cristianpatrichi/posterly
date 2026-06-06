"""Adapter between persisted projects and the ``collage_a4`` renderer.

Two responsibilities only:

1. :func:`generate_auto_layout` -- compute a normalized starting arrangement by
   REUSING ``collage_a4.balanced_slots`` and ``collage_a4.scatter_sizes`` (the
   collage math is not reimplemented here). It honors the project settings
   (seed, spacing, rotation_intensity, order_mode, orientation) and each image's
   natural aspect ratio.
2. :func:`export_project` -- build a layout dict from the persisted settings +
   layout, map each ``image_id`` to its absolute upload path, translate the
   ``border``/``feather`` sliders to pixel render params, and call
   ``collage_a4.create_collage_from_layout``.
"""

from __future__ import annotations

import os
import random
import tempfile
from pathlib import Path
from typing import Optional

import collage_a4
from collage_a4 import (
    balanced_slots,
    canvas_size_for,
    create_collage_from_layout,
    scatter_sizes,
)

from . import storage

# Rotation slider 0..1 -> max absolute rotation in degrees. 1.0 ~ the CLI's
# +-8.5deg jitter range, slightly extended to ~12deg per the design note.
MAX_ROTATION_DEGREES = 12.0

# Margin (page padding in px) interpolated from the spacing slider. More spacing
# => larger margin => photos pulled inward and smaller. The endpoints bracket the
# CLI's hand-tuned margins (105/120) so the default sits in a familiar range.
MIN_MARGIN_PX = 80
MAX_MARGIN_PX = 220

# Soft-oval/paper white border (px) interpolated from the border slider. Kept in
# a tasteful range: even at max it's a subtle print frame (~1.6% of the A4 width),
# so the export reads as framed photos rather than huge white cards, and stays
# close to the frontend preview. 0.5 lands near the CLI's hand-tuned ~30-38px.
MIN_BORDER_PX = 10
MAX_BORDER_PX = 56

# Soft-oval feather/glow radius (px) interpolated from the feather slider.
MIN_FEATHER_PX = 2
MAX_FEATHER_PX = 70


def _lerp(low: float, high: float, t: float) -> float:
    t = max(0.0, min(1.0, t))
    return low + (high - low) * t


def generate_auto_layout(images: list[dict], settings: dict) -> list[dict]:
    """Compute a normalized starting layout for ``images`` given ``settings``.

    Returns a list of item dicts, one per image, each shaped as::

        {image_id, x, y, width, height, rotation, z_index, look}

    Coordinates and sizes are normalized canvas fractions (0..1), ready to feed
    straight into ``create_collage_from_layout`` (via :func:`export_project`).
    """
    if not images:
        return []

    orientation = settings.get("orientation", "landscape")
    paper = settings.get("paper_size", "A4")
    look = settings.get("look", "soft-oval")
    order_mode = settings.get("order_mode", "random")
    seed = settings.get("seed")
    spacing = float(settings.get("spacing", 0.5))
    rotation_intensity = float(settings.get("rotation_intensity", 0.5))

    canvas_w, canvas_h = canvas_size_for(orientation, paper)
    rng = random.Random(seed)

    # Order: "random" shuffles deterministically with the seed; "manual" keeps the
    # existing image order (e.g. upload order or a prior arrangement).
    ordered = list(images)
    if order_mode == "random":
        rng.shuffle(ordered)

    margin = int(round(_lerp(MIN_MARGIN_PX, MAX_MARGIN_PX, spacing)))
    max_rotation = _lerp(0.0, MAX_ROTATION_DEGREES, rotation_intensity)

    slots = balanced_slots(len(ordered), (canvas_w, canvas_h), margin, rng=rng)

    items: list[dict] = []
    for index, (image, slot) in enumerate(zip(ordered, slots)):
        slot_x, slot_y, slot_w, slot_h = slot

        # Reuse the CLI's size scatter to pick a target box inside the slot.
        target_w, target_h = scatter_sizes(len(ordered), slot_w, slot_h, rng)

        # Respect the photo's natural aspect ratio so it is not over-cropped:
        # fit the natural box inside the scattered target box (contain).
        nat_w = max(1, int(image.get("width", 1)))
        nat_h = max(1, int(image.get("height", 1)))
        aspect = nat_w / nat_h
        if target_w / target_h > aspect:
            # Target too wide for the photo: clamp width to match aspect.
            target_w = target_h * aspect
        else:
            # Target too tall: clamp height to match aspect.
            target_h = target_w / aspect

        # Clamp the target box to the canvas BEFORE normalizing. scatter_sizes
        # applies a *1.15 boost for small image counts, so a square/tall photo's
        # contained box can exceed the canvas height; without this clamp the
        # normalized width/height could exceed 1.0 and LayoutItem (le=1.0) would
        # reject it (HTTP 500). Clamping keeps normalized sizes in 0..1.
        target_w = min(target_w, canvas_w)
        target_h = min(target_h, canvas_h)

        rotation = rng.uniform(-max_rotation, max_rotation) if max_rotation > 0 else 0.0

        items.append(
            {
                "image_id": image["id"],
                "x": slot_x / canvas_w,
                "y": slot_y / canvas_h,
                "width": target_w / canvas_w,
                "height": target_h / canvas_h,
                "rotation": rotation,
                "z_index": index,
                "look": look,
            }
        )

    return items


def _border_px(settings: dict) -> int:
    return int(round(_lerp(MIN_BORDER_PX, MAX_BORDER_PX, float(settings.get("border", 0.5)))))


def _feather_px(settings: dict) -> int:
    return int(round(_lerp(MIN_FEATHER_PX, MAX_FEATHER_PX, float(settings.get("feather", 0.5)))))


def build_collage_layout(project: dict) -> dict:
    """Translate a persisted project into a ``create_collage_from_layout`` dict.

    Resolves every layout item's ``image_id`` to the ABSOLUTE path of the stored
    upload. Items whose image is missing on disk (or unknown id) are skipped.
    """
    settings = project.get("settings", {})
    project_id = project["id"]

    by_id = {image["id"]: image for image in project.get("images", [])}

    items: list[dict] = []
    for entry in project.get("layout", []):
        image = by_id.get(entry["image_id"])
        if image is None:
            continue
        path: Optional[Path] = storage.resolve_upload_path(project_id, image["filename"])
        if path is None:
            continue
        item: dict = {
            "image_path": str(path),
            "x": entry["x"],
            "y": entry["y"],
            "width": entry["width"],
            "height": entry["height"],
            "rotation": entry.get("rotation", 0.0),
            "z_index": entry.get("z_index", 0),
            "look": entry.get("look", settings.get("look", "soft-oval")),
        }
        # Per-photo feather override (0..1) -> px; None falls back to the global.
        override = entry.get("feather")
        if override is not None:
            item["feather"] = _feather_px({"feather": override})
        items.append(item)

    return {
        "orientation": settings.get("orientation", "landscape"),
        "paper": settings.get("paper_size", "A4"),
        "background": settings.get("background", "#f4efe6"),
        "items": items,
    }


def export_project(project: dict, write_png: bool = True, write_pdf: bool = True):
    """Render a project to its ``exports/`` dir.

    Returns ``(png_path, pdf_path)``. Either path may be ``None`` when that
    format was not requested. Raises ``ValueError`` if there is nothing
    renderable (no resolvable layout items).
    """
    settings = project.get("settings", {})
    project_id = project["id"]

    layout = build_collage_layout(project)
    if not layout["items"]:
        raise ValueError("nothing to render: project has no resolvable layout items")

    png_target = storage.export_png_path(project_id)
    pdf_target = storage.export_pdf_path(project_id)

    border = _border_px(settings)
    feather = _feather_px(settings)

    # Render to UNIQUE temp files, then os.replace into the final paths. This
    # makes export atomic: a download (or a concurrent export) never sees a
    # half-written PNG/PDF, and two concurrent exports can't interleave on the
    # fixed collage-print.* paths. (Same atomic-write discipline as save_project.)
    exports_dir = png_target.parent
    exports_dir.mkdir(parents=True, exist_ok=True)
    # Temp files keep the real .png/.pdf extension so PIL infers the format; the
    # ".exp-" prefix marks them as in-flight export artifacts for the cleanup
    # sweep to reap if a render ever crashes mid-write.
    fd_png, tmp_png = tempfile.mkstemp(dir=str(exports_dir), prefix=".exp-", suffix=".png")
    os.close(fd_png)
    tmp_pdf: Optional[str] = None
    if write_pdf:
        fd_pdf, tmp_pdf = tempfile.mkstemp(dir=str(exports_dir), prefix=".exp-", suffix=".pdf")
        os.close(fd_pdf)
    try:
        # create_collage_from_layout ALWAYS writes the PNG and optionally the PDF.
        create_collage_from_layout(
            layout,
            tmp_png,
            tmp_pdf,
            border=border,
            feather=feather,
        )
        if write_png:
            os.replace(tmp_png, str(png_target))
        if write_pdf and tmp_pdf is not None:
            os.replace(tmp_pdf, str(pdf_target))
    finally:
        for leftover in (tmp_png, tmp_pdf):
            if leftover and os.path.exists(leftover):
                try:
                    os.unlink(leftover)
                except OSError:
                    pass

    return (
        png_target if write_png else None,
        pdf_target if write_pdf else None,
    )
