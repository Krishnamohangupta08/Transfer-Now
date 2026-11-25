"""Upload & Share — pick files, create a TransferNow link (speed + ETA,
scheduling, auto-retry)."""
from __future__ import annotations

import math
import os
import threading
import time
from datetime import datetime, timedelta
from typing import Optional

from PyQt6 import sip
from PyQt6.QtCore import (
    QPoint, QPointF, QPropertyAnimation, QRectF, QTimer, QUrl, Qt, pyqtProperty, pyqtSignal,
)
from PyQt6.QtGui import (
    QColor, QDesktopServices, QFont, QGuiApplication, QIcon, QPainter, QPainterPath, QPen, QPixmap,
)
from PyQt6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QGraphicsDropShadowEffect, QLabel, QProgressBar,
    QScrollArea, QSizePolicy, QWidget,
)

from core import limits, ziputil
from core.emailgen import random_email
from core.engine import MAX_BYTES, STEPS, EngineOptions
from core.store import human_size
from core.tasks import FolderScanThread, UploadThread, ZipThread, screen_batch
from ..hub import HUB
from ..i18n import tr
from ..menus import LineEdit, RoundMenu
from ..smoothscroll import enable_smooth_scroll
from PyQt6.QtWidgets import QLineEdit as _QLineEdit
QLineEdit_Trailing = _QLineEdit.ActionPosition.TrailingPosition
from .. import icons, motion
from ..dropfx import DropFX, drag_info, drag_title, has_folder, paint_badge, paint_glow_border, paint_top_light
from ..theme import _mix, theme
from ..widgets import (
    Badge, Button, ElidedLabel, ExtTile, hline, Pill, QrWidget, Segmented, SpeedGraph, Switch,
    clear_layout, eta_text, hbox, label, repolish, speed_text, vbox,
)
from .base import Page

_EXT_ICONS = {
    "package": {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".apk", ".iso"},
    "image": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".ico", ".tiff", ".heic"},
    "film": {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"},
    "music": {".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a"},
    "code": {".py", ".js", ".ts", ".html", ".css", ".json", ".xml", ".yml", ".yaml",
             ".c", ".cpp", ".h", ".java", ".rs", ".go", ".sh", ".bat"},
    "file-text": {".txt", ".md", ".pdf", ".doc", ".docx", ".rtf", ".csv", ".log",
                  ".ppt", ".pptx", ".xls", ".xlsx"},
}


def file_icon_for(name: str) -> str:
    ext = os.path.splitext(name)[1].lower()
    for icon_name, exts in _EXT_ICONS.items():
        if ext in exts:
            return icon_name
    return "file"


# ------------------------------------------------------------------ dropzone
class DropZone(QFrame):
    """"Comet Dock" drop zone (motion lives in ``ui.dropfx``).

    Idle: calm panel, faint dot grid, three file tiles.  Drag-over: a comet circles
    the border, a spotlight follows the cursor, nearby dots swell, the tiles lift and
    fan out on a spring.  Drop: ripple from the drop point, tiles are absorbed, the
    border flashes green and a check pops.  Adding files smoothly folds the zone to
    its compact strip (and back when the list is cleared).
    """
    files_dropped = pyqtSignal(list)
    browse = pyqtSignal()
    browse_folder = pyqtSignal()
    FULL_H, COMPACT_H = 262, 96

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("dropzone")
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setProperty("drag", False)
        self._drag = False
        self._count = 0
        self._size = ""
        self._folder = False
        self._compact = False
        self._h_anim: QPropertyAnimation | None = None
        self.fx = DropFX(self)
        self.fx.tick.connect(self.update)
        self.fx.tick.connect(self._on_fx_tick)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._lay = vbox(self, (24, 20, 24, 22), 6)
        self._lay.addStretch(1)
        self._fan = QWidget()                       # room for the fanned tiles (painted)
        self._fan.setFixedHeight(84)
        self._fan.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._lay.addWidget(self._fan)
        self.title = label(tr("up.drop"))
        self.title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.title.setStyleSheet("font-family: 'Inter Tight', 'Inter'; font-size: 22px; font-weight: 500;"
                                 " letter-spacing: -0.6px;")
        self.hint = label(tr("up.drop_hint"), "subtitle")
        self.hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._lay.addWidget(self.title)
        self._lay.addWidget(self.hint)
        self.btns = QWidget()                      # "Browse files" + "Add folder"
        self.btns.setObjectName("page")
        brow = hbox(self.btns, spacing=10)
        self.btn = Button(tr("up.browse"), "", "plus")
        self.btn.clicked.connect(self.browse.emit)
        self.btn_folder = Button(tr("up.browse_folder"), "", "folder")
        self.btn_folder.clicked.connect(self.browse_folder.emit)
        brow.addWidget(self.btn)
        brow.addWidget(self.btn_folder)
        self._lay.addSpacing(8)
        self._lay.addWidget(self.btns, 0, Qt.AlignmentFlag.AlignCenter)
        from PyQt6.QtWidgets import QGraphicsOpacityEffect
        self._btn_fx = QGraphicsOpacityEffect(self.btns)      # buttons melt away while dragging
        self._btn_fx.setOpacity(1.0)
        self.btns.setGraphicsEffect(self._btn_fx)
        self._lay.addStretch(1)
        self.set_compact(False, animate=False)
        from ..i18n import i18n
        i18n.changed.connect(self._retranslate)
        theme.changed.connect(self.update)

    # -------------------------------------------------------------- text
    def _headline(self) -> str:
        if self._drag and self._count > 0:
            return drag_title(self._count, self._folder)
        return tr("up.drop_compact") if self._compact else tr("up.drop")

    def _subline(self) -> str:
        if self._drag:
            return (f"{self._size}  ·  " if self._size else "") + tr("up.drop_hint")
        return tr("up.drop_hint")

    def _retranslate(self) -> None:
        self.title.setText(self._headline())
        self.hint.setText(self._subline())
        self.btn.setText(tr("up.browse"))
        self.btn_folder.setText(tr("up.browse_folder"))

    def _on_fx_tick(self) -> None:
        self._btn_fx.setOpacity(max(0.0, 1.0 - self.fx.t * 1.8))

    # ------------------------------------------------- smooth compact <-> full
    def _get_zone_h(self) -> int:
        return self.height()

    def _set_zone_h(self, v: int) -> None:
        self.setFixedHeight(int(v))

    zoneH = pyqtProperty(int, _get_zone_h, _set_zone_h)

    def set_compact(self, compact: bool, animate: bool = True) -> None:
        changed = compact != self._compact
        self._compact = compact
        self._fan.setVisible(not compact)
        self.hint.setVisible(not compact)
        self.btns.setVisible(not compact)
        self._lay.setContentsMargins(24, 14 if compact else 20, 24, 14 if compact else 22)
        self.title.setText(self._headline())
        target = self.COMPACT_H if compact else self.FULL_H
        if self._h_anim is not None:
            self._h_anim.stop()
            self._h_anim = None
        if not (animate and changed and self.isVisible()):
            self._settle_height(target)
            return
        self._h_anim = motion.anim_prop(self, b"zoneH", self.height(), target, 320,
                                        on_done=lambda t=target: self._settle_height(t))

    def _settle_height(self, target: int) -> None:
        self._h_anim = None
        self.setMinimumHeight(target)
        self.setMaximumHeight(16777215)

    # ------------------------------------------------------------ drag state
    def _set_drag(self, on: bool, urls=None) -> None:
        self._drag = on
        if on and urls is not None:
            self._count, self._size = drag_info(urls)
            self._folder = has_folder(urls)
        elif not on:
            self._count, self._size, self._folder = 0, "", False
        self.setProperty("drag", on)
        self.title.setText(self._headline())
        self.hint.setText(self._subline())
        self.update()

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.browse.emit()
        super().mouseReleaseEvent(e)

    def dragEnterEvent(self, e) -> None:  # noqa: N802
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._set_drag(True, e.mimeData().urls())
            self.fx.begin(e.position())

    def dragMoveEvent(self, e) -> None:  # noqa: N802
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self.fx.move(e.position())

    def dragLeaveEvent(self, e) -> None:  # noqa: N802
        self._set_drag(False)
        self.fx.end()

    def dropEvent(self, e) -> None:  # noqa: N802
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        self._set_drag(False)
        if paths:
            e.acceptProposedAction()
            self.fx.drop(e.position())
            self.files_dropped.emit(paths)
        else:
            self.fx.end()

    # ---------------------------------------------------------------- paint
    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = theme.pal
        fx = self.fx
        t = fx.t
        rad = 22.0
        r = QRectF(self.rect()).adjusted(0.75, 0.75, -0.75, -0.75)
        shape = QPainterPath()
        shape.addRoundedRect(r, rad, rad)
        p.fillPath(shape, QColor(c["surface"]))
        paint_top_light(p, r, rad, fx)
        if t < 0.99:                                  # resting dot grid melts away on drag
            p.setOpacity(1.0 - 0.85 * t)
            p.drawPixmap(0, 0, self._dot_pixmap(r))
            p.setOpacity(1.0)
        # hairline border that warms toward the accent
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(_mix(c["border"], c["accent"], min(1.0, t))), 1.0))
        p.drawRoundedRect(r, rad, rad)
        paint_glow_border(p, r, rad, fx)

        if not self._compact:
            fr = self._fan.geometry()
            paint_badge(p, QPointF(fr.center().x(), fr.center().y() + 4), fx,
                        0.80 + 0.20 * max(0.0, min(1.0, fx.pop)))
        elif t > 0.02 or fx.dropped:                  # compact strip: badge beside the title
            half = self.title.fontMetrics().horizontalAdvance(self.title.text()) / 2
            p.setOpacity(max(t, 1.0 if fx.dropped else 0.0))
            paint_badge(p, QPointF(self.width() / 2 - half - 46, self.height() / 2), fx, 0.52)
            p.setOpacity(1.0)

    def _dot_pixmap(self, r: QRectF) -> QPixmap:
        """Resting 16 px dot grid, rendered once per (size, scale, theme)."""
        dpr = self.devicePixelRatioF()
        key = (self.width(), self.height(), dpr, theme.pal["text"])
        if getattr(self, "_dots_key", None) != key:
            pm = QPixmap(int(self.width() * dpr), int(self.height() * dpr))
            pm.setDevicePixelRatio(dpr)
            pm.fill(Qt.GlobalColor.transparent)
            q = QPainter(pm)
            q.setRenderHint(QPainter.RenderHint.Antialiasing)
            q.setPen(Qt.PenStyle.NoPen)
            col = QColor(theme.pal["text"])
            col.setAlpha(34)
            q.setBrush(col)
            yy = r.top() + 14
            while yy < r.bottom():
                xx = r.left() + 14
                while xx < r.right():
                    q.drawEllipse(QPointF(xx, yy), 0.9, 0.9)
                    xx += 16
                yy += 16
            q.end()
            self._dots_pm, self._dots_key = pm, key
        return self._dots_pm


