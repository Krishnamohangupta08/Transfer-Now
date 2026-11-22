"""Create the application icon files (same artwork as the in-app logo), Pillow only.

    python tools/make_icon.py

Writes assets/icon.ico (Windows, 16-256 px), assets/icon.icns (macOS) and
assets/icon.png (512 px, Linux / preview).

The logo is an ink rounded square with a white "ring - line - ring" route glyph.
Geometry mirrors ``ui/icons.py::logo_svg`` (64-unit grid).  Every ICO size is
drawn on its own with a larger, bolder glyph at small sizes so it stays
readable in the taskbar and tray.
"""
from __future__ import annotations

import os
import sys

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "assets")

INK = (0x0F, 0x12, 0x18, 255)
WHITE = (255, 255, 255, 255)
LOGO_RADIUS = 18                            # keep in sync with ui/icons.py


def glyph_scale(px: int) -> float:          # keep in sync with icons.logo_scale
    return 1.5 if px <= 24 else 1.3 if px <= 40 else 1.15 if px <= 64 else 1.0


def render(px: int, supersample: int = 16) -> Image.Image:
    """Draw the logo for a ``px`` x ``px`` icon (supersampled, then downscaled)."""
    big = min(max(px * supersample, 512), 2048)
    k_grid = big / 64
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # rounded-square tile (radius 18 of 64)
    d.rounded_rectangle([0, 0, big - 1, big - 1], radius=LOGO_RADIUS * k_grid, fill=INK)

    # glyph: ring --- ring
    k = glyph_scale(px)
    ring_r, ring_w, line_w = 3.5 * k, 2.6 * k, 2.3 * k
    cl, cr = 32 - 12.3 * k, 32 + 12.3 * k
    G = k_grid

    def ring(cx: float) -> None:
        ro, ri = (ring_r + ring_w / 2) * G, (ring_r - ring_w / 2) * G
        d.ellipse([cx * G - ro, 32 * G - ro, cx * G + ro, 32 * G + ro], fill=WHITE)
        d.ellipse([cx * G - ri, 32 * G - ri, cx * G + ri, 32 * G + ri], fill=INK)

    ring(cl)
    ring(cr)
    x0, x1, hw = (cl + ring_r) * G, (cr - ring_r) * G, line_w * G / 2
    d.rounded_rectangle([x0 - hw, 32 * G - hw, x1 + hw, 32 * G + hw], radius=hw, fill=WHITE)

    return img.resize((px, px), Image.LANCZOS)


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
    render(512).save(os.path.join(OUT, "icon.png"))
    frames = {s: render(s) for s in sizes}
    frames[256].save(os.path.join(OUT, "icon.ico"), format="ICO",
                     sizes=[(s, s) for s in sizes],
                     append_images=[frames[s] for s in sizes if s != 256])
    try:
        render(1024).save(os.path.join(OUT, "icon.icns"))
    except Exception as exc:  # noqa: BLE001 — .icns is optional (macOS only)
        print("  (skipped icon.icns:", exc, ")")
    print("Icon written to", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
