"""Drag & drop motion — "Soft Lift".

Calm, premium, modern: no particles, no spinning things, nothing busy.

* **Glow border** — a hairline accent border fades in with a soft inner glow that
  breathes very slowly.
* **Light from above** — a faint accent wash drops from the top edge.
* **Upload badge** — a round badge with a floating cloud arrow and two slow
  "breathing" rings.  It lifts on a spring when the drag enters.
* **Drop** — the badge morphs into a green check (spring), one soft ring leaves it,
  the glow flashes green and everything settles.
* **Window overlay** — the page behind is frosted (a cheap pre-rendered blur) and a
  clean floating card appears in the middle.

One tiny engine (``DropFX``) drives the zone and the overlay, and it only ticks
while something is moving.
"""
from __future__ import annotations

import math
import os

from PyQt6.QtCore import QElapsedTimer, QEvent, QObject, QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
)
from PyQt6.QtWidgets import QWidget

from . import icons
from .i18n import tr
from .theme import _mix, theme

DROP_LIFE = 0.7         # s — success morph + ring


def _spring(x: float, v: float, target: float, dt: float,
            k: float = 170.0, c: float = 16.0) -> tuple[float, float]:
    """Semi-implicit spring, sub-stepped so a slow frame never explodes."""
    n = max(1, int(dt / 0.008))
    h = dt / n
    for _ in range(n):
        v += (k * (target - x) - c * v) * h
        x += v * h
    return x, v


