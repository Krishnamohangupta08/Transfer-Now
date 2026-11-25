"""Download — paste a TransferNow link and save its files (live speed graph)."""
from __future__ import annotations

import os
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices, QGuiApplication
from PyQt6.QtWidgets import QFileDialog, QFrame, QLabel, QProgressBar, QWidget

from core.downloader import parse_transfer_id
from core.store import human_size
from core.tasks import DownloadThread, FetchThread
from ..i18n import i18n, tr
from ..menus import LineEdit
from ..widgets import (
    Badge, Button, ElidedLabel, IconPill, SpeedGraph, clear_layout, eta_text, hbox, hline,
    label, repolish, speed_text, vbox,
)
from .base import Page
from .upload import file_icon_for

S_EMPTY, S_LOADING, S_INFO, S_RUNNING, S_DONE, S_ERROR = range(6)


def _center():
    from PyQt6.QtCore import Qt
    return Qt.AlignmentFlag.AlignHCenter


class _HintLabel(QLabel):
    """Caption that takes no space (and no layout gap) while it is empty."""

    def __init__(self) -> None:
        super().__init__("")
        from PyQt6.QtCore import Qt
        # error hints may embed remote-derived text: never let Qt parse it
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setProperty("role", "caption")
        self.hide()

    def setText(self, text: str) -> None:  # noqa: N802
        super().setText(text)
        self.setVisible(bool(text))


class _Step(QFrame):
    """Numbered "how it works" tile for the empty state."""

    def __init__(self, n: int, tkey: str, dkey: str) -> None:
        super().__init__()
        self.setObjectName("inner")
        self._tk, self._dk = tkey, dkey
        lay = vbox(self, (18, 16, 18, 16), 8)
        num = QLabel(str(n))
        num.setObjectName("badge")
        num.setFixedSize(28, 28)
        num.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(num, 0, Qt.AlignmentFlag.AlignLeft)
        self.t = label(tr(tkey), "h2")
        self.d = label(tr(dkey), "caption", wrap=True)
        lay.addWidget(self.t)
        lay.addWidget(self.d)
        lay.addStretch(1)

    def retranslate(self) -> None:
        self.t.setText(tr(self._tk))
        self.d.setText(tr(self._dk))


