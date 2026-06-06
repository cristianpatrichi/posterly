#!/usr/bin/env python3
"""Generate brand-neutral, license-free (CC0) sample images for demos/screenshots.

These are fully synthetic gradients + soft shapes -- no third-party photos -- so
they can be redistributed in a public repo without any licensing concern. Run:

    python samples/generate_samples.py

Output: samples/sample-01.jpg ... in a mix of portrait/landscape aspect ratios.
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

OUT = Path(__file__).resolve().parent

# (name, size, [color stops top->bottom], accent color)
PALETTES = [
    ("01", (1200, 1600), [(255, 184, 108), (244, 114, 130), (124, 58, 160)], (255, 233, 170)),
    ("02", (1600, 1200), [(125, 211, 252), (59, 130, 246), (30, 27, 75)], (224, 242, 254)),
    ("03", (1200, 1500), [(167, 243, 208), (16, 185, 129), (6, 78, 59)], (236, 253, 245)),
    ("04", (1500, 1200), [(253, 224, 71), (249, 115, 22), (124, 45, 18)], (255, 247, 214)),
    ("05", (1200, 1600), [(244, 114, 182), (147, 51, 234), (30, 27, 75)], (250, 232, 255)),
    ("06", (1600, 1200), [(148, 163, 184), (51, 65, 85), (15, 23, 42)], (226, 232, 240)),
    ("07", (1300, 1300), [(94, 234, 212), (45, 212, 191), (15, 118, 110)], (240, 253, 250)),
    ("08", (1200, 1600), [(251, 207, 232), (236, 72, 153), (131, 24, 67)], (255, 241, 248)),
]


def _lerp(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def _gradient(size: tuple[int, int], stops: list[tuple[int, int, int]]) -> Image.Image:
    w, h = size
    img = Image.new("RGB", size)
    px = img.load()
    seg = len(stops) - 1
    for y in range(h):
        f = y / max(1, h - 1) * seg
        i = min(seg - 1, int(f))
        color = _lerp(stops[i], stops[i + 1], f - i)
        for x in range(w):
            px[x, y] = color
    return img


def _blobs(img: Image.Image, accent: tuple[int, int, int], n: int = 5) -> Image.Image:
    w, h = img.size
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    for k in range(n):
        # deterministic pseudo-random placement (no RNG -> reproducible build)
        cx = int((math.sin(k * 12.9898) * 0.5 + 0.5) * w)
        cy = int((math.sin(k * 78.233) * 0.5 + 0.5) * h)
        r = int((0.12 + 0.10 * ((k * 37) % 5) / 4) * min(w, h))
        alpha = 26 + (k * 53) % 36
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=accent + (alpha,))
    overlay = overlay.filter(ImageFilter.GaussianBlur(min(w, h) * 0.04))
    return Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")


def main() -> None:
    for name, size, stops, accent in PALETTES:
        img = _gradient(size, stops)
        img = _blobs(img, accent)
        path = OUT / f"sample-{name}.jpg"
        img.save(path, "JPEG", quality=82, optimize=True)
        print(f"wrote {path.relative_to(OUT.parent)} ({size[0]}x{size[1]})")


if __name__ == "__main__":
    main()
