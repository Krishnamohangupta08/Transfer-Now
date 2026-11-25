"""Buttery wheel scrolling for any QAbstractScrollArea (pages, panes, lists).

Qt jumps a fixed number of pixels per wheel notch.  ``SmoothScroller`` instead
accumulates a *target* position and glides the scrollbar towards it with a
frame-rate independent exponential ease, so a wheel notch becomes a short,
decelerating slide.  Touchpads (pixel deltas) are followed almost 1:1, dragging
the scrollbar / keyboard / programmatic scrolling stay instant and always win.

    from .smoothscroll import enable_smooth_scroll
    enable_smooth_scroll(scroll_area)
"""
from __future__ import annotations

import math

from PyQt6.QtCore import QElapsedTimer, QEvent, QObject, Qt, QTimer
from PyQt6.QtWidgets import QAbstractScrollArea

STEP_PX = 96          # pixels per wheel notch (120 units)
TAU_MS = 85.0         # ease time-constant: lower = snappier, higher = floatier
TOUCHPAD_TAU_MS = 28.0
FRAME_MS = 8          # ~120 Hz tick; the real cadence follows the event loop


class SmoothScroller(QObject):
    def __init__(self, area: QAbstractScrollArea, step: int = STEP_PX, tau: float = TAU_MS) -> None:
        super().__init__(area)
        self._area = area
        self._bar = area.verticalScrollBar()
        self._step = step
        self._tau = tau
        self._pos = float(self._bar.value())
        self._target = self._pos
        self._busy = False                    # True while *we* move the bar
        self._fast = False                    # last input was a touchpad
        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(FRAME_MS)
        self._timer.timeout.connect(self._tick)
        area.viewport().installEventFilter(self)
        self._bar.installEventFilter(self)
        self._bar.valueChanged.connect(self._on_value)
        self._bar.rangeChanged.connect(self._on_range)
        self._bar.sliderPressed.connect(self._stop)

    def attach(self, widget) -> None:
        """Also drive this scroller from wheel events landing on ``widget`` (e.g. a
        non-scrolling table that sits inside the page)."""
        widget.installEventFilter(self)

    def scroll_to(self, value: int) -> None:
        """Glide to ``value`` (clamped) instead of jumping."""
        bar = self._bar
        if not self._timer.isActive():
            self._pos = float(bar.value())
        self._fast = False
        self._target = float(min(max(value, bar.minimum()), bar.maximum()))
        if not self._timer.isActive():
            self._clock.start()
            self._timer.start()

    # ------------------------------------------------------------ plumbing
    def _stop(self) -> None:
        self._timer.stop()
        self._pos = self._target = float(self._bar.value())

    def _on_value(self, value: int) -> None:
        """Someone else moved the bar (drag, keys, scroll_to_...): follow it."""
        if not self._busy:
            self._timer.stop()
            self._pos = self._target = float(value)

    def _on_range(self, lo: int, hi: int) -> None:
        self._target = min(max(self._target, lo), hi)
        self._pos = min(max(self._pos, lo), hi)

    def _tick(self) -> None:
        dt = max(1.0, self._clock.restart() or FRAME_MS)
        tau = TOUCHPAD_TAU_MS if self._fast else self._tau
        diff = self._target - self._pos
        if abs(diff) < 0.6:
            self._pos = self._target
            self._timer.stop()
        else:
            self._pos += diff * (1.0 - math.exp(-dt / tau))
        self._busy = True
        try:
            self._bar.setValue(int(round(self._pos)))
        finally:
            self._busy = False

    # --------------------------------------------------------------- wheel
    def eventFilter(self, obj, ev) -> bool:           # noqa: N802 (Qt API)
        if ev.type() != QEvent.Type.Wheel:
            return False
        if ev.modifiers() & Qt.KeyboardModifier.ControlModifier:
            return False
        bar = self._bar
        if bar.maximum() <= bar.minimum():
            return False                              # nothing to scroll: let parents have it
        pd = ev.pixelDelta().y()
        ad = ev.angleDelta().y()
        if pd == 0 and ad == 0:
            return False                              # horizontal-only gesture
        if pd:
            delta, self._fast = -float(pd), True
        else:
            delta, self._fast = -ad / 120.0 * self._step, False
        if not self._timer.isActive():
            self._pos = self._target = float(bar.value())
        self._target = min(max(self._target + delta, bar.minimum()), bar.maximum())
        if not self._timer.isActive():
            self._clock.start()
            self._timer.start()
        ev.accept()
        return True


def enable_smooth_scroll(area: QAbstractScrollArea, step: int = STEP_PX) -> SmoothScroller:
    """Attach (once) and return the smooth scroller of ``area``."""
    existing = getattr(area, "_smooth_scroller", None)
    if existing is not None:
        return existing
    sc = SmoothScroller(area, step)
    area._smooth_scroller = sc                         # type: ignore[attr-defined]
    return sc
