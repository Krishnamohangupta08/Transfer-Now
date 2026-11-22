"""Dashboard — Porcelain & Signal bento grid, computed from real history + live jobs.

12-column bento (14 px gaps, tile radius 22):
    hero live-transfer tile (8) + quick-drop tile (4)
    four stat tiles (3 each)
    weekly activity (7) + link health (5)
"""
from __future__ import annotations

from datetime import datetime, timedelta

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, QVariantAnimation, QEasingCurve
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen, QRadialGradient
from PyQt6.QtWidgets import (
    QFrame, QGraphicsDropShadowEffect, QGridLayout, QHBoxLayout, QSizePolicy, QWidget,
)

from core.store import expires_at, human_size, is_active, parse_dt
from ..hub import HUB
from ..i18n import tr
from ..theme import rgba, theme
from ..widgets import (
    Badge, BarChart, Button, DonutRing, ElidedLabel, Pill, Sparkline, eta_text, hbox, label,
    repolish, speed_text, vbox,
)
from .base import Page

DAY_KEYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _greeting() -> str:
    h = datetime.now().hour
    return "Good morning" if h < 12 else "Good afternoon" if h < 18 else "Good evening"


def _date_text() -> str:
    d = datetime.now()
    return d.strftime("%A, ") + str(d.day) + d.strftime(" %B")


def _soft_shadow(w: QWidget) -> None:
    """One tiny, soft panel shadow (0 12 32 -12 at ~10 % ink)."""
    fx = QGraphicsDropShadowEffect(w)
    fx.setBlurRadius(28)
    fx.setOffset(0, 8)
    col = QColor(theme.pal["shadow"])
    col.setAlpha(26 if not theme.dark else 90)
    fx.setColor(col)
    w.setGraphicsEffect(fx)
    theme.changed.connect(lambda: _soft_shadow_color(fx))


def _soft_shadow_color(fx: QGraphicsDropShadowEffect) -> None:
    try:
        col = QColor(theme.pal["shadow"])
        col.setAlpha(26 if not theme.dark else 90)
        fx.setColor(col)
    except RuntimeError:        # effect already deleted with its widget
        pass