class FileRow(QFrame):
    removed = pyqtSignal(str)

    def __init__(self, path: str, size: int) -> None:
        super().__init__()
        self.setObjectName("frow")
        self.setFixedHeight(56)
        lay = hbox(self, (0, 0, 0, 0), 14)
        name = os.path.basename(path)
        lay.addWidget(ExtTile(name, 38), 0, Qt.AlignmentFlag.AlignVCenter)
        nm = ElidedLabel(name)
        nm.setTextFormat(Qt.TextFormat.PlainText)      # untrusted filename: plain text
        nm.setStyleSheet("font-size: 14px; font-weight: 500;")
        lay.addWidget(nm, 1)
        sz = label(human_size(size), "muted")
        sz.setStyleSheet("font-size: 13px;")
        lay.addWidget(sz)
        rm = Button("", "ghost", "x", "icon")
        rm.setToolTip("Remove")
        rm.clicked.connect(lambda: self.removed.emit(path))
        lay.addWidget(rm)
        self._path = path

    def contextMenuEvent(self, e) -> None:  # noqa: N802
        m = RoundMenu(parent=self)
        a_show = m.addAction("Show in folder")
        a_copy = m.addAction("Copy path")
        m.addSeparator()
        a_rm = m.addAction("Remove from list")
        act = m.exec(e.globalPos())
        if act is a_show:
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(self._path)))
        elif act is a_copy:
            QGuiApplication.clipboard().setText(self._path)
        elif act is a_rm:
            self.removed.emit(self._path)


class CapacityBar(QWidget):
    """Segmented capacity bar: every file is a segment that fills a share of the 5 GB."""

    def __init__(self) -> None:
        super().__init__()
        self._sizes: list[int] = []
        self._cap = 1
        self.setFixedHeight(12)
        theme.changed.connect(self.update)

    def set_data(self, sizes: list[int], cap: int) -> None:
        self._sizes, self._cap = list(sizes), max(1, cap)
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = theme.pal
        w, h = self.width(), self.height()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(c["surface_hi"] if not theme.dark else c["surface3"]))
        p.drawRoundedRect(QRectF(0, 0, w, h), h / 2, h / 2)
        total = sum(self._sizes)
        if total <= 0:
            return
        over = total > self._cap
        gap = 3.0
        scale = w / max(total, self._cap)
        x = 0.0
        n = len(self._sizes)
        base = QColor(c["danger"] if over else c["accent"])
        tints = (0.0, 0.5, 0.25, 0.7)
        if n * (8.0 + gap) > w:               # hundreds of files: one solid fill instead
            p.setBrush(base)
            p.drawRoundedRect(QRectF(0, 0, max(8.0, total * scale), h), h / 2, h / 2)
            return
        for i, sz in enumerate(self._sizes):
            sw = max(8.0, sz * scale - (gap if i < n - 1 else 0.0))
            col = QColor(base)
            t = tints[i % len(tints)]
            if t:
                col = QColor(int(col.red() + (255 - col.red()) * t),
                             int(col.green() + (255 - col.green()) * t),
                             int(col.blue() + (255 - col.blue()) * t))
            p.setBrush(col)
            p.drawRoundedRect(QRectF(x, 0, sw, h), h / 2, h / 2)
            x += sw + gap


# ---------------------------------------------------------------- job model
class UploadJob:
    """One transfer: several files -> one share link. Many jobs can be queued."""
    _seq = 0

    def __init__(self, files: list[str], sizes: dict, email: str,
                 scheduled_at: float = 0.0) -> None:
        UploadJob._seq += 1
        self.id = UploadJob._seq
        self.files = list(files)
        self.sizes = dict(sizes)             # path -> bytes (kept for restart restore)
        self.total = sum(sizes[f] for f in files)
        self.email = email
        self.state = "scheduled" if scheduled_at > time.monotonic() else "queued"
        # queued | scheduled | running | done | failed
        self.scheduled_at = scheduled_at
        self.retries = 0
        self.created_at = time.time()       # wall clock, for the Queue date filters
        self.finished_at = 0.0
        self.pct = 0
        self.step = -1
        self.error = ""
        self.link = ""
        self.started = 0.0
        self.elapsed = 0.0
        self.thread: Optional[UploadThread] = None
        self.data: dict = {}
        # speed tracking
        self.speed = 0.0                    # bytes/s (smoothed)
        self.speed_samples: list[float] = []
        self._last_t = 0.0
        self._last_bytes = 0.0
        self.sent = 0                       # real bytes confirmed by the site (0 = unknown)

    @property
    def title(self) -> str:
        names = [os.path.basename(f) for f in self.files]
        return names[0] + (f"  +{len(names) - 1} more" if len(names) > 1 else "")

    # ---------------------------------------------------------------- speed
    def _on_pct(self, pct: int) -> None:
        now = time.monotonic()
        # real bytes scraped from the site beat the pct-derived estimate
        approx_bytes = float(self.sent) if self.sent else self.total * max(0, min(100, pct)) / 100.0
        if self._last_t and now > self._last_t:
            dt = now - self._last_t
            db = approx_bytes - self._last_bytes
            inst = max(0.0, db / dt) if dt > 0.01 else self.speed
            self.speed = inst if self.speed == 0 else self.speed * 0.7 + inst * 0.3
            self.speed_samples.append(self.speed)
            if len(self.speed_samples) > 64:
                self.speed_samples = self.speed_samples[-64:]
        self._last_t = now
        self._last_bytes = approx_bytes

    @property
    def eta(self) -> float:
        if self.speed > 0 and self.pct < 100:
            remaining = self.total * (100 - self.pct) / 100.0
            return remaining / self.speed
        return 0.0


