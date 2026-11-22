"""Transfer Queue — a dedicated page for every upload job in one place.

The Upload page still owns the jobs (and the worker threads); this page
observes them through ``ui.hub.HUB`` and adds queue-level controls:
filtering, parallel-slot setting, reordering, retry-all-failed and
clear-finished — with live progress, speed and ETA per row.
"""
from __future__ import annotations

import time
from datetime import datetime

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QFrame, QProgressBar, QWidget

from core.store import human_size
from ..hub import HUB
from ..i18n import tr
from .. import motion
from ..menus import RoundMenu
from ..widgets import (
    Badge, Button, ElidedLabel, Pill, Segmented, clear_layout, eta_text, hbox,
    label, repolish, speed_text, vbox,
)
from .base import Page

_ACTIVE = ("queued", "scheduled", "running")


def _job_icon(state: str) -> str:
    return {"scheduled": "timer", "done": "check-circle",
            "failed": "alert-circle"}.get(state, "cloud-upload")


LINK_DAYS = 7                      # a TransferNow link works for 7 days


def job_time(job) -> float:
    """When the transfer finished (or, while still pending, when it was created)."""
    return float(getattr(job, "finished_at", 0.0) or getattr(job, "created_at", 0.0) or 0.0)


def link_state(job) -> tuple[str, float]:
    """('active' | 'soon' | 'expired', seconds left) for a finished transfer."""
    left = job_time(job) + LINK_DAYS * 86400 - time.time()
    if left <= 0:
        return "expired", 0.0
    return ("soon" if left < 86400 else "active"), left


def _left_text(secs: float) -> str:
    secs = int(secs)
    if secs >= 86400:
        return f"{secs // 86400}d"
    if secs >= 3600:
        return f"{secs // 3600}h"
    return f"{max(1, secs // 60)}m"


def _filters_match(state: str, mode: str) -> bool:
    if mode == "all":
        return True
    if mode == "active":
        return state in _ACTIVE
    if mode == "queued":
        return state in ("queued", "scheduled")
    return state == mode                      # done | failed