# --------------------------------------------------------------- flight line
class FlightLine(QWidget):
    """The signature element: hollow node -> dashed track -> hollow node.

    The completed part is a solid line fading in from the left; the head is a dot
    with a two-ring halo that gently pulses (the only looping motion; it runs only
    while a transfer is live *and* the widget is visible).
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._progress = 0.0
        self._live = False
        self._done = False
        self._pulse = 0.0
        self.setFixedHeight(34)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(1800)
        self._anim.setLoopCount(-1)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._anim.valueChanged.connect(self._tick)
        theme.changed.connect(self.update)

    def set_state(self, progress: float, live: bool, done: bool = False) -> None:
        self._progress = max(0.0, min(1.0, progress))
        self._live, self._done = live, done
        self._sync_anim()
        self.update()

    def _sync_anim(self) -> None:
        run = self._live and not self._done and self.isVisible()
        if run and self._anim.state() != QVariantAnimation.State.Running:
            self._anim.start()
        elif not run and self._anim.state() == QVariantAnimation.State.Running:
            self._anim.stop()

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        self._sync_anim()

    def hideEvent(self, e) -> None:  # noqa: N802
        super().hideEvent(e)
        self._sync_anim()

    def _tick(self, v) -> None:
        self._pulse = float(v)
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = theme.pal
        w, h = self.width(), self.height()
        cy = h / 2
        r_node = 7.0
        x0, x1 = r_node + 1, w - r_node - 1
        fg2 = QColor(c["hero_fg2"])
        flight = QColor(c["success"] if self._done else c["flight"])
        # dashed track
        dash = QColor(fg2)
        dash.setAlpha(150)
        pen = QPen(dash, 1.5, Qt.PenStyle.CustomDashLine)
        pen.setDashPattern([3.0, 3.0])
        p.setPen(pen)
        p.drawLine(QPointF(x0 + r_node, cy), QPointF(x1 - r_node, cy))
        head_x = x0 + (x1 - x0) * (1.0 if self._done else self._progress)
        # completed part: solid, fading in from the left
        if head_x > x0 + r_node:
            from PyQt6.QtGui import QLinearGradient
            g = QLinearGradient(x0, 0, max(head_x, x0 + 1), 0)
            a = QColor(flight)
            a.setAlpha(40)
            g.setColorAt(0.0, a)
            g.setColorAt(0.35, flight)
            g.setColorAt(1.0, flight)
            p.setPen(QPen(g, 3.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(x0 + r_node, cy), QPointF(head_x, cy))
        # end nodes (hollow; filled with a check when done)
        node_fill = QColor(c["hero_bg"])
        for x, filled in ((x0, False), (x1, self._done)):
            p.setPen(QPen(QColor(c["hero_fg"]) if not filled else flight, 1.8))
            p.setBrush(flight if filled else node_fill)
            p.drawEllipse(QPointF(x, cy), r_node - 0.9, r_node - 0.9)
            if filled:
                p.setPen(QPen(QColor(c["hero_bg"]), 1.8, Qt.PenStyle.SolidLine,
                              Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
                p.drawPolyline([QPointF(x - 3, cy), QPointF(x - 0.8, cy + 2.4), QPointF(x + 3, cy - 2.4)])
        # head: dot + two-ring halo (pulses gently)
        if self._live and not self._done and self._progress > 0:
            k = self._pulse
            for i, (rad, alpha) in enumerate(((17.0 + 2.0 * k, 46), (12.0 + 1.2 * k, 90))):
                ha = QColor(flight)
                ha.setAlpha(int(alpha * (1.0 - 0.25 * k) if i == 0 else alpha))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(ha)
                p.drawEllipse(QPointF(head_x, cy), rad, rad)
            p.setBrush(flight)
            p.drawEllipse(QPointF(head_x, cy), 8.0, 8.0)
            p.setBrush(QColor(c["hero_bg"]))
            p.drawEllipse(QPointF(head_x, cy), 2.6, 2.6)


# ------------------------------------------------------------------ hero tile
class HeroChip(QFrame):
    """Status chip: a true pill (radius = height / 2), painted directly so the
    corners are always fully round whatever the stylesheet does."""

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(r, r.height() / 2, r.height() / 2)
        p.fillPath(path, QColor(theme.pal["hero_chip"]))


class HeroTile(QFrame):
    """Inverted live-transfer tile: ink-black in light themes, light in dark themes."""

    def __init__(self, cancel_cb) -> None:
        super().__init__()
        self.setObjectName("hero")
        self._cancel_cb = cancel_cb
        self._job_id = 0
        self.setMinimumHeight(232)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        lay = vbox(self, (30, 26, 30, 22), 0)

        top = hbox(spacing=16)
        left = vbox(spacing=0)
        self.chip = HeroChip()
        self.chip.setObjectName("herochip")
        cl = hbox(self.chip, (11, 4, 13, 4), 7)
        self.chip_dot = QFrame()
        self.chip_dot.setFixedSize(8, 8)
        self.chip_dot.setObjectName("herodot")
        cl.addWidget(self.chip_dot, 0, Qt.AlignmentFlag.AlignVCenter)
        self.chip_text = label(tr("dash.sending"))
        self.chip_text.setObjectName("herochiptext")
        cl.addWidget(self.chip_text)
        self.chip.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        left.addWidget(self.chip, 0, Qt.AlignmentFlag.AlignLeft)
        left.addSpacing(14)
        self.name = ElidedLabel("")
        self.name.setObjectName("heroname")
        left.addWidget(self.name)
        self.sub = label("")
        self.sub.setObjectName("herosub")
        left.addWidget(self.sub)
        top.addLayout(left, 1)

        right = vbox(spacing=2)
        spd = hbox(spacing=6)
        spd.addStretch(1)
        self.speed_num = label("0")
        self.speed_num.setObjectName("herospeed")
        self.speed_unit = label("MB/s")
        self.speed_unit.setObjectName("herounit")
        spd.addWidget(self.speed_num, 0, Qt.AlignmentFlag.AlignBottom)
        spd.addWidget(self.speed_unit, 0, Qt.AlignmentFlag.AlignBottom)
        right.addLayout(spd)
        self.eta = label("")
        self.eta.setObjectName("herosub")
        self.eta.setAlignment(Qt.AlignmentFlag.AlignRight)
        right.addWidget(self.eta)
        top.addLayout(right)
        lay.addLayout(top)
        lay.addStretch(1)

        self.flight = FlightLine()
        lay.addWidget(self.flight)
        lay.addSpacing(6)

        bottom = hbox(spacing=14)
        self.node_a = label(tr("dash.this_pc"))
        self.node_a.setObjectName("herosmall")
        bottom.addWidget(self.node_a)
        bottom.addSpacing(26)
        self.sent = label("")
        self.sent.setObjectName("herosmall")
        bottom.addWidget(self.sent)
        bottom.addStretch(1)
        self.node_b = label("transfernow.net/dl/…")
        self.node_b.setObjectName("herosmall")
        bottom.addWidget(self.node_b)
        self.cancel_btn = Button(tr("dash.cancel"), "", "", "sm")
        self.cancel_btn.setObjectName("herobtn")
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.clicked.connect(self._on_cancel)
        bottom.addWidget(self.cancel_btn)
        lay.addLayout(bottom)
        theme.changed.connect(self._restyle)
        self._restyle()
        self.set_idle()

    def _restyle(self) -> None:
        c = theme.pal
        self.setStyleSheet(f"""
            QLabel#heroname {{ color: {c['hero_fg']}; font-family: "Inter Tight", "Inter"; font-size: 24px;
                font-weight: 600; letter-spacing: -0.6px; }}
            QLabel#herosub {{ color: {c['hero_fg2']}; font-size: 13px; }}
            QLabel#herosmall {{ color: {c['hero_fg']}; font-size: 13px; font-weight: 500; }}
            QLabel#herospeed {{ color: {c['hero_fg']}; font-family: "Inter Tight", "Inter"; font-size: 64px;
                font-weight: 500; letter-spacing: -2px; }}
            QLabel#herounit {{ color: {c['hero_fg2']}; font-size: 16px; font-weight: 500; padding-bottom: 8px; }}
            QFrame#herochip {{ background: transparent; border: none; }}
            QLabel#herochiptext {{ background: transparent; }}
            QLabel#herochiptext {{ color: {c['hero_fg']}; font-size: 12px; font-weight: 600; }}
            QFrame#herodot {{ background: {c['flight']}; border-radius: 4px; }}
            QPushButton#herobtn {{ background: {c['hero_chip']}; color: {c['hero_fg']}; border: none;
                border-radius: 16px; min-height: 32px; max-height: 32px; padding: 0 18px; font-weight: 500; }}
            QPushButton#herobtn:hover {{ background: {_alpha(c['hero_fg'], 0.18)}; }}
        """)

    # ------------------------------------------------------------ painting
    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = theme.pal
        path = QPainterPath()
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path.addRoundedRect(r, 22, 22)
        p.fillPath(path, QColor(c["hero_bg"]))
        # the only glow allowed: a soft radial in the top-right corner
        p.setClipPath(path)
        g = QRadialGradient(QPointF(r.right() - 40, r.top() + 30), r.width() * 0.34)
        a = QColor(c["flight"])
        a.setAlpha(70 if not theme.dark else 60)
        g.setColorAt(0.0, a)
        g.setColorAt(1.0, QColor(a.red(), a.green(), a.blue(), 0))
        p.fillRect(self.rect(), g)

    # ------------------------------------------------------------- content
    def _on_cancel(self) -> None:
        if self._job_id:
            self._cancel_cb(self._job_id)

    def set_idle(self) -> None:
        self._job_id = 0
        self.chip_dot.setStyleSheet(f"background: {theme.pal['hero_fg2']}; border-radius: 4px;")
        self.chip_text.setText(tr("dash.idle"))
        self.name.set_full_text(tr("dash.idle_title"))
        self.sub.setText(tr("dash.idle_sub"))
        self.speed_num.setText("0")
        self.speed_unit.setText("MB/s")
        self.eta.setText("")
        self.sent.setText("")
        self.cancel_btn.hide()
        self.flight.set_state(0.0, False)

    def set_job(self, job) -> None:
        self._job_id = job.id
        self.chip_dot.setStyleSheet("")
        self.chip_text.setText(tr("dash.sending"))
        self.name.set_full_text(job.title.split("  +")[0])
        n = len(job.files)
        self.sub.setText(f"{n} file{'s' if n != 1 else ''}, {human_size(job.total)}")
        spd = speed_text(job.speed) if job.speed > 0 else "0 MB/s"
        num, _sp, unit = spd.partition(" ")
        self.speed_num.setText(num)
        self.speed_unit.setText(unit or "MB/s")
        eta = job.eta
        if eta > 0:
            self.eta.setText(f"{int(eta)} seconds left" if eta < 60 else tr("dash.seconds_left", t=eta_text(eta)))
        else:
            self.eta.setText("")
        sent = float(job.sent) if job.sent else job.total * job.pct / 100.0
        self.sent.setText(f"{human_size(sent)} {tr('dash.of')} {human_size(job.total)}, {job.pct}%")
        self.cancel_btn.show()
        self.flight.set_state(job.pct / 100.0, job.state == "running")


def _alpha(hex_color: str, a: float) -> str:
    return rgba(hex_color, a)


# ------------------------------------------------------------------ quick drop
class DropTile(QFrame):
    """Dashed quick-drop tile; click opens the file picker on Upload."""

    def __init__(self, on_click) -> None:
        super().__init__()
        self.setObjectName("droptile")
        self._on_click = on_click
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(232)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._hover = False
        lay = vbox(self, (20, 20, 20, 20), 0)
        lay.addStretch(1)
        self.plus = Badge("plus", 52, "accent")
        lay.addWidget(self.plus, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addSpacing(14)
        self.title = label(tr("dash.drop_files"))
        self.title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.title.setStyleSheet("font-size: 16px; font-weight: 600; letter-spacing: -0.3px;")
        lay.addWidget(self.title)
        self.sub = label(tr("dash.drop_sub"), "caption")
        self.sub.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(self.sub)
        lay.addStretch(1)
        theme.changed.connect(self.update)

    def retranslate(self) -> None:
        self.title.setText(tr("dash.drop_files"))
        self.sub.setText(tr("dash.drop_sub"))

    def enterEvent(self, e) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self._on_click()
        super().mouseReleaseEvent(e)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = theme.pal
        r = QRectF(self.rect()).adjusted(0.75, 0.75, -0.75, -0.75)
        if self._hover:
            fill = QColor(c["accent"])
            fill.setAlpha(14)
            p.setBrush(fill)
        else:
            p.setBrush(Qt.BrushStyle.NoBrush)
        pen = QPen(QColor(c["accent"] if self._hover else c["border_strong"]), 1.2, Qt.PenStyle.CustomDashLine)
        pen.setDashPattern([4.0, 4.0])
        p.setPen(pen)
        p.drawRoundedRect(r, 22, 22)


# ------------------------------------------------------------------- stat tile
class StatCard(QFrame):
    def __init__(self, icon: str, caption_key: str) -> None:
        super().__init__()
        self.setObjectName("tile")
        self._caption_key = caption_key
        self.setMinimumHeight(146)
        lay = vbox(self, (22, 18, 22, 18), 0)
        top = hbox(spacing=10)
        top.addWidget(Badge(icon, 34))
        top.addStretch(1)
        self.delta = Pill("", "success")
        top.addWidget(self.delta)
        lay.addLayout(top)
        lay.addStretch(1)
        self.caption = label("", "muted")
        self.caption.setStyleSheet("font-size: 14px;")
        lay.addWidget(self.caption)
        bottom = hbox(spacing=8)
        self.value = label("0", "big")
        bottom.addWidget(self.value, 0, Qt.AlignmentFlag.AlignBottom)
        bottom.addStretch(1)
        self.spark = Sparkline()
        bottom.addWidget(self.spark, 0, Qt.AlignmentFlag.AlignBottom)
        lay.addLayout(bottom)
        self.retranslate()

    def retranslate(self) -> None:
        self.caption.setText(tr(self._caption_key))

    def set(self, value: str, delta: str, series: list[float], tone: str = "success") -> None:
        self.value.setText(value)
        self.delta.setText(delta)
        self.delta.set_tone(tone)
        self.delta.setVisible(bool(delta))
        self.spark.set_values(series)


class LegendRow(QWidget):
    def __init__(self, token: str) -> None:
        super().__init__()
        self._token = token
        lay = hbox(self, (0, 0, 0, 0), 10)
        self.dot = QFrame()
        self.dot.setFixedSize(8, 8)
        lay.addWidget(self.dot, 0, Qt.AlignmentFlag.AlignVCenter)
        self.name = label("", "muted")
        lay.addWidget(self.name, 1)
        self.value = label("0")
        self.value.setStyleSheet("font-weight: 600;")
        lay.addWidget(self.value)
        theme.changed.connect(self._paint)
        self._paint()

    def _paint(self) -> None:
        self.dot.setStyleSheet(f"background: {theme.pal[self._token]}; border-radius: 4px;")

    def set(self, name: str, value: int) -> None:
        self.name.setText(name)
        self.value.setText(str(value))


# ---------------------------------------------------------------------- page
class DashboardPage(Page):
    key = "dashboard"
    overline_key = "nav.workspace"
    title_key = ""
    subtitle_key = ""

    def __init__(self, ctx) -> None:
        self._title = _greeting()
        super().__init__(ctx)
        self.subtitle_label.setText(_date_text())
        self.subtitle_label.show()

        # header actions ------------------------------------------------------
        self.header_right.setSpacing(10)
        self.dn_btn = Button(tr("dash.download_link"), "", "download-arrow", "lg")
        self.dn_btn.clicked.connect(lambda: ctx.navigate("download"))
        self.up_btn = Button(tr("dash.new_upload"), "primary", "plus", "lg")
        self.up_btn.clicked.connect(lambda: ctx.navigate("upload"))
        self.header_right.addWidget(self.dn_btn)
        self.header_right.addWidget(self.up_btn)

        # row 1: hero (8) + quick drop (4) -----------------------------------------
        row1 = hbox(spacing=14)
        self.hero = HeroTile(self._cancel_job)
        self.drop = DropTile(self._browse)
        row1.addWidget(self.hero, 8)
        row1.addWidget(self.drop, 4)
        self.body.addLayout(row1)

        # row 2: four stat tiles (3 each) -----------------------------------------
        self.cards = [StatCard("arrow-up", "stat.transfers"), StatCard("file", "stat.files"),
                      StatCard("database", "stat.data"), StatCard("link", "stat.active")]
        self.row2 = QGridLayout()
        self.row2.setContentsMargins(0, 0, 0, 0)
        self.row2.setSpacing(14)
        self._wide = None
        self._layout_cards(True)
        self.body.addLayout(self.row2)

        # row 3: weekly activity (7) + link health (5) ------------------------------
        row3 = hbox(spacing=14)
        self.week_card = QFrame()
        self.week_card.setObjectName("tile")
        self.week_card.setMinimumHeight(250)
        wl = vbox(self.week_card, (24, 20, 24, 14), 0)
        wh = hbox()
        self.week_title = label(tr("dash.week"), "h2")
        wh.addWidget(self.week_title)
        wh.addStretch(1)
        self.week_sub = label("", "caption")
        wh.addWidget(self.week_sub)
        wl.addLayout(wh)
        self.bar = BarChart()
        wl.addWidget(self.bar, 1)
        row3.addWidget(self.week_card, 7)

        self.health_card = QFrame()
        self.health_card.setObjectName("tile")
        hl = vbox(self.health_card, (24, 20, 24, 20), 0)
        self.health_title = label(tr("dash.health"), "h2")
        hl.addWidget(self.health_title)
        self.health_sub = label("", "caption")
        hl.addWidget(self.health_sub)
        hl.addSpacing(6)
        hrow = hbox(spacing=20)
        hrow.addStretch(1)
        self.ring = DonutRing()
        self.ring.setFixedSize(126, 126)
        hrow.addWidget(self.ring, 0, Qt.AlignmentFlag.AlignVCenter)
        leg = vbox(spacing=12)
        leg.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self.leg_active = LegendRow("success")
        self.leg_soon = LegendRow("warning")
        self.leg_expired = LegendRow("danger")
        for w in (self.leg_active, self.leg_soon, self.leg_expired):
            w.setMinimumWidth(150)
            leg.addWidget(w)
        hrow.addLayout(leg)
        hrow.addStretch(1)
        hl.addLayout(hrow, 1)
        row3.addWidget(self.health_card, 5)
        self.body.addLayout(row3)
        self.body.addStretch(1)

        for tile in (self.drop, *self.cards, self.week_card, self.health_card):
            _soft_shadow(tile)

        ctx.history_changed.connect(self.refresh)
        self._clock = QTimer(self)
        self._clock.setInterval(60_000)
        self._clock.timeout.connect(lambda: self.isVisible() and self.refresh())
        self._clock.start()
        HUB.jobs_changed.connect(self._sync_hero)
        HUB.jobs_updated.connect(self._sync_hero)
        self.refresh()
        self._sync_hero()

    def _layout_cards(self, wide: bool) -> None:
        if wide == self._wide:
            return
        self._wide = wide
        for c in self.cards:
            self.row2.removeWidget(c)
        for i, c in enumerate(self.cards):
            self.row2.addWidget(c, 0 if wide else i // 2, i if wide else i % 2)
        for col in range(4):
            self.row2.setColumnStretch(col, 1 if (wide or col < 2) else 0)

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._layout_cards(self.viewport().width() >= 840)

    # ----------------------------------------------------------------- hero
    def _live_job(self):
        jobs = HUB.jobs
        return next((j for j in jobs if getattr(j, "state", "") == "running"), None)

    def _sync_hero(self) -> None:
        if not self.isVisible():
            return
        job = self._live_job()
        if job is None:
            if self.hero._job_id:                      # noqa: SLF001
                self.hero.set_idle()
        else:
            self.hero.set_job(job)

    def _cancel_job(self, job_id: int) -> None:
        page = HUB.page
        if page is not None:
            page.cancel_job(job_id)

    def _browse(self) -> None:
        self.ctx.navigate("upload")
        win = self.window()
        if hasattr(win, "page"):
            QTimer.singleShot(0, lambda: win.page("upload").browse())

    def on_show(self) -> None:
        self.title_label.setText(_greeting())
        self.subtitle_label.setText(_date_text())
        self.refresh()
        self._sync_hero()

    def _retranslate(self) -> None:
        self._title = _greeting()
        self.title_label.setText(self._title)
        self.subtitle_label.setText(_date_text())
        self.subtitle_label.show()
        self.up_btn.setText(tr("dash.new_upload"))
        self.dn_btn.setText(tr("dash.download_link"))
        self.week_title.setText(tr("dash.week"))
        self.health_title.setText(tr("dash.health"))
        self.drop.retranslate()
        for c in self.cards:
            c.retranslate()
        self._sync_hero_text()
        self.refresh()

    def _sync_hero_text(self) -> None:
        self.hero.node_a.setText(tr("dash.this_pc"))
        self.hero.cancel_btn.setText(tr("dash.cancel"))
        if self.hero._job_id:                        # noqa: SLF001
            self.hero.chip_text.setText(tr("dash.sending"))
        else:
            self.hero.set_idle()

    # ----------------------------------------------------------------- data
    def refresh(self) -> None:
        entries = self.ctx.history.entries()
        uploads = [e for e in entries if e.get("kind") == "upload"]
        now = datetime.now().astimezone()
        today = now.date()
        days = [today - timedelta(days=6 - i) for i in range(7)]

        def per_day(vals):
            out = [0.0] * 7
            for e, v in vals:
                dt = parse_dt(e.get("date", ""))
                if dt is None:
                    continue
                d = dt.astimezone().date()
                if d in days:
                    out[days.index(d)] += v
            return out

        week_cut = now - timedelta(days=7)

        def this_week(e):
            dt = parse_dt(e.get("date", ""))
            return dt is not None and dt >= week_cut

        n_week = sum(1 for e in uploads if this_week(e))
        f_week = sum(len(e.get("files", [])) for e in uploads if this_week(e))
        b_week = sum(e.get("total_bytes", 0) for e in uploads if this_week(e))
        active = [e for e in uploads if is_active(e)]
        soon = [e for e in active if (expires_at(e) - now).total_seconds() < 86400]

        self.cards[0].set(str(len(uploads)), tr("stat.week", n=n_week) if n_week else "",
                          per_day([(e, 1) for e in uploads]))
        self.cards[1].set(f"{sum(len(e.get('files', [])) for e in uploads):,}",
                          tr("stat.week", n=f_week) if f_week else "",
                          per_day([(e, len(e.get("files", []))) for e in uploads]))
        self.cards[2].set(human_size(sum(e.get("total_bytes", 0) for e in entries)),
                          tr("stat.week_bytes", b=human_size(b_week)) if b_week else "",
                          per_day([(e, e.get("total_bytes", 0)) for e in entries]))
        self.cards[3].set(str(len(active)), tr("dash.expiring_n", n=len(soon)) if soon else "",
                          per_day([(e, 1) for e in active]), "warning")

        # weekly capsule chart (sizes; falls back to counts)
        sizes = per_day([(e, e.get("total_bytes", 0)) for e in entries])
        counts = per_day([(e, 1) for e in entries])
        vals = sizes if any(sizes) else counts
        labels = [DAY_KEYS[d.weekday()] for d in days[:-1]] + [tr("dash.today")]
        self.bar.set_values(vals, labels, human_size(sizes[-1]) if sizes[-1] > 0 else "")
        total_n = int(sum(counts))
        self.week_sub.setText(tr("dash.week_sub", n=total_n, size=human_size(sum(sizes))))

        # link health ring
        n_up, n_act, n_soon = len(uploads), len(active), len(soon)
        n_exp = n_up - n_act
        frac = (n_act / n_up) if n_up else 0.0
        self.ring.set_segments([(n_act - n_soon, "success"), (n_soon, "warning"), (n_exp, "danger")],
                               f"{int(round(frac * 100))}%")
        self.health_sub.setText(tr("dash.active_of", a=n_act, n=n_up))
        self.leg_active.set(tr("dash.leg_active"), n_act - n_soon)
        self.leg_soon.set(tr("dash.leg_soon"), n_soon)
        self.leg_expired.set(tr("dash.leg_expired"), n_exp)