class JobCard(QFrame):
    """Live card for one transfer (scheduled / queued / uploading / ready / failed)."""
    cancel = pyqtSignal(int)
    retry = pyqtSignal(int)
    dismiss = pyqtSignal(int)
    copy = pyqtSignal(int)
    qr = pyqtSignal(int)

    def __init__(self, job: UploadJob) -> None:
        super().__init__()
        self.job = job
        self.setObjectName("row")
        lay = vbox(self, (14, 12, 14, 12), 7)
        top = hbox(spacing=10)
        self.badge = Badge("cloud-upload", 38)
        top.addWidget(self.badge)
        col = vbox(spacing=1)
        self.title = ElidedLabel(job.title)
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        self.sub = label("", "caption")
        col.addWidget(self.title)
        col.addWidget(self.sub)
        top.addLayout(col, 1)
        self.pill = Pill("")
        top.addWidget(self.pill)
        self.btn_cancel = Button("", "ghost", "x", "icon")
        self.btn_cancel.setToolTip("Cancel")
        self.btn_cancel.clicked.connect(lambda: self.cancel.emit(job.id))
        top.addWidget(self.btn_cancel)
        lay.addLayout(top)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        lay.addWidget(self.bar)

        # live stats row: step text | speed | ETA
        stats = hbox(spacing=12)
        self.step = label("", "caption")
        self.step.setTextFormat(Qt.TextFormat.PlainText)   # can carry an error message
        stats.addWidget(self.step, 1)
        self.speed_lb = label("", "caption")
        self.speed_lb.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        stats.addWidget(self.speed_lb)
        self.eta_lb = label("", "caption")
        self.eta_lb.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.eta_lb.setMinimumWidth(90)
        stats.addWidget(self.eta_lb)
        lay.addLayout(stats)

        self.graph = SpeedGraph()
        self.graph.set_values(job.speed_samples)
        lay.addWidget(self.graph)

        self.link_row = QWidget()
        lr = hbox(self.link_row, spacing=8)
        self.link_edit = LineEdit()
        self.link_edit.setReadOnly(True)
        lr.addWidget(self.link_edit, 1)
        self.btn_copy = Button(tr("btn.copy"), "primary", "copy", "sm")
        self.btn_copy.clicked.connect(lambda: self.copy.emit(job.id))
        self.btn_open = Button(tr("btn.open"), "", "external", "sm")
        self.btn_open.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(job.link)))
        self.btn_qr = Button(tr("btn.qr"), "", "qr", "sm")
        self.btn_qr.clicked.connect(lambda: self.qr.emit(job.id))
        for b in (self.btn_copy, self.btn_open, self.btn_qr):
            lr.addWidget(b)
        lay.addWidget(self.link_row)

        self.btn_row = QWidget()
        br = hbox(self.btn_row, spacing=8)
        br.addStretch(1)
        self.btn_retry = Button(tr("btn.retry"), "", "refresh", "sm")
        self.btn_retry.clicked.connect(lambda: self.retry.emit(job.id))
        self.btn_dismiss = Button(tr("btn.dismiss"), "ghost", "", "sm")
        self.btn_dismiss.clicked.connect(lambda: self.dismiss.emit(job.id))
        br.addWidget(self.btn_retry)
        br.addWidget(self.btn_dismiss)
        lay.addWidget(self.btn_row)
        self.refresh()

    def refresh(self) -> None:
        j = self.job
        n = len(j.files)
        meta = f"{n} file{'s' if n != 1 else ''} · {human_size(j.total)}"
        running, queued = j.state == "running", j.state == "queued"
        scheduled = j.state == "scheduled"
        done, failed = j.state == "done", j.state == "failed"
        self.bar.setVisible(running or queued)
        self.graph.setVisible(running)
        self.step.setVisible(running or queued or scheduled or failed)
        self.speed_lb.setVisible(running)
        self.eta_lb.setVisible(running)
        self.link_row.setVisible(done)
        self.btn_row.setVisible(done or failed)
        self.btn_retry.setVisible(failed)
        self.btn_cancel.setVisible(running or queued or scheduled)
        self.btn_dismiss.setText(tr("btn.dismiss"))
        if scheduled:
            left = max(0, int(j.scheduled_at - time.monotonic()))
            self.pill.setText(tr("up.scheduled"))
            self.pill.set_tone("accent")
            self.badge.set_icon("timer")
            self.sub.setText(meta)
            self.step.setText(tr("up.starts_in", t=eta_text(left)))
        elif queued:
            self.pill.setText(tr("up.queued"))
            self.pill.set_tone("")
            self.badge.set_icon("cloud-upload")
            self.sub.setText(meta)
            self.step.setText(tr("up.waiting"))
            self.bar.setValue(0)
        elif running:
            self.pill.setText(f"{j.pct}%")
            self.pill.set_tone("accent")
            if j.sent > 0:
                meta += f" · {tr('up.sent_of', sent=human_size(j.sent), total=human_size(j.total))}"
            self.sub.setText(meta + f" · {int(time.monotonic() - j.started)}s")
            label_txt = STEPS[j.step][1] if 0 <= j.step < len(STEPS) else "Starting…"
            self.step.setText(label_txt)
            self.bar.setValue(j.pct)
            self.speed_lb.setText(speed_text(j.speed) if j.speed > 0 else "—")
            self.eta_lb.setText(f"{eta_text(j.eta)} {tr('dl.left')}" if j.eta > 0 else "")
            self.graph.set_values(j.speed_samples)
        elif done:
            self.pill.setText(tr("up.ready"))
            self.pill.set_tone("success")
            self.badge.set_icon("check-circle")
            self.sub.setText(meta + f" · created in {j.elapsed:.0f}s")
            self.link_edit.setText(j.link)
            self.link_edit.setCursorPosition(0)
        elif failed:
            self.pill.setText(tr("up.failed"))
            self.pill.set_tone("danger")
            self.badge.set_icon("alert-circle")
            self.sub.setText(meta)
            self.step.setText(j.error)
            self.step.setWordWrap(True)
            self.step.setProperty("role", "danger")
            repolish(self.step)


