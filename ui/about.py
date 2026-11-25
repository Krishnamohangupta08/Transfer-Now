"""About — shared building blocks for the About dialog and the Settings ▸ About pane.

* ``DeveloperHero``  — dark, premium profile card: rounded portrait, name, role
  line and version chips, with quiet concentric rings in the corner.
* ``FeatureGrid``    — two-column "what's inside" grid of icon + text tiles.
* ``tech_string()``  — "PyQt x · Qt y · Python z".

The hero is deliberately *always dark* (like a product card), so the developer
photo — shot on black — melts into it in every theme.
"""
from __future__ import annotations

import os
import sys

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import QGridLayout, QLabel, QSizePolicy, QWidget

from . import assets
from .i18n import i18n, tr
from .theme import _mix, theme
from .widgets import Badge, hbox, label, vbox

HERO_BG = "#090B0F"
PHOTO_FILE = "developer.png"


# ------------------------------------------------------------------ helpers
def asset_path(name: str) -> str:
    """Locate a bundled asset (dev tree or PyInstaller bundle)."""
    return assets.find(name) or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                              "assets", name)


def tech_string() -> str:
    try:
        from PyQt6.QtCore import PYQT_VERSION_STR, QT_VERSION_STR
        return f"PyQt {PYQT_VERSION_STR} · Qt {QT_VERSION_STR} · Python {sys.version.split()[0]}"
    except Exception:  # noqa: BLE001
        return f"Python {sys.version.split()[0]}"


def _initials(name: str) -> str:
    parts = [w for w in name.split() if w]
    return "".join(w[0] for w in parts[:2]).upper() or "?"


# ----------------------------------------------------------------- portrait
class Portrait(QWidget):
    """Rounded-square developer photo with a hairline ring (falls back to initials)."""

    def __init__(self, name: str, size: int = 148) -> None:
        super().__init__()
        self._name = name
        self._size = size
        self._src = assets.pixmap(PHOTO_FILE)       # disk, else the copy embedded in the exe
        self.setFixedSize(size, size)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
                         | QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        rad = r.width() * 0.26
        path = QPainterPath()
        path.addRoundedRect(r, rad, rad)

        if not self._src.isNull():
            dpr = self.devicePixelRatioF()
            side = int(self.width() * dpr)
            pm = self._src.scaled(side, side, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                  Qt.TransformationMode.SmoothTransformation)
            pm.setDevicePixelRatio(dpr)
            p.setClipPath(path)
            p.drawPixmap(QPointF(0, 0), pm)
            p.setClipping(False)
        else:                                            # photo missing -> monogram tile
            p.fillPath(path, QColor(_mix(HERO_BG, theme.pal["accent"], 0.35)))
            f = QFont(self.font())
            f.setPixelSize(int(self.width() * 0.34))
            f.setWeight(QFont.Weight.DemiBold)
            p.setFont(f)
            p.setPen(QColor("#FFFFFF"))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, _initials(self._name))

        p.setPen(QPen(QColor(255, 255, 255, 38), 1.2))   # hairline ring
        p.drawPath(path)


