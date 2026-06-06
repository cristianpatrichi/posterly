#!/usr/bin/env python3
import argparse
import math
import random
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageOps


DPI = 300
A4_PORTRAIT = (2480, 3508)
A4_LANDSCAPE = (3508, 2480)

# Print sizes in PORTRAIT pixel dimensions at 300 DPI (short side, long side).
# Export always stays 300 DPI; the pixel count scales with the chosen paper so
# photos are rendered at full print resolution. A4 matches A4_PORTRAIT exactly,
# so the CLI auto-layout stays byte-identical.
PAPER_SIZES = {
    "A5": (1748, 2480),   # 148 x 210 mm
    "A4": (2480, 3508),   # 210 x 297 mm
    "A3": (3508, 4961),   # 297 x 420 mm
    "A2": (4961, 7016),   # 420 x 594 mm
    "A1": (7016, 9933),   # 594 x 841 mm
    "A0": (9933, 14043),  # 841 x 1189 mm
    "letter": (2550, 3300),  # 8.5 x 11 in
    "legal": (2550, 4200),   # 8.5 x 14 in
    "30x40cm": (3543, 4724),    # 30 x 40 cm
    "50x70cm": (5906, 8268),    # 50 x 70 cm
    "70x100cm": (8268, 11811),  # 70 x 100 cm
    "100x100cm": (11811, 11811),  # 1 x 1 m (square)
    "100x140cm": (11811, 16535),  # 1 x 1.4 m
}

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}


@dataclass
class PhotoLayout:
    """A photo placed manually on the canvas, in normalized 0..1 coordinates."""

    image_path: str
    # Photo center on the canvas: x = fraction of width, y = fraction of height.
    x: float
    y: float
    # Target size before rotation, as a fraction of the canvas.
    width: float
    height: float
    # Degrees; positive = counter-clockwise, as in create_collage.
    rotation: float = 0.0
    # Higher z = drawn later (on top).
    z_index: int = 0
    # "soft-oval" or "paper".
    look: str = "soft-oval"
    # Optional per-photo feather (px). None => use the collage-wide feather.
    feather: float | None = None


@dataclass
class CollageLayout:
    """Page-level container: the canvas settings plus the list of photos."""

    orientation: str = "landscape"
    background: str = "#f4efe6"
    # Paper size (key from PAPER_SIZES); export stays at 300 DPI.
    paper: str = "A4"
    items: list = field(default_factory=list)


def find_images(input_dir):
    input_path = Path(input_dir)
    if not input_path.exists():
        raise FileNotFoundError(f"Folder not found: {input_path}")

    images = [
        path
        for path in sorted(input_path.iterdir())
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    ]
    if not images:
        raise ValueError(f"No images found in: {input_path}")
    return images


def load_image(path):
    with Image.open(path) as image:
        return ImageOps.exif_transpose(image).convert("RGB")


def fit_photo(image, target_width, target_height):
    return ImageOps.fit(
        image,
        (int(target_width), int(target_height)),
        method=Image.Resampling.LANCZOS,
        centering=(0.5, 0.45),
    )