class QueueRow(QFrame):
    """One transfer in the queue: badge · title · state · controls · progress."""

    HEIGHT_TITLE = 20

    def __init__(self, job, page: "QueuePage") -> None:
        super().__init__()
        self.job = job
        self.page = page
        self._filter_mode = "all"
        self.setObjectName("row")
        lay = vbox(self, (12, 9, 12, 9), 6)

        # ---- top line: badge · title+meta · position · state · buttons
        top = hbox(spacing=8)
        self.badge = Badge(_job_icon(job.state), 36)
        top.addWidget(self.badge)
        col = vbox(spacing=1)
        self.title = ElidedLabel(job.title)
        self.meta = label("", "caption")
        col.addWidget(self.title)
        col.addWidget(self.meta)
        top.addLayout(col, 1)

        self.position = Pill("")
        top.addWidget(self.position)
        self.pill = Pill("")
        top.addWidget(self.pill)
        self.link_pill = Pill("")                 # Active · 6d left / Expired
        top.addWidget(self.link_pill)

        self.btn_up = Button("", "ghost", "arrow-up", "icon")
        self.btn_up.setToolTip(tr("qu.move_up"))
        self.btn_up.clicked.connect(lambda: self.page.move(self.job.id, -1))
        self.btn_down = Button("", "ghost", "arrow-down", "icon")
        self.btn_down.setToolTip(tr("qu.move_down"))
        self.btn_down.clicked.connect(lambda: self.page.move(self.job.id, +1))
        self.btn_cancel = Button("", "ghost", "x", "icon")
        self.btn_cancel.setToolTip(tr("btn.cancel"))
        self.btn_cancel.clicked.connect(lambda: self.page.cancel(self.job.id))
        self.btn_qr = Button("", "ghost", "qr", "icon")
        self.btn_qr.setToolTip(tr("qu.qr"))
        self.btn_qr.clicked.connect(lambda: self.page.qr(self.job.id))
        self.btn_copy = Button("", "ghost", "copy", "icon")
        self.btn_copy.setToolTip(tr("btn.copy"))
        self.btn_copy.clicked.connect(lambda: self.page.copy_link(self.job.id))
        self.btn_retry = Button("", "ghost", "refresh", "icon")
        self.btn_retry.setToolTip(tr("btn.retry"))
        self.btn_retry.clicked.connect(lambda: self.page.retry(self.job.id))
        self.btn_remove = Button("", "ghost", "trash", "icon")
        self.btn_remove.setToolTip(tr("btn.remove"))
        self.btn_remove.clicked.connect(lambda: self.page.remove(self.job.id))
        actions = hbox(spacing=2)               # tight macOS-style icon cluster
        for b in (self.btn_up, self.btn_down, self.btn_cancel, self.btn_qr, self.btn_copy,
                  self.btn_retry, self.btn_remove):
            actions.addWidget(b)
        top.addSpacing(4)
        top.addLayout(actions)
        lay.addLayout(top)

        # ---- second line: per-state content
        self.state_area = QWidget()
        sa = hbox(self.state_area, (44, 0, 0, 0), 10)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        sa.addWidget(self.bar, 1)
        self.speed_lb = label("", "caption")
        self.speed_lb.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        sa.addWidget(self.speed_lb)
        self.eta_lb = label("", "caption")
        self.eta_lb.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.eta_lb.setMinimumWidth(86)
        sa.addWidget(self.eta_lb)
        self.info = label("", "caption", wrap=True)
        sa.addWidget(self.info, 1)
        self.link_view = ElidedLabel("", "link")
        sa.addWidget(self.link_view, 1)
        self.state_area.setVisible(False)
        lay.addWidget(self.state_area)

        self.refresh()

    # ------------------------------------------------------------- refresh
    def set_position(self, pos: int) -> None:
        self.position.setText(f"#{pos}" if pos > 0 else "")
        self.position.setVisible(pos > 0)
        if pos > 0:
            self.position.setToolTip(tr("qu.position", n=pos))

    def refresh(self) -> None:
        j = self.job
        n = len(j.files)
        running, queued = j.state == "running", j.state == "queued"
        scheduled, done, failed = (j.state == "scheduled", j.state == "done",
                                   j.state == "failed")
        meta = f"{n} file{'s' if n != 1 else ''} · {human_size(j.total)}"
        if j.state != "failed" and self.info.property("role") == "danger":
            self.info.setProperty("role", "caption")     # retrying: back to normal text
            repolish(self.info)
        self.badge.set_icon(_job_icon(j.state))
        self.title.set_full_text(j.title)
        self.title.setToolTip(j.title)

        self.btn_up.setVisible(queued or scheduled)
        self.btn_down.setVisible(queued or scheduled)
        self.btn_cancel.setVisible(j.state in _ACTIVE)
        self.btn_copy.setVisible(done)
        self.btn_qr.setVisible(done)
        self.link_pill.setVisible(done)
        self.btn_retry.setVisible(failed)
        self.btn_remove.setVisible(done or failed)

        self.bar.setVisible(running)
        self.speed_lb.setVisible(running)
        self.eta_lb.setVisible(running)
        self.info.setVisible(queued or scheduled or failed)
        self.link_view.setVisible(done)
        self.state_area.setVisible(running or queued or scheduled or done or failed)

        if scheduled:
            left = max(0, int(j.scheduled_at - time.monotonic()))
            self.pill.setText(tr("up.scheduled"))
            self.pill.set_tone("accent")
            self.meta.setText(meta)
            self.info.setText(tr("up.starts_in", t=eta_text(left)))
        elif queued:
            self.pill.setText(tr("up.queued"))
            self.pill.set_tone("")
            self.meta.setText(meta)
            self.info.setText(tr("up.waiting"))
            self.bar.setValue(0)
        elif running:
            self.pill.setText(f"{j.pct}%")
            self.pill.set_tone("accent")
            if j.sent > 0:
                meta += f" · {tr('up.sent_of', sent=human_size(j.sent), total=human_size(j.total))}"
            self.meta.setText(meta + f" · {int(time.monotonic() - j.started)}s")
            self.bar.setValue(j.pct)
            self.speed_lb.setText(speed_text(j.speed) if j.speed > 0 else "—")
            self.eta_lb.setText(f"{eta_text(j.eta)} {tr('dl.left')}" if j.eta > 0 else "")
        elif done:
            self.pill.setText(tr("up.ready"))
            self.pill.set_tone("success")
            when = datetime.fromtimestamp(job_time(j)).strftime("%d %b, %H:%M")
            self.meta.setText(meta + f" · {j.elapsed:.0f}s · {when}")
            st, left = link_state(j)
            if st == "expired":
                self.link_pill.setText(tr("qu.link_expired"))
                self.link_pill.set_tone("danger")
            else:
                self.link_pill.setText(tr("qu.link_soon" if st == "soon" else "qu.link_left",
                                          t=_left_text(left)))
                self.link_pill.set_tone("warning" if st == "soon" else "success")
            self.btn_qr.setEnabled(st != "expired")
            self.btn_qr.setToolTip(tr("qu.qr_expired") if st == "expired" else tr("qu.qr"))
            self.link_view.set_full_text(j.link)
            self.link_view.setToolTip(j.link)
            self.btn_copy.setToolTip(f"{tr('btn.copy')} · {j.link}")
        elif failed:
            self.pill.setText(tr("up.failed"))
            self.pill.set_tone("danger")
            self.meta.setText(meta)
            self.info.setProperty("role", "danger")
            repolish(self.info)
            self.info.setText(j.error)

    def retranslate(self) -> None:
        self.btn_up.setToolTip(tr("qu.move_up"))
        self.btn_down.setToolTip(tr("qu.move_down"))
        self.btn_cancel.setToolTip(tr("btn.cancel"))
        self.btn_copy.setToolTip(tr("btn.copy"))
        self.btn_retry.setToolTip(tr("btn.retry"))
        self.btn_remove.setToolTip(tr("btn.remove"))
        self.refresh()


