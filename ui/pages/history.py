"""History — modern, searchable list of past uploads and downloads + link checker.

Rows are painted by a delegate (file tile, name + meta, type, size, date, status
pill).  Every icon in the list is drawn as *vector* (``icons.paint``), so the
status marks stay razor-sharp on any display scale.
"""
from __future__ import annotations

import os
from datetime import datetime

from PyQt6.QtCore import QEvent, QModelIndex, QPointF, QRectF, QSize, Qt, QTimer, QUrl
from PyQt6.QtGui import (
    QColor, QDesktopServices, QFont, QFontMetrics, QGuiApplication, QKeySequence,
    QPainter, QPainterPath, QPen, QShortcut,
)
from PyQt6.QtWidgets import (
    QAbstractItemView, QFrame, QHeaderView, QProgressBar, QSizePolicy, QStackedWidget,
    QStyle, QStyledItemDelegate, QStyleOptionViewItem, QTableWidget, QTableWidgetItem,
    QWidget,
)

from core.store import LINK_CHECK_V, human_size, is_active, parse_dt, time_ago
from core.tasks import LinkCheckThread
from .. import icons
from ..dialogs import confirm
from ..i18n import i18n, tr
from ..menus import LineEdit, RoundMenu
from ..theme import _mix, theme
from ..widgets import Badge, Button, Segmented, hbox, label, vbox
from .base import Page

COLS = ["hist.col.name", "hist.col.type", "hist.col.size", "hist.col.date", "hist.col.status"]
ROW_H = 78            # one "floating" row card = 78px incl. 4px breathing room top + bottom
PLATE_INSET_X, PLATE_INSET_Y, PLATE_RADIUS = 2, 4, 18
ROLE_ROW = Qt.ItemDataRole.UserRole + 1

_EXT_ICON = {
    "image": ("jpg jpeg png gif webp heic svg bmp tiff raw psd ai", "image"),
    "film": ("mp4 mov mkv avi webm m4v wmv flv", "film"),
    "music": ("mp3 wav flac aac ogg m4a aiff", "music"),
    "text": ("pdf doc docx txt rtf md odt pages xls xlsx csv ppt pptx key", "file-text"),
    "pack": ("zip rar 7z tar gz tgz iso dmg", "package"),
    "code": ("py js ts html css json xml c cpp h java go rs sh", "code"),
}


# soft per-type hue for the file tile (None -> follow the theme accent / neutral text)
TILE_HUE = {
    "image": "#8B5CF6", "film": "#F0577A", "music": "#14B8A6", "file-text": None,
    "package": "#F59E0B", "code": "#10B981", "file": None,
}


def file_icon(entry: dict) -> str:
    files = entry.get("files") or []
    if len(files) > 1:
        return "package"
    name = files[0].get("name", "") if files else ""
    ext = os.path.splitext(name)[1].lstrip(".").lower()
    for exts, icon in _EXT_ICON.values():
        if ext in exts.split():
            return icon
    return "file"


# ------------------------------------------------------------------- status
def status_of(e: dict, checking: bool = False) -> tuple[str, str, str, str]:
    """(key, tone, icon, text) for one history entry.

    keys: checking | verified | active | expired | downloaded.  ``verified``
    means the live link check confirmed it; ``active`` is estimated from the
    expiry date.  A link past its expiry is always *expired*, whatever an
    older check said.
    """
    if e.get("kind") != "upload":
        return "downloaded", "accent", "arrow-down", tr("pill.downloaded")
    if checking:
        return "checking", "muted", "refresh", tr("pill.checking")
    if not e.get("link") or not is_active(e):
        return "expired", "danger" if e.get("link") else "muted", "x-circle", tr("pill.state_expired")
    if e.get("link_state") == "active" and e.get("link_check_v") == LINK_CHECK_V:
        return "verified", "success", "check-circle", tr("pill.active_ok")
    return "active", "success", "clock", tr("pill.active")