def _smooth(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def _fmt_size(n: int) -> str:
    v = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if v < 1024 or unit == "GB":
            return f"{v:.0f} {unit}" if unit == "B" else f"{v:.1f} {unit}"
        v /= 1024
    return f"{n} B"


def has_folder(urls) -> bool:
    """True when anything being dragged is a folder."""
    for u in urls:
        if u.isLocalFile() and os.path.isdir(u.toLocalFile()):
            return True
    return False


def drag_title(count: int, folder: bool) -> str:
    """Headline for a drag: files, a single folder, or a mix of items."""
    if count <= 0:
        return tr("up.drop")
    if folder:
        return tr("up.drop_folder") if count == 1 else tr("up.drop_items", n=count)
    return tr("up.drop_1") if count == 1 else tr("up.drop_n", n=count)


def drag_info(urls) -> tuple[int, str]:
    """(item count, human total size or '') for what is being dragged."""
    paths = [u.toLocalFile() for u in urls if u.isLocalFile()]
    total, known = 0, False
    for p in paths[:200]:
        try:
            if os.path.isfile(p):
                total += os.path.getsize(p)
                known = True
        except OSError:
            pass
    return len(paths), (_fmt_size(total) if known and total else "")


# --------------------------------------------------------------------- engine
class DropFX(QObject):
    tick = pyqtSignal()
    settled = pyqtSignal()

    def __init__(self, host: QWidget) -> None:
        super().__init__(host)
        self.host = host
        self.t = 0.0                      # eased 0..1 presence of the drag
        self.pop, self._popv = 0.0, 0.0   # spring: badge lift
        self.done, self._donev = 0.0, 0.0  # spring: success check
        self.target = 0.0
        self.phase = 0.0                  # slow ambient clock, 0..1 turns
        self.dropped = False
        self.drop_p = 0.0                 # 0..1 progress of the drop sequence
        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(12)
        self._timer.timeout.connect(self._step)

    @property
    def active(self) -> bool:
        return self.target > 0.5

    def begin(self, *_a) -> None:
        self.target = 1.0
        self.dropped = False
        self.drop_p = 0.0
        self._start()

    def move(self, *_a, **_k) -> None:     # cursor position is intentionally unused
        pass

    def end(self) -> None:
        self.target = 0.0
        self._start()

    def drop(self, *_a) -> None:
        self.dropped = True
        self.drop_p = 0.0
        self.target = 0.0
        self._start()

    def _start(self) -> None:
        if not self._timer.isActive():
            self._clock.start()
            self._timer.start()

    def _step(self) -> None:
        dt = min(0.05, self._clock.restart() / 1000.0) or 0.012
        rate = 8.0 if self.target > self.t else 5.5
        self.t += (self.target - self.t) * (1 - math.exp(-dt * rate))
        if abs(self.t - self.target) < 0.002:
            self.t = self.target
        pop_goal = 1.0 if self.target > 0.5 else 0.0
        self.pop, self._popv = _spring(self.pop, self._popv, pop_goal, dt)
        done_goal = 1.0 if (self.dropped and self.drop_p < 0.85) else 0.0
        self.done, self._donev = _spring(self.done, self._donev, done_goal, dt, 160.0, 14.0)
        if self.t > 0.01 or self.dropped:
            self.phase = (self.phase + dt / 2.2) % 1.0          # one breath ≈ 2.2 s
        if self.dropped:
            self.drop_p = min(1.0, self.drop_p + dt / DROP_LIFE)
            if self.drop_p >= 1.0:
                self.dropped = False
        self.tick.emit()
        resting = (self.t == self.target and not self.dropped
                   and abs(self.pop - pop_goal) < 0.002 and abs(self._popv) < 0.01
                   and abs(self.done) < 0.002 and abs(self._donev) < 0.01)
        if resting:
            self.pop, self.done = pop_goal, 0.0
            self._timer.stop()
            self.tick.emit()
            self.settled.emit()

    @property
    def flash(self) -> float:
        """1 -> 0 over the drop: drives the green flash."""
        return (1.0 - _smooth(self.drop_p)) if self.dropped else 0.0


# -------------------------------------------------------------------- painters
def accent_color() -> QColor:
    return QColor(theme.pal["accent"])


def paint_top_light(p: QPainter, r: QRectF, rad: float, fx: DropFX) -> None:
    """Faint accent wash falling from the top edge."""
    if fx.t < 0.01:
        return
    g = QLinearGradient(r.topLeft(), r.bottomLeft())
    a = accent_color()
    top = QColor(a)
    top.setAlpha(int(46 * fx.t))
    mid = QColor(a)
    mid.setAlpha(int(14 * fx.t))
    end = QColor(a)
    end.setAlpha(0)
    g.setColorAt(0.0, top)
    g.setColorAt(0.45, mid)
    g.setColorAt(1.0, end)
    shape = QPainterPath()
    shape.addRoundedRect(r, rad, rad)
    p.fillPath(shape, g)


def paint_glow_border(p: QPainter, r: QRectF, rad: float, fx: DropFX) -> None:
    """Crisp accent line + soft layered inner glow that breathes slowly."""
    c = theme.pal
    flash = fx.flash
    col = QColor(_mix(c["accent"], c["success"], flash)) if flash else accent_color()
    vis = max(fx.t, flash)
    if vis < 0.02:
        return
    breath = 0.86 + 0.14 * math.sin(fx.phase * 2 * math.pi)
    p.setBrush(Qt.BrushStyle.NoBrush)
    for width, alpha in ((16, 9), (11, 14), (7, 22), (4, 34)):
        g = QColor(col)
        g.setAlpha(int(alpha * vis * breath * (1.0 + 0.8 * flash)))
        p.setPen(QPen(g, width))
        p.drawRoundedRect(r, rad, rad)
    line = QColor(col)
    line.setAlpha(int(235 * vis))
    p.setPen(QPen(line, 1.6))
    p.drawRoundedRect(r, rad, rad)


def paint_badge(p: QPainter, centre: QPointF, fx: DropFX, scale: float = 1.0) -> None:
    """Upload badge: disc + floating cloud arrow + breathing rings (+ success morph)."""
    c = theme.pal
    t, pop = fx.t, max(0.0, fx.pop)
    acc = accent_color()
    R = 33.0 * scale * (1.0 + 0.07 * min(1.2, pop))
    bob = math.sin(fx.phase * 2 * math.pi) * 2.8 * scale * t
    ctr = QPointF(centre.x(), centre.y() + bob * 0.6)
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(Qt.BrushStyle.NoBrush)
    # breathing rings (two, half a cycle apart)
    if t > 0.02:
        for k in (0.0, 0.5):
            u = (fx.phase + k) % 1.0
            ring = QColor(acc)
            ring.setAlpha(int(78 * t * (1 - u) ** 1.7))
            p.setPen(QPen(ring, 1.5))
            rr = R * (1.0 + 0.95 * u)
            p.drawEllipse(ctr, rr, rr)
    # soft drop shadow
    if t > 0.02:
        sh = QColor(acc)
        for i, a in enumerate((10, 16, 22)):
            sh.setAlpha(int(a * t))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(sh)
            q = R + 9 - i * 3
            p.drawEllipse(QPointF(ctr.x(), ctr.y() + 4), q, q)
    # disc
    p.setBrush(QColor(_mix(c["surface"], c["accent"], 0.13 + 0.12 * t)))
    edge = QColor(acc)
    edge.setAlpha(int(70 + 150 * t))
    p.setPen(QPen(edge, 1.4))
    p.drawEllipse(ctr, R, R)
    # glyph
    glyph_op = 1.0 - min(1.0, fx.done * 1.3)
    if glyph_op > 0.01:
        p.setOpacity(glyph_op)
        s = R * 1.0
        icons.paint(p, "cloud-upload", c["accent"],
                    QRectF(ctr.x() - s * 0.5, ctr.y() - s * 0.5 - bob * 0.4, s, s), 1.7)
        p.setOpacity(1.0)
    # success morph
    if fx.done > 0.01:
        d = fx.done
        rad = R * max(0.0, d)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(c["success"]))
        p.drawEllipse(ctr, rad, rad)
        icons.paint(p, "check", "#FFFFFF",
                    QRectF(ctr.x() - rad * 0.5, ctr.y() - rad * 0.5, rad, rad), 2.6, duo=False)
    # one soft ring leaves the badge on drop
    if fx.dropped:
        k = _smooth(fx.drop_p)
        ring = QColor(c["success"])
        ring.setAlpha(int(120 * (1 - k) ** 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(ring, 2.0))
        rr = R * (1.0 + 2.4 * k)
        p.drawEllipse(ctr, rr, rr)
    p.restore()


def frosted(pm: QPixmap) -> QPixmap:
    """Cheap, smooth blur: shrink hard then scale back up with bilinear filtering."""
    if pm.isNull():
        return pm
    dpr = pm.devicePixelRatio()
    img = pm.toImage()
    w, h = img.width(), img.height()
    small = img.scaled(max(1, w // 4), max(1, h // 4), Qt.AspectRatioMode.IgnoreAspectRatio,
                       Qt.TransformationMode.SmoothTransformation)
    small = small.scaled(max(1, w // 16), max(1, h // 16), Qt.AspectRatioMode.IgnoreAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    big = small.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio,
                       Qt.TransformationMode.SmoothTransformation)
    out = QPixmap.fromImage(big)
    out.setDevicePixelRatio(dpr)
    return out


# --------------------------------------------------------------- window overlay
class WindowDropOverlay(QWidget):
    """Full-sheet "drop anywhere" overlay: frosted page + one clean floating card.

    The page is grabbed once when the drag enters, composited onto the solid window
    colour (the app window itself is translucent, so a raw grab has see-through
    pixels at the rounded corners and the right/bottom inset) and blurred once.  The
    overlay then paints that fully opaque still, so the desktop can never show
    through.  On drop the frost fades away and the live page shows through.
    """

    RADIUS = 22.0
    CARD_W, CARD_H = 400, 236

    def __init__(self, parent: QWidget, inset: tuple[int, int, int, int] = (0, 0, 10, 10)) -> None:
        super().__init__(parent)
        self._inset = inset
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self._count = 0
        self._size = ""
        self._folder = False
        self._snap = QPixmap()
        self._frost = QPixmap()
        self.fx = DropFX(self)
        self.fx.tick.connect(self.update)
        self.fx.settled.connect(self._maybe_hide)
        parent.installEventFilter(self)
        self.setGeometry(parent.rect())
        self.hide()

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802
        if obj is self.parentWidget() and ev.type() == QEvent.Type.Resize:
            self.setGeometry(self.parentWidget().rect())
        return super().eventFilter(obj, ev)

    def begin(self, urls, pos=None) -> None:
        self._count, self._size = drag_info(urls)
        self._folder = has_folder(urls)
        if not self.isVisible():
            self.setGeometry(self.parentWidget().rect())
            self._snap = self._solid_grab()
            self._frost = frosted(self._snap)
            self.show()
            self.raise_()
        self.fx.begin()

    def _solid_grab(self) -> QPixmap:
        """Snapshot of the parent on top of the solid window colour — no alpha anywhere."""
        grab = self.parentWidget().grab()
        base = QPixmap(grab.size())
        base.setDevicePixelRatio(grab.devicePixelRatio())
        base.fill(QColor(theme.pal["bg"]))
        q = QPainter(base)
        q.drawPixmap(0, 0, grab)
        q.end()
        return base

    def move(self, pos) -> None:  # noqa: A003
        pass

    def end(self) -> None:
        self.fx.end()

    def drop(self, pos) -> None:
        self._snap = QPixmap()                  # live page shows; only the frost fades out
        self.fx.drop()

    def _maybe_hide(self) -> None:
        if not self.fx.active:
            self.hide()
            self._snap = QPixmap()
            self._frost = QPixmap()

    def paintEvent(self, _e) -> None:  # noqa: N802
        fx = self.fx
        c = theme.pal
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
                         | QPainter.RenderHint.SmoothPixmapTransform)
        l, t_, r_, b = self._inset
        full = QRectF(self.rect()).adjusted(l, t_, -r_, -b)
        t = fx.t
        veil_k = t * (1.0 - _smooth(fx.drop_p * 3.0)) if fx.dropped else t

        # --- backdrop: sharp still, then the frosted still fading in over it
        if not self._snap.isNull():
            p.drawPixmap(0, 0, self._snap)
        if not self._frost.isNull() and veil_k > 0.003:
            p.setOpacity(min(1.0, veil_k * 1.15))
            p.drawPixmap(0, 0, self._frost)
            p.setOpacity(1.0)
        shape = QPainterPath()
        shape.addRoundedRect(full, self.RADIUS, self.RADIUS)
        veil = QColor(c["sheet"])
        veil.setAlpha(int((120 if theme.dark else 150) * veil_k))
        p.fillPath(shape, veil)

        # --- glow frame around the sheet
        frame = full.adjusted(10, 10, -10, -10)
        paint_glow_border(p, frame, self.RADIUS - 6, fx)

        # --- floating card
        if veil_k > 0.01 or fx.dropped:
            appear = _smooth(fx.t / 0.9)
            leave = 1.0 - _smooth((fx.drop_p - 0.55) / 0.45) if fx.dropped else 1.0
            op = appear * leave
            sc = (0.93 + 0.07 * min(1.1, fx.pop)) * (1.0 - 0.04 * (1 - leave))
            cx, cy = full.center().x(), full.center().y()
            w, h = self.CARD_W * sc, self.CARD_H * sc
            card = QRectF(cx - w / 2, cy - h / 2 + (1 - appear) * 14, w, h)
            p.setOpacity(max(0.0, op))
            # layered soft shadow
            for i in range(9):
                sh = QColor(0, 0, 0, int((7 if theme.dark else 4) * (1 - i / 9)))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(sh)
                g = i * 3
                p.drawRoundedRect(card.adjusted(-g, -g + 8, g, g + 8), 28 + g, 28 + g)
            cp = QPainterPath()
            cp.addRoundedRect(card, 28, 28)
            p.fillPath(cp, QColor(c["surface"]))
            top = QLinearGradient(card.topLeft(), card.bottomLeft())
            wash = QColor(c["accent"])
            wash.setAlpha(26)
            none = QColor(c["accent"])
            none.setAlpha(0)
            top.setColorAt(0.0, wash)
            top.setColorAt(0.55, none)
            p.fillPath(cp, top)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(_mix(c["border_strong"], c["accent"], 0.35)), 1.0))
            p.drawPath(cp)
            paint_badge(p, QPointF(card.center().x(), card.top() + 88 * sc), fx, 1.15 * sc)
            tf = QFont(self.font())
            tf.setPixelSize(int(21 * sc))
            tf.setWeight(QFont.Weight.DemiBold)
            p.setFont(tf)
            p.setPen(QColor(c["text"]))
            title = drag_title(self._count, self._folder)
            p.drawText(QRectF(card.left(), card.top() + 150 * sc, card.width(), 30 * sc),
                       Qt.AlignmentFlag.AlignCenter, title)
            hf = QFont(self.font())
            hf.setPixelSize(int(13 * sc))
            p.setFont(hf)
            p.setPen(QColor(c["muted"]))
            sub = (f"{self._size}  ·  " if self._size else "") + tr("up.drop_hint")
            p.drawText(QRectF(card.left() + 16, card.top() + 182 * sc, card.width() - 32, 22 * sc),
                       Qt.AlignmentFlag.AlignCenter, sub)
            p.setOpacity(1.0)
        p.end()
