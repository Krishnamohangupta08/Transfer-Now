"""Windows-11-style icon animation helpers.

Fluent / WinUI icons never just *sit* there: they spring in, lift on hover,
squash on press and settle with a small overshoot.  This module supplies the
pieces the rest of the UI uses to get that feel:

    ``spring()``      a QVariantAnimation on an OutBack curve (the "settle" bounce)
    ``glide()``       the same on a plain ease-out curve (no overshoot)
    ``AnimatedLogo``  the brand mark: intro pop, hover lift + glow, shine sweep,
                      press squash, click ripple and an optional idle "breathe"

Set ``iconfx.ENABLED = False`` to turn every icon animation off (values snap).
"""
from __future__ import annotations

import math

from PyQt6.QtCore import (
    QByteArray, QEasingCurve, QPointF, QRectF, Qt, QTimer, QVariantAnimation, pyqtSignal,
)
from PyQt6.QtGui import (
    QColor, QLinearGradient, QPainter, QPainterPath, QPen, QPolygonF, QRadialGradient,
)
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QWidget

from . import icons
from .theme import theme

ENABLED = True


# ------------------------------------------------------------------ helpers
def _curve(kind: str, overshoot: float = 1.5) -> QEasingCurve:
    if kind == "back":
        ec = QEasingCurve(QEasingCurve.Type.OutBack)
        ec.setOvershoot(overshoot)
        return ec
    if kind == "sine":
        return QEasingCurve(QEasingCurve.Type.InOutSine)
    return QEasingCurve(QEasingCurve.Type.OutCubic)


def make_anim(owner: QWidget, on_value, *, kind: str = "cubic", overshoot: float = 1.5):
    """A reusable QVariantAnimation that feeds ``on_value(float)``."""
    a = QVariantAnimation(owner)
    a.setEasingCurve(_curve(kind, overshoot))
    a.valueChanged.connect(lambda v: on_value(float(v)))
    return a


def run(anim: QVariantAnimation, start: float, end: float, ms: int, *, kind: str | None = None,
        overshoot: float = 1.5) -> None:
    """Restart ``anim`` from ``start`` to ``end`` (snaps when animations are off)."""
    anim.stop()
    if kind is not None:
        anim.setEasingCurve(_curve(kind, overshoot))
    if not ENABLED or ms <= 0:
        anim.setStartValue(end)
        anim.setEndValue(end)
        anim.setDuration(1)
        anim.start()
        return
    anim.setStartValue(float(start))
    anim.setEndValue(float(end))
    anim.setDuration(int(ms))
    anim.start()


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))