class DownloadPage(Page):
    key = "download"
    overline_key = "nav.transfer"
    title_key = "dl.title"
    subtitle_key = "dl.subtitle"

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        self._info: Optional[dict] = None
        self._token = 0
        self._fetch: Optional[FetchThread] = None
        self._dl: Optional[DownloadThread] = None
        self._saved = ""
        self._state = S_EMPTY
        self._closing = False               # shutdown(): stale callbacks become no-ops

        # link card ----------------------------------------------------------
        card = QFrame()
        card.setObjectName("card")
        cl = vbox(card, (24, 22, 24, 20), 14)
        head = hbox(spacing=14)
        head.addWidget(Badge("link", 44, round_=False), 0, Qt.AlignmentFlag.AlignVCenter)
        hc = vbox(spacing=1)
        self.link_label = label(tr("dl.link_label"), "h2")
        self.link_sub = label(tr("dl.link_sub"), "caption")
        hc.addWidget(self.link_label)
        hc.addWidget(self.link_sub)
        head.addLayout(hc, 1)
        cl.addLayout(head)
        row = hbox(spacing=10)
        self.link = LineEdit()
        self.link.setPlaceholderText("https://www.transfernow.net/dl/…")
        self.link.setClearButtonEnabled(True)
        self.link.setMinimumHeight(48)
        row.addWidget(self.link, 1)
        self.paste_btn = Button(tr("dl.paste"), "", "paste", "lg")
        self.paste_btn.clicked.connect(self.paste)
        row.addWidget(self.paste_btn)
        self.fetch_btn = Button(tr("dl.fetch"), "primary", "search", "lg")
        self.fetch_btn.clicked.connect(self.fetch_now)
        row.addWidget(self.fetch_btn)
        cl.addLayout(row)
        self.hint = _HintLabel()
        cl.addWidget(self.hint)
        self.body.addWidget(card)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(500)
        self._debounce.timeout.connect(self._auto_fetch)
        self.link.textChanged.connect(self._on_text)
        self.link.returnPressed.connect(self.fetch_now)

        # info card ----------------------------------------------------------
        self.info_card = QFrame()
        self.info_card.setObjectName("card")
        il = vbox(self.info_card, (24, 22, 24, 22), 16)
        top = hbox(spacing=14)
        top.addWidget(Badge("package", 52, round_=False), 0, Qt.AlignmentFlag.AlignVCenter)
        col = vbox(spacing=3)
        self.info_title = label("", "h2")
        self.info_title.setTextFormat(Qt.TextFormat.PlainText)   # remote title: plain text
        self.info_sub = label("", "muted")
        col.addWidget(self.info_title)
        col.addWidget(self.info_sub)
        top.addLayout(col, 1)
        self.info_chips = hbox(spacing=8)
        top.addLayout(self.info_chips)
        il.addLayout(top)

        files_box = QFrame()
        files_box.setObjectName("inner")
        self.files_lay = vbox(files_box, (16, 4, 16, 4), 0)
        il.addWidget(files_box)

        save_box = QFrame()
        save_box.setObjectName("inner")
        save = hbox(save_box, (14, 12, 14, 12), 12)
        save.addWidget(Badge("folder", 36, round_=False), 0, Qt.AlignmentFlag.AlignVCenter)
        sc = vbox(spacing=1)
        self.save_to_lb = label(tr("dl.save_to"), "caption")
        self.save_path = ElidedLabel("")
        sc.addWidget(self.save_to_lb)
        sc.addWidget(self.save_path)
        save.addLayout(sc, 1)
        self.change_btn = Button(tr("dl.change"), "", "folder", "sm")
        self.change_btn.clicked.connect(self.choose_folder)
        save.addWidget(self.change_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        il.addWidget(save_box)

        self.dl_btn = Button(tr("dl.download_files"), "primary", "cloud-download", "lg")
        self.dl_btn.clicked.connect(self.start)
        il.addWidget(self.dl_btn)
        self.body.addWidget(self.info_card)

        # progress card ------------------------------------------------------
        self.run_card = QFrame()
        self.run_card.setObjectName("card")
        rl = vbox(self.run_card, (26, 24, 26, 24), 14)
        r1 = hbox(spacing=14)
        r1.addWidget(Badge("cloud-download", 48), 0, Qt.AlignmentFlag.AlignVCenter)
        self.run_title = label(tr("dl.downloading"), "h2")
        r1.addWidget(self.run_title)
        r1.addStretch(1)
        self.run_pct = label("0%", "big")
        r1.addWidget(self.run_pct)
        rl.addLayout(r1)
        self.run_bar = QProgressBar()
        self.run_bar.setRange(0, 1000)
        rl.addWidget(self.run_bar)
        gbox = QFrame()
        gbox.setObjectName("inner")
        gl = vbox(gbox, (12, 10, 12, 10), 0)
        self.graph = SpeedGraph()
        self.graph.setMinimumHeight(72)
        gl.addWidget(self.graph)
        rl.addWidget(gbox)
        self.run_stats = label("", "muted")
        rl.addWidget(self.run_stats)
        acts = hbox(spacing=10)
        self.pause_btn = Button(tr("btn.pause"), "", "pause")
        self.pause_btn.clicked.connect(self.toggle_pause)
        self.cancel_btn = Button(tr("btn.cancel"), "danger", "x")
        self.cancel_btn.clicked.connect(self.cancel)
        acts.addWidget(self.pause_btn)
        acts.addWidget(self.cancel_btn)
        acts.addStretch(1)
        rl.addLayout(acts)
        self.body.addWidget(self.run_card)

        # done card ----------------------------------------------------------
        self.done_card = QFrame()
        self.done_card.setObjectName("card")
        dl = vbox(self.done_card, (26, 24, 26, 24), 16)
        dh = hbox(spacing=16)
        dh.addWidget(Badge("check-circle", 56, "success"), 0, Qt.AlignmentFlag.AlignVCenter)
        dc = vbox(spacing=2)
        self.done_title = label(tr("dl.complete"), "h2")
        self.done_sub = label("", "muted")
        dc.addWidget(self.done_title)
        dc.addWidget(self.done_sub)
        dh.addLayout(dc, 1)
        dl.addLayout(dh)
        pbox = QFrame()
        pbox.setObjectName("inner")
        pl = hbox(pbox, (14, 12, 14, 12), 12)
        pl.addWidget(Badge("folder", 32, round_=False), 0, Qt.AlignmentFlag.AlignVCenter)
        self.done_path = ElidedLabel("", "muted")
        pl.addWidget(self.done_path, 1)
        dl.addWidget(pbox)
        da = hbox(spacing=10)
        self.open_folder_btn = Button(tr("btn.open_folder"), "primary", "folder")
        self.open_folder_btn.clicked.connect(self.open_folder)
        self.open_file_btn = Button(tr("btn.open_file"), "", "external")
        self.open_file_btn.clicked.connect(self.open_file)
        self.another_btn = Button(tr("btn.another"), "", "plus")
        self.another_btn.clicked.connect(self.reset)
        for b in (self.open_folder_btn, self.open_file_btn, self.another_btn):
            da.addWidget(b)
        da.addStretch(1)
        dl.addLayout(da)
        self.body.addWidget(self.done_card)

        # error card ---------------------------------------------------------
        self.err_card = QFrame()
        self.err_card.setObjectName("card")
        el = hbox(self.err_card, (22, 20, 22, 20), 16)
        el.addWidget(Badge("alert-circle", 48, "danger"), 0, Qt.AlignmentFlag.AlignTop)
        self.err_text = label("", "muted", wrap=True, selectable=True)
        self.err_text.setTextFormat(Qt.TextFormat.PlainText)     # error text: plain text
        ec = vbox(spacing=3)
        self.err_title = label(tr("dl.error"), "h2")
        ec.addWidget(self.err_title)
        ec.addWidget(self.err_text)
        el.addLayout(ec, 1)
        self.retry_btn = Button(tr("btn.retry"), "", "refresh")
        self.retry_btn.clicked.connect(self._retry)
        el.addWidget(self.retry_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        self.body.addWidget(self.err_card)

        # empty state: headline + 3 "how it works" steps -----------------------
        self.empty = QFrame()
        self.empty.setObjectName("card")
        em = vbox(self.empty, (24, 36, 24, 26), 6)
        em.addWidget(Badge("cloud-download", 60), 0, _center())
        em.addSpacing(6)
        self.empty_title = label(tr("dl.empty_title"), "h2")
        em.addWidget(self.empty_title, 0, _center())
        self.empty_sub = label(tr("dl.empty_sub"), "muted")
        em.addWidget(self.empty_sub, 0, _center())
        em.addSpacing(18)
        steps = hbox(spacing=12)
        self._steps = [_Step(1, "dl.s1_t", "dl.s1_d"), _Step(2, "dl.s2_t", "dl.s2_d"),
                       _Step(3, "dl.s3_t", "dl.s3_d")]
        for st in self._steps:
            steps.addWidget(st, 1)
        em.addLayout(steps)
        self.body.addWidget(self.empty)
        self.body.addStretch(1)

        self._last_pct = -1
        self._set_state(S_EMPTY)
        self._update_save_label()
        ctx.settings.on_change(lambda k: self._update_save_label() if k == "download_dir" else None)
        i18n.changed.connect(self._retranslate)

    # ------------------------------------------------------------------ state
    def _set_state(self, state: int) -> None:
        self._state = state
        self.empty.setVisible(state in (S_EMPTY, S_LOADING))
        self.info_card.setVisible(state == S_INFO)
        self.run_card.setVisible(state == S_RUNNING)
        self.done_card.setVisible(state == S_DONE)
        self.err_card.setVisible(state == S_ERROR)
        busy = state == S_RUNNING
        self.link.setEnabled(not busy)
        self.fetch_btn.setEnabled(not busy and state != S_LOADING)
        if state == S_LOADING:
            self.hint.setText(tr("dl.lookup"))
            self._hint_tone("")

    def _hint_tone(self, tone: str) -> None:
        self.hint.setProperty("role", {"ok": "success", "bad": "danger"}.get(tone, "caption"))
        repolish(self.hint)

    def _update_save_label(self) -> None:
        self.save_path.set_full_text(self.ctx.settings.download_folder)

    def _retranslate(self) -> None:
        self.link_label.setText(tr("dl.link_label"))
        self.link_sub.setText(tr("dl.link_sub"))
        for st in self._steps:
            st.retranslate()
        self.paste_btn.setText(tr("dl.paste"))
        self.fetch_btn.setText(tr("dl.fetch"))
        self.save_to_lb.setText(tr("dl.save_to"))
        self.change_btn.setText(tr("dl.change"))
        self.dl_btn.setText(tr("dl.download_files"))
        self.run_title.setText(tr("dl.downloading"))
        self.pause_btn.setText(tr("btn.pause"))
        self.cancel_btn.setText(tr("btn.cancel"))
        self.done_title.setText(tr("dl.complete"))
        self.open_folder_btn.setText(tr("btn.open_folder"))
        self.open_file_btn.setText(tr("btn.open_file"))
        self.another_btn.setText(tr("btn.another"))
        self.err_title.setText(tr("dl.error"))
        self.retry_btn.setText(tr("btn.retry"))
        self.empty_title.setText(tr("dl.empty_title"))
        self.empty_sub.setText(tr("dl.empty_sub"))
        if self._state == S_LOADING:
            self.hint.setText(tr("dl.lookup"))

    # ---------------------------------------------------------------- link in
    def paste(self) -> None:
        text = QGuiApplication.clipboard().text().strip()
        if text:
            self.link.setText(text)
            self.fetch_now()
        else:
            self.ctx.toast(tr("toast.clip_empty"), "info")

    def prefill(self, link: str) -> None:
        self.link.setText(link)
        self.fetch_now()

    def _on_text(self, text: str) -> None:
        if self._state == S_RUNNING or self._closing:
            return
        text = text.strip()
        self._debounce.stop()
        if not text:
            self._invalidate()
            self.hint.setText("")
            self.link.setProperty("state", "")
            repolish(self.link)
            return
        ok = parse_transfer_id(text) is not None
        self.link.setProperty("state", "" if ok else "error")
        repolish(self.link)
        if ok:
            if (self._info is not None
                    and parse_transfer_id(text) != self._info.get("transfer_id")):
                self._invalidate()          # the card on screen is for another link
            self.hint.setText("")
            self._debounce.start()
        else:
            self._invalidate()              # an invalid string must not leave a stale card
            self.hint.setText(tr("dl.not_link"))
            self._hint_tone("bad")

    def _invalidate(self) -> None:
        """Drop the shown info card: the field no longer matches it.  Bumps the
        request token so any in-flight fetch becomes stale, and returns to the
        empty state while keeping the hint line."""
        self._token += 1
        self._debounce.stop()
        self._info = None
        if self._state in (S_LOADING, S_INFO, S_DONE, S_ERROR):
            self._set_state(S_EMPTY)

    def _matches_current(self, info: dict) -> bool:
        """Belt and braces: a fetch response must still describe what the field
        shows, even if its token somehow matches."""
        tid = parse_transfer_id(self.link.text().strip())
        if tid != info.get("transfer_id"):
            return False
        link = str(info.get("link") or "")
        return not link or parse_transfer_id(link) == tid

    def _auto_fetch(self) -> None:
        if self._state not in (S_RUNNING, S_LOADING) and parse_transfer_id(self.link.text()):
            self.fetch_now()

    def fetch_now(self) -> None:
        if self._state == S_RUNNING:
            return
        text = self.link.text().strip()
        if not parse_transfer_id(text):
            self.hint.setText(tr("dl.bad_link"))
            self._hint_tone("bad")
            return
        self._debounce.stop()
        self._token += 1
        t = FetchThread(self._token, text, self)
        t.info.connect(self._on_info)
        t.failed.connect(self._on_fetch_failed)
        t.finished.connect(lambda th=t: self._fetch_done(th))
        self._fetch = t
        self._set_state(S_LOADING)
        t.start()

    def _fetch_done(self, t: FetchThread) -> None:
        if t is self._fetch:
            self._fetch = None
        try:
            t.deleteLater()
        except RuntimeError:
            pass

    def _on_info(self, token: int, info: dict) -> None:
        if self._closing or token != self._token or not self._matches_current(info):
            return
        self._info = info
        self.info_title.setText(info.get("title") or "Files are ready")
        n = len(info["files"])
        self.info_sub.setText(f"{n} file{'s' if n != 1 else ''} · {human_size(info['total_bytes'])}")
        clear_layout(self.info_chips)
        if info.get("expiry"):
            self.info_chips.addWidget(IconPill("clock", f"Expires {info['expiry']}", "accent"))
        clear_layout(self.files_lay)
        shown = info["files"][:8]
        for i, f in enumerate(shown):
            if i:
                self.files_lay.addWidget(hline())
            r = QWidget()
            rl = hbox(r, (0, 10, 0, 10), 12)
            rl.addWidget(Badge(file_icon_for(f["name"]), 34, round_=False))
            nm = ElidedLabel(f["name"])
            nm.setTextFormat(Qt.TextFormat.PlainText)      # untrusted remote name
            rl.addWidget(nm, 1)
            rl.addWidget(label(human_size(f["size"]) if f["size"] else "", "caption"))
            self.files_lay.addWidget(r)
        if n > 8:
            self.files_lay.addWidget(hline())
            more = label(f"+ {n - 8} more files", "caption")
            more.setContentsMargins(0, 10, 0, 10)
            self.files_lay.addWidget(more)
        self.hint.setText(tr("dl.found"))
        self._hint_tone("ok")
        self.link.setProperty("state", "ok")
        repolish(self.link)
        self._set_state(S_INFO)

    def _on_fetch_failed(self, token: int, msg: str) -> None:
        if self._closing or token != self._token:
            return
        self._info = None
        self.hint.setText(msg)
        self._hint_tone("bad")
        self.link.setProperty("state", "error")
        repolish(self.link)
        self._set_state(S_EMPTY)

    # --------------------------------------------------------------- download
    def choose_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Save files to",
                                             self.ctx.settings.download_folder)
        if d:
            self.ctx.settings.set("download_dir", d)

    def is_running(self) -> bool:
        return self._dl is not None and self._dl.isRunning()

    def can_close(self) -> bool:
        return not self.is_running()

    def trigger_primary(self) -> None:
        if self._state == S_INFO:
            self.start()

    def start(self) -> None:
        if self.is_running():
            return
        if (not self._info
                or parse_transfer_id(self.link.text().strip()) != self._info.get("transfer_id")):
            self.fetch_now()                # stale/missing card: re-fetch, never trust it
            return
        folder = self.ctx.settings.download_folder
        try:
            os.makedirs(folder, exist_ok=True)
            if not os.access(folder, os.W_OK):
                raise OSError("not writable")
        except OSError:
            self.ctx.toast("Can't write to the download folder — choose another", "error")
            return
        t = DownloadThread(self._info["transfer_id"], folder, self)
        t.progress.connect(self._on_progress)
        t.status.connect(lambda m: self.run_stats.setText(m))
        t.success.connect(self._on_success)
        t.failed.connect(self._on_failed)
        t.finished.connect(lambda th=t: self._on_finished(th))
        self._dl = t
        self._last_pct = -1
        self._samples: list[float] = []
        self.run_bar.setValue(0)
        self.run_pct.setText("0%")
        self.run_title.setText(tr("dl.downloading"))
        self.run_stats.setText("Preparing…")
        self.pause_btn.setText(tr("btn.pause"))
        self.pause_btn.set_icon_name("pause")
        self.graph.clear()
        self._set_state(S_RUNNING)
        self.ctx.activity.emit(tr("misc.downloading"), 0)
        t.start()

    def _on_finished(self, t: DownloadThread) -> None:
        if t is self._dl:
            self._dl = None
        try:
            t.deleteLater()
        except RuntimeError:
            pass

    def _on_progress(self, done: int, total: int, speed: float) -> None:
        if self._closing:
            return
        self.graph.push(speed)
        if total:
            self.run_bar.setValue(int(1000 * done / total))
            pct = int(100 * done / total)
            eta = (total - done) / speed if speed > 0 else 0
            self.run_stats.setText(f"{human_size(done)} of {human_size(total)} · "
                                   f"{speed_text(speed)} · {eta_text(eta)} {tr('dl.left')}")
        else:
            pct = 0
            self.run_bar.setRange(0, 0)
            self.run_stats.setText(f"{human_size(done)} · {speed_text(speed)}")
        if pct != self._last_pct:
            self._last_pct = pct
            self.run_pct.setText(f"{pct}%" if total else "")
            self.ctx.activity.emit(tr("misc.downloading"), pct)

    def toggle_pause(self) -> None:
        if not self.is_running():
            return
        paused = not self._dl.paused
        self._dl.set_paused(paused)
        self.run_title.setText(tr("dl.paused") if paused else tr("dl.downloading"))
        self.pause_btn.setText(tr("btn.resume") if paused else tr("btn.pause"))
        self.pause_btn.set_icon_name("play" if paused else "pause")

    def cancel(self) -> None:
        if self.is_running():
            self._dl.stop()

    def shutdown(self) -> None:
        self._closing = True                # queued callbacks from here on are no-ops
        self._token += 1
        if self._dl is not None:
            self._dl.stop()
            self._dl.wait(5000)
        if self._fetch is not None and self._fetch.isRunning():
            self._fetch.wait(3000)

    def _on_success(self, path: str) -> None:
        if self._closing:
            return
        self.ctx.activity.emit("", -1)
        self._saved = path
        info = self._info or {}
        self.ctx.add_history({
            "kind": "download", "files": info.get("files", []),
            "total_bytes": info.get("total_bytes", 0) or _size(path),
            "link": info.get("link", ""), "save_path": path,
        })
        self.run_bar.setRange(0, 1000)
        self.done_path.set_full_text(path)
        n = len((self._info or {}).get("files", []))
        self.done_sub.setText(f"{n} file{'s' if n != 1 else ''} · "
                              f"{human_size((self._info or {}).get('total_bytes', 0) or _size(path))}")
        self._set_state(S_DONE)
        self.ctx.toast(tr("toast.dl_complete"))
        if self.ctx.settings.get("open_folder_after_download", True):
            self.open_folder()

    def _on_failed(self, msg: str) -> None:
        if self._closing:
            return
        self.ctx.activity.emit("", -1)
        self.run_bar.setRange(0, 1000)
        if msg == "cancelled":
            self.ctx.toast(tr("toast.dl_cancelled"), "info")
            self._set_state(S_INFO if self._info else S_EMPTY)
            return
        self.err_text.setText(msg)
        self._set_state(S_ERROR)

    def _retry(self) -> None:
        if self._info:
            self._set_state(S_INFO)
            self.start()
        else:
            self.fetch_now()

    def open_folder(self) -> None:
        folder = os.path.dirname(self._saved) if self._saved else self.ctx.settings.download_folder
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def open_file(self) -> None:
        if self._saved and os.path.exists(self._saved):
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._saved))

    def reset(self) -> None:
        self._info = None
        self._saved = ""
        self.link.clear()
        self.link.setEnabled(True)
        self._set_state(S_EMPTY)


def _size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0