# --------------------------------------------------------------------- hero
class DeveloperHero(QWidget):
    """Premium dark profile card used by both About surfaces."""

    def __init__(self, name: str, version: str, compact: bool = False) -> None:
        super().__init__()
        self._compact = compact
        self._version = version
        photo = 108 if compact else 150
        outer = hbox(self, (20, 20, 26, 20) if compact else (26, 26, 32, 26), 22 if compact else 28)
        outer.addWidget(Portrait(name, photo), 0, Qt.AlignmentFlag.AlignVCenter)

        col = vbox(spacing=0)
        self.cap = QLabel()
        self.name = QLabel(name)
        self.role = QLabel()
        self.role.setWordWrap(True)
        col.addWidget(self.cap)
        col.addSpacing(6)
        col.addWidget(self.name)
        col.addSpacing(6)
        col.addWidget(self.role)
        col.addSpacing(14 if compact else 18)
        chips = hbox(spacing=8)
        self.chip_v = QLabel(f"v{version}")
        self.chip_t = QLabel("Porcelain & Signal")
        chips.addWidget(self.chip_v)
        chips.addWidget(self.chip_t)
        chips.addStretch(1)
        col.addLayout(chips)
        outer.addLayout(col, 1)

        for w in (self.cap, self.name, self.role, self.chip_v, self.chip_t):
            w.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        theme.changed.connect(self._restyle)
        i18n.changed.connect(self._retranslate)
        self._restyle()
        self._retranslate()

    # ------------------------------------------------------------------ text
    def _retranslate(self) -> None:
        self.cap.setText(tr("dlg.developed_by").upper())
        self.role.setText(tr("about.tagline"))

    def _restyle(self) -> None:
        acc = theme.pal["accent"]
        acc_on_dark = acc if theme.dark else _mix(acc, "#FFFFFF", 0.5)
        a = QColor(acc_on_dark)
        cap_f = QFont(self.cap.font())
        cap_f.setPixelSize(11)
        cap_f.setWeight(QFont.Weight.DemiBold)
        cap_f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.4)
        self.cap.setFont(cap_f)
        self.cap.setStyleSheet(f"color: {acc_on_dark}; background: transparent;")

        name_f = QFont(self.name.font())
        name_f.setPixelSize(26 if self._compact else 34)
        name_f.setWeight(QFont.Weight.DemiBold)
        name_f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, -0.8)
        self.name.setFont(name_f)
        self.name.setStyleSheet("color: #FFFFFF; background: transparent;")

        role_f = QFont(self.role.font())
        role_f.setPixelSize(13 if self._compact else 14)
        self.role.setFont(role_f)
        self.role.setStyleSheet("color: #9AA2B1; background: transparent;")

        base = ("border-radius: 12px; padding: 4px 11px; font-size: 12px; font-weight: 600;"
                " background: {bg}; color: {fg};")
        self.chip_v.setStyleSheet(base.format(
            bg=f"rgba({a.red()}, {a.green()}, {a.blue()}, 0.18)", fg=acc_on_dark))
        self.chip_t.setStyleSheet(base.format(bg="rgba(255, 255, 255, 0.08)", fg="#C9CEDA"))
        self.update()

    # ----------------------------------------------------------------- paint
    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(r, 24, 24)
        p.fillPath(path, QColor(HERO_BG))

        # quiet concentric rings, top-right — flat strokes, no glow
        p.setClipPath(path)
        p.setBrush(Qt.BrushStyle.NoBrush)
        acc = QColor(theme.pal["accent"] if theme.dark else _mix(theme.pal["accent"], "#FFFFFF", 0.4))
        centre = QPointF(r.right() - 34, r.top() + 26)
        for i, rad in enumerate((64, 110, 160, 214, 272)):
            ring = QColor(acc)
            ring.setAlpha(max(10, 46 - i * 8))
            p.setPen(QPen(ring, 1))
            p.drawEllipse(centre, rad, rad)
        p.setClipping(False)

        p.setPen(QPen(QColor(255, 255, 255, 26), 1))
        p.drawPath(path)


# ------------------------------------------------------------ feature grid
class FeatureGrid(QWidget):
    """Two-column grid of [icon tile] + text, for the "what's inside" list."""

    def __init__(self, features, columns: int = 2) -> None:
        super().__init__()
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(12)
        self._labels: list[tuple[QLabel, str]] = []
        for i, (icon_name, text) in enumerate(features):
            cell = QWidget()
            cell.setMinimumHeight(52)                   # room for three wrapped lines
            row = hbox(cell, spacing=12)
            row.addWidget(Badge(icon_name, 32, "accent", round_=False), 0,
                          Qt.AlignmentFlag.AlignVCenter)
            lbl = label(tr(text), "muted", wrap=True)     # ``text`` is an i18n key
            self._labels.append((lbl, text))
            row.addWidget(lbl, 1)
            grid.addWidget(cell, i // columns, i % columns)
        for c in range(columns):
            grid.setColumnStretch(c, 1)
        i18n.changed.connect(self._retranslate)

    def _retranslate(self) -> None:
        for lbl, key in self._labels:
            lbl.setText(tr(key))