# --------------------------------------------------------------- brand logo
class AnimatedLogo(QWidget):
    """The Transfer Now mark with Windows-style micro-interactions.

    * intro   — pops in from 55 % with a springy overshoot, then one shine sweep
    * hover   — lifts to 108 %, a soft accent glow blooms, the shine sweeps again
    * press   — squashes to 92 %, springs back on release
    * click   — a ripple ring radiates from the tile (``clicked`` is emitted)
    * breathe — optional slow idle pulse (welcome screen)

    The widget is ``size + 2 * pad`` square so scaling / glow never clip; callers
    that need the glyph at an exact spot should subtract ``pad`` from the margin.
    """
    clicked = pyqtSignal()

    def __init__(self, size: int = 34, pad: int = 3, glow: bool = False, breathe: bool = False,
                 intro: bool = True, interactive: bool = True, drag_through: bool = False,
                 invert: bool | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._size, self._pad = int(size), int(pad)
        self._static_glow, self._breathe_on = glow, breathe
        self._intro_on, self._interactive = intro, interactive
        self._invert = invert                   # None = follow the theme, True/False = force tile colours
        self._drag_through = drag_through       # let presses reach a draggable title bar
        self._intro_v = 0.0 if intro else 1.0       # 0 -> 1 (overshoots slightly)
        self._hover = 0.0
        self._press = 0.0
        self._shine = -1.0                           # -1 = idle, 0..1 = sweeping
        self._ripple = -1.0                          # -1 = idle, 0..1 = expanding
        self._phase = 0.0                            # breathe phase 0..1
        self._started = False
        self._renderer: QSvgRenderer | None = None

        total = self._size + 2 * self._pad
        self.setFixedSize(total, total)
        if interactive:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self._a_intro = make_anim(self, self._set_intro, kind="back", overshoot=1.25)
        self._a_hover = make_anim(self, self._set_hover)
        self._a_press = make_anim(self, self._set_press)
        self._a_shine = make_anim(self, self._set_shine)
        self._a_ripple = make_anim(self, self._set_ripple)
        self._a_breathe = make_anim(self, self._set_phase, kind="sine")
        self._a_breathe.setLoopCount(-1)
        self._a_intro.finished.connect(lambda: QTimer.singleShot(90, self.sweep))
        self._a_shine.finished.connect(lambda: self._set_shine(-1.0))
        self._a_ripple.finished.connect(lambda: self._set_ripple(-1.0))

        theme.changed.connect(self._rebuild)
        self._rebuild()

    # ---------------------------------------------------------------- state
    def _set_intro(self, v: float) -> None:
        self._intro_v = v
        self.update()

    def _set_hover(self, v: float) -> None:
        self._hover = v
        self.update()

    def _set_press(self, v: float) -> None:
        self._press = v
        self.update()

    def _set_shine(self, v: float) -> None:
        self._shine = v
        self.update()

    def _set_ripple(self, v: float) -> None:
        self._ripple = v
        self.update()

    def _set_phase(self, v: float) -> None:
        self._phase = v
        self.update()

    def _rebuild(self) -> None:
        px = max(64, int(self._size * 4))            # vector — rendered at paint size anyway
        svg = icons.logo_svg(px, dark=theme.dark if self._invert is None else self._invert,
                             logical=self._size)
        self._renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
        self.update()

    def set_size(self, size: int) -> None:
        self._size = int(size)
        total = self._size + 2 * self._pad
        self.setFixedSize(total, total)
        self._rebuild()

    # ------------------------------------------------------------ public fx
    def sweep(self) -> None:
        """One diagonal shine pass across the tile."""
        if ENABLED and self.isVisible():
            run(self._a_shine, 0.0, 1.0, 780, kind="sine")

    def pulse(self) -> None:
        """Ripple ring (also fired on click)."""
        if ENABLED and self.isVisible():
            run(self._a_ripple, 0.0, 1.0, 620)

    def play_intro(self) -> None:
        self._intro_v = 0.0
        run(self._a_intro, 0.0, 1.0, 640)

    # --------------------------------------------------------------- events
    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        if not self._started:
            self._started = True
            if self._intro_on and ENABLED:
                self.play_intro()
            else:
                self._intro_v = 1.0
        if self._breathe_on and ENABLED and self._a_breathe.state() != QVariantAnimation.State.Running:
            self._a_breathe.setStartValue(0.0)
            self._a_breathe.setEndValue(1.0)
            self._a_breathe.setDuration(3800)
            self._a_breathe.start()

    def hideEvent(self, e) -> None:  # noqa: N802
        self._a_breathe.stop()
        super().hideEvent(e)

    def enterEvent(self, e) -> None:  # noqa: N802
        if self._interactive:
            run(self._a_hover, self._hover, 1.0, 180)
            if self._shine < 0:
                self.sweep()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        if self._interactive:
            run(self._a_hover, self._hover, 0.0, 240)
            run(self._a_press, self._press, 0.0, 240, kind="back", overshoot=2.2)
        super().leaveEvent(e)

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if self._interactive and not self._drag_through and e.button() == Qt.MouseButton.LeftButton:
            run(self._a_press, self._press, 1.0, 90)
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if self._interactive and not self._drag_through and e.button() == Qt.MouseButton.LeftButton:
            run(self._a_press, self._press, 0.0, 360, kind="back", overshoot=2.6)
            if self.rect().contains(e.position().toPoint()):
                self.pulse()
                self.clicked.emit()
            e.accept()
            return
        super().mouseReleaseEvent(e)

    # ---------------------------------------------------------------- paint
    def paintEvent(self, _e) -> None:  # noqa: N802
        if self._renderer is None:
            return
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        s = float(self._size)
        cx = cy = self.width() / 2.0
        accent = QColor(theme.pal["accent"])

        breathe = math.sin(self._phase * math.tau) if self._breathe_on else 0.0
        intro = self._intro_v
        scale = lerp(0.55, 1.0, intro) * (1.0 + 0.085 * self._hover - 0.085 * self._press
                                          + 0.022 * breathe)
        opacity = clamp01(intro * 2.4)

        # soft glow behind the tile (hover bloom, or always-on for the welcome mark)
        glow = clamp01(max(self._hover * 0.85, 0.55 if self._static_glow else 0.0)
                       + 0.18 * max(0.0, breathe)) * opacity
        if glow > 0.01:
            r = self.width() / 2.0
            g = QRadialGradient(QPointF(cx, cy + s * 0.04), r)
            c0 = QColor(accent)
            c0.setAlpha(int(110 * glow))
            c1 = QColor(accent)
            c1.setAlpha(int(34 * glow))
            c2 = QColor(accent)
            c2.setAlpha(0)
            g.setColorAt(0.0, c0)
            g.setColorAt(0.55, c1)
            g.setColorAt(1.0, c2)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(g)
            p.drawEllipse(QPointF(cx, cy), r, r)

        # ripple ring (click) — grows from the tile edge but never past the widget bounds
        # (an unbounded ring was cut off at the widget edge and only its corners showed)
        if self._ripple >= 0:
            t = self._ripple
            room = max(0.0, self._pad - 1.2)                  # px available around the tile
            grow = room * (1.0 - (1.0 - t) ** 2)              # ease-out
            sw = max(0.8, 1.6 * (1.0 - t * 0.6))
            ring = QColor(accent)
            ring.setAlphaF(0.65 * (1.0 - t) ** 1.2)
            p.setPen(QPen(ring, sw))
            p.setBrush(Qt.BrushStyle.NoBrush)
            half = s * scale / 2.0 + grow
            rad = icons.LOGO_RADIUS / 64.0 * s * scale + grow
            p.drawRoundedRect(QRectF(cx - half, cy - half, half * 2, half * 2), rad, rad)

        # the mark itself — vector render under a scale transform stays razor sharp
        p.save()
        p.setOpacity(opacity)
        p.translate(cx, cy)
        p.scale(scale, scale)
        p.translate(-s / 2, -s / 2)
        self._renderer.render(p, QRectF(0, 0, s, s))

        # shine sweep, clipped to the rounded tile
        if self._shine >= 0:
            radius = icons.LOGO_RADIUS / 64.0 * s
            clip = QPainterPath()
            clip.addRoundedRect(QRectF(0, 0, s, s), radius, radius)
            p.setClipPath(clip)
            t = self._shine
            bw = s * 0.55
            x = lerp(-bw * 1.2, s + bw * 0.4, t)
            skew = s * 0.35
            band = QPolygonF([QPointF(x, 0), QPointF(x + bw, 0),
                              QPointF(x + bw - skew, s), QPointF(x - skew, s)])
            gr = QLinearGradient(x - skew / 2, 0, x + bw - skew / 2, 0)
            hi = QColor(255, 255, 255, 0)
            mid = QColor(255, 255, 255, 120 if theme.dark is False else 150)
            gr.setColorAt(0.0, hi)
            gr.setColorAt(0.5, mid)
            gr.setColorAt(1.0, hi)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(gr)
            p.drawPolygon(band)
        p.restore()
        p.end()
