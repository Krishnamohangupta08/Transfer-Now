"""macOS-style tooltips for the whole app.

A single global event filter intercepts Qt's ``QEvent.ToolTip`` and shows a
custom bubble instead of the native tooltip: a small, rounded card with a
soft drop shadow and a quick fade-in, placed below-right of the cursor and
flipped when it would leave the screen — the way macOS does it.

Every widget that calls ``setToolTip()`` gets this for free; theme changes
restyle the bubble automatically.

    from ui.tips import tips
    tips.install(app)
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import (
    QEasingCurve, QEvent, QObject, QPropertyAnimation, Qt, QTimer,
)
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QApplication, QFrame, QGraphicsDropShadowEffect, QLabel, QVBoxLayout, QWidget,
)

from .theme import theme

#: fade-in / fade-out durations (ms)
_FADE_IN, _FADE_OUT = 130, 90
#: offset of the bubble relative to the cursor (macOS places it low-right)
_DX, _DY = 16, 22
#: margin kept from the shadow so it is never clipped
_SHADOW_PAD = (24, 18, 24, 26)
#: longest line before the text wraps
_MAX_TEXT_WIDTH = 340


class TipBubble(QWidget):
    """Frameless, translucent tooltip window: shadowed card + fading label."""

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(*_SHADOW_PAD)
        self.card = QFrame()
        self.card.setObjectName("mactip")
        outer.addWidget(self.card)
        card_lay = QVBoxLayout(self.card)
        card_lay.setContentsMargins(11, 7, 11, 8)
        self.text = QLabel(self.card)
        self.text.setObjectName("tiplbl")
        self.text.setTextFormat(Qt.TextFormat.PlainText)
        self.text.setWordWrap(True)
        self.text.setMaximumWidth(_MAX_TEXT_WIDTH)
        card_lay.addWidget(self.text)

        shadow = QGraphicsDropShadowEffect(self.card)
        shadow.setBlurRadius(26)
        shadow.setOffset(0, 9)
        shadow.setColor(QColor(0, 0, 0, 95 if theme.dark else 60))
        self.card.setGraphicsEffect(shadow)

        # fade via windowOpacity — reliable for top-level tooltip windows
        self._anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    # ---------------------------------------------------------------- api
    def popup(self, text: str, global_pos) -> None:
        self.text.setText(text)
        self.adjustSize()
        self._place(global_pos)
        try:                                   # a pending fade-out must not kill the show
            self._anim.finished.disconnect()
        except TypeError:
            pass
        if not self.isVisible():
            self.setWindowOpacity(0.0)
            self.show()
        self._anim.stop()
        self._anim.setStartValue(self.windowOpacity())
        self._anim.setEndValue(1.0)
        self._anim.setDuration(_FADE_IN if self.windowOpacity() < 0.55 else 40)
        self._anim.start()

    def fade_out(self) -> None:
        if not self.isVisible():
            return
        self._anim.stop()
        self._anim.setStartValue(self.windowOpacity())
        self._anim.setEndValue(0.0)
        self._anim.setDuration(_FADE_OUT)
        try:
            self._anim.finished.disconnect()
        except TypeError:
            pass
        self._anim.finished.connect(self.hide)
        self._anim.start()

    # ------------------------------------------------------------- layout
    def _place(self, global_pos) -> None:
        w, h = self.width(), self.height()
        # layout margins are shadow padding; the visible card starts inside
        cw, ch = w - _SHADOW_PAD[0] - _SHADOW_PAD[2], h - _SHADOW_PAD[1] - _SHADOW_PAD[3]
        x = global_pos.x() + _DX
        y = global_pos.y() + _DY
        screen = None
        anchor_screen = QApplication.widgetAt(global_pos)
        if anchor_screen is not None:
            screen = anchor_screen.screen()
        geo = screen.availableGeometry() if screen is not None else None
        if geo is not None:
            x = min(x, geo.right() - cw - 8)
            y = min(y, geo.bottom() - ch - 10)
            if y < geo.top() + 4:                       # doesn't fit below -> above cursor
                y = max(geo.top() + 4, global_pos.y() - _DY - ch)
                x = min(max(geo.left() + 8, x), geo.right() - cw - 8)
            x = max(geo.left() + 8, x)
        self.move(x - _SHADOW_PAD[0], y - _SHADOW_PAD[1])


class TipManager(QObject):
    """Global event filter: replace every native tooltip with the bubble."""

    def __init__(self) -> None:
        super().__init__()
        self.bubble: Optional[TipBubble] = None
        self.anchor: Optional[QWidget] = None
        self._guard = QTimer(self)                 # safety: never stick forever
        self._guard.setSingleShot(True)
        self._guard.timeout.connect(self.fade_out)
        theme.changed.connect(self._retheme)

    # ---------------------------------------------------------------- api
    def install(self, app: QApplication) -> None:
        app.installEventFilter(self)

    def show_tip(self, anchor: QWidget, global_pos) -> None:
        text = anchor.toolTip()
        if not text:
            return
        if self.bubble is None:
            self.bubble = TipBubble()
        if self.anchor is not None and self.anchor is not anchor:
            try:
                self.anchor.destroyed.disconnect()
            except (TypeError, RuntimeError):
                pass
        self.anchor = anchor
        try:
            anchor.destroyed.connect(self.fade_out)
        except RuntimeError:
            pass
        self.bubble.popup(text, global_pos)
        self._guard.start(15_000)                  # macOS tooltips linger while hovered

    def fade_out(self) -> None:
        self._guard.stop()
        self.anchor = None
        b, self.bubble = self.bubble, None
        if b is None:
            return
        try:
            b.fade_out()
        except RuntimeError:                       # C++ object already gone
            pass

    def _retheme(self) -> None:
        if self.bubble is not None and self.bubble.isVisible():
            self.bubble.card.style().unpolish(self.bubble.card)
            self.bubble.card.style().polish(self.bubble.card)

    # ----------------------------------------------------------- filtering
    def eventFilter(self, obj, ev) -> bool:  # noqa: N802
        t = ev.type()
        if t == QEvent.Type.ToolTip:
            w = obj if isinstance(obj, QWidget) else None
            gpos = getattr(ev, "globalPos", None)
            if w is not None and w.toolTip() and w.isEnabled() and callable(gpos):
                self.show_tip(w, gpos())
                return True                          # no native tooltip, please
        elif t in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick,
                   QEvent.Type.Wheel, QEvent.Type.MouseButtonRelease):
            self.fade_out()
        elif obj is self.anchor and t in (QEvent.Type.Leave, QEvent.Type.Hide,
                                          QEvent.Type.Close, QEvent.Type.Destroy,
                                          QEvent.Type.WindowDeactivate):
            self.fade_out()
        return False


tips = TipManager()
