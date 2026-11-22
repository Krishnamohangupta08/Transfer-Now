"""Common page scaffold: scrollable, centred column with a header."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QScrollArea, QWidget

from ..ctx import Ctx
from ..i18n import i18n, tr
from ..widgets import hbox, label, vbox

MAX_WIDTH = 1180


class Page(QScrollArea):
    key = ""
    title_key = ""
    subtitle_key = ""
    overline_key = ""

    def __init__(self, ctx: Ctx) -> None:
        super().__init__()
        self.ctx = ctx
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        host = QWidget()
        host.setObjectName("page")
        self.setWidget(host)
        outer = hbox(host, (34, 30, 34, 26))
        outer.addStretch(1)
        col = QWidget()
        col.setObjectName("page")
        col.setMaximumWidth(MAX_WIDTH)
        col.setMinimumWidth(0)
        outer.addWidget(col, 100)
        outer.addStretch(1)

        self.body = vbox(col, spacing=14)
        head = vbox(spacing=4)
        self.overline_label = label("", "overline")
        self.overline_label.hide()              # Porcelain & Signal: no overlines / eyebrows
        head.addWidget(self.overline_label)
        self.title_label = label("", "h1")
        head.addWidget(self.title_label)
        self.subtitle_label = label("", "subtitle", wrap=True)
        head.addWidget(self.subtitle_label)
        self.header_right = hbox()
        top = hbox(spacing=12)
        top.addLayout(head, 1)
        top.addLayout(self.header_right)
        top.setAlignment(self.header_right, Qt.AlignmentFlag.AlignVCenter)
        self.body.addLayout(top)
        self.body.addSpacing(8)
        self._header()
        i18n.changed.connect(self.retranslate)

    # ------------------------------------------------------------- header
    def _header(self) -> None:
        self.overline_label.setText(tr(self.overline_key))
        self.title_label.setText(tr(self.title_key))
        self.subtitle_label.setText(tr(self.subtitle_key))
        if not self.subtitle_key:
            self.subtitle_label.hide()

    def retranslate(self) -> None:
        """Re-apply every static translated label (language switch)."""
        self._header()
        self._retranslate()

    def _retranslate(self) -> None:
        """Per-page hook."""

    def on_show(self) -> None:
        """Called every time the page becomes visible."""

    def can_close(self) -> bool:
        return True

    def shutdown(self) -> None:
        """Stop background work (window closing)."""
