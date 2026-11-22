"""Blurred + dimmed, rounded-corner backdrop shown behind modal dialogs."""
from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPainterPath, QPixmap
from PyQt6.QtWidgets import (
    QGraphicsBlurEffect, QGraphicsPixmapItem, QGraphicsScene, QWidget,
)

from . import motion
from .theme import theme


def blurred(pix: QPixmap, radius: float = 9.0, shrink: int = 2) -> QPixmap:
    """Blur a pixmap. Works on a shrunken copy (fast + smoother), device-pixel safe."""
    src = QPixmap(pix)
    src.setDevicePixelRatio(1.0)
    small = src.scaled(max(1, src.width() // shrink), max(1, src.height() // shrink),
                       Qt.AspectRatioMode.IgnoreAspectRatio,
                       Qt.TransformationMode.SmoothTransformation)
    scene = QGraphicsScene()
    item = QGraphicsPixmapItem(small)
    fx = QGraphicsBlurEffect()
    fx.setBlurRadius(radius)
    fx.setBlurHints(QGraphicsBlurEffect.BlurHint.QualityHint)
    item.setGraphicsEffect(fx)
    scene.addItem(item)
    out = QImage(small.size(), QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    scene.render(p, QRectF(out.rect()), QRectF(small.rect()))
    p.end()
    return QPixmap.fromImage(out)


class DimOverlay(QWidget):
    """Covers the window frame with a blurred snapshot of itself + a dark tint."""
    RADIUS = 13

    def __init__(self, frame: QWidget) -> None:
        super().__init__(frame)
        self._frame = frame
        self._pix: QPixmap | None = None
        self._round = True
        self._gen = 0                 # guards overlapping fade in/out cycles
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.hide()

    def present(self, rounded: bool) -> None:
        """Snapshot the frame (overlay hidden), blur it, fade the overlay in."""
        self._gen += 1
        self.hide()
        motion.clear_effect(self)
        self._round = rounded
        self._pix = blurred(self._frame.grab())
        self.setGeometry(self._frame.rect())
        self.show()
        self.raise_()
        motion.fade_in(self, motion.DUR,
                       lambda: motion.clear_effect(self))

    def dismiss(self) -> None:
        """Fade the backdrop away, then release the snapshot."""
        self._gen += 1
        gen = self._gen

        def done() -> None:
            if gen != self._gen:      # a new dialog presented meanwhile
                return
            motion.clear_effect(self)
            self.hide()
            self._pix = None

        if not self.isVisible():
            done()
            return
        motion.fade_out(self, motion.DUR_FAST, done)

    def paintEvent(self, _e) -> None:  # noqa: N802
        if self._pix is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(self.rect())
        if self._round:
            r = r.adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(r, self.RADIUS, self.RADIUS) if self._round else path.addRect(r)
        p.setClipPath(path)
        p.fillRect(r, QColor(theme.pal["bg"]))
        p.drawPixmap(r, self._pix, QRectF(self._pix.rect()))
        p.fillRect(r, QColor(0, 0, 0, 120 if theme.dark else 85))

    def mousePressEvent(self, e) -> None:  # noqa: N802
        e.accept()          # swallow clicks; the dialog is modal anyway

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        e.accept()
