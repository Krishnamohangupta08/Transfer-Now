"""Collapsible sidebar — a smooth expanded (icon + label) <-> rail (icon only) transition.

``Sidebar`` owns one animated property, ``collapse`` (0.0 = expanded, 1.0 = rail).
Every frame it derives the whole look from that single number:

* the frame width glides 232 px <-> 68 px on the Shade curve,
* text-only parts (brand text, section headings, version / theme row) cross-fade
  and fold their height so nothing pops,
* each ``NavButton`` slides its icon to the centre and fades its label,
* the brand logo re-centres itself.

Usage (see ``window._build_sidebar``)::

    sb = Sidebar()
    sb.register_fade(widget)            # fades out when collapsing
    sb.register_fold(widget)            # fades AND folds its height away
    sb.register_nav(button)             # NavButton: icon slides, label fades
    sb.brand_layout = brand_hbox        # logo re-centres
    sb.set_collapsed(True, animate=False)
"""
from __future__ import annotations

from PyQt6.QtCore import QEvent, QPropertyAnimation, QRect, Qt, pyqtProperty, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPixmap
from PyQt6.QtWidgets import QFrame, QGraphicsOpacityEffect, QSizePolicy, QWidget

from .theme import theme

from . import motion

EXPANDED_W = 232
COLLAPSED_W = 68
DURATION = 280                      # ms — a touch longer than DUR: it is a layout move
_QWIDGETSIZE_MAX = 16777215


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _clamp(v: float) -> float:
    return max(0.0, min(1.0, v))


class Sidebar(QFrame):
    changed = pyqtSignal(bool)      # True = now collapsed (emitted when the move starts)
    moving = pyqtSignal(bool)       # True when a glide starts, False when it has finished

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("sidebar")
        self._t = 0.0
        self._collapsed = False
        self._anim: QPropertyAnimation | None = None
        self._moving = False
        self._fades: list[tuple[QWidget, QGraphicsOpacityEffect]] = []
        self._folds: list[tuple[QWidget, QGraphicsOpacityEffect, int]] = []
        self._navs: list = []
        self.brand_layout = None
        self.setFixedWidth(EXPANDED_W)

    # ------------------------------------------------------------ registration
    def register_fade(self, w: QWidget) -> None:
        fx = QGraphicsOpacityEffect(w)
        fx.setOpacity(1.0)
        w.setGraphicsEffect(fx)
        self._fades.append((w, fx))

    def register_fold(self, w: QWidget) -> None:
        """Fade + shrink the widget's height to ~0 while collapsing."""
        fx = QGraphicsOpacityEffect(w)
        fx.setOpacity(1.0)
        w.setGraphicsEffect(fx)
        self._folds.append((w, fx, max(1, w.sizeHint().height())))

    def register_nav(self, btn) -> None:
        btn.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        btn.setMinimumWidth(0)
        self._navs.append(btn)

    # ------------------------------------------------------------------ state
    def is_collapsed(self) -> bool:
        return self._collapsed

    def toggle(self) -> None:
        self.set_collapsed(not self._collapsed)

    def set_collapsed(self, collapsed: bool, animate: bool = True) -> None:
        collapsed = bool(collapsed)
        end = 1.0 if collapsed else 0.0
        changed = collapsed != self._collapsed
        self._collapsed = collapsed
        if self._anim is not None:
            self._anim.stop()
            self._anim = None
        if animate and self.isVisible() and abs(self._t - end) > 1e-3:
            if not self._moving:
                self._moving = True
                self.moving.emit(True)
            self._anim = motion.anim_prop(self, b"collapse", self._t, end, DURATION,
                                          on_done=self._finished)
        else:
            self.collapse = end
            if self._moving:
                self._finished()
        if changed:
            self.changed.emit(collapsed)

    def _finished(self) -> None:
        self._anim = None
        if self._moving:
            self._moving = False
            self.moving.emit(False)

    # --------------------------------------------------- animated "collapse" prop
    def _get_collapse(self) -> float:
        return self._t

    def _set_collapse(self, v: float) -> None:
        self._t = _clamp(float(v))
        self._apply()

    collapse = pyqtProperty(float, _get_collapse, _set_collapse)

    def _apply(self) -> None:
        t = self._t
        self.setFixedWidth(round(_lerp(EXPANDED_W, COLLAPSED_W, t)))
        text_op = _clamp(1.0 - t * 2.4)              # labels are gone by ~42 % of the move
        for w, fx in self._fades:
            fx.setOpacity(text_op)
            w.setVisible(text_op > 0.01 or not self._collapsed)
        for w, fx, h0 in self._folds:
            fx.setOpacity(text_op)
            w.setMaximumHeight(_QWIDGETSIZE_MAX if t <= 0.001 else max(0, round(_lerp(h0, 0, _clamp(t * 1.25)))))
        for b in self._navs:
            b.set_collapse(t)
        if self.brand_layout is not None:
            m = self.brand_layout.contentsMargins()
            self.brand_layout.setContentsMargins(round(_lerp(8, 3, t)), m.top(), m.right(), m.bottom())


class FrozenView(QWidget):
    """Opaque still of the page, pinned top-left inside the sheet frame.

    Shown while the sidebar glides so the live page isn't re-laid-out each frame.
    """

    def __init__(self, sheet: QWidget, shot: QPixmap) -> None:
        super().__init__(sheet)
        self._shot = shot
        self._sheet = sheet
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        sheet.installEventFilter(self)
        self._fit()

    def _fit(self) -> None:
        s = self._sheet
        self.setGeometry(QRect(1, 1, max(0, s.width() - 2), max(0, s.height() - 2)))

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802
        if obj is self._sheet and ev.type() == QEvent.Type.Resize:
            self._fit()
        return super().eventFilter(obj, ev)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(theme.pal["sheet"]))
        p.drawPixmap(0, 0, self._shot)