def status_tip(e: dict, key: str) -> str:
    if key == "verified":
        when = time_ago(e.get("checked_at", ""))
        return tr("hist.tip_verified", when=when) if when else tr("hist.tip_verified_now")
    if key == "active":
        return tr("hist.tip_estimated")
    if key == "expired":
        return tr("hist.tip_expired")
    if key == "checking":
        return tr("hist.checking_one")
    return ""


# ----------------------------------------------------------------- delegate
class RowDelegate(QStyledItemDelegate):
    """Paints each history row as a floating, rounded card (tile, text, chips, pill).

    A row is five cells; every cell draws its slice of the same rounded plate
    (clipped to the cell), so the card reads as one continuous surface.
    """

    def __init__(self, table: QTableWidget) -> None:
        super().__init__(table)
        self.table = table
        self.hover_row = -1
        self.compact = False

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        return QSize(option.rect.width(), ROW_H)

    def _font(self, base: QFont, px: int, weight: QFont.Weight) -> QFont:
        f = QFont(base)
        f.setPixelSize(px)
        f.setWeight(weight)
        return f

    @staticmethod
    def _tile_colors(icon_name: str, kind: str) -> tuple[QColor, QColor]:
        """(tile fill, glyph colour) tinted by file type."""
        c = theme.pal
        hue = TILE_HUE.get(icon_name)
        if hue is None:
            hue = c["accent"] if kind == "upload" else c["text2"]
        fill = _mix(c["surface"], hue, 0.22 if theme.dark else 0.12)
        glyph = _mix(hue, "#FFFFFF", 0.18) if theme.dark else hue
        return QColor(fill), QColor(glyph)

    def paint(self, p: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        row = index.data(ROLE_ROW)
        if not row:
            return
        e, status = row["entry"], row["status"]
        c = theme.pal
        col = index.column()
        last = col == self.table.columnCount() - 1
        r = QRectF(option.rect)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = index.row() == self.hover_row
        up = e.get("kind") == "upload"

        p.save()
        p.setClipRect(r)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
                         | QPainter.RenderHint.SmoothPixmapTransform)

        # --- the floating row card (one rounded plate, sliced per cell)
        ext = 24                                   # overshoot so inner cell edges never show
        plate = QRectF(r.left() - (0 if col == 0 else ext), r.top() + PLATE_INSET_Y,
                       r.width() + (0 if col == 0 else ext) + (0 if last else ext),
                       r.height() - 2 * PLATE_INSET_Y)
        if col == 0:
            plate.setLeft(r.left() + PLATE_INSET_X)
        if last:
            plate.setRight(r.right() - PLATE_INSET_X)
        plate.adjust(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(plate, PLATE_RADIUS, PLATE_RADIUS)
        if selected:
            fill, line = QColor(c["accent_soft"]), QColor(c["accent"])
            line.setAlpha(150)
        elif hovered:
            fill, line = QColor(c["surface3"]), QColor(c["border_strong"])
        else:
            fill, line = QColor(c["surface"]), QColor(c["border"])
        p.fillPath(path, fill)
        p.setPen(QPen(line, 1))
        p.drawPath(path)

        base = option.font
        cy = r.center().y()

        if col == 0:
            # file tile — tinted by type, with a tiny direction badge
            tile = QRectF(r.left() + PLATE_INSET_X + 14, cy - 23, 46, 46)
            tp = QPainterPath()
            tp.addRoundedRect(tile, 14, 14)
            tfill, glyph = self._tile_colors(row["icon"], e.get("kind", ""))
            p.fillPath(tp, tfill)
            icons.paint(p, row["icon"], glyph.name(),
                        QRectF(tile.center().x() - 12, tile.center().y() - 12, 24, 24), 1.6)
            dot = QRectF(tile.right() - 11, tile.bottom() - 11, 17, 17)
            p.setPen(QPen(fill, 2))
            p.setBrush(QColor(c["accent"] if up else c["text2"]))
            p.drawEllipse(dot)
            icons.paint(p, "arrow-up" if up else "arrow-down", c["on_accent"] if up else c["surface"],
                        dot.adjusted(3.5, 3.5, -3.5, -3.5), 2.4)

            tx = tile.right() + 16
            tw = r.right() - tx - 10
            f1 = self._font(base, 14, QFont.Weight.DemiBold)
            f2 = self._font(base, 12, QFont.Weight.Normal)
            p.setFont(f1)
            p.setPen(QColor(c["text"]))
            fm1 = QFontMetrics(f1)
            name, extra = row["name"], row["extra"]
            ew = fm1.horizontalAdvance(extra) + 30 if extra else 0
            shown = fm1.elidedText(name, Qt.TextElideMode.ElideMiddle, max(20, int(tw - ew)))
            p.drawText(QPointF(tx, cy - 3), shown)
            if extra:                                   # "+3" chip next to the name
                f3 = self._font(base, 11, QFont.Weight.DemiBold)
                fm3 = QFontMetrics(f3)
                cw = fm3.horizontalAdvance(extra) + 14
                chip = QRectF(tx + fm1.horizontalAdvance(shown) + 8, cy - 3 - 13, cw, 19)
                cp = QPainterPath()
                cp.addRoundedRect(chip, 9.5, 9.5)
                p.fillPath(cp, QColor(c["surface3"] if not hovered else c["surface"]))
                p.setFont(f3)
                p.setPen(QColor(c["text2"]))
                p.drawText(QPointF(chip.left() + 7, chip.bottom() - 5), extra)
            p.setFont(f2)
            p.setPen(QColor(c["muted"]))
            meta = row["meta"]
            if self.compact:                      # TYPE column is hidden when narrow
                meta = (tr("hist.type_up") if up else tr("hist.type_down")) + "  ·  " + meta
            p.drawText(QPointF(tx, cy + 15),
                       QFontMetrics(f2).elidedText(meta, Qt.TextElideMode.ElideRight, int(tw)))

        elif col == 1:
            # type as a quiet outlined chip
            f = self._font(base, 12, QFont.Weight.Medium)
            text = tr("hist.type_up") if up else tr("hist.type_down")
            fm = QFontMetrics(f)
            w = 8 + 14 + 6 + fm.horizontalAdvance(text) + 10
            chip = QRectF(r.left() + 4, cy - 13, w, 26)
            cp = QPainterPath()
            cp.addRoundedRect(chip, 13, 13)
            p.setPen(QPen(QColor(c["border_strong"]), 1))
            p.drawPath(cp)
            icons.paint(p, "arrow-up" if up else "arrow-down", c["text2"],
                        QRectF(chip.left() + 8, cy - 7, 14, 14), 1.9)
            p.setFont(f)
            p.setPen(QColor(c["text2"]))
            p.drawText(QPointF(chip.left() + 8 + 14 + 6, cy + 4.2), text)

        elif col == 2:
            p.setFont(self._font(base, 13, QFont.Weight.DemiBold))
            p.setPen(QColor(c["text"]))
            p.drawText(QPointF(r.left() + 8, cy + 4.5), row["size"])

        elif col == 3:
            p.setFont(self._font(base, 13, QFont.Weight.DemiBold))
            p.setPen(QColor(c["text"]))
            p.drawText(QPointF(r.left() + 8, cy - 3), row["date"])
            p.setFont(self._font(base, 12, QFont.Weight.Normal))
            p.setPen(QColor(c["muted"]))
            p.drawText(QPointF(r.left() + 8, cy + 14), row["ago"])

        else:
            self._pill(p, r, cy, status, base)

        p.restore()

    def _pill(self, p: QPainter, r: QRectF, cy: float, status, base: QFont) -> None:
        key, tone, icon, text = status
        c = theme.pal
        fg = {"success": c["success"], "danger": c["danger"], "accent": c["accent"],
              "muted": c["muted"]}[tone]
        bg = {"success": c["success_bg"], "danger": c["danger_bg"], "accent": c["accent_soft"],
              "muted": c["surface3"]}[tone]
        f = self._font(base, 12, QFont.Weight.DemiBold)
        fm = QFontMetrics(f)
        w = 10 + 16 + 6 + fm.horizontalAdvance(text) + 12
        pill = QRectF(r.left() + 8, cy - 14, w, 28)
        path = QPainterPath()
        path.addRoundedRect(pill, 14, 14)
        p.fillPath(path, QColor(bg))
        icons.paint(p, icon, fg, QRectF(pill.left() + 9, cy - 8, 16, 16), 1.9)
        p.setFont(f)
        p.setPen(QColor(fg))
        p.drawText(QPointF(pill.left() + 9 + 16 + 6, cy + 4.3), text)


class _StatBadge(QWidget):
    """Rounded-square tone badge for a summary tile: tinted plate, hairline, crisp glyph."""

    def __init__(self, icon: str, tone: str) -> None:
        super().__init__()
        self._icon, self._tone = icon, tone
        self.setFixedSize(46, 46)
        theme.changed.connect(self.update)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = theme.pal
        tone = c[self._tone]
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setBrush(QColor(_mix(c["surface"], tone, 0.20 if theme.dark else 0.13)))
        edge = QColor(tone)
        edge.setAlpha(70)
        p.setPen(QPen(edge, 1.0))
        p.drawRoundedRect(r, 15, 15)
        glyph = _mix(tone, "#FFFFFF", 0.22) if theme.dark else tone
        icons.paint(p, self._icon, glyph, QRectF(self.width() / 2 - 11, self.height() / 2 - 11, 22, 22), 1.7)


class _Meter(QWidget):
    """Slim rounded share bar used under each summary number."""

    def __init__(self, tone: str) -> None:
        super().__init__()
        self._tone, self._v = tone, 0.0
        self.setFixedHeight(5)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        theme.changed.connect(self.update)

    def set_value(self, v: float) -> None:
        self._v = max(0.0, min(1.0, v))
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = theme.pal
        r = QRectF(self.rect())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(c["surface3"]))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        if self._v > 0:
            w = max(r.height(), r.width() * self._v)
            p.setBrush(QColor(c[self._tone]))
            p.drawRoundedRect(QRectF(r.left(), r.top(), w, r.height()), r.height() / 2, r.height() / 2)


