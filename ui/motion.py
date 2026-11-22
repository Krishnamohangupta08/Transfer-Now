"""Shade motion system.

Shade's interface moves on one signature curve — cubic-bezier(0.4, 0, 0.2, 1)
— with 150 ms for state changes and ~200 ms for entrances.  The helpers here
wrap PyQt's animation classes so every transition in the app (page switches,
dialog opens, the dim backdrop, toasts, activity reveals) shares exactly that
feel, the same way the web app's radix/tailwind transitions do.

    motion.DUR          150 ms  — standard transition (colours, slides)
    motion.DUR_FAST     120 ms  — exits and micro-feedback
    motion.DUR_ENTER    220 ms  — dialog / popover entrances
    motion.ease()       cubic-bezier(0.4, 0, 0.2, 1)
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import (
    QAbstractAnimation, QEasingCurve, QPoint, QPointF, QPropertyAnimation,
)
from PyQt6.QtWidgets import QGraphicsOpacityEffect, QWidget

# --------------------------------------------------------------------------- #
#  Constants                                                                  #
# --------------------------------------------------------------------------- #
DUR_FAST = 120
DUR = 150
DUR_ENTER = 220
DUR_EXIT = 140

OFFSET_ENTER = 10          # px a page/dialog rises while fading in (radix-style)
ZOOM_ENTER = 0.97          # dialogs start at 97 % scale (radix zoom-in-95 style)


def ease() -> QEasingCurve:
    """cubic-bezier(0.4, 0, 0.2, 1) — the single Shade transition curve."""
    ec = QEasingCurve(QEasingCurve.Type.BezierSpline)
    ec.addCubicBezierSegment(QPointF(0.4, 0.0), QPointF(0.2, 1.0), QPointF(1.0, 1.0))
    return ec


def ease_out() -> QEasingCurve:
    """Decelerating curve for entrances (radix ease-out feel)."""
    ec = QEasingCurve(QEasingCurve.Type.BezierSpline)
    ec.addCubicBezierSegment(QPointF(0.16, 1.0), QPointF(0.3, 1.0), QPointF(1.0, 1.0))
    return ec


def ease_in() -> QEasingCurve:
    """Accelerating curve for exits."""
    ec = QEasingCurve(QEasingCurve.Type.BezierSpline)
    ec.addCubicBezierSegment(QPointF(0.7, 0.0), QPointF(0.84, 0.0), QPointF(1.0, 1.0))
    return ec


# --------------------------------------------------------------------------- #
#  Low-level helpers                                                          #
# --------------------------------------------------------------------------- #
def _anim(target: QObject_, prop: bytes, start, end, ms: int,
          curve: QEasingCurve, on_done=None) -> QPropertyAnimation:
    a = QPropertyAnimation(target, prop, target)
    a.setDuration(ms)
    a.setStartValue(start)
    a.setEndValue(end)
    a.setEasingCurve(curve)
    if on_done is not None:
        a.finished.connect(on_done)
    a.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
    return a


# QObject_ exists only to keep the type hints light without importing QObject
QObject_ = object


# --------------------------------------------------------------------------- #
#  Fades                                                                      #
# --------------------------------------------------------------------------- #
def fade_in(w: QWidget, ms: int = DUR, on_done=None) -> QPropertyAnimation:
    """Fade a widget in (0 -> 1) using a temporary opacity effect."""
    fx = w.graphicsEffect()
    if not isinstance(fx, QGraphicsOpacityEffect):
        fx = QGraphicsOpacityEffect(w)
        w.setGraphicsEffect(fx)
    fx.setOpacity(0.0)
    return _anim(fx, b"opacity", 0.0, 1.0, ms, ease(), on_done)


def fade_out(w: QWidget, ms: int = DUR_EXIT, on_done=None) -> QPropertyAnimation:
    """Fade a widget out; ``on_done`` should hide/delete it."""
    fx = w.graphicsEffect()
    if not isinstance(fx, QGraphicsOpacityEffect):
        fx = QGraphicsOpacityEffect(w)
        w.setGraphicsEffect(fx)
        fx.setOpacity(1.0)
    return _anim(fx, b"opacity", fx.opacity(), 0.0, ms, ease_in(), on_done)


def clear_effect(w: QWidget) -> None:
    """Drop the temporary opacity effect so native rendering resumes."""
    fx = w.graphicsEffect()
    if fx is not None:
        w.setGraphicsEffect(None)


# --------------------------------------------------------------------------- #
#  Composite entrances                                                        #
# --------------------------------------------------------------------------- #
def page_in(page: QWidget) -> None:
    """Page transition: a pure cross-fade on the decelerating curve.

    The 10 px rise was dropped on purpose: moving a widget that sits under an opacity
    effect forces the effect to re-render every frame, which is what made page changes
    stutter.  A fade alone reuses the cached render and stays buttery.
    """
    if not page.isVisible():
        clear_effect(page)
        return
    fx = page.graphicsEffect()
    if not isinstance(fx, QGraphicsOpacityEffect):
        fx = QGraphicsOpacityEffect(page)
        page.setGraphicsEffect(fx)
    fx.setOpacity(0.0)
    _anim(fx, b"opacity", 0.0, 1.0, 230, ease_out(), lambda: clear_effect(page))


def dialog_in(dialog: QWidget) -> None:
    """Dialog entrance: fade + a gentle rise (radix fade-in + slide)."""
    home = dialog.pos()

    def restore() -> None:
        clear_effect(dialog)
        QWidget.move(dialog, home)

    fade_in(dialog, DUR_ENTER, restore)
    _anim(dialog, b"pos", QPoint(home.x(), home.y() + OFFSET_ENTER),
          QPoint(home), DUR_ENTER, ease())


def dialog_out(dialog: QWidget, on_done) -> None:
    """Dialog exit: quick fade."""
    fx = dialog.graphicsEffect()
    if not isinstance(fx, QGraphicsOpacityEffect):
        on_done()
        return
    _anim(fx, b"opacity", fx.opacity(), 0.0, DUR_EXIT, ease_in(), on_done)


# --------------------------------------------------------------------------- #
#  Property-driven reveals (height/opacity pairs)                             #
# --------------------------------------------------------------------------- #
def anim_prop(target: QObject_, prop: bytes, start, end, ms: int = DUR,
              out: bool = False, on_done=None) -> QPropertyAnimation:
    """Animate any Qt property on the Shade curve."""
    return _anim(target, prop, start, end, ms, ease_in() if out else ease(),
                 on_done)


class FadeToggle:
    """Show/hide a widget with a quick fade instead of a hard flip."""

    def __init__(self, w: QWidget) -> None:
        self.w = w
        self._anim: Optional[QPropertyAnimation] = None

    def show(self) -> None:
        self.w.show()
        fade_in(self.w, DUR, lambda: clear_effect(self.w))

    def hide(self) -> None:
        if not self.w.isVisible():
            return

        def done() -> None:
            clear_effect(self.w)
            self.w.hide()

        fade_out(self.w, DUR_FAST, done)