# --------------------------------------------------------------------- page
class UploadPage(Page):
    key = "upload"
    MAX_ROWS = 60                      # rows drawn in the list (every file is still sent)
    BIG_DROP = 2000                    # above this, batches run in CHUNK-sized slices
    CHUNK = 500                        # …so the event loop gets a breath between them
    overline_key = "nav.transfer"
    title_key = "up.title"
    subtitle_key = "up.subtitle"

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        self._scans: list[FolderScanThread] = []   # folders being read in the background
        self._zip: Optional[ZipThread] = None            # smart-ZIP packaging in the background
        self._sel_lock = threading.RLock()               # guards the 20-item admission
        self.files: list[str] = []
        self._sizes: dict[str, int] = {}
        self.jobs: list[UploadJob] = []
        self.cards: dict[int, JobCard] = {}
        self._closing = False
        self._restoring = False
        self._orphans: list[UploadThread] = []     # threads of removed jobs still winding down
        self._email = random_email()

        self._build_compose()
        self._build_jobs()
        self.body.addStretch(1)

        self._tick = QTimer(self)
        self._tick.setInterval(500)          # snappy: elapsed / ETA / scheduled pumps
        self._tick.timeout.connect(self._on_tick)
        theme.changed.connect(self._refresh_limit)
        self._refresh_compose()
        self._restore_queue()                # bring back last session's transfers
        ziputil.sweep_stale(keep=tuple(f for j in self.jobs for f in j.files))
        HUB.attach(self)                     # Queue page observes these jobs

    # ------------------------------------------------------------- compose
    def _build_compose(self) -> None:
        page = QWidget()
        page.setObjectName("page")
        row = hbox(page, spacing=16)
        row.setAlignment(Qt.AlignmentFlag.AlignTop)

        left = vbox(spacing=14)
        self.drop = DropZone()
        self.drop.files_dropped.connect(self.add_paths)
        self.drop.browse.connect(self.browse)
        self.drop.browse_folder.connect(self.browse_folder)
        left.addWidget(self.drop)

        self.files_card = QFrame()
        self.files_card.setObjectName("card")
        fl = vbox(self.files_card, (26, 22, 26, 14), 8)
        head = hbox()
        self.files_title = label(tr("up.selected"), "h2")
        head.addWidget(self.files_title)
        head.addStretch(1)
        self.clear_btn = Button(tr("up.clear_all"), "ghost", "", "sm")
        self.clear_btn.clicked.connect(self.clear_files)
        head.addWidget(self.clear_btn)
        fl.addLayout(head)
        self.limit_label = label("", "caption", wrap=True)      # "Files selected: 12 / 20"
        self.limit_label.setObjectName("limit_label")
        fl.addWidget(self.limit_label)
        self.capacity = CapacityBar()
        fl.addWidget(self.capacity)
        self.meter_text = label("", "caption")
        fl.addWidget(self.meter_text)
        fl.addSpacing(2)
        self.rows_lay = vbox(spacing=0)
        fl.addLayout(self.rows_lay)
        self.meter = QProgressBar()            # kept for compatibility; the capacity bar replaces it
        self.meter.setRange(0, 1000)
        self.meter.hide()
        # ZIP packaging progress (hidden unless a smart ZIP is being built)
        self.pack_row = QFrame()
        self.pack_row.setObjectName("card")
        pk = vbox(self.pack_row, (26, 16, 26, 16), 8)
        prow = hbox(spacing=10)
        self.pack_label = label("", "h2")
        prow.addWidget(self.pack_label, 1)
        self.pack_cancel = Button("Cancel", "ghost", "", "sm")
        self.pack_cancel.clicked.connect(self._cancel_zip)
        prow.addWidget(self.pack_cancel)
        pk.addLayout(prow)
        self.pack_bar = QProgressBar()
        self.pack_bar.setRange(0, 1000)
        self.pack_bar.setTextVisible(False)
        self.pack_bar.setFixedHeight(6)
        pk.addWidget(self.pack_bar)
        self.pack_file = label("", "caption")
        pk.addWidget(self.pack_file)
        self.pack_row.hide()
        left.addWidget(self.pack_row)
        left.addWidget(self.files_card)
        left.addStretch(1)
        row.addLayout(left, 7)

        right = vbox(spacing=14)
        panel = QFrame()
        panel.setObjectName("card")
        pl = vbox(panel, (26, 22, 26, 20), 0)

        # -- sender
        self.sender_title = label(tr("up.sender"), "h2")
        pl.addWidget(self.sender_title)
        pl.addSpacing(12)
        self.mode = Segmented([("random", tr("up.random")), ("fixed", tr("up.my_email"))],
                              self.ctx.settings.get("sender_mode", "random"))
        self.mode.changed.connect(self._on_mode)
        pl.addWidget(self.mode, 0, Qt.AlignmentFlag.AlignLeft)
        pl.addSpacing(12)
        self.email_edit = LineEdit()
        self.email_edit.setPlaceholderText("you@example.com")
        self.email_edit.editingFinished.connect(self._on_email_edit)
        pl.addWidget(self.email_edit)
        # the regenerate control lives inside the field, at its trailing edge
        self.regen = self.email_edit.addAction(QIcon(), QLineEdit_Trailing)
        self.regen.setToolTip("New random sender")
        self.regen.triggered.connect(self._regen)
        self._regen_icon()
        theme.changed.connect(self._regen_icon)
        self.email_hint = label("", "caption", wrap=True)
        self.email_hint.hide()                 # the hint stays in the tooltip, mockup shows none
        pl.addWidget(self.email_hint)
        pl.addSpacing(18)
        pl.addWidget(hline())
        pl.addSpacing(18)

        # -- when to send
        self.sched_title = label(tr("up.schedule"), "h2")
        pl.addWidget(self.sched_title)
        pl.addSpacing(12)
        self.sched_mode = Segmented([("now", tr("up.now")), ("later", tr("up.later"))], "now")
        self.sched_mode.changed.connect(self._on_sched_mode)
        pl.addWidget(self.sched_mode, 0, Qt.AlignmentFlag.AlignLeft)
        self._when_dt = datetime.now().replace(second=0, microsecond=0) + timedelta(minutes=10)
        self.when = Button("", "ghost", "calendar-clock", "sm")
        self.when.setToolTip(tr("sched.title"))
        self.when.clicked.connect(self._pick_when)
        self.when.hide()
        pl.addSpacing(6)
        pl.addWidget(self.when, 0, Qt.AlignmentFlag.AlignLeft)
        self._sync_when_label()
        # auto-retry lives in Settings → Transfers; the switch stays alive (hidden) so the setting is honoured
        self.auto_retry = Switch(bool(self.ctx.settings.get("auto_retry", True)))
        self.auto_retry.toggled.connect(lambda v: self.ctx.settings.set("auto_retry", v))
        self.auto_retry.hide()
        self.retry_lbl = label(tr("up.auto_retry"), "muted")
        self.retry_lbl.hide()
        pl.addSpacing(18)
        pl.addWidget(hline())
        pl.addSpacing(16)

        # -- summary (no title: three quiet key/value rows)
        self.summary_title = label(tr("up.summary"), "h2")
        self.summary_title.hide()
        self.sum_files = self._kv(pl, tr("up.files"))
        pl.addSpacing(10)
        self.sum_size = self._kv(pl, tr("up.total_size"))
        pl.addSpacing(10)
        self._kv(pl, tr("up.link_expiry"), "7 days")
        right.addWidget(panel)

        self.go = Button(tr("up.get_link"), "signal", "zap", "xl")
        self.go.clicked.connect(self.start)
        self._go_fx = QGraphicsDropShadowEffect(self.go)
        self._go_fx.setBlurRadius(30)
        self._go_fx.setOffset(0, 10)
        self._go_fx_color()
        theme.changed.connect(self._go_fx_color)
        self.go.setGraphicsEffect(self._go_fx)
        right.addWidget(self.go)
        self.go_hint = label(tr("up.go_hint"), "caption")
        self.go_hint.setWordWrap(True)
        self.go_hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        right.addWidget(self.go_hint, 0, Qt.AlignmentFlag.AlignHCenter)
        right.addStretch(1)
        row.addLayout(right, 5)
        self.body.addWidget(page)

    def _regen_icon(self) -> None:
        self.regen.setIcon(QIcon(icons.pixmap("refresh", theme.pal["muted"], 16, 1.6)))

    def _go_fx_color(self) -> None:
        col = QColor(theme.pal["accent"])
        col.setAlpha(80 if not theme.dark else 70)
        self._go_fx.setColor(col)

    @staticmethod
    def _kv(lay, key: str, value: str = "—") -> QLabel:
        r = hbox()
        r.addWidget(label(key, "muted"))
        r.addStretch(1)
        v = label(value)
        v.setStyleSheet("font-weight: 600;")
        r.addWidget(v)
        lay.addLayout(r)
        return v

    # ---------------------------------------------------------------- jobs
    def _build_jobs(self) -> None:
        self.jobs_card = QFrame()
        self.jobs_card.setObjectName("card")
        jl = vbox(self.jobs_card, (18, 16, 18, 16), 12)
        head = hbox(spacing=10)
        self.jobs_title = label(tr("up.transfers"), "h2")
        head.addWidget(self.jobs_title)
        head.addStretch(1)
        self.open_queue = Button(tr("up.open_queue"), "ghost", "queue", "sm")
        self.open_queue.clicked.connect(lambda: self.ctx.navigate("queue"))
        head.addWidget(self.open_queue)
        self.clear_done = Button(tr("up.clear_finished"), "ghost", "", "sm")
        self.clear_done.clicked.connect(self.clear_finished)
        head.addWidget(self.clear_done)
        jl.addLayout(head)
        self.jobs_lay = vbox(spacing=10)
        jl.addLayout(self.jobs_lay)
        self.body.addWidget(self.jobs_card)
        self.jobs_card.hide()

    # ============================================================ file logic
    def browse(self) -> None:
        if not self._can_add():
            return
        paths, _ = QFileDialog.getOpenFileNames(self, tr("up.choose_files"))
        if paths:
            self.add_paths(paths)

    def browse_folder(self) -> None:
        if not self._can_add():
            return
        folder = QFileDialog.getExistingDirectory(self, tr("up.choose_folder"))
        if folder:
            self.add_paths([folder])

    def add_paths(self, paths: list[str]) -> None:
        """Add files; any folder is opened and every file inside it is added (no zipping)."""
        self._add_batch(paths)

    def _slots_left(self) -> int:
        return max(0, limits.MAX_FILES_PER_TRANSFER - len(self.files))

    def _busy_packaging(self) -> bool:
        return self._zip is not None

    def _can_add(self) -> bool:
        """UI-level guard (the real enforcement is in ``_commit`` and the engine)."""
        if self._busy_packaging():
            self.ctx.toast("A ZIP archive is being created — please wait.", "info")
            return False
        if self._slots_left() <= 0:
            self.ctx.toast(f"This transfer already holds {limits.MAX_FILES_PER_TRANSFER} items. "
                           "Start it first, or remove an item.", "warning")
            return False
        return True

    def _add_batch(self, paths: list[str], source: str = "", trusted: bool = False) -> int:
        """Add files to the transfer (max ``MAX_FILES_PER_TRANSFER`` items).

        Pipeline: folder expansion -> ``filesafety.screen_batch`` (links, sensitive
        names; ``trusted`` skips the sensitive prompt only) -> admission against the
        20-item limit.  An overflow is offered as one ZIP.  Returns the number added
        synchronously (0 when a folder scan / ZIP is still running).
        """
        if self._closing or sip.isdeleted(self):
            return 0
        skipped = {"unreadable": 0, "empty": 0, "big": 0}
        cands: list[str] = []
        sizes: dict[str, int] = {}
        flag_set: set[str] = set()
        paths = list(paths)
        if len(paths) > self.BIG_DROP:          # huge drop: yield between chunks
            for i in range(0, len(paths), self.CHUNK):
                self._collect(paths[i:i + self.CHUNK], skipped, trusted, cands, sizes, flag_set)
                QApplication.processEvents()
        else:
            self._collect(paths, skipped, trusted, cands, sizes, flag_set)
        ctx = {"sizes": sizes, "flag_set": flag_set, "skipped": skipped, "source": source}
        if not cands:
            self._report_skips(ctx)
            self._refresh_compose()
            return 0
        return self._admit(cands, ctx)

    def _collect(self, paths: list[str], skipped: dict, trusted: bool, cands: list[str],
                 sizes: dict[str, int], flag_set: set[str]) -> None:
        """Cheap per-file checks for one bounded chunk; no content is read here."""
        files: list[str] = []
        for p in paths:
            ap = os.path.abspath(p)
            if os.path.isdir(ap):
                self._scan_folder(ap)           # folders expand in the background
            else:
                files.append(ap)
        if trusted:
            allowed = files
        else:
            allowed, flagged = screen_batch(files, "")
            skipped["unreadable"] += len(files) - len(allowed)   # SKIP: links, unreadable
            flag_set.update(s.rsplit(" — ", 1)[0] for s in flagged)
        seen = set(cands)
        for p in allowed:
            if p in self._sizes or p in seen:
                continue
            if not os.path.isfile(p) or not os.access(p, os.R_OK):
                skipped["unreadable"] += 1
                continue
            try:
                size = os.path.getsize(p)
            except OSError:
                skipped["unreadable"] += 1
                continue
            if size == 0:
                skipped["empty"] += 1
                continue
            seen.add(p)
            cands.append(p)
            sizes[p] = size

    def _report_skips(self, ctx: dict) -> None:
        skipped = ctx["skipped"]
        if skipped["big"]:
            self.ctx.toast(tr("up.over_size", n=skipped["big"]), "error")
        if skipped["unreadable"]:
            self.ctx.toast(tr("up.unreadable", n=skipped["unreadable"]), "error")

    # ---- admission: the 20-item limit -------------------------------------
    def _admit(self, paths: list[str], ctx: dict) -> int:
        with self._sel_lock:
            plan = limits.plan_admission(self.files, paths)
        decision = "add"
        if plan.overflow:
            decision = self._confirm_overflow(paths, plan)
        if decision == "zip":
            self._start_zip(paths, ctx)
            return 0
        if decision != "add":
            self._report_skips(ctx)
            self._refresh_compose()
            return 0
        return self._commit(paths, ctx)

    def _commit(self, paths: list[str], ctx: dict) -> int:
        """The single place that appends to the selection.  Re-plans under the lock, so
        concurrent callbacks can never push the list past the limit."""
        sizes, flag_set, skipped = ctx["sizes"], ctx["flag_set"], ctx["skipped"]
        added = sensitive = over = 0
        with self._sel_lock:
            plan = limits.plan_admission(self.files, paths)
            total = sum(self._sizes.values())
            for p in plan.accepted:
                size = sizes.get(p) or self._file_size(p)
                if size <= 0:
                    skipped["unreadable"] += 1
                    continue
                if total + size > MAX_BYTES:
                    skipped["big"] += 1
                    continue
                self.files.append(p)
                self._sizes[p] = size
                total += size
                added += 1
                sensitive += p in flag_set
            over = len(plan.overflow)
        if ctx.get("source") and added:
            self.ctx.toast(tr("up.added_from", n=added, name=ctx["source"]), "success")
        if sensitive:
            self.ctx.toast(f"{sensitive} file(s) look sensitive — double-check before "
                           "sending", "warning")
        if over:
            self.ctx.toast(f"Limit of {limits.MAX_FILES_PER_TRANSFER} items reached — "
                           f"{over} file(s) were not added.", "warning")
        self._report_skips(ctx)
        self._refresh_compose()
        return added

    @staticmethod
    def _file_size(path: str) -> int:
        try:
            return os.path.getsize(path)
        except OSError:
            return 0

    def _confirm_overflow(self, paths: list[str], plan) -> str:
        """Ask what to do when a batch would exceed the 20-item limit.

        Returns ``"zip"`` (package the files into one archive) or ``"cancel"``;
        ``"add"`` keeps only what fits.  Tests override this to simulate the choice.
        """
        n = len(paths)
        can_zip = n >= 1 and len(self.files) + 1 <= limits.MAX_FILES_PER_TRANSFER
        try:
            from ..dialogs import FramelessDialog
            d = FramelessDialog(self.window(), 480, (24, 22, 24, 20))
            d.body.addWidget(label("Too many files for one transfer", "h2"))
            d.body.addSpacing(4)
            if can_zip:
                d.body.addWidget(label(
                    f"You selected {n} files. Transfer Now supports a maximum of "
                    f"{limits.MAX_FILES_PER_TRANSFER} upload items per transfer. "
                    "Your files will be combined into one ZIP archive.", "muted", wrap=True))
            else:
                d.body.addWidget(label(
                    f"This transfer already holds {len(self.files)} of {limits.MAX_FILES_PER_TRANSFER} "
                    "items, so there is no room for a ZIP archive. Start a separate transfer, or "
                    "remove items from the current selection first.", "muted", wrap=True))
            d.body.addSpacing(10)
            row = hbox(spacing=10)
            row.addStretch(1)
            result = {"v": "cancel"}
            cancel = Button("Cancel" if can_zip else "Close", "")
            cancel.clicked.connect(d.reject)
            row.addWidget(cancel)
            if can_zip:
                go = Button("Create ZIP and Upload", "primary")

                def pick() -> None:
                    result["v"] = "zip"
                    d.accept()
                go.clicked.connect(pick)
                go.setDefault(True)
                row.addWidget(go)
            d.body.addLayout(row)
            d.exec()
            return result["v"]
        except Exception:                   # noqa: BLE001
            return "cancel"

    # ---- smart ZIP ---------------------------------------------------------
    def _start_zip(self, paths: list[str], ctx: dict) -> None:
        if self._zip is not None or not paths:
            return
        th = ZipThread(paths, self)
        self._zip = th
        self._zip_n = len(paths)
        self.pack_label.setText(f"Creating ZIP archive ({len(paths)} files)…")
        self.pack_bar.setValue(0)
        self.pack_file.setText("")
        self.pack_cancel.setEnabled(True)
        self.pack_row.show()
        th.progress.connect(self._zip_progress)
        th.done.connect(lambda res, t=th: self._zip_done(t, res))
        th.failed.connect(lambda m, t=th: self._zip_failed(t, m))
        th.cancelled.connect(lambda t=th: self._zip_cancelled(t))
        th.start()
        self._refresh_compose()

    def _cancel_zip(self) -> None:
        if self._zip is not None:
            self.pack_cancel.setEnabled(False)
            self._zip.stop()

    def _zip_progress(self, done: int, total: int, name: str) -> None:
        if self._closing or sip.isdeleted(self):
            return
        self.pack_bar.setValue(int(1000 * done / total) if total else 0)
        self.pack_file.setText(name)

    def _zip_finish(self, th: ZipThread) -> None:
        if self._zip is th:
            self._zip = None
        try:
            th.deleteLater()
        except RuntimeError:
            pass
        if not self._closing and not sip.isdeleted(self):
            self.pack_row.hide()

    def _zip_done(self, th: ZipThread, res) -> None:
        self._zip_finish(th)
        if self._closing or sip.isdeleted(self):
            ziputil.cleanup_archive(res.path)
            return
        with self._sel_lock:
            fits = len(self.files) + 1 <= limits.MAX_FILES_PER_TRANSFER
            if fits and res.size <= MAX_BYTES - sum(self._sizes.values()):
                self.files.append(res.path)
                self._sizes[res.path] = res.size
                ok = True
            else:
                ok = False
        if not ok:
            ziputil.cleanup_archive(res.path)
            self.ctx.toast("The ZIP archive does not fit in this transfer (item count or size limit).",
                           "error")
            self._refresh_compose()
            return
        self.ctx.toast(f"{len(res.entries)} files were packaged into {os.path.basename(res.path)}.",
                       "success")
        self._refresh_compose()
        if self.go.isEnabled():
            self.start()                    # "Create ZIP and Upload"

    def _zip_failed(self, th: ZipThread, msg: str) -> None:
        self._zip_finish(th)
        if not self._closing and not sip.isdeleted(self):
            self.ctx.toast(msg or "The ZIP archive could not be created.", "error")
            self._refresh_compose()

    def _zip_cancelled(self, th: ZipThread) -> None:
        self._zip_finish(th)
        if not self._closing and not sip.isdeleted(self):
            self.ctx.toast("ZIP creation cancelled — no files were changed.", "info")
            self._refresh_compose()

    def _scan_folder(self, folder: str) -> None:
        name = os.path.basename(os.path.normpath(folder)) or folder
        t = FolderScanThread(folder, self)
        t.progress.connect(lambda f, n, th=t: self._scan_progress(th, n))
        t.done.connect(lambda f, files, flagged, skipped, th=t, nm=name:
                       self._scan_done(th, nm, f, files, flagged, skipped))
        t.failed.connect(lambda f, m, th=t, nm=name: self._scan_failed(th, nm))
        self._scans.append(t)
        self.ctx.toast(tr("up.reading_folder", name=name), "info")
        t.start()
        self._refresh_compose()

    def _scan_progress(self, th: FolderScanThread, n: int) -> None:
        """Throttled progress from the scan thread — cheap go-button update only."""
        if self._closing or sip.isdeleted(self):
            return
        if self._scans:
            self.go.setText(f"{tr('btn.preparing')} ({n})")

    def _scan_finish(self, th: FolderScanThread) -> None:
        if th is not None and th in self._scans:
            self._scans.remove(th)
        if th is not None:
            try:
                th.deleteLater()
            except RuntimeError:
                pass
        if not self._closing and not sip.isdeleted(self):
            self._refresh_compose()         # never leave “Get link” stuck on “Preparing…”

    def _scan_done(self, th: Optional[FolderScanThread], name: str, folder: str,
                   files: list, flagged: list, skipped: int = 0) -> None:
        self._scan_finish(th)
        if self._closing or sip.isdeleted(self):   # shutdown may have raced the signal
            return
        if not files and not flagged:
            self.ctx.toast(tr("up.folder_empty", name=name), "error")
            self._refresh_compose()
            return
        if files:
            self._add_batch(files, source=name)
        if flagged and self._confirm_flagged(folder or name, flagged):
            self._add_batch(self._flagged_paths(flagged), source=name, trusted=True)

    def _scan_failed(self, th: FolderScanThread, name: str) -> None:
        self._scan_finish(th)
        if self._closing or sip.isdeleted(self):
            return
        self.ctx.toast(tr("up.folder_failed", name=name), "error")
        self._refresh_compose()

    @staticmethod
    def _flagged_paths(flagged: list[str]) -> list[str]:
        """``"path — reason"`` strings → the paths (reason separator: em dash)."""
        return [s.rsplit(" — ", 1)[0] for s in flagged if s]

    def _confirm_flagged(self, folder: str, flagged: list[str]) -> bool:
        """Non-blocking prompt about files the scanner flagged as sensitive.

        The allowed files are already in the list; flagged ones wait for an
        explicit decision.  The dialog stays non-modal — the compose area keeps
        working either way.  Returns True only when the flagged files were
        added right away; the real dialog adds them from its own button, so it
        always returns False here (tests override this method to simulate the
        user's choice).
        """
        try:
            from ..dialogs import FramelessDialog
            d = FramelessDialog(self.window(), 500, (24, 22, 24, 20))
            d.setModal(False)               # a prompt, not a roadblock
            self._flag_dialog = d
            d.body.addWidget(label(f"Skipped {len(flagged)} sensitive "
                                   f"file{'s' if len(flagged) != 1 else ''} from {folder}",
                                   "h2", wrap=True))
            d.body.addSpacing(2)
            d.body.addWidget(label("These names can hide credentials or private data. "
                                   "Send them only if you really mean to.", "muted",
                                   wrap=True))
            d.body.addSpacing(8)
            list_box = QFrame()
            list_box.setObjectName("inner")
            ll = vbox(list_box, (12, 10, 12, 10), 6)
            for line in flagged[:20]:       # plain-text paths with their reason
                ll.addWidget(label(line, "caption", wrap=True))
            if len(flagged) > 20:
                ll.addWidget(label(tr("up.more_files", n=len(flagged) - 20), "muted"))
            area = QScrollArea()
            area.setWidgetResizable(True)
            area.setFrameShape(QFrame.Shape.NoFrame)
            area.setWidget(list_box)
            area.setMaximumHeight(240)
            enable_smooth_scroll(area)
            d.body.addWidget(area, 1)
            d.body.addSpacing(10)
            row = hbox(spacing=10)
            row.addStretch(1)
            keep = Button("Keep them out", "")
            keep.setDefault(True)
            keep.clicked.connect(d.reject)

            def add_anyway() -> None:
                d.accept()
                self._add_batch(self._flagged_paths(flagged), source=folder, trusted=True)

            add = Button("Add them anyway", "primary")
            add.clicked.connect(add_anyway)
            row.addWidget(keep)
            row.addWidget(add)
            d.body.addLayout(row)
            keep.setFocus()
            d.show()                        # non-blocking: no exec()
            return False
        except Exception:                   # noqa: BLE001 — never lose files over a dialog
            return False

    def _cleanup_tmp(self) -> None:
        """Kept for the callers that tidy up after a transfer; nothing is written to disk now."""

    def remove_file(self, path: str) -> None:
        with self._sel_lock:
            if path in self._sizes:
                self.files.remove(path)
                del self._sizes[path]
                ziputil.cleanup_archive(path)          # no-op for anything but our own archives
        self._refresh_compose()

    def clear_files(self) -> None:
        with self._sel_lock:
            gone = list(self.files)
            self.files.clear()
            self._sizes.clear()
        for p in gone:
            ziputil.cleanup_archive(p)
        self._cleanup_tmp()
        self._refresh_compose()

    @staticmethod
    def _enter_row(row: QWidget, delay_ms: int) -> None:
        """Hold a new row invisible, then fade it in after ``delay_ms`` (stagger)."""
        from PyQt6.QtWidgets import QGraphicsOpacityEffect
        fx = QGraphicsOpacityEffect(row)
        fx.setOpacity(0.0)
        row.setGraphicsEffect(fx)

        def go() -> None:
            try:
                motion.fade_in(row, 320, lambda: motion.clear_effect(row))
            except RuntimeError:                 # row was rebuilt/deleted meanwhile
                pass
        QTimer.singleShot(delay_ms + 60, go)

    def _refresh_limit(self) -> None:
        """'Files selected: 12 / 20' + remaining slots; locks the drop zone when full."""
        if sip.isdeleted(self):
            return
        n, cap = len(self.files), limits.MAX_FILES_PER_TRANSFER
        left = cap - n
        if left <= 0:
            txt = (f"Files selected: {n} / {cap} — limit reached. Start this transfer, remove an "
                   "item, or use a separate transfer to add more.")
            color = theme.pal["warning"]
        else:
            txt = f"Files selected: {n} / {cap} · {left} slot{'s' if left != 1 else ''} left"
            color = theme.pal["muted"]
        self.limit_label.setText(txt)
        self.limit_label.setStyleSheet(f"color: {color};")
        self.limit_label.setVisible(n > 0)
        full = left <= 0 or self._zip is not None
        self.drop.setEnabled(not full)
        self.drop.setToolTip("" if not full else
                             ("A ZIP archive is being created." if self._zip is not None else
                              f"Limit of {cap} items reached."))

    def _files_title(self) -> str:
        n = len(self.files)
        return f"{tr('up.selected')} · {n}" if n else tr("up.selected")

    def _refresh_compose(self) -> None:
        n = len(self.files)
        total = sum(self._sizes.values())
        self.files_card.setVisible(n > 0)
        self.drop.set_compact(n > 0)
        clear_layout(self.rows_lay)
        seen = getattr(self, "_row_paths", set())
        fresh = 0
        shown = self.files[:self.MAX_ROWS]      # a folder can hold thousands of files
        for i, p in enumerate(shown):
            if i:
                self.rows_lay.addWidget(hline())
            r = FileRow(p, self._sizes[p])
            r.removed.connect(self.remove_file)
            self.rows_lay.addWidget(r)
            if p not in seen:                    # newly added: staggered fade-in
                self._enter_row(r, fresh * 70)
                fresh += 1
        if n > len(shown):
            self.rows_lay.addWidget(hline())
            more = label(tr("up.more_files", n=n - len(shown)), "muted")
            more.setContentsMargins(0, 12, 0, 8)
            self.rows_lay.addWidget(more)
        self._row_paths = set(shown)
        self.files_title.setText(self._files_title())
        self.meter.setValue(int(1000 * total / MAX_BYTES) if total else 0)
        self.capacity.set_data([self._sizes[p] for p in self.files], MAX_BYTES)
        self.meter_text.setText(tr("up.size_used", used=human_size(total)))
        self.sum_files.setText(f"{n} / {limits.MAX_FILES_PER_TRANSFER}" if n else "—")
        self._refresh_limit()
        self.sum_size.setText(human_size(total) if n else "—")
        busy = bool(self._scans or self._zip)
        self.go.setEnabled(n > 0 and not busy)
        self.go.setText(tr("btn.preparing") if busy else tr("up.get_link"))
        self.go_hint.setText(tr("up.go_eta", size=human_size(total)))
        self.go_hint.setVisible(n > 0)
        self._go_fx.setEnabled(self.go.isEnabled())
        self._refresh_sender()

    # ----------------------------------------------------------- sender
    def _refresh_sender(self) -> None:
        fixed = self.mode.value() == "fixed"
        self.regen.setVisible(not fixed)
        self.email_edit.setReadOnly(not fixed)
        if fixed:
            self.email_edit.setText(self.ctx.settings.get("fixed_email", ""))
            self.email_edit.setCursorPosition(0)
            self.email_hint.setText(tr("up.hint_fixed"))
        else:
            self.email_edit.setText(self._email)
            self.email_edit.setCursorPosition(0)
            self.email_hint.setText(tr("up.hint_random"))
        self._validate_email()

    def _on_mode(self, key: str) -> None:
        self.ctx.settings.set("sender_mode", key)
        self._refresh_sender()

    def _regen(self) -> None:
        self._email = random_email()
        self._refresh_sender()

    def _on_email_edit(self) -> None:
        if self.mode.value() == "fixed":
            self.ctx.settings.set("fixed_email", self.email_edit.text().strip())
            self._validate_email()

    @staticmethod
    def _valid_email(text: str) -> bool:
        import re
        return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]{2,}", text.strip()))

    def _validate_email(self) -> bool:
        if self.mode.value() != "fixed":
            self.email_edit.setProperty("state", "")
            repolish(self.email_edit)
            return True
        ok = self._valid_email(self.email_edit.text())
        self.email_edit.setProperty("state", "ok" if ok else "error")
        repolish(self.email_edit)
        return ok

    def _sender_email(self) -> str:
        if self.mode.value() == "fixed":
            return self.email_edit.text().strip()
        return self._email

    # ------------------------------------------------------------ schedule
    def _sync_when_label(self) -> None:
        self.when.setText(self._when_dt.strftime("%a %d %b · %H:%M"))

    def _pick_when(self) -> None:
        """Open the custom date & time dialog (calendar + HH/MM steppers)."""
        from ..dialogs import pick_date_time
        dt = pick_date_time(self.window(), self._when_dt)
        if dt is not None:
            self._when_dt = dt
            self._sync_when_label()

    def _on_sched_mode(self, key: str) -> None:
        self.when.setVisible(key == "later")
        if key == "later":
            if self._when_dt <= datetime.now():
                self._when_dt = (datetime.now().replace(second=0, microsecond=0)
                                 + timedelta(minutes=10))
            self._sync_when_label()
            self._pick_when()               # choosing “Later” opens the picker

    # ------------------------------------------------------------ persistence
    def _save_queue(self) -> None:
        """Snapshot every not-running job so the queue survives a restart."""
        if self._restoring:
            return
        now_mono = time.monotonic()
        now_wall = time.time()
        out = []
        for j in self.jobs:
            if j.state == "running":
                continue                   # never resurrect a half-done upload
            entry = {
                "files": list(j.files),
                "sizes": {p: int(s) for p, s in j.sizes.items()},
                "email": j.email,
                "state": j.state,
                "total": int(j.total),
                "link": j.link,
                "error": j.error,
                "elapsed": round(float(j.elapsed), 1),
                "created_at": float(j.created_at),
                "finished_at": float(j.finished_at),
            }
            if j.state == "scheduled":
                entry["sched_epoch"] = now_wall + max(0.0, j.scheduled_at - now_mono)
            out.append(entry)
        self.ctx.settings.set("queue_cache", out[-40:])

    def _restore_queue(self) -> None:
        """Recreate the transfers saved by the previous session."""
        raw = self.ctx.settings.get("queue_cache", [])
        if not isinstance(raw, list) or not raw:
            return
        self._restoring = True
        restored = 0
        for e in raw[-40:]:
            try:
                if not isinstance(e, dict):
                    continue
                files = [p for p in (e.get("files") or []) if isinstance(p, str)]
                if not files:
                    continue
                saved_sizes = e.get("sizes") or {}
                sizes = {p: int(saved_sizes.get(p, 0) or 0) for p in files}
                state = e.get("state", "queued")
                if state not in ("queued", "scheduled", "done", "failed"):
                    state = "queued"
                sched_epoch = float(e.get("sched_epoch") or 0.0)
                sched_mono = (time.monotonic() + (sched_epoch - time.time())
                              if sched_epoch else 0.0)
                if state == "scheduled" and sched_mono <= time.monotonic():
                    state = "queued"           # slot passed while the app was closed
                job = UploadJob(files, sizes, e.get("email", ""),
                                scheduled_at=sched_mono if state == "scheduled" else 0.0)
                job.state = state
                job.error = e.get("error", "")
                job.elapsed = float(e.get("elapsed") or 0.0)
                job.created_at = float(e.get("created_at") or job.created_at)
                job.finished_at = float(e.get("finished_at") or 0.0)
                # restored links are re-validated before they can ever be
                # opened/copied/QR-shown (A4-03/L5): a tampered or corrupt
                # cache entry must not become a tappable URL
                from core.downloader import parse_transfer_id
                if not parse_transfer_id(e.get("link") or ""):
                    job.link = ""
                    if state == "done":
                        job.state, job.error = "failed", "Invalid link"
                else:
                    job.link = e.get("link", "")
                if state == "done" and not job.link:
                    job.state, job.error = "failed", job.error or "Link unavailable"
                if state in ("queued", "scheduled"):
                    missing = [p for p in files if not os.path.isfile(p)]
                    if missing:
                        job.state = "failed"
                        job.error = f"File no longer exists: {os.path.basename(missing[0])}"
                self._add_job(job)
                restored += 1
            except Exception:  # noqa: BLE001 — one bad entry must not break startup
                continue
        self._restoring = False
        self._save_queue()
        if restored:
            QTimer.singleShot(0, self._pump)   # resume whatever was waiting

    # ============================================================ job queue
    def _max_parallel(self) -> int:
        try:
            return max(1, min(4, int(self.ctx.settings.get("parallel_uploads", 2))))
        except (TypeError, ValueError):
            return 2

    def _active_jobs(self) -> list[UploadJob]:
        return [j for j in self.jobs if j.state in ("queued", "running", "scheduled")]

    def is_running(self) -> bool:
        return bool(self._active_jobs())

    def can_close(self) -> bool:
        return not self.is_running() and not self._orphans

    def trigger_primary(self) -> None:
        if self.go.isEnabled():
            self.start()

    def start(self) -> None:
        """Turn the selected files into a queued (or scheduled) transfer."""
        if not self.files or self._scans or self._zip:
            return
        if self.mode.value() == "fixed":
            self._on_email_edit()
            if not self._validate_email():
                self.ctx.toast("Enter a valid sender email first", "error")
                return
        try:
            limits.check_item_count(self.files)
        except ValueError as exc:
            self.ctx.toast(str(exc), "error")
            return
        missing = [p for p in self.files if not os.path.isfile(p)]
        if missing:
            self.ctx.toast(f"File no longer exists: {os.path.basename(missing[0])}", "error")
            for p in missing:
                self.remove_file(p)
            return
        sched_at = 0.0
        if self.sched_mode.value() == "later":
            dt = max(self._when_dt, datetime.now() + timedelta(minutes=1))
            sched_at = time.monotonic() + max(0.0, (dt - datetime.now()).total_seconds())
        job = UploadJob(self.files, self._sizes, self._sender_email(), scheduled_at=sched_at)
        self.files = []
        self._sizes = {}
        self._email = random_email()
        self._add_job(job)
        self._refresh_compose()
        if job.state == "scheduled":
            self.ctx.toast(tr("toast.scheduled",
                              time=self._when_dt.strftime("%H:%M")), "info")
        elif len(self._active_jobs()) > 1:
            self.ctx.toast(tr("toast.queued"), "info")
        else:
            self.ctx.toast(tr("toast.started"), "info")
        self.ctx.navigate("queue")          # transfers are shown on the Queue page

    def _add_job(self, job: UploadJob) -> None:
        self.jobs.append(job)
        card = JobCard(job)
        card.cancel.connect(self.cancel_job)
        card.retry.connect(self.retry_job)
        card.dismiss.connect(self.dismiss_job)
        card.copy.connect(self.copy_job_link)
        card.qr.connect(self.show_qr)
        self.cards[job.id] = card
        self.jobs_lay.insertWidget(0, card)          # newest first
        self.jobs_card.hide()           # transfers live on the Queue page
        self._update_jobs_header()
        QTimer.singleShot(0, self._scroll_to_jobs)   # keep the strip in view
        if not self._restoring:
            self._pump()
        HUB.jobs_changed.emit()
        self._save_queue()

    def _scroll_to_jobs(self) -> None:
        """Reveal the Transfers strip the moment a transfer is queued."""
        if self._closing or not self.jobs_card.isVisible():
            return
        host = self.widget()
        if host is None:
            return
        y = self.jobs_card.mapTo(host, QPoint(0, 0)).y()
        enable_smooth_scroll(self).scroll_to(max(0, y - 8))

    def _pump(self) -> None:
        running = sum(1 for j in self.jobs if j.state == "running")
        now = time.monotonic()
        for j in self.jobs:
            if running >= self._max_parallel():
                break
            if j.state == "scheduled" and now >= j.scheduled_at:
                j.state = "queued"
                self._card(j)
            if j.state == "queued":
                self._launch(j)
                running += 1
        self._emit_activity()

    def _launch(self, job: UploadJob) -> None:
        opts = EngineOptions(files=list(job.files), email=job.email,
                             human_pace=bool(self.ctx.settings.get("human_pace", True)))
        th = UploadThread(opts, self)
        job.thread = th
        job.state = "running"
        job.started = time.monotonic()
        job._last_t = 0.0
        job._last_bytes = 0.0
        th.step.connect(lambda k, m, p, j=job: self._on_step(j, k, p))
        th.progress.connect(lambda p, j=job: self._on_progress(j, p))
        th.uploaded.connect(lambda s, t, j=job: self._on_bytes(j, s, t))
        th.success.connect(lambda d, j=job: self._on_success(j, d))
        th.failed.connect(lambda m, j=job: self._on_failed(j, m))
        th.finished.connect(lambda t=th, j=job: self._on_thread_finished(j, t))
        self._card(job)
        if not self._tick.isActive():
            self._tick.start()
        th.start()

    def _card(self, job: UploadJob) -> None:
        c = self.cards.get(job.id)
        if c is not None:
            c.refresh()

    def _on_step(self, job: UploadJob, key: str, pct: int) -> None:
        job.step = next((i for i, (k, _t, _p) in enumerate(STEPS) if k == key), job.step)
        self._on_progress(job, pct)

    def _on_progress(self, job: UploadJob, pct: int) -> None:
        if self._closing:
            return
        if job.state != "running":
            return
        job.pct = max(job.pct, min(100, pct))
        job._on_pct(job.pct)
        self._card(job)
        self._emit_activity()
        HUB.jobs_updated.emit()

    def _on_bytes(self, job: UploadJob, sent: int, total: int) -> None:
        """Real byte counter extracted from the site — instant, honest data."""
        if self._closing or job.state != "running":
            return
        sent = max(0, min(int(sent), job.total if job.total else int(sent)))
        if sent <= job.sent:
            return
        job.sent = sent
        derived = int(100 * sent / job.total) if job.total else job.pct
        self._on_progress(job, max(job.pct, min(98, derived)))

    def _on_success(self, job: UploadJob, data: dict) -> None:
        if self._closing:
            return
        job.state = "done"
        job.pct = 100
        job.data = data
        job.link = data.get("link", "")
        job.finished_at = time.time()
        for f in job.files:                    # generated ZIPs are not needed once the link exists
            ziputil.cleanup_archive(f)
        job.elapsed = data.get("elapsed_s") or (time.monotonic() - job.started)
        self.ctx.add_history({
            "kind": "upload", "files": data.get("files", []),
            "total_bytes": data.get("total_bytes", 0), "link": job.link,
            "email": data.get("email", ""), "expiry": data.get("expiry", "7 days"),
            "elapsed_s": data.get("elapsed_s"),
        })
        if self.ctx.settings.get("auto_copy_link", True):
            QGuiApplication.clipboard().setText(job.link)
            self.ctx.toast(tr("toast.link_ready_copy", name=job.title))
        else:
            self.ctx.toast(tr("toast.link_ready", name=job.title))
        self._card(job)
        self._update_jobs_header()
        self._save_queue()
        HUB.jobs_updated.emit()

    def _on_failed(self, job: UploadJob, msg: str) -> None:
        if self._closing:
            return
        if job.state in ("done",):
            return
        if msg == "cancelled":
            self._remove_job(job)
            self.ctx.toast(tr("toast.cancelled"), "info")
            return
        # automatic retry (up to 2 extra attempts)
        if bool(self.ctx.settings.get("auto_retry", True)) and job.retries < 2:
            job.retries += 1
            job.state, job.pct, job.step, job.error = "queued", 0, -1, ""
            job._last_t = 0.0
            job._last_bytes = 0.0
            job.speed = 0.0
            job.sent = 0
            c = self.cards.get(job.id)
            if c is not None:
                c.step.setProperty("role", "caption")
                repolish(c.step)
                c.badge.set_icon("cloud-upload")
            self._card(job)
            self.ctx.toast(tr("toast.auto_retry", n=job.retries), "warning")
            self._update_jobs_header()
            self._save_queue()
            self._pump()
            HUB.jobs_updated.emit()
            return
        job.state = "failed"
        job.error = msg
        self._card(job)
        self._update_jobs_header()
        self._save_queue()
        HUB.jobs_updated.emit()

    def _on_thread_finished(self, job: UploadJob, th: UploadThread) -> None:
        if job.thread is th:
            job.thread = None
        if th in self._orphans:
            self._orphans.remove(th)
        try:
            th.deleteLater()
        except RuntimeError:
            pass
        if self._closing:
            return
        self._cleanup_tmp()
        self._pump()
        self._update_jobs_header()

    # ------------------------------------------------------------ job actions
    def _find(self, job_id: int) -> Optional[UploadJob]:
        return next((j for j in self.jobs if j.id == job_id), None)

    def cancel_job(self, job_id: int) -> None:
        j = self._find(job_id)
        if j is None:
            return
        if j.state in ("queued", "scheduled"):
            self._remove_job(j)
        elif j.state == "running" and j.thread is not None:
            j.step = -1
            j.thread.stop()
            c = self.cards.get(j.id)
            if c is not None:
                c.btn_cancel.setEnabled(False)
                c.step.setText("Cancelling…")

    def retry_job(self, job_id: int) -> None:
        j = self._find(job_id)
        if j is not None and j.state == "failed":
            j.state, j.pct, j.step, j.error = "queued", 0, -1, ""
            j.sent = 0
            c = self.cards.get(j.id)
            if c is not None:
                c.step.setProperty("role", "caption")
                repolish(c.step)
                c.badge.set_icon("cloud-upload")
            self._card(j)
            self._save_queue()
            self._pump()

    def move_job(self, job_id: int, delta: int) -> None:
        """Reorder a queued/scheduled job (Queue page arrows) — launch order
        follows list order, so this changes which transfer starts next."""
        j = self._find(job_id)
        if j is None or j.state not in ("queued", "scheduled"):
            return
        i = self.jobs.index(j)
        k = i + delta
        while 0 <= k < len(self.jobs) and self.jobs[k].state == "running":
            k += delta                       # never jump over a running upload
        if not (0 <= k < len(self.jobs)) or self.jobs[k].state not in ("queued", "scheduled"):
            return
        self.jobs[i], self.jobs[k] = self.jobs[k], self.jobs[i]
        self._pump()
        self._save_queue()
        HUB.jobs_changed.emit()

    def dismiss_job(self, job_id: int) -> None:
        j = self._find(job_id)
        if j is not None and j.state in ("done", "failed"):
            self._remove_job(j)

    def clear_finished(self) -> None:
        for j in [j for j in self.jobs if j.state in ("done", "failed")]:
            self._remove_job(j)

    def _remove_job(self, job: UploadJob) -> None:
        if job.thread is not None and job.thread not in self._orphans:
            self._orphans.append(job.thread)
        if job in self.jobs:
            self.jobs.remove(job)
        for f in job.files:                    # retention: a kept ZIP goes away with its job
            ziputil.cleanup_archive(f)
        card = self.cards.pop(job.id, None)
        if card is not None:
            self.jobs_lay.removeWidget(card)
            card.setParent(None)
            card.deleteLater()
        if not self.jobs:
            self.jobs_card.hide()
        self._update_jobs_header()
        self._cleanup_tmp()
        self._emit_activity()
        self._pump()
        self._save_queue()
        HUB.jobs_changed.emit()

    def copy_job_link(self, job_id: int) -> None:
        j = self._find(job_id)
        if j is not None and j.link:
            QGuiApplication.clipboard().setText(j.link)
            self.ctx.toast(tr("toast.link_copied"))

    def show_qr(self, job_id: int) -> None:
        j = self._find(job_id)
        if j is None or not j.link:
            return
        from ..qrdialog import QrExportDialog
        QrExportDialog(self.window(), j.link, self.ctx.toast).exec()

    # ----------------------------------------------------------------- status
    def _update_jobs_header(self) -> None:
        active = len(self._active_jobs())
        running = sum(1 for j in self.jobs if j.state == "running")
        scheduled = sum(1 for j in self.jobs if j.state == "scheduled")
        done = sum(1 for j in self.jobs if j.state == "done")
        failed = sum(1 for j in self.jobs if j.state == "failed")
        bits = []
        if running:
            bits.append(f"{running} {tr('misc.uploading').lower()}")
        if scheduled:
            bits.append(f"{scheduled} {tr('up.scheduled').lower()}")
        if active - running - scheduled:
            bits.append(f"{active - running - scheduled} {tr('up.queued').lower()}")
        if done:
            bits.append(f"{done} {tr('up.ready').lower()}")
        if failed:
            bits.append(f"{failed} {tr('up.failed').lower()}")
        self.jobs_title.setText(tr("up.transfers") + (f"  ·  {', '.join(bits)}" if bits else ""))
        self.clear_done.setVisible(any(j.state in ("done", "failed") for j in self.jobs))
        if not any(j.state in ("running", "scheduled") for j in self.jobs):
            self._tick.stop()

    def _on_tick(self) -> None:
        # refresh running cards + pump scheduled jobs whose time has come
        due = [j for j in self.jobs if j.state == "scheduled"]
        if due:
            self._pump()
        for j in self.jobs:
            if j.state in ("running", "scheduled"):
                self._card(j)
        HUB.jobs_updated.emit()

    def _emit_activity(self) -> None:
        running = [j for j in self.jobs if j.state == "running"]
        waiting = sum(1 for j in self.jobs if j.state in ("queued", "scheduled"))
        if not running and not waiting:
            self.ctx.activity.emit("", -1)
            return
        pct = int(sum(j.pct for j in running) / len(running)) if running else 0
        n = len(running) + waiting
        self.ctx.activity.emit(tr("misc.uploading") if n == 1
                               else tr("up.uploading_n", n=n), pct)

    def _retranslate(self) -> None:
        self.files_title.setText(self._files_title())
        self.clear_btn.setText(tr("up.clear_all"))
        self.sender_title.setText(tr("up.sender"))
        self.mode.set_option_text("random", tr("up.random"))
        self.mode.set_option_text("fixed", tr("up.my_email"))
        self.summary_title.setText(tr("up.summary"))
        self.sched_title.setText(tr("up.schedule"))
        self.sched_mode.set_option_text("now", tr("up.now"))
        self.sched_mode.set_option_text("later", tr("up.later"))
        self.retry_lbl.setText(tr("up.auto_retry"))
        self.go.setText(tr("up.get_link") if self.go.isEnabled() and self.files
                        else tr("btn.preparing") if self._scans else tr("up.get_link"))
        self.jobs_title.setText(tr("up.transfers"))
        self.open_queue.setText(tr("up.open_queue"))
        self.clear_done.setText(tr("up.clear_finished"))
        self.drop._retranslate()
        self._refresh_sender()
        for c in self.cards.values():
            c.btn_copy.setText(tr("btn.copy"))
            c.btn_open.setText(tr("btn.open"))
            c.btn_qr.setText(tr("btn.qr"))
            c.btn_retry.setText(tr("btn.retry"))
            c.btn_dismiss.setText(tr("btn.dismiss"))
            c.refresh()

    def shutdown(self) -> None:
        self._closing = True
        HUB.detach(self)
        for z in list(self._scans):
            z.stop()
            z.wait(15000)
        self._scans.clear()                  # joined scans leave no dangling callbacks
        if self._zip is not None:
            self._zip.stop()
            self._zip.wait(15000)
        self._zip = None
        threads = [j.thread for j in self.jobs if j.thread is not None] + list(self._orphans)
        for j in self.jobs:
            if j.thread is not None:
                j.state = "failed"
        for t in threads:
            t.stop()
        from core import procs
        procs.kill_all()                     # browsers die now, so the threads end quickly
        for t in threads:
            try:
                t.wait(4000)
            except RuntimeError:
                pass