def framed_photo(image, size, border=30, shadow=30, feather=None):
    # feather is accepted for a uniform render_photo signature; the paper look has
    # no soft edge, so it is intentionally ignored.
    photo = fit_photo(image, size[0], size[1])
    framed = ImageOps.expand(photo, border=2, fill=(230, 226, 218))
    framed = ImageOps.expand(framed, border=border, fill="white").convert("RGBA")

    shadow_layer = Image.new("RGBA", (framed.width + shadow * 2, framed.height + shadow * 2), (0, 0, 0, 0))
    shadow_mask = Image.new("RGBA", framed.size, (0, 0, 0, 76))
    shadow_layer.alpha_composite(shadow_mask, (shadow + 8, shadow + 10))
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(radius=max(8, shadow // 2)))
    shadow_layer.alpha_composite(framed, (shadow - 8, shadow - 10))
    return shadow_layer


def soft_oval_photo(image, size, border=42, feather=26, shadow=30):
    photo = fit_photo(image, size[0], size[1]).convert("RGBA")
    paper_w = photo.width + border * 2
    paper_h = photo.height + border * 2

    paper = Image.new("RGBA", (paper_w, paper_h), (255, 255, 255, 255))
    paper = ImageOps.expand(paper, border=2, fill=(230, 226, 218)).convert("RGBA")

    mask = Image.new("L", photo.size, 0)
    draw = ImageDraw.Draw(mask)
    # Inner padding: inset the oval from the photo box by a small uniform margin
    # so an even white border surrounds the oval (this padding continues straight
    # into the card frame -- the oval starts right where the frame begins).
    pad = max(8, round(min(photo.size) * 0.08))
    # For very small photos, pad cannot exceed half the side.
    pad = min(pad, (photo.width - 1) // 2, (photo.height - 1) // 2)
    pad = max(0, pad)
    draw.ellipse((pad, pad, photo.width - pad, photo.height - pad), fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(radius=feather))

    # White glow halo around the oval. Both its softness AND its strength scale
    # with feather, so the minimum keeps the OVAL shape but with only a faint,
    # thin halo (~a quarter of the old fixed 24px / 190-alpha glow) instead of a
    # thick white band bleeding over the photo edge.
    fnorm = max(0.0, min(1.0, (feather - 2) / 68.0))
    glow_mask = mask.filter(ImageFilter.GaussianBlur(radius=max(feather, 6)))
    glow = Image.new("RGBA", photo.size, (255, 255, 255, int(round(48 + 142 * fnorm))))
    paper.alpha_composite(
        Image.composite(glow, Image.new("RGBA", photo.size, (0, 0, 0, 0)), glow_mask),
        (border + 2, border + 2),
    )

    oval_photo = Image.new("RGBA", photo.size, (0, 0, 0, 0))
    oval_photo.alpha_composite(photo)
    oval_photo.putalpha(mask)
    paper.alpha_composite(oval_photo, (border + 2, border + 2))

    shadow_layer = Image.new(
        "RGBA",
        (paper.width + shadow * 2, paper.height + shadow * 2),
        (0, 0, 0, 0),
    )
    shadow_mask = Image.new("RGBA", paper.size, (0, 0, 0, 62))
    shadow_layer.alpha_composite(shadow_mask, (shadow + 8, shadow + 10))
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(radius=max(10, shadow // 2)))
    shadow_layer.alpha_composite(paper, (shadow - 8, shadow - 10))
    return shadow_layer


def render_photo(image, size, look="soft-oval", border=30, shadow=30, feather=None):
    # feather=None keeps the historical default (26) so existing callers and the
    # CLI auto-layout stay byte-identical; an explicit value threads through to the
    # soft-oval glow so the API border/feather sliders can change the output.
    if look == "paper":
        return framed_photo(image, size, border=border, shadow=shadow)
    effective_feather = 26 if feather is None else feather
    return soft_oval_photo(image, size, border=max(border, 38), feather=effective_feather, shadow=shadow)


def paste_rotated(canvas, photo, center, angle, edge_padding=42, clamp=True):
    rotated = photo.rotate(angle, expand=True, resample=Image.Resampling.BICUBIC)
    if clamp:
        max_left = max(edge_padding, canvas.width - rotated.width - edge_padding)
        max_top = max(edge_padding, canvas.height - rotated.height - edge_padding)
        left = int(max(edge_padding, min(max_left, center[0] - rotated.width / 2)))
        top = int(max(edge_padding, min(max_top, center[1] - rotated.height / 2)))
        canvas.alpha_composite(rotated, (left, top))
        return

    # Exact WYSIWYG placement: the center lands precisely on the given coordinate,
    # even if the photo partly extends off the page.
    left = int(round(center[0] - rotated.width / 2))
    top = int(round(center[1] - rotated.height / 2))

    # Compose ONLY the visible region (the intersection of the rotated photo with
    # the page), not a full-page layer. The result is pixel-identical to the old
    # approach (paste-with-mask on a transparent layer, then alpha_composite), but
    # memory and time scale with the photo size, not the page -- essential for
    # large formats (e.g. 1 m at 300 DPI ~ 139 MP).
    x0 = max(0, left)
    y0 = max(0, top)
    x1 = min(canvas.width, left + rotated.width)
    y1 = min(canvas.height, top + rotated.height)
    if x0 >= x1 or y0 >= y1:
        return  # fully off the page

    piece = rotated.crop((x0 - left, y0 - top, x1 - left, y1 - top))
    region = Image.new("RGBA", piece.size, (0, 0, 0, 0))
    region.paste(piece, (0, 0), piece)
    canvas.alpha_composite(region, (x0, y0))


def grid_slots(count, canvas_size, margin, rng):
    width, height = canvas_size
    usable_w = width - margin * 2
    usable_h = height - margin * 2
    ratio = usable_w / usable_h
    cols = max(1, math.ceil(math.sqrt(count * ratio)))
    rows = max(1, math.ceil(count / cols))
    cell_w = usable_w / cols
    cell_h = usable_h / rows

    slots = []
    for row in range(rows):
        for col in range(cols):
            if len(slots) == count:
                return slots
            jitter_x = rng.uniform(-0.12, 0.12) * cell_w
            jitter_y = rng.uniform(-0.12, 0.12) * cell_h
            slots.append((margin + cell_w * (col + 0.5) + jitter_x, margin + cell_h * (row + 0.5) + jitter_y, cell_w, cell_h))
    return slots


def balanced_slots(count, canvas_size, margin, seed=None, rng=None):
    rng = rng or random.Random(seed)
    width, height = canvas_size
    usable_w = width - margin * 2
    usable_h = height - margin * 2
    rows = max(1, round(math.sqrt(count * usable_h / usable_w)))
    rows = min(rows, count)
    base = count // rows
    remainder = count % rows
    row_counts = [base + (1 if row < remainder else 0) for row in range(rows)]

    if rows > 2:
        middle = rows // 2
        row_counts = row_counts[1:middle + 1] + row_counts[:1] + row_counts[middle + 1:]

    slots = []
    row_h = usable_h / rows
    for row, cols in enumerate(row_counts):
        cell_w = usable_w / cols
        y = margin + row_h * (row + 0.5)
        y += rng.uniform(-0.10, 0.10) * row_h
        x_shift = rng.uniform(-0.06, 0.06) * cell_w

        for col in range(cols):
            x = margin + cell_w * (col + 0.5) + x_shift
            x += rng.uniform(-0.08, 0.08) * cell_w
            slots.append((x, y, cell_w * 1.10, row_h * 1.12))

    return slots[:count]


def perimeter_slots(count, canvas_size, margin, center_rect, rng):
    width, height = canvas_size
    cols, rows = (5, 3) if width > height else (3, 5)
    usable_w = width - margin * 2
    usable_h = height - margin * 2
    cell_w = usable_w / cols
    cell_h = usable_h / rows
    inflated = (
        center_rect[0] - 90,
        center_rect[1] - 90,
        center_rect[2] + 90,
        center_rect[3] + 90,
    )

    candidates = []
    for row in range(rows):
        for col in range(cols):
            x = margin + cell_w * (col + 0.5)
            y = margin + cell_h * (row + 0.5)
            if inflated[0] <= x <= inflated[2] and inflated[1] <= y <= inflated[3]:
                continue
            jitter_x = rng.uniform(-0.12, 0.12) * cell_w
            jitter_y = rng.uniform(-0.12, 0.12) * cell_h
            candidates.append((x + jitter_x, y + jitter_y, cell_w, cell_h))

    rng.shuffle(candidates)
    if len(candidates) >= count:
        return candidates[:count]
    return grid_slots(count, canvas_size, margin, rng)


def scatter_sizes(image_count, slot_w, slot_h, rng):
    if image_count >= 18:
        width_scale = rng.uniform(0.90, 1.08)
        height_scale = rng.uniform(0.80, 1.00)
    elif image_count >= 10:
        width_scale = rng.uniform(0.90, 1.12)
        height_scale = rng.uniform(0.82, 1.02)
    else:
        width_scale = rng.uniform(0.78, 1.02)
        height_scale = rng.uniform(0.76, 1.00)

    max_w = slot_w * width_scale
    max_h = slot_h * height_scale
    if image_count <= 6:
        max_w *= 1.15
        max_h *= 1.15
    if rng.random() < 0.18:
        max_w *= 0.88
    return max(220, int(max_w)), max(220, int(max_h))


def parse_color(value):
    try:
        return ImageColor.getrgb(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid color: {value}") from exc


def canvas_size_for(orientation, paper="A4"):
    portrait = PAPER_SIZES.get(paper, PAPER_SIZES["A4"])
    return portrait if orientation == "portrait" else (portrait[1], portrait[0])


def _normalize_layout(layout):
    """Accept a CollageLayout or a dict (from JSON) and return a CollageLayout."""
    if isinstance(layout, CollageLayout):
        items = [
            item if isinstance(item, PhotoLayout) else PhotoLayout(**item)
            for item in layout.items
        ]
        return CollageLayout(
            orientation=layout.orientation,
            background=layout.background,
            paper=layout.paper,
            items=items,
        )

    data = dict(layout)
    items = [
        item if isinstance(item, PhotoLayout) else PhotoLayout(**item)
        for item in data.get("items", [])
    ]
    return CollageLayout(
        orientation=data.get("orientation", "landscape"),
        background=data.get("background", "#f4efe6"),
        paper=data.get("paper", "A4"),
        items=items,
    )


def _save_canvas(canvas, output_png, output_pdf=None):
    output_png = Path(output_png)
    output_png.parent.mkdir(parents=True, exist_ok=True)

    rgb_canvas = canvas.convert("RGB")
    rgb_canvas.save(output_png, dpi=(DPI, DPI), quality=95)

    if output_pdf:
        output_pdf = Path(output_pdf)
        output_pdf.parent.mkdir(parents=True, exist_ok=True)
        rgb_canvas.save(output_pdf, "PDF", resolution=DPI)

    return output_png


def create_collage_from_layout(layout, output_png, output_pdf=None, border=None, feather=None):
    """Render a normalized layout to a print-ready image.

    Accepts a CollageLayout or a plain dict, renders the normalized placements
    WYSIWYG (no clamping), draws in ascending z_index order (stable), writes a
    PNG (+ optional PDF) and returns the PNG path.

    border/feather are optional: when None, rendering keeps exactly the historical
    behavior (border=30, feather=26 from render_photo). When given (px), they pass
    through to render_photo to control the frame and the oval glow.
    """
    layout = _normalize_layout(layout)
    canvas_size = canvas_size_for(layout.orientation, layout.paper)
    canvas = Image.new("RGBA", canvas_size, parse_color(layout.background) + (255,))
    canvas_w, canvas_h = canvas_size

    # render_photo defaults to border=30 when no explicit argument is given.
    render_kwargs = {}
    if border is not None:
        render_kwargs["border"] = border
    if feather is not None:
        render_kwargs["feather"] = feather

    # Higher z = drawn later (on top). Stable sort.
    for item in sorted(layout.items, key=lambda current: current.z_index):
        # A photo with zero/negative size must not be rendered at all.
        if item.width <= 0 or item.height <= 0:
            continue
        image = load_image(item.image_path)
        # Minimum pixel threshold large enough that the soft-oval geometry never
        # inverts, even if the frontend sends tiny boxes.
        target_w = max(40, round(item.width * canvas_w))
        target_h = max(40, round(item.height * canvas_h))
        # Per-photo feather overrides the collage-wide value (so the slider can
        # apply to just the selected photos); fall back to the global kwarg.
        item_kwargs = dict(render_kwargs)
        if getattr(item, "feather", None) is not None:
            item_kwargs["feather"] = item.feather
        photo = render_photo(image, (target_w, target_h), look=item.look, **item_kwargs)
        center = (item.x * canvas_w, item.y * canvas_h)
        paste_rotated(canvas, photo, center, item.rotation, clamp=False)

    return _save_canvas(canvas, output_png, output_pdf)


def create_collage(
    input_dir,
    output_png,
    output_pdf=None,
    orientation="landscape",
    background="#f4efe6",
    center_image=None,
    look="soft-oval",
    seed=None,
):
    rng = random.Random(seed)
    canvas_size = canvas_size_for(orientation)
    canvas = Image.new("RGBA", canvas_size, parse_color(background) + (255,))
    canvas_w, canvas_h = canvas_size

    image_paths = find_images(input_dir)
    center_path = Path(center_image).resolve() if center_image else None
    if center_path:
        image_paths = [path for path in image_paths if path.resolve() != center_path]

    rng.shuffle(image_paths)
    margin = 120 if orientation == "landscape" else 105

    # Express the auto-layout placements as PhotoLayout items, but draw them
    # through the clamping path (clamp=True) to keep behavior identical. Alongside
    # the item, keep the target size in pixels so no drift appears from the
    # fraction -> pixels -> fraction rounding.
    placements = []  # tuples (item: PhotoLayout, image, target_size_px)

    center_rect = None
    if center_path:
        center_source = load_image(center_path)
        if center_source.height >= center_source.width:
            center_size = (860, 1320) if orientation == "landscape" else (980, 1420)
        else:
            center_size = (1260, 900) if orientation == "landscape" else (1040, 760)

        center_photo = render_photo(center_source, center_size, look=look, border=34, shadow=28)
        center = (canvas_size[0] / 2, canvas_size[1] / 2)
        paste_rotated(canvas, center_photo, center, rng.uniform(-2.0, 2.0))
        center_rect = (
            center[0] - center_photo.width / 2,
            center[1] - center_photo.height / 2,
            center[0] + center_photo.width / 2,
            center[1] + center_photo.height / 2,
        )

    slots = (
        perimeter_slots(len(image_paths), canvas_size, margin, center_rect, rng)
        if center_rect
        else balanced_slots(len(image_paths), canvas_size, margin, rng=rng)
    )

    for path, slot in zip(image_paths, slots):
        image = load_image(path)
        target = scatter_sizes(len(image_paths), slot[2], slot[3], rng)
        if image.height > image.width and target[0] > target[1]:
            target = (int(target[0] * 0.78), int(target[1] * 1.08))
        elif image.width > image.height and target[1] > target[0]:
            target = (int(target[0] * 1.08), int(target[1] * 0.78))

        angle = rng.uniform(-8.5, 8.5)
        item = PhotoLayout(
            image_path=str(path),
            x=slot[0] / canvas_w,
            y=slot[1] / canvas_h,
            width=target[0] / canvas_w,
            height=target[1] / canvas_h,
            rotation=angle,
            look=look,
        )
        placements.append((item, image, target))

    for item, image, target in placements:
        photo = render_photo(image, target, look=item.look)
        center = (item.x * canvas_w, item.y * canvas_h)
        paste_rotated(canvas, photo, center, item.rotation, clamp=True)

    return _save_canvas(canvas, output_png, output_pdf)


def build_parser():
    parser = argparse.ArgumentParser(
        description="Generate a print-ready A4 collage at 300 DPI."
    )
    parser.add_argument(
        "input_dir",
        nargs="?",
        default="samples",
        help="Folder of photos. Default: samples",
    )
    parser.add_argument(
        "--output",
        default="collage-a4-print.png",
        help="Output PNG file. Default: collage-a4-print.png",
    )
    parser.add_argument(
        "--pdf",
        default="collage-a4-print.pdf",
        help="Output PDF file. Use --pdf '' to disable the PDF.",
    )
    parser.add_argument(
        "--orientation",
        choices=["landscape", "portrait"],
        default="landscape",
        help="A4 page orientation. Default: landscape",
    )
    parser.add_argument(
        "--background",
        default="#f4efe6",
        type=parse_color,
        help="Background: a color name or hex, e.g. white or #f4efe6.",
    )
    parser.add_argument(
        "--center",
        default=None,
        help="Optional: a photo placed large in the center of the collage.",
    )
    parser.add_argument(
        "--look",
        choices=["soft-oval", "paper"],
        default="soft-oval",
        help="Photo style: soft-oval for an oval glow, or paper for a rectangular photo.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Number for a reproducible result, e.g. --seed 10.",
    )
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    background = "#%02x%02x%02x" % args.background
    pdf = args.pdf if args.pdf else None
    output = create_collage(
        args.input_dir,
        args.output,
        pdf,
        orientation=args.orientation,
        background=background,
        center_image=args.center,
        look=args.look,
        seed=args.seed,
    )
    print(f"Generated: {output}")
    if pdf:
        print(f"Generated: {pdf}")


if __name__ == "__main__":
    main()