# --------------------------------------------------------------------- page
class HistoryPage(Page):
    key = "history"
    overline_key = "nav.workspace"
    title_key = "hist.title"
    subtitle_key = "hist.subtitle"

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        self._rows: list[dict] = []
        self._check: LinkCheckThread | None = None
        self._pending: set[str] = set()
        self._check_tally = (0, 0)
        # premium / calm: the page still scrolls (wheel, touch, keys) but never shows a bar
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        # ---- summary tiles (number + share-of-total meter)
        stats = hbox(spacing=12)
        self._stat: dict[str, object] = {}
        self._stat_cap: dict[str, object] = {}
        self._meter: dict[str, _Meter] = {}
        for key, icon, tone, cap in (("total", "history", "accent", "hist.stat_total"),
                                     ("active", "check-circle", "success", "hist.stat_active"),
                                     ("expired", "x-circle", "danger", "hist.stat_expired"),
                                     ("down", "arrow-down", "accent", "hist.stat_down")):
            tile = QFrame()
            tile.setObjectName("card")
            tl = vbox(tile, (18, 16, 18, 16), 12)
            top = hbox(spacing=16)
            top.addWidget(_StatBadge(icon, tone), 0, Qt.AlignmentFlag.AlignVCenter)
            col = vbox(spacing=0)
            num = label("0", "stat")
            capl = label(tr(cap), "caption")
            col.addWidget(num)
            col.addWidget(capl)
            top.addLayout(col, 1)
            tl.addLayout(top)
            meter = _Meter(tone)
            tl.addWidget(meter)
            self._stat[key], self._stat_cap[key] = num, (capl, cap)
            self._meter[key] = meter
            stats.addWidget(tile, 1)
        self.body.addLayout(stats)

        # ---- toolbar
        bar = hbox(spacing=10)
        self.search = LineEdit()
        self.search.setPlaceholderText(tr("hist.search"))
        self.search.setClearButtonEnabled(True)
        self.search.setMaximumWidth(340)
        self.search.setMinimumWidth(150)
        bar.addWidget(self.search, 100)
        self.filter = Segmented([("all", tr("hist.all")), ("upload", tr("hist.uploads")),
                                 ("download", tr("hist.downloads"))], "all")
        bar.addWidget(self.filter)
        bar.addStretch(1)
        self.check_btn = Button(tr("hist.check"), "", "shield-check", "sm")
        self.check_btn.clicked.connect(lambda: self.check_links())
        bar.addWidget(self.check_btn)
        self.clear_btn = Button(tr("hist.clear"), "danger", "trash", "sm")
        self.clear_btn.clicked.connect(self.clear_all)
        bar.addWidget(self.clear_btn)
        self.body.addLayout(bar)

        self.prog = QProgressBar()
        self.prog.setTextVisible(False)
        self.prog.setRange(0, 100)
        self.prog.hide()
        self.body.addWidget(self.prog)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(180)
        self._debounce.timeout.connect(self.refresh)
        self.search.textChanged.connect(lambda _t: self._debounce.start())
        self.filter.changed.connect(lambda _k: self.refresh())

        # ---- list: rows are floating cards, so the table sits straight on the sheet
        self.stack = QStackedWidget()
        self.stack.setObjectName("page")
        self.table = QTableWidget(0, len(COLS))
        self.table.setObjectName("hist")
        self.table.setHorizontalHeaderLabels([tr(k) for k in COLS])
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setFrameShape(QFrame.Shape.NoFrame)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        # no scroll bars anywhere: the table grows to its content, the page scrolls
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.table.setMouseTracking(True)
        self.table.verticalHeader().setDefaultSectionSize(ROW_H)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.delegate = RowDelegate(self.table)
        self.table.setItemDelegate(self.delegate)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for i, w in ((1, 124), (2, 100), (3, 150), (4, 148)):
            h.setSectionResizeMode(i, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(i, w)
        h.setHighlightSections(False)
        h.setMinimumSectionSize(60)
        h.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)
        self.table.itemSelectionChanged.connect(self._sel_changed)
        self.table.itemDoubleClicked.connect(lambda _i: self.open_selected())
        self.table.entered.connect(self._hover)
        self.table.viewport().installEventFilter(self)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.stack.addWidget(self.table)

        empty = QFrame()
        empty.setObjectName("card")
        el = vbox(empty, (20, 56, 20, 56), 6)
        el.addWidget(Badge("history", 60), 0, Qt.AlignmentFlag.AlignHCenter)
        el.addSpacing(6)
        self.empty_title = label(tr("hist.no_transfers"), "h2")
        self.empty_sub = label(tr("hist.no_transfers_sub"), "muted")
        el.addWidget(self.empty_title, 0, Qt.AlignmentFlag.AlignHCenter)
        el.addWidget(self.empty_sub, 0, Qt.AlignmentFlag.AlignHCenter)
        self.stack.addWidget(empty)
        self.body.addWidget(self.stack)

        # ---- footer
        foot = hbox(spacing=8)
        self.count = label("", "caption")
        foot.addWidget(self.count)
        foot.addStretch(1)
        self.copy_btn = Button(tr("hist.copy_link"), "", "copy", "sm")
        self.copy_btn.clicked.connect(self.copy_selected)
        self.open_btn = Button(tr("hist.open"), "", "external", "sm")
        self.open_btn.clicked.connect(self.open_selected)
        self.del_btn = Button(tr("hist.delete"), "danger", "trash", "sm")
        self.del_btn.clicked.connect(self.delete_selected)
        for b in (self.copy_btn, self.open_btn, self.del_btn):
            foot.addWidget(b)
        self.body.addLayout(foot)
        self.body.addStretch(1)

        sc = QShortcut(QKeySequence(Qt.Key.Key_Delete), self.table)
        sc.setContext(Qt.ShortcutContext.WidgetShortcut)
        sc.activated.connect(self.delete_selected)

        ctx.history_changed.connect(self.refresh)
        self._clock = QTimer(self)
        self._clock.setInterval(60_000)
        self._clock.timeout.connect(lambda: self.isVisible() and self.refresh())
        self._clock.start()
        theme.changed.connect(self.refresh)
        i18n.changed.connect(self._retranslate)
        self.refresh()
        self._sel_changed()

    # ----------------------------------------------------------------- misc
    def eventFilter(self, obj, ev) -> bool:  # noqa: N802
        tbl = getattr(self, "table", None)
        if tbl is not None and obj is tbl.viewport():
            if ev.type() == QEvent.Type.Leave:
                self._hover(None)
            elif ev.type() == QEvent.Type.Resize:
                QTimer.singleShot(0, self._fit_columns)
        return super().eventFilter(obj, ev)

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._fit_columns()

    def _fit_columns(self) -> None:
        """Keep the NAME column readable: drop the TYPE column and tighten the
        rest when the window is narrow."""
        tbl = getattr(self, "table", None)
        if tbl is None:
            return
        w = tbl.viewport().width() or self.viewport().width() - 100
        narrow = w < 700
        tbl.setColumnHidden(1, narrow)
        widths = {1: 124, 2: 100, 3: 150, 4: 148} if not narrow else {2: 84, 3: 132, 4: 138}
        for c, cw in widths.items():
            tbl.setColumnWidth(c, cw)
        self.delegate.compact = narrow
        tbl.viewport().update()

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        self._fit_columns()

    def _hover(self, idx) -> None:
        row = idx.row() if idx is not None and idx.isValid() else -1
        if row != self.delegate.hover_row:
            self.delegate.hover_row = row
            self.table.viewport().update()

    def on_show(self) -> None:
        self.refresh()        # status (active/expired) may have changed

    def _retranslate(self) -> None:
        self.search.setPlaceholderText(tr("hist.search"))
        self.filter.set_option_text("all", tr("hist.all"))
        self.filter.set_option_text("upload", tr("hist.uploads"))
        self.filter.set_option_text("download", tr("hist.downloads"))
        self.check_btn.setText(tr("hist.check"))
        self.clear_btn.setText(tr("hist.clear"))
        self.table.setHorizontalHeaderLabels([tr(k) for k in COLS])
        for capl, key in self._stat_cap.values():
            capl.setText(tr(key))
        self.copy_btn.setText(tr("hist.copy_link"))
        self.open_btn.setText(tr("hist.open"))
        self.del_btn.setText(tr("hist.delete"))
        self.refresh()

    # ------------------------------------------------------------------ data
    def _filtered(self) -> list[dict]:
        kind = self.filter.value()
        q = self.search.text().strip().lower()
        out = []
        for e in self.ctx.history.entries():
            if kind != "all" and e.get("kind") != kind:
                continue
            if q:
                hay = " ".join([
                    " ".join(f.get("name", "") for f in e.get("files", [])),
                    e.get("link", ""), e.get("email", ""), e.get("save_path", "")]).lower()
                if q not in hay:
                    continue
            out.append(e)
        return out

    @staticmethod
    def _short_link(link: str) -> str:
        return link.replace("https://", "").replace("http://", "").replace("www.", "")

    @staticmethod
    def _ago(e: dict, dt) -> str:
        if dt is None:
            return ""
        rel = time_ago(e.get("date", ""))
        rel = "" if rel[:1].isalpha() and rel[0] in "JFMASOND" and "," in rel else rel
        return dt.strftime("%H:%M") + (f"  ·  {rel}" if rel else "")

    def _row_model(self, e: dict) -> dict:
        files = e.get("files") or []
        name = files[0].get("name", "Transfer") if files else "Transfer"
        extra = f"+{len(files) - 1}" if len(files) > 1 else ""
        n = len(files)
        meta_bits = [tr("hist.n_files", n=n) if n != 1 else tr("hist.n_file")]
        if e.get("kind") == "upload" and e.get("link"):
            meta_bits.append(self._short_link(e["link"]))
        elif e.get("save_path"):
            meta_bits.append(os.path.basename(os.path.dirname(e["save_path"])) or e["save_path"])
        dt = parse_dt(e.get("date", ""))
        status = status_of(e, str(e.get("id")) in self._pending)
        return {"entry": e, "name": name, "extra": extra, "meta": "  ·  ".join(meta_bits),
                "icon": file_icon(e), "size": human_size(e.get("total_bytes", 0)),
                "date": dt.strftime("%b %d, %Y") if dt else e.get("date", ""),
                "ago": self._ago(e, dt),
                "status": status}

    def refresh(self) -> None:
        keep = {e["id"] for e in self._selected()}
        self._rows = self._filtered()
        allx = self.ctx.history.entries()
        total = len(allx)
        self.stack.setCurrentIndex(0 if self._rows else 1)
        self.clear_btn.setEnabled(total > 0)
        if total and not self._rows:
            self.empty_title.setText(tr("hist.no_matches"))
            self.empty_sub.setText(tr("hist.no_matches_sub"))
        else:
            self.empty_title.setText(tr("hist.no_transfers"))
            self.empty_sub.setText(tr("hist.no_transfers_sub"))

        # summary tiles (always over the full history)
        act = exp = 0
        for e in allx:
            k = status_of(e)[0]
            act += k in ("verified", "active")
            exp += k == "expired"
        self._stat["total"].setText(str(total))
        self._stat["active"].setText(str(act))
        self._stat["expired"].setText(str(exp))
        n_down = sum(1 for e in allx if e.get("kind") != "upload")
        self._stat["down"].setText(str(n_down))
        for k, v in (("total", total), ("active", act), ("expired", exp), ("down", n_down)):
            self._meter[k].set_value(v / total if total else 0.0)

        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(self._rows))
        for r, e in enumerate(self._rows):
            model = self._row_model(e)
            for c in range(len(COLS)):
                it = self.table.item(r, c)
                if it is None:
                    it = QTableWidgetItem()
                    self.table.setItem(r, c, it)
                it.setData(ROLE_ROW, model)
                it.setData(Qt.ItemDataRole.UserRole, e.get("id"))
                it.setText("")
                if c == 0:
                    it.setToolTip(e.get("link", ""))
                elif c == 4:
                    it.setToolTip(status_tip(e, model["status"][0]))
                else:
                    it.setToolTip("")
            if e["id"] in keep:
                self.table.selectRow(r)
        self.table.setUpdatesEnabled(True)
        # the table is exactly as tall as its rows — the *page* scrolls (bar hidden)
        head = self.table.horizontalHeader().height() or 40
        self.table.setFixedHeight(head + ROW_H * len(self._rows) + 12)
        self.stack.setFixedHeight(self.table.height() if self._rows
                                  else self.stack.widget(1).sizeHint().height())
        self.table.viewport().update()
        if self._check is None or not self._check.isRunning():
            self.count.setText(tr("hist.records", n=len(self._rows), total=total))
        self._sel_changed()

    def _selected(self) -> list[dict]:
        ids = {self.table.item(i.row(), 0).data(Qt.ItemDataRole.UserRole)
               for i in self.table.selectedIndexes() if self.table.item(i.row(), 0)}
        return [e for e in self._rows if e.get("id") in ids]

    def _sel_changed(self) -> None:
        sel = self._selected()
        self.del_btn.setEnabled(bool(sel))
        self.del_btn.setText(f"{tr('hist.delete')} ({len(sel)})" if len(sel) > 1
                             else tr('hist.delete'))
        self.copy_btn.setEnabled(len(sel) == 1 and bool(sel[0].get("link")))
        self.open_btn.setEnabled(len(sel) == 1 and
                                 bool(sel[0].get("link") or sel[0].get("save_path")))

    # ------------------------------------------------------------ link check
    def check_links(self, entries: list[dict] | None = None) -> None:
        """Verify share links against the live site (all uploads, or ``entries``)."""
        if self._check is not None and self._check.isRunning():
            return
        src = entries if entries is not None else self.ctx.history.entries()
        pairs = [(str(e["id"]), e["link"], e.get("date", "")) for e in src
                 if e.get("kind") == "upload" and e.get("link")]
        if not pairs:
            self.ctx.toast(tr("toast.check_none"), "info")
            return
        self._pending = {p[0] for p in pairs}
        self.check_btn.setEnabled(False)
        self.check_btn.setText(tr("hist.checking_btn"))
        self.prog.setValue(0)
        self.prog.show()
        self.ctx.toast(tr("toast.checking"), "info")
        th = LinkCheckThread(pairs, self)
        th.checked.connect(self._on_checked)
        th.progress.connect(self._on_check_progress)
        th.done_all.connect(self._on_check_done)
        th.finished.connect(lambda t=th: self._check_finished(t))
        self._check = th
        self.refresh()
        th.start()

    def _on_checked(self, entry_id: str, state: str, valid_until: str) -> None:
        self._pending.discard(entry_id)
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        if state in ("active", "expired"):
            fields = {"link_state": state, "checked_at": now, "link_check_v": LINK_CHECK_V}
            if valid_until:
                fields["link_valid_until"] = valid_until
            self.ctx.history.update_entry(entry_id, **fields)
        # "unknown" never overwrites an earlier verdict — the site simply
        # couldn't be reached / answered ambiguously.
        self._debounce.start()

    def _on_check_progress(self, done: int, total: int) -> None:
        self.prog.setValue(int(done * 100 / max(1, total)))
        self.count.setText(tr("hist.checking", done=done, total=total))

    def _on_check_done(self, active: int, expired: int, unknown: int) -> None:
        self._pending.clear()
        if unknown and not (active or expired):
            self.ctx.toast(tr("toast.check_failed"), "error")
        elif unknown:
            self.ctx.toast(tr("toast.check_done3", a=active, e=expired, u=unknown), "warning")
        else:
            self.ctx.toast(tr("toast.check_done", a=active, e=expired), "success")
        self.refresh()

    def _check_finished(self, t: LinkCheckThread) -> None:
        if t is self._check:
            self._check = None
        try:
            t.deleteLater()
        except RuntimeError:
            pass
        self._pending.clear()
        self.prog.hide()
        self.check_btn.setEnabled(True)
        self.check_btn.setText(tr("hist.check"))
        self.refresh()

    # --------------------------------------------------------------- actions
    def copy_selected(self) -> None:
        sel = self._selected()
        if sel and sel[0].get("link"):
            QGuiApplication.clipboard().setText(sel[0]["link"])
            self.ctx.toast(tr("toast.link_copied"))

    def open_selected(self) -> None:
        sel = self._selected()
        if not sel:
            return
        e = sel[0]
        if e.get("kind") == "download" and e.get("save_path") and os.path.exists(e["save_path"]):
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(e["save_path"])))
        elif e.get("link"):
            QDesktopServices.openUrl(QUrl(e["link"]))

    def delete_selected(self) -> None:
        sel = self._selected()
        if sel:
            self.ctx.remove_history([e["id"] for e in sel])
            self.ctx.toast(f"Removed {len(sel)} record{'s' if len(sel) != 1 else ''}", "info")

    def clear_all(self) -> None:
        n = len(self.ctx.history.entries())
        if not n:
            return
        if confirm(self.window(), "Clear history", f"Remove all {n} records?",
                   "This only clears this list. Share links stay valid until they expire.",
                   yes="Clear all", danger=True):
            self.ctx.clear_history()
            self.ctx.toast(tr("toast.history_cleared"), "info")

    def _menu(self, pos) -> None:
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        if not self.table.item(row, 0).isSelected():
            self.table.selectRow(row)
        sel = self._selected()
        one = len(sel) == 1
        m = RoundMenu(parent=self)
        a_copy = m.addAction(tr("hist.copy_link"))
        a_open = m.addAction(tr("hist.open"))
        a_check = m.addAction(tr("hist.check_sel"))
        m.addSeparator()
        a_del = m.addAction(tr("hist.delete"))
        a_copy.setEnabled(one and bool(sel[0].get("link")))
        a_open.setEnabled(one)
        a_check.setEnabled(any(e.get("kind") == "upload" and e.get("link") for e in sel))
        act = m.exec(self.table.viewport().mapToGlobal(pos))
        if act is a_copy:
            self.copy_selected()
        elif act is a_open:
            self.open_selected()
        elif act is a_check:
            self.check_links(sel)
        elif act is a_del:
            self.delete_selected()

    def shutdown(self) -> None:
        if self._check is not None:
            self._check.stop()
            self._check.wait(3000)