class QueuePage(Page):
    key = "queue"
    overline_key = "nav.transfer"
    title_key = "qu.title"
    subtitle_key = "qu.subtitle"

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        self.rows: dict[int, QueueRow] = {}
        self._filter_mode = "all"

        # ------------------------------------------------ toolbar
        bar = QFrame()
        bar.setObjectName("card")
        bar_lay = vbox(bar, (18, 14, 18, 14), 10)
        bl = hbox(spacing=10)
        bar_lay.addLayout(bl)
        self.filter = Segmented([
            ("all", tr("qu.filter_all")), ("active", tr("qu.filter_active")),
            ("queued", tr("qu.filter_queued")), ("done", tr("qu.filter_done")),
            ("failed", tr("qu.filter_failed")),
        ], "all")
        self.filter.changed.connect(self._set_filter)
        bl.addWidget(self.filter)
        bl.addStretch(1)
        self.parallel_title = label(tr("qu.parallel"), "caption")
        bl.addWidget(self.parallel_title)
        self.parallel = Segmented(
            [("1", "1"), ("2", "2"), ("3", "3"), ("4", "4")], str(self._parallel()))
        self.parallel.changed.connect(self._set_parallel)
        bl.addWidget(self.parallel)

        # second row: date-wise / month-wise filters
        dl = hbox(spacing=10)
        self.date_title = label(tr("qu.date_label"), "caption")
        dl.addWidget(self.date_title)
        self._date_mode = "any"
        self._month = ""                           # "" = every month, else "YYYY-MM"
        self.date_seg = Segmented([
            ("any", tr("qu.date_any")), ("today", tr("qu.date_today")),
            ("week", tr("qu.date_week")), ("month", tr("qu.date_month")),
        ], "any")
        self.date_seg.changed.connect(self._set_date)
        dl.addWidget(self.date_seg)
        self.month_btn = Button(tr("qu.month_all"), "", "calendar", "sm")
        self.month_btn.clicked.connect(self._pick_month)
        dl.addWidget(self.month_btn)
        dl.addStretch(1)
        bar_lay.addLayout(dl)
        self.body.addWidget(bar)

        # ------------------------------------------------ queue list
        self.card = QFrame()
        self.card.setObjectName("card")
        cl = vbox(self.card, (20, 18, 20, 18), 10)
        head = hbox(spacing=10)
        self.count_title = label("", "h2")
        head.addWidget(self.count_title)
        head.addStretch(1)
        self.btn_copy_all = Button(tr("qu.copy_all"), "ghost", "copy-check", "sm")
        self.btn_copy_all.clicked.connect(self.copy_all)
        head.addWidget(self.btn_copy_all)
        self.btn_retry_failed = Button(tr("qu.retry_failed"), "", "refresh", "sm")
        self.btn_retry_failed.clicked.connect(self.retry_failed)
        head.addWidget(self.btn_retry_failed)
        self.btn_clear = Button(tr("up.clear_finished"), "ghost", "trash", "sm")
        self.btn_clear.clicked.connect(self.clear_finished)
        head.addWidget(self.btn_clear)
        cl.addLayout(head)
        self.rows_lay = vbox(spacing=8)
        cl.addLayout(self.rows_lay)
        note_row = hbox(spacing=0)
        note_row.addStretch(1)
        self.filter_note = label(tr("qu.filter_empty"), "muted")
        note_row.addWidget(self.filter_note)
        note_row.addStretch(1)
        cl.addLayout(note_row)
        self.body.addWidget(self.card)

        # ------------------------------------------------ empty state
        self.empty = QFrame()
        self.empty.setObjectName("card")
        el = vbox(self.empty, (44, 36, 44, 36), 10)
        el.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.empty_icon = Badge("check-list", 56)
        self.empty_title = label(tr("qu.empty"), "h2")
        # fixed-width wrapper keeps the wrapped label from clipping its last line
        self.empty_sub = label(tr("qu.empty_sub"), "muted", wrap=True)
        self.empty_sub.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.empty_sub.setFixedWidth(430)
        sub_row = hbox(spacing=0)
        sub_row.addStretch(1)
        sub_row.addWidget(self.empty_sub)
        sub_row.addStretch(1)
        self.empty_btn = Button(tr("qu.new_upload"), "primary", "plus")
        self.empty_btn.clicked.connect(lambda: self.ctx.navigate("upload"))
        el.addWidget(self.empty_icon, 0, Qt.AlignmentFlag.AlignHCenter)
        el.addWidget(self.empty_title, 0, Qt.AlignmentFlag.AlignHCenter)
        el.addLayout(sub_row)
        el.addWidget(self.empty_btn, 0, Qt.AlignmentFlag.AlignHCenter)
        self.body.addWidget(self.empty)
        self.body.addStretch(1)

        HUB.jobs_changed.connect(self.rebuild)
        HUB.jobs_updated.connect(self.refresh_rows)
        self._tick = QTimer(self)
        self._tick.setInterval(500)
        self._tick.timeout.connect(self._on_tick)
        self.rebuild()

    # ------------------------------------------------------------ helpers
    def _parallel(self) -> int:
        try:
            return max(1, min(4, int(self.ctx.settings.get("parallel_uploads", 2))))
        except (TypeError, ValueError):
            return 2

    def _jobs(self) -> list:
        return HUB.jobs

    # -------------------------------------------------------------- build
    def _match(self, j) -> bool:
        """State filter AND date filter AND month filter."""
        if not _filters_match(j.state, self._filter_mode):
            return False
        ts = datetime.fromtimestamp(job_time(j)) if job_time(j) else None
        now = datetime.now()
        if self._month:
            return ts is not None and ts.strftime("%Y-%m") == self._month
        if self._date_mode == "today":
            return ts is not None and ts.date() == now.date()
        if self._date_mode == "week":
            return ts is not None and (now - ts).total_seconds() <= 7 * 86400
        if self._date_mode == "month":
            return ts is not None and (ts.year, ts.month) == (now.year, now.month)
        return True

    def _ordered(self) -> list:
        """Pending work first (in queue order), then finished transfers, newest on top."""
        jobs = self._jobs()
        active = [j for j in jobs if j.state in _ACTIVE]
        finished = sorted((j for j in jobs if j.state not in _ACTIVE), key=job_time, reverse=True)
        return active + finished

    def rebuild(self) -> None:
        clear_layout(self.rows_lay)
        self.rows.clear()
        pos_of: dict[int, int] = {}
        pending = 0
        for j in self._jobs():
            if j.state in ("queued", "scheduled"):
                pending += 1
                pos_of[j.id] = pending              # #1, #2, … inside the pending queue
        for j in self._ordered():
            row = QueueRow(j, self)
            self.rows_lay.addWidget(row)           # parent first: a hidden-by-default orphan must never be shown
            row.set_position(pos_of.get(j.id, 0))
            row.setVisible(self._match(j))
            self.rows[j.id] = row
        self._sync_month_button()
        self._update_head()

    def refresh_rows(self) -> None:
        for row in self.rows.values():
            if row.isVisible():
                row.refresh()
        self._update_head()

    def _update_head(self) -> None:
        jobs = self._jobs()
        running = sum(1 for j in jobs if j.state == "running")
        scheduled = sum(1 for j in jobs if j.state == "scheduled")
        queued = sum(1 for j in jobs if j.state == "queued")
        done = sum(1 for j in jobs if j.state == "done")
        failed = sum(1 for j in jobs if j.state == "failed")
        bits = []
        if running:
            bits.append(f"{running} {tr('misc.uploading').lower()}")
        if scheduled:
            bits.append(f"{scheduled} {tr('up.scheduled').lower()}")
        if queued:
            bits.append(f"{queued} {tr('up.queued').lower()}")
        if done:
            bits.append(f"{done} {tr('up.ready').lower()}")
        if failed:
            bits.append(f"{failed} {tr('up.failed').lower()}")
        self.count_title.setText(tr("qu.title") + (f"  ·  {', '.join(bits)}" if bits else ""))
        has_jobs = bool(jobs)
        self.card.setVisible(has_jobs)
        self.empty.setVisible(not has_jobs)
        matched = sum(1 for row in self.rows.values() if self._match(row.job))
        self.filter_note.setVisible(has_jobs and matched == 0)
        self.btn_retry_failed.setEnabled(failed > 0 and HUB.page is not None)
        self.btn_copy_all.setEnabled(done > 0)
        self.btn_clear.setEnabled((done + failed) > 0 and HUB.page is not None)
        if has_jobs and not self._tick.isActive():
            self._tick.start()

    # ------------------------------------------------------------ actions
    def _set_filter(self, mode: str) -> None:
        self._filter_mode = mode
        self._apply_filters()

    def _set_date(self, mode: str) -> None:
        self._date_mode = mode
        self._month = ""                            # the quick ranges and the month picker are exclusive
        self._sync_month_button()
        self._apply_filters()

    def _months(self) -> list[str]:
        seen = {datetime.fromtimestamp(job_time(j)).strftime("%Y-%m") for j in self._jobs() if job_time(j)}
        return sorted(seen, reverse=True)

    def _sync_month_button(self) -> None:
        self.month_btn.setText(
            datetime.strptime(self._month, "%Y-%m").strftime("%B %Y") if self._month else tr("qu.month_all"))

    def _pick_month(self) -> None:
        m = RoundMenu(parent=self)
        acts = {m.addAction(tr("qu.month_all")): ""}
        if self._months():
            m.addSeparator()
        for key in self._months():
            acts[m.addAction(datetime.strptime(key, "%Y-%m").strftime("%B %Y"))] = key
        chosen = m.exec(self.month_btn.mapToGlobal(self.month_btn.rect().bottomLeft()))
        if chosen is None:
            return
        self._month = acts[chosen]
        if self._month:
            self._date_mode = "any"
            self.date_seg.set_value("any")
        self._sync_month_button()
        self._apply_filters()

    def _apply_filters(self) -> None:
        shown = []
        for row in self.rows.values():
            ok = self._match(row.job)
            was = row.isVisible()
            row.setVisible(ok)
            if ok and not was:
                shown.append(row)
        # rows that (re)appear glide in with a short stagger; nothing else moves
        for i, row in enumerate(shown[:8]):
            QTimer.singleShot(i * 30, lambda r=row: self._fade_row(r))
        self._update_head()

    @staticmethod
    def _fade_row(row) -> None:
        try:
            if row.isVisible():
                motion.fade_in(row, motion.DUR_ENTER, lambda r=row: motion.clear_effect(r))
        except RuntimeError:                         # row deleted by a rebuild meanwhile
            pass

    def _set_parallel(self, value: str) -> None:
        self.ctx.settings.set("parallel_uploads", int(value))
        if HUB.page is not None:
            HUB.page._pump()

    def move(self, job_id: int, delta: int) -> None:
        if HUB.page is not None:
            HUB.page.move_job(job_id, delta)

    def cancel(self, job_id: int) -> None:
        if HUB.page is not None:
            HUB.page.cancel_job(job_id)
        self.refresh_rows()

    def retry(self, job_id: int) -> None:
        if HUB.page is not None:
            HUB.page.retry_job(job_id)

    def remove(self, job_id: int) -> None:
        if HUB.page is not None:
            HUB.page.dismiss_job(job_id)

    def qr(self, job_id: int) -> None:
        if HUB.page is not None:
            HUB.page.show_qr(job_id)

    def copy_link(self, job_id: int) -> None:
        j = next((x for x in self._jobs() if x.id == job_id), None)
        if j is not None and j.link:
            QGuiApplication.clipboard().setText(j.link)
            self.ctx.toast(tr("toast.link_copied"))

    def copy_all(self) -> None:
        """Copy every ready link at once (newline separated)."""
        links = [j.link for j in self._jobs() if j.state == "done" and j.link]
        if not links:
            return
        QGuiApplication.clipboard().setText("\n".join(links))
        self.ctx.toast(tr("toast.links_copied", n=len(links)))

    def retry_failed(self) -> None:
        if HUB.page is None:
            return
        for j in [x for x in self._jobs() if x.state == "failed"]:
            HUB.page.retry_job(j.id)

    def clear_finished(self) -> None:
        if HUB.page is not None:
            HUB.page.clear_finished()

    # -------------------------------------------------------------- timing
    def _on_tick(self) -> None:
        jobs = self._jobs()
        if not any(j.state in _ACTIVE for j in jobs):
            self._tick.stop()
        self.refresh_rows()

    def on_show(self) -> None:
        self.rebuild()
        if any(j.state in _ACTIVE for j in self._jobs()):
            self._tick.start()

    def hideEvent(self, e) -> None:  # noqa: N802
        self._tick.stop()
        super().hideEvent(e)

    # ---------------------------------------------------------- lifecycle
    def can_close(self) -> bool:
        return True

    def shutdown(self) -> None:
        self._tick.stop()

    def _retranslate(self) -> None:
        self.parallel_title.setText(tr("qu.parallel"))
        self.date_title.setText(tr("qu.date_label"))
        for key, name in (("any", "qu.date_any"), ("today", "qu.date_today"),
                          ("week", "qu.date_week"), ("month", "qu.date_month")):
            self.date_seg.set_option_text(key, tr(name))
        self._sync_month_button()
        for key, name in (("all", "qu.filter_all"), ("active", "qu.filter_active"),
                          ("queued", "qu.filter_queued"), ("done", "qu.filter_done"),
                          ("failed", "qu.filter_failed")):
            self.filter.set_option_text(key, tr(name))
        self.btn_retry_failed.setText(tr("qu.retry_failed"))
        self.btn_copy_all.setText(tr("qu.copy_all"))
        self.btn_clear.setText(tr("up.clear_finished"))
        self.empty_title.setText(tr("qu.empty"))
        self.empty_sub.setText(tr("qu.empty_sub"))
        self.empty_btn.setText(tr("qu.new_upload"))
        self.filter_note.setText(tr("qu.filter_empty"))
        for row in self.rows.values():
            row.retranslate()
        self._update_head()
