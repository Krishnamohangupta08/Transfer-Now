"""Changelog — native release-history page that renders ``CHANGELOG.md``.

Nothing is hardcoded here: ``core.changelog.ChangelogParser`` reads the file
(mtime-cached, never raises) and this page draws whatever it returns — a
version header, the latest release in full and older releases as collapsible
cards on a quiet timeline.  Missing / empty / malformed files get a proper
empty state instead of an error.
"""
from __future__ import annotations

import html

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
    QPolygonF, QRadialGradient,
)
from PyQt6.QtWidgets import QFrame, QGraphicsOpacityEffect, QPushButton, QSizePolicy, QWidget

from core.changelog import SECTION_ORDER, ChangelogParser, Release
from core.version import APP_VERSION
from ..i18n import tr
from .. import assets, iconfx, icons, motion
from ..iconfx import AnimatedLogo
from ..theme import _mix, theme
from ..widgets import Button, IconLabel, Pill, hbox, label, vbox
from .base import Page

RAIL_W = 22
DOT_Y = 27                      # centre of the timeline dot, from the card top

_TONES = {"New Features": "accent", "Improvements": "", "Bug Fixes": "warning",
          "Security": "success", "Breaking Changes": "danger"}

# section title -> (icon, palette key) — one colour + glyph per kind of change
_KIND = {"New Features": ("sparkles", "accent"), "Improvements": ("trending", "success"),
         "Bug Fixes": ("shield-check", "warning"), "Security": ("shield", "success"),
         "Breaking Changes": ("alert", "danger")}
_DEFAULT_KIND = ("file-text", "text2")
# the same colours, brightened for the always-dark hero card
_HERO_TONE = {"New Features": None, "Improvements": "#4ADE9B", "Bug Fixes": "#FBBF4A",
              "Security": "#4ADE9B", "Breaking Changes": "#FF7A6E"}


def _kind(title: str) -> tuple[str, str]:
    return _KIND.get(title, _DEFAULT_KIND)


def _tone_color(key: str) -> QColor:
    return QColor(theme.pal.get(key, theme.pal["accent"]))


def _rgba(color: QColor, alpha: float) -> str:
    return f"rgba({color.red()},{color.green()},{color.blue()},{alpha})"


def _release_date_text(rel: Release) -> str:
    """The recorded date, or '' — nothing is shown for a release without one."""
    return rel.date or ""


def banner_path(version: str) -> str:
    """Asset key of the banner for one release (banners/v<version>.png) or '' when none.
    Resolved from disk first, then from the copy embedded in the app."""
    rel = f"banners/v{version}.png"
    return rel if assets.exists(rel) else ""


