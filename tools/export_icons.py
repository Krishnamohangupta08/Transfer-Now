"""Export the SVG source of every icon to ``assets/icons/``.

Every glyph in the app is generated SVG (no font glyphs, no bitmaps):
    assets/icons/<name>.svg        — every UI icon (24-grid, currentColor stroke)
    assets/icons/logo.svg          — the app logo (ink squircle, ring-line-ring)
    assets/icons/logo-inverted.svg — the same logo for dark surfaces
    assets/icons/flags/<code>.svg  — the 8 language country flags

Run after touching ``ui/icons.py`` or the flags in ``ui/i18n.py``:

    python tools/export_icons.py
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])   # noqa: E402

from ui import icons  # noqa: E402
from ui.i18n import _FLAGS  # noqa: E402
from ui.theme import theme  # noqa: E402


def export(out_dir: str) -> tuple[int, int]:
    """Write every icon + the logo + all flags; returns (icons, flags)."""
    theme.apply(app, "dark", None)
    n_icon = n_flag = 0

    # ---- every UI icon, stroke tinted with currentColor so the files stay neutral
    icon_dir = os.path.join(out_dir, "icons")
    os.makedirs(icon_dir, exist_ok=True)
    for name in icons.ICONS:
        path = os.path.join(icon_dir, f"{name}.svg")
        with open(path, "w", encoding="utf-8") as f:
            f.write(icons.icon_svg(name, "currentColor", 24, 1.5))
        n_icon += 1

    # ---- the app logo: ink tile (light surfaces) + inverted tile (dark surfaces)
    for fname, dark in (("logo.svg", False), ("logo-inverted.svg", True)):
        with open(os.path.join(icon_dir, fname), "w", encoding="utf-8") as f:
            f.write(icons.logo_svg(64, dark=dark))
        n_icon += 1

    # ---- country flags for the language picker
    flag_dir = os.path.join(icon_dir, "flags")
    os.makedirs(flag_dir, exist_ok=True)
    for code, body in _FLAGS.items():
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="60" height="40" '
               f'viewBox="0 0 60 40">{body}</svg>')
        with open(os.path.join(flag_dir, f"{code}.svg"), "w", encoding="utf-8") as f:
            f.write(svg)
        n_flag += 1
    return n_icon, n_flag


def main() -> int:
    out = os.path.join(ROOT, "assets")
    n_icon, n_flag = export(out)
    print(f"exported {n_icon} icons + {n_flag} flags -> {os.path.join(out, 'icons')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