class _Banner(QWidget):
    """Release banner: scales to the card width (aspect kept), rounded corners.

    The height is set *explicitly* from the width (see ``resizeEvent``) instead of
    through Qt's heightForWidth.  heightForWidth inside nested layouts goes stale when
    sibling sections are shown/hidden (the filter bar), which left the row reserving
    more height than the card received: the card was centred lower and the banner got
    squeezed.  A plain fixed height has no such failure mode.
    """

    def __init__(self, path: str) -> None:
        super().__init__()
        self._src = assets.pixmap(path)
        self._cache: tuple[int, QPixmap] | None = None
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName("Release banner")
        if self.ok():
            self.setFixedHeight(self._h_for(520))

    def ok(self) -> bool:
        return not self._src.isNull()

    def _h_for(self, w: int) -> int:
        if not self.ok():
            return 0
        return max(1, round(w * self._src.height() / self._src.width()))

    def heightForWidth(self, w: int) -> int:  # noqa: N802  (kept for callers/tests)
        return self._h_for(w)

    def sizeHint(self):  # noqa: N802
        from PyQt6.QtCore import QSize
        return QSize(520, self._h_for(520))

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        if self.ok() and self.width() > 1:
            h = self._h_for(self.width())
            if h != self.height():
                self.setFixedHeight(h)

    def paintEvent(self, _e) -> None:  # noqa: N802
        if not self.ok() or self.width() < 2:
            return
        dpr = self.devicePixelRatioF()
        key = int(self.width() * dpr)
        if self._cache is None or self._cache[0] != key:
            pm = self._src.scaledToWidth(key, Qt.TransformationMode.SmoothTransformation)
            pm.setDevicePixelRatio(dpr)
            self._cache = (key, pm)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 12, 12)
        p.setClipPath(path)
        p.drawPixmap(0, 0, self._cache[1])
        p.setClipping(False)
        p.setPen(QPen(QColor(theme.pal["border"]), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 12, 12)


class _Rail(QWidget):
    """Timeline gutter: a gradient hairline with one dot; the latest dot pulses softly."""

    def __init__(self, first: bool, last: bool, latest: bool) -> None:
        super().__init__()
        self._first, self._last, self._latest = first, last, latest
        self._pulse = 0.0
        self.setFixedWidth(RAIL_W)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        theme.changed.connect(self.update)
        self._a = iconfx.make_anim(self, self._set_pulse, kind="sine")
        self._a.setLoopCount(-1)

    def _set_pulse(self, v: float) -> None:
        self._pulse = v
        self.update()

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        if self._latest and iconfx.ENABLED:
            self._a.setStartValue(0.0)
            self._a.setEndValue(1.0)
            self._a.setDuration(2400)
            self._a.start()

    def hideEvent(self, e) -> None:  # noqa: N802
        self._a.stop()
        super().hideEvent(e)

    def paintEvent(self, _e) -> None:  # noqa: N802
        c = theme.pal
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        x = RAIL_W / 2
        line = QLinearGradient(0, 0, 0, self.height())
        top = QColor(c["accent"]) if self._first else QColor(c["border_strong"])
        top.setAlpha(150 if self._first else 255)
        line.setColorAt(0.0, top)
        line.setColorAt(min(1.0, 260 / max(1, self.height())), QColor(c["border_strong"]))
        line.setColorAt(1.0, QColor(c["border_strong"]))
        p.setPen(QPen(line, 1.6))
        if not self._last:
            p.drawLine(QPointF(x, DOT_Y if self._first else 0), QPointF(x, self.height()))
        elif not self._first:
            p.drawLine(QPointF(x, 0), QPointF(x, DOT_Y))
        p.setPen(Qt.PenStyle.NoPen)
        if self._latest:
            halo = QColor(c["accent"])
            halo.setAlpha(int(70 * (1.0 - self._pulse)))
            rr = 6 + 6 * self._pulse
            p.setBrush(halo)
            p.drawEllipse(QRectF(x - rr, DOT_Y - rr, rr * 2, rr * 2))
            p.setBrush(QColor(c["accent_soft"]))
            p.drawEllipse(QRectF(x - 8, DOT_Y - 8, 16, 16))
            p.setBrush(QColor(c["accent"]))
            p.drawEllipse(QRectF(x - 4.5, DOT_Y - 4.5, 9, 9))
        else:
            p.setBrush(QColor(c["sheet"]))
            p.setPen(QPen(QColor(c["border_strong"]), 1.6))
            p.drawEllipse(QRectF(x - 4.5, DOT_Y - 4.5, 9, 9))


def _stats_text(rel: Release) -> str:
    """'12 new features · 4 improvements' — counts straight from the parsed sections."""
    names = {"New Features": ("new feature", "new features"), "Improvements": ("improvement", "improvements"),
             "Bug Fixes": ("fix", "fixes"), "Breaking Changes": ("breaking change", "breaking changes")}
    bits = []
    for t in SECTION_ORDER:
        n = sum(len(sec.items) for sec in rel.sections if sec.title == t)
        if n and t in names:
            bits.append(f"{n} {names[t][n != 1]}")
    return " · ".join(bits)


def _counts(rel: Release) -> list[tuple[str, int]]:
    """[(section title, item count)] in display order."""
    order = {t: i for i, t in enumerate(SECTION_ORDER)}
    out: dict[str, int] = {}
    for sec in sorted(rel.sections, key=lambda s: order.get(s.title, len(order))):
        out[sec.title] = out.get(sec.title, 0) + len(sec.items)
    return [(t, n) for t, n in out.items() if n]


# ------------------------------------------------------------------- hero
class _StatChip(QWidget):
    """Translucent pill on the dark hero: coloured dot, bold count, label."""

    def __init__(self, count: int, text: str, tone: str) -> None:
        super().__init__()
        self._n, self._text, self._tone = str(count), text, QColor(tone)
        self._f = QFont(self.font())
        self._f.setPixelSize(12)
        self._fb = QFont(self._f)
        self._fb.setWeight(QFont.Weight.Bold)
        self._fb.setPixelSize(13)
        wn = QFontMetrics(self._fb).horizontalAdvance(self._n)
        wt = QFontMetrics(self._f).horizontalAdvance(text)
        self._wn = wn
        self.setFixedSize(14 + 12 + wn + 6 + wt + 14, 28)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setBrush(QColor(255, 255, 255, 22))
        p.setPen(QPen(QColor(255, 255, 255, 34), 1))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        glow = QColor(self._tone)
        glow.setAlpha(70)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(glow)
        p.drawEllipse(QPointF(15, r.center().y()), 5.5, 5.5)
        p.setBrush(self._tone)
        p.drawEllipse(QPointF(15, r.center().y()), 3, 3)
        p.setFont(self._fb)
        p.setPen(QColor("#FFFFFF"))
        x = 14 + 12
        p.drawText(QRectF(x, 0, self._wn + 2, self.height()), Qt.AlignmentFlag.AlignVCenter, self._n)
        p.setFont(self._f)
        p.setPen(QColor(255, 255, 255, 190))
        p.drawText(QRectF(x + self._wn + 6, 0, self.width(), self.height()),
                   Qt.AlignmentFlag.AlignVCenter, self._text)


class _Hero(QFrame):
    """Premium dark header: gradient + accent glow + dot grid + a one-off shine sweep."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("cl_hero")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self._shine = -1.0
        self._started = False
        self._a = iconfx.make_anim(self, self._set_shine, kind="sine")
        self._a.finished.connect(self._end_shine)
        theme.changed.connect(self.update)

    def _set_shine(self, v: float) -> None:
        self._shine = v
        self.update()

    def _end_shine(self) -> None:
        self._set_shine(-1.0)

    def _go(self) -> None:
        if self.isVisible():
            iconfx.run(self._a, 0.0, 1.0, 1500, kind="sine")

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        if not self._started:
            self._started = True
            QTimer.singleShot(420, self._go)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        acc = QColor(theme.pal["accent"])
        base = "#090B10"
        g = QLinearGradient(r.topLeft(), r.bottomRight())
        g.setColorAt(0.0, QColor(base))
        g.setColorAt(0.55, QColor(_mix(base, theme.pal["accent"], 0.22)))
        g.setColorAt(1.0, QColor(_mix(base, theme.pal["accent"], 0.55)))
        path = QPainterPath()
        path.addRoundedRect(r, 22, 22)
        p.fillPath(path, g)
        p.save()
        p.setClipPath(path)
        glow = QRadialGradient(QPointF(r.right() - r.width() * 0.10, r.top() + r.height() * 0.05),
                               r.width() * 0.55)
        c0 = QColor(acc)
        c0.setAlpha(120)
        c1 = QColor(acc)
        c1.setAlpha(0)
        glow.setColorAt(0.0, c0)
        glow.setColorAt(1.0, c1)
        p.fillRect(r, glow)
        # dotted grid, fading out towards the left
        step = 16
        x0 = r.width() * 0.45
        for ix in range(int(x0 // step), int(r.width() // step) + 1):
            x = ix * step + 8
            k = max(0.0, min(1.0, (x - x0) / (r.width() - x0)))
            p.setPen(QPen(QColor(255, 255, 255, int(8 + 30 * k)), 2.0, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap))
            for iy in range(int(r.height() // step) + 1):
                p.drawPoint(QPointF(x, iy * step + 8))
        if self._shine >= 0:                          # diagonal light band
            w, h = r.width(), r.height()
            bw = w * 0.18
            x = iconfx.lerp(-bw * 2, w + bw, self._shine)
            sk = h * 0.6
            band = QPolygonF([QPointF(x, 0), QPointF(x + bw, 0), QPointF(x + bw - sk, h), QPointF(x - sk, h)])
            gr = QLinearGradient(x - sk / 2, 0, x + bw - sk / 2, 0)
            gr.setColorAt(0.0, QColor(255, 255, 255, 0))
            gr.setColorAt(0.5, QColor(255, 255, 255, 34))
            gr.setColorAt(1.0, QColor(255, 255, 255, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(gr)
            p.drawPolygon(band)
        p.restore()
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 30), 1))
        p.drawPath(path)


class _BadgePill(QWidget):
    """Fully rounded, custom-painted badge.

    Replaces QLabel + stylesheet badges, whose corners render square-ish on some
    platforms/DPI settings and whose box could be wider/taller than the text.
    ``fg``/``bg``/``border`` are callables (or plain values) so the badge follows
    the theme without needing a restyle pass.
    """

    def __init__(self, text: str, fg, bg, border=None, *, px: int = 11, weight=None,
                 hpad: int = 10, height: int = 22, min_w: int = 0) -> None:
        super().__init__()
        self._text, self._fg, self._bg, self._bd = text, fg, bg, border
        f = QFont(self.font())
        f.setPixelSize(px)
        f.setWeight(weight or QFont.Weight.DemiBold)
        self._f = f
        w = QFontMetrics(f).horizontalAdvance(text) + hpad * 2 + 2
        self.setFixedSize(max(min_w, w), height)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        theme.changed.connect(self.update)

    @staticmethod
    def _v(x):
        return QColor(x() if callable(x) else x)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setBrush(self._v(self._bg))
        if self._bd is not None:
            p.setPen(QPen(self._v(self._bd), 1))
        else:
            p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        p.setFont(self._f)
        p.setPen(self._v(self._fg))
        p.drawText(QRectF(self.rect()), Qt.AlignmentFlag.AlignCenter, self._text)


# --------------------------------------------------------------- filtering
class _FilterChip(QPushButton):
    """Pill toggle used by the filter bar."""

    def __init__(self, text: str, tone: str = "accent") -> None:
        super().__init__(text)
        self._tone = tone
        self._hov = 0.0
        self._kbd_focus = False
        self._a = iconfx.make_anim(self, self._set_hov)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # Keyboard-only focus: a mouse click must never leave an outline behind.
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        f = QFont(self.font())
        f.setPixelSize(12)
        f.setWeight(QFont.Weight.DemiBold)
        self._f = f
        self.setFont(f)                        # measure with the exact font we paint with
        # Custom-painted: switch off the global QPushButton stylesheet (its square box,
        # border and padding must never show through behind the pill).
        # (QSS min-height overrides setFixedHeight, so it is pinned to the pill's 30 px.)
        self.setStyleSheet("QPushButton { background: transparent; border: none; padding: 0; "
                           "min-height: 30px; max-height: 30px; }")
        self.setFixedHeight(30)
        # +36 padding and a small safety margin so a label ("All", "New Features") is
        # never clipped or elided, whatever the platform's font rendering does.
        self.setFixedWidth(QFontMetrics(f).horizontalAdvance(text) + 44)
        theme.changed.connect(self.update)

    def _set_hov(self, v: float) -> None:
        self._hov = v
        self.update()

    def focusInEvent(self, e) -> None:  # noqa: N802
        self._kbd_focus = e.reason() in (Qt.FocusReason.TabFocusReason,
                                         Qt.FocusReason.BacktabFocusReason)
        super().focusInEvent(e)

    def enterEvent(self, e) -> None:  # noqa: N802
        iconfx.run(self._a, self._hov, 1.0, 140)
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        iconfx.run(self._a, self._hov, 0.0, 200)
        super().leaveEvent(e)

    def paintEvent(self, _e) -> None:  # noqa: N802
        c = theme.pal
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        on = self.isChecked()
        tone = _tone_color(self._tone)
        if on:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(tone)
            fg = QColor(c["on_accent"] if self._tone == "accent" else "#FFFFFF")
        else:
            bd = QColor(c["border_strong"])
            hi = QColor(tone)
            hi.setAlpha(int(120 * self._hov))
            p.setPen(QPen(hi if self._hov > 0.02 else bd, 1))
            fill = QColor(c["surface"])
            p.setBrush(fill)
            fg = QColor(c["text"] if self._hov > 0.4 else c["text2"])
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        p.setFont(self._f)
        p.setPen(fg)
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.text())
        if self.hasFocus() and self._kbd_focus:
            p.setBrush(Qt.BrushStyle.NoBrush)   # keyboard users only
            p.setPen(QPen(QColor(c["accent"]), 1.5))
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), r.height() / 2, r.height() / 2)


class _FilterBar(QWidget):
    """'All · New Features · Improvements …' — one active chip at a time."""
    changed = pyqtSignal(str)                  # "" = everything

    def __init__(self, titles: list[tuple[str, int]], active: str = "") -> None:
        super().__init__()
        self.chips: dict[str, _FilterChip] = {}
        row = hbox(self, (0, 0, 0, 0), 8)
        total = sum(n for _t, n in titles)
        for key, text, tone in [("", f"All  {total}", "accent")] + [
                (t, f"{t}  {n}", _kind(t)[1]) for t, n in titles]:
            chip = _FilterChip(text, tone if tone in theme.pal else "accent")
            chip.clicked.connect(lambda _=False, k=key: self.pick(k))
            row.addWidget(chip)
            self.chips[key] = chip
        row.addStretch(1)
        self.active = active if active in self.chips else ""
        self._sync()

    def pick(self, key: str) -> None:
        if key == self.active:
            self._sync()
            return
        self.active = key
        self._sync()
        self.changed.emit(key)

    def _sync(self) -> None:
        for k, c in self.chips.items():
            c.setChecked(k == self.active)


# ----------------------------------------------------------- release body
class _IconTile(QWidget):
    """Rounded square with a tinted glyph — the section's identity mark."""

    def __init__(self, icon: str, tone: str, size: int = 30) -> None:
        super().__init__()
        self._icon, self._tone = icon, tone
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        theme.changed.connect(self.update)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        col = _tone_color(self._tone)
        bg = QColor(col)
        bg.setAlpha(34 if theme.dark else 28)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(QRectF(self.rect()), 10, 10)
        s = 16
        p.drawPixmap(QPointF((self.width() - s) / 2, (self.height() - s) / 2),
                     icons.pixmap(self._icon, col.name(), s, 1.7))


class _Bullet(QWidget):
    """Small tone dot with a soft halo, aligned to the first text line."""

    def __init__(self, tone: str) -> None:
        super().__init__()
        self._tone = tone
        self.setFixedSize(14, 20)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        theme.changed.connect(self.update)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        col = _tone_color(self._tone)
        halo = QColor(col)
        halo.setAlpha(46)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(halo)
        p.drawEllipse(QPointF(7, 10.5), 6, 6)
        p.setBrush(col)
        p.drawEllipse(QPointF(7, 10.5), 2.6, 2.6)


class _Section(QFrame):
    """One kind of change (e.g. New Features) as a softly tinted panel."""

    def __init__(self, title: str, items: list[str]) -> None:
        super().__init__()
        self.title = title
        icon, self._tone = _kind(title)
        self.setObjectName("cl_sec")
        lay = vbox(self, (16, 14, 16, 16), 10)
        head = hbox(spacing=10)
        head.addWidget(_IconTile(icon, self._tone), 0, Qt.AlignmentFlag.AlignVCenter)
        self.title_label = label(title, "")
        self.title_label.setStyleSheet("font-size: 14px; font-weight: 600;")
        head.addWidget(self.title_label, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addStretch(1)
        self.count_label = _BadgePill(
            str(len(items)),
            fg=lambda: _tone_color(self._tone),
            bg=lambda: QColor(*_tone_color(self._tone).getRgb()[:3], 41),
            px=11, weight=QFont.Weight.Bold, hpad=9, height=22, min_w=28)
        head.addWidget(self.count_label, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addLayout(head)
        body = vbox(spacing=9)
        body.setContentsMargins(40, 0, 0, 0)
        for text in items:
            r = hbox(spacing=8)
            r.addWidget(_Bullet(self._tone), 0, Qt.AlignmentFlag.AlignTop)
            lbl = label(text, "", wrap=True, selectable=True)
            head_txt, sep, rest = text.partition(" — ")
            if sep and len(head_txt) <= 60:           # "Feature — details": emphasise the lead-in
                lbl.setTextFormat(Qt.TextFormat.RichText)
                lbl.setText(f"<b>{html.escape(head_txt)}</b> — {html.escape(rest)}")
            r.addWidget(lbl, 1)
            body.addLayout(r)
        lay.addLayout(body)
        self._restyle()
        theme.changed.connect(self._restyle)

    def _restyle(self) -> None:
        self.count_label.update()
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        col = _tone_color(self._tone)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        fill, line = QColor(col), QColor(col)
        fill.setAlpha(20 if theme.dark else 13)
        line.setAlpha(52 if theme.dark else 44)
        p.setPen(QPen(line, 1))
        p.setBrush(fill)
        p.drawRoundedRect(r, 14, 14)


class _Header(QFrame):
    """Clickable / keyboard-toggleable card header (Space, Enter)."""
    clicked = pyqtSignal()

    def __init__(self, interactive: bool) -> None:
        super().__init__()
        self._interactive = interactive
        if interactive:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if self._interactive and e.button() == Qt.MouseButton.LeftButton \
                and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if self._interactive and e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter,
                                             Qt.Key.Key_Space):
            self.clicked.emit()
            return
        super().keyPressEvent(e)


class ReleaseCard(QFrame):
    """One release.  Latest is always open; older ones collapse."""

    def __init__(self, rel: Release, latest: bool, expanded: bool) -> None:
        super().__init__()
        self.rel = rel
        self.latest = latest
        self.expanded = expanded or latest
        self._hover = 0.0
        self._filter = ""
        self.sections: list[_Section] = []
        self.setObjectName("card")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self._a_hover = iconfx.make_anim(self, self._set_hover)
        lay = vbox(self, (22, 18, 22, 18), 0)

        self.header = _Header(not latest)
        self.header.setObjectName("cl_head")
        row = hbox(self.header, (0, 0, 0, 0), 10)
        self.version_label = label(f"v{rel.version}", "h2")
        self.version_label.setStyleSheet("font-size: 19px; font-weight: 700; letter-spacing: -0.3px;")
        row.addWidget(self.version_label)
        if latest:
            row.addWidget(Pill(tr("cl.latest"), "accent"))
        row.addStretch(1)
        self.date_icon = IconLabel("calendar", 14, "muted")
        row.addWidget(self.date_icon)
        self.date_label = label(_release_date_text(rel), "caption")
        row.addWidget(self.date_label)
        if not rel.date:                    # nothing is invented for an undated release
            self.date_label.hide()
            self.date_icon.hide()
        self.chevron = IconLabel("chevron-down", 16, "muted")
        row.addWidget(self.chevron)            # parent it FIRST: showing a parentless widget pops a window
        self.chevron.setVisible(not latest)
        lay.addWidget(self.header)
        self.header.clicked.connect(self.toggle)

        stats = _stats_text(rel)
        self.stats_label = label(stats, "caption")
        if not stats:
            self.stats_label.hide()
        lay.addWidget(self.stats_label)

        self.banner = None
        bp = banner_path(rel.version)
        if bp:                                       # artwork right under the version header
            b = _Banner(bp)
            if b.ok():
                lay.addSpacing(14)
                lay.addWidget(b)
                self.banner = b

        self.body = QWidget()
        self.body.setObjectName("cl_body")
        bl = vbox(self.body, (0, 16, 0, 0), 12)
        self._fill(bl, rel)
        lay.addWidget(self.body)
        self._apply()

    # ------------------------------------------------------------ paint
    def _set_hover(self, v: float) -> None:
        self._hover = v
        self.update()

    def enterEvent(self, e) -> None:  # noqa: N802
        if not self.latest:
            iconfx.run(self._a_hover, self._hover, 1.0, 160)
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        if not self.latest:
            iconfx.run(self._a_hover, self._hover, 0.0, 220)
        super().leaveEvent(e)

    def paintEvent(self, e) -> None:  # noqa: N802
        super().paintEvent(e)                      # QSS card background
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        acc = QColor(theme.pal["accent"])
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(r, 20, 20)
        if self.latest:                            # soft accent wash from the top edge
            p.save()
            p.setClipPath(path)
            wash = QLinearGradient(0, 0, 0, min(220.0, r.height()))
            top = QColor(acc)
            top.setAlpha(34 if theme.dark else 22)
            wash.setColorAt(0.0, top)
            acc0 = QColor(acc)
            acc0.setAlpha(0)
            wash.setColorAt(1.0, acc0)
            p.fillRect(r, wash)
            p.restore()
            edge = QColor(acc)
            edge.setAlpha(110)
            p.setPen(QPen(edge, 1.2))
        elif self._hover > 0.01:
            edge = QColor(acc)
            edge.setAlpha(int(100 * self._hover))
            p.setPen(QPen(edge, 1.2))
        else:
            return
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)

    # ------------------------------------------------------------ content
    def _fill(self, bl, rel: Release) -> None:
        self.summary_label = None
        self.nothing_label = label("Nothing in this category for this release.", "caption")
        self.nothing_label.hide()
        if rel.summary:
            self.summary_label = label(rel.summary, "muted", wrap=True, selectable=True)
            bl.addWidget(self.summary_label)
        if rel.is_empty():
            bl.addWidget(label(tr("cl.no_notes"), "caption"))
            return
        order = {t: i for i, t in enumerate(SECTION_ORDER)}
        for sec in sorted(rel.sections, key=lambda s: order.get(s.title, len(order))):
            w = _Section(sec.title, list(sec.items))
            self.sections.append(w)
            bl.addWidget(w)
        bl.addWidget(self.nothing_label)

    # ------------------------------------------------------------ filter
    def apply_filter(self, title: str) -> None:
        """Show only the sections called ``title`` ('' = all)."""
        self._filter = title
        shown = 0
        for w in self.sections:
            vis = (not title) or w.title == title
            w.setVisible(vis)
            shown += 1 if vis else 0
        if self.summary_label is not None:
            self.summary_label.setVisible(not title)       # focus on the category
        self.nothing_label.setVisible(bool(title) and shown == 0 and bool(self.sections))
        self.updateGeometry()

    # ------------------------------------------------------------ expand
    def toggle(self) -> None:
        if not self.latest:
            self.set_expanded(not self.expanded)

    def set_expanded(self, on: bool) -> None:
        was = self.expanded
        self.expanded = bool(on) or self.latest
        self._apply()
        if self.expanded and not was and self.isVisible():      # gentle reveal
            motion.fade_in(self.body, 240, lambda: motion.clear_effect(self.body))

    def _apply(self) -> None:
        self.body.setVisible(self.expanded)
        self.chevron.set_icon("chevron-down" if self.expanded else "chevron-right")
        self.header.setToolTip("" if self.latest else
                               tr("cl.collapse") if self.expanded else tr("cl.expand"))
        self.header.setAccessibleName(f"v{self.rel.version}")
        self.updateGeometry()


class ChangelogPage(Page):
    key = "changelog"
    title_key = "cl.title"
    subtitle_key = "cl.subtitle"

    def __init__(self, ctx) -> None:
        self.parser = ChangelogParser()
        self.cards: list[ReleaseCard] = []
        self._filter = ""
        super().__init__(ctx)
        self.toggle_btn = Button("Expand all", "ghost", "chevron-down")
        self.toggle_btn.clicked.connect(self.toggle_all)
        self.header_right.addWidget(self.toggle_btn)
        self.reload_btn = Button(tr("cl.reload"), "", "refresh")
        self.reload_btn.clicked.connect(lambda: self.reload(True))
        self.header_right.addWidget(self.reload_btn)
        self.content = vbox(spacing=16)
        self.body.addLayout(self.content)
        self.body.addStretch(1)
        self.reload()

    def toggle_all(self) -> None:
        older = [c for c in self.cards if not c.latest]
        if not older:
            return
        expand = not all(c.expanded for c in older)
        for c in older:
            c.set_expanded(expand)
        self._sync_toggle()

    def _sync_toggle(self) -> None:
        older = [c for c in self.cards if not c.latest]
        self.toggle_btn.setVisible(bool(older))             # page child: safe
        self.toggle_btn.setText("Collapse all" if older and all(c.expanded for c in older)
                                else "Expand all")

    def _on_filter(self, title: str) -> None:
        self._filter = title
        bar = getattr(self, "filter_bar", None)
        host = self.widget()
        sb = self.verticalScrollBar()
        # Anchor: remember where the filter bar sits on screen so the page does not jump.
        anchor = bar.mapTo(host, bar.rect().topLeft()).y() - sb.value() if bar is not None else None
        for c in self.cards:
            c.apply_filter(title)
            if title and not c.latest and not c.expanded and c.sections:
                c.set_expanded(True)                       # show matches in older releases too
        self._sync_toggle()
        if anchor is not None:
            host.layout().activate()                       # settle sizes before measuring
            sb.setValue(bar.mapTo(host, bar.rect().topLeft()).y() - anchor)

    # ------------------------------------------------------------ data
    def on_show(self) -> None:
        self.reload()                       # cheap: parser caches on file mtime

    def _retranslate(self) -> None:
        if hasattr(self, "reload_btn"):
            self.reload_btn.setText(tr("cl.reload"))
            self.reload(True)

    def reload(self, force: bool = False) -> None:
        data = self.parser.load()
        if not force and data is getattr(self, "_data", None) and self.cards is not None \
                and self.content.count():
            return                          # same parse result: keep the page as is (no flicker)
        self._data = data
        self.setUpdatesEnabled(False)       # rebuild in one paint
        try:
            self._rebuild(data)
        finally:
            self.setUpdatesEnabled(True)

    def _rebuild(self, data) -> None:
        self._clear()
        if data.status != "ok" or not data.releases:
            self._empty(data.status)
            return
        rels = data.releases
        reveal: list[QWidget] = []
        hero = self._version_card(rels)
        self.content.addWidget(hero)
        reveal.append(hero)
        # filter bar: only the categories that exist somewhere in the file
        totals: dict[str, int] = {}
        for r in rels:
            for t, n in _counts(r):
                totals[t] = totals.get(t, 0) + n
        order = {t: i for i, t in enumerate(SECTION_ORDER)}
        titles = sorted(totals.items(), key=lambda kv: order.get(kv[0], len(order)))
        self.filter_bar = None
        if len(titles) > 1:
            self.filter_bar = _FilterBar(titles, self._filter)
            self.filter_bar.changed.connect(self._on_filter)
            self.content.addWidget(self.filter_bar)
            reveal.append(self.filter_bar)
        else:
            self._filter = ""
        for i, rel in enumerate(rels):
            if i == 1:
                self.content.addSpacing(4)
                head = label(tr("cl.older"), "h2")
                head.setContentsMargins(RAIL_W + 10, 0, 0, 0)
                self.content.addWidget(head)
            row = self._row(rel, i, len(rels))
            self.content.addLayout(row)
        for c in self.cards:
            c.header.clicked.connect(self._sync_toggle)
            reveal.append(c)
        if self._filter:
            self._on_filter(self._filter)
        self._sync_toggle()
        self._reveal(reveal)

    def _reveal(self, widgets: list[QWidget]) -> None:
        """Staggered fade-in so the page assembles itself instead of popping in."""
        if not iconfx.ENABLED:
            return
        for i, w in enumerate(widgets):
            fx = QGraphicsOpacityEffect(w)
            fx.setOpacity(0.0)
            w.setGraphicsEffect(fx)
            QTimer.singleShot(60 + 90 * i, lambda w=w, fx=fx: self._reveal_one(w, fx))

    @staticmethod
    def _reveal_one(w: QWidget, fx: QGraphicsOpacityEffect) -> None:
        try:
            if w.graphicsEffect() is not fx:
                return
            motion.anim_prop(fx, b"opacity", 0.0, 1.0, 340, on_done=lambda: _safe_clear(w))
        except RuntimeError:                      # widget was rebuilt away meanwhile
            pass

    def _clear(self) -> None:
        self.cards = []
        while self.content.count():
            it = self.content.takeAt(0)
            w = it.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
            elif it.layout() is not None:
                self._drop_layout(it.layout())

    def _drop_layout(self, lay) -> None:
        while lay.count():
            it = lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
            elif it.layout() is not None:
                self._drop_layout(it.layout())

    # ------------------------------------------------------------ pieces
    def _version_card(self, releases: list[Release]) -> QFrame:
        cur = next((r for r in releases if r.version == APP_VERSION), releases[0])
        hero = _Hero()
        col = vbox(hero, (26, 24, 26, 22), 16)
        top = hbox(spacing=16)
        logo = AnimatedLogo(46, pad=4, invert=True, intro=True)
        top.addWidget(logo, 0, Qt.AlignmentFlag.AlignVCenter)
        txt = vbox(spacing=3)
        name = label("Transfer Now", "")
        name.setStyleSheet("color: #FFFFFF; background: transparent; font-size: 22px; "
                           "font-weight: 700; letter-spacing: -0.4px;")
        txt.addWidget(name)
        self.current_label = label(f"{tr('cl.current')} {APP_VERSION}", "")
        self.current_label.setStyleSheet("color: rgba(255,255,255,0.72); background: transparent; "
                                         "font-size: 13px;")
        txt.addWidget(self.current_label)
        top.addLayout(txt, 1)
        if cur.date:
            when = _BadgePill(f"{tr('cl.released')} · {cur.date}",
                              fg=QColor(255, 255, 255, 204), bg=QColor(255, 255, 255, 26),
                              border=QColor(255, 255, 255, 42), px=12, hpad=13, height=28)
            top.addWidget(when, 0, Qt.AlignmentFlag.AlignTop)
        col.addLayout(top)
        chips = hbox(spacing=8)
        acc_hi = _mix(theme.pal["accent"], "#FFFFFF", 0.40)
        for t, n in _counts(cur):
            tone = _HERO_TONE.get(t) or acc_hi
            chips.addWidget(_StatChip(n, t.lower(), tone))
        chips.addWidget(_StatChip(len(releases), "releases" if len(releases) != 1 else "release", "#C9CFDB"))
        chips.addStretch(1)
        col.addLayout(chips)
        return hero

    def _row(self, rel: Release, i: int, n: int):
        row = hbox(spacing=10)
        row.addWidget(_Rail(i == 0, i == n - 1, i == 0))
        card = ReleaseCard(rel, latest=(i == 0), expanded=False)
        self.cards.append(card)
        row.addWidget(card, 1, Qt.AlignmentFlag.AlignTop)   # never vertically centred
        return row

    def _empty(self, status: str) -> None:
        card = QFrame()
        card.setObjectName("card")
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        col = vbox(card, (24, 36, 24, 36), 8)
        ic = IconLabel("scroll-text", 28, "muted")
        col.addWidget(ic, 0, Qt.AlignmentFlag.AlignHCenter)
        title = {"missing": "cl.missing", "invalid": "cl.invalid"}.get(status, "cl.empty")
        hint = {"missing": "cl.missing_hint", "invalid": "cl.invalid_hint"}.get(
            status, "cl.empty_hint")
        t = label(tr(title), "h2")
        t.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        col.addWidget(t)
        h = label(tr(hint), "muted", wrap=True)
        h.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        col.addWidget(h)
        self.empty_state = card
        self.content.addWidget(card)


def _safe_clear(w: QWidget) -> None:
    try:
        motion.clear_effect(w)
    except RuntimeError:
        pass
