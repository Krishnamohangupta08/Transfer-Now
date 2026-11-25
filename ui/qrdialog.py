"""QR code dialog: live preview + Save PNG / Copy image / Regenerate.

Rendering and disk writes run on a small worker thread so the UI never
blocks; the payload (a transfer link) is held only in memory and never logged.
"""
from __future__ import annotations

import os
from typing import Callable, Optional

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QGuiApplication, QImage
from PyQt6.QtWidgets import QFileDialog, QWidget

from core import qrexport
from .dialogs import FramelessDialog
from .widgets import Button, QrWidget, Segmented, hbox, hline, label, vbox

Notify = Callable[[str, str], None]          # (message, kind: success|error|info|warning)


class QrWorker(QThread):
    """Renders PNG bytes, optionally saving them atomically to ``path``."""
    done = pyqtSignal(str, bytes)            # saved path ('' for copy), png bytes
    failed = pyqtSignal(str)

    def __init__(self, payload: str, size: int, transparent: bool, path: str = "",
                 parent=None) -> None:
        super().__init__(parent)
        self._payload, self._size, self._transparent, self._path = payload, size, transparent, path

    def run(self) -> None:
        try:
            png = qrexport.render_png(self._payload, self._size, self._transparent)
            saved = qrexport.save_png(png, self._path) if self._path else ""
            self.done.emit(saved, png)
        except qrexport.QrExportError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001 — message only, never the payload
            self.failed.emit(f"Unexpected error ({type(exc).__name__}).")


class QrExportDialog(FramelessDialog):
    PREVIEW = 220

    def __init__(self, parent: QWidget, payload: str, notify: Optional[Notify] = None,
                 title: str = "Scan to open the link") -> None:
        super().__init__(parent, 340, (24, 22, 24, 20))
        self._payload = payload
        self._notify = notify or (lambda msg, kind="info": None)
        self._worker: Optional[QrWorker] = None
        self._mode = ""                       # 'save' | 'copy'

        self.body.addWidget(label(title, "h2"), 0, Qt.AlignmentFlag.AlignHCenter)
        self.body.addSpacing(6)
        self.preview = QrWidget(self.PREVIEW)
        self.body.addWidget(self.preview, 0, Qt.AlignmentFlag.AlignHCenter)
        self.status = label("", "muted", wrap=True)
        self.status.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.body.addWidget(self.status)
        self.body.addSpacing(8)

        # primary action row — compact, near the preview
        row = hbox(spacing=8)
        self.save_btn = Button("Save QR Code", "primary")
        self.save_btn.setDefault(True)
        self.save_btn.clicked.connect(self.save)
        self.copy_btn = Button("Copy QR Image", "")
        self.copy_btn.clicked.connect(self.copy)
        row.addWidget(self.save_btn, 1)
        row.addWidget(self.copy_btn, 1)
        self.body.addLayout(row)

        # progressive disclosure: advanced options
        self.opt_toggle = Button("Export options", "ghost")
        self.opt_toggle.setCheckable(True)
        self.opt_toggle.toggled.connect(self._toggle_options)
        self.body.addWidget(self.opt_toggle)
        self.options = QWidget()
        ol = vbox(self.options, (0, 2, 0, 4), 8)
        ol.addWidget(label("Image size", "caption"))
        self.size_seg = Segmented([(str(s), str(s)) for s in qrexport.SIZES],
                                  str(qrexport.DEFAULT_SIZE))
        ol.addWidget(self.size_seg)
        ol.addWidget(label("Background", "caption"))
        self.bg_seg = Segmented([("white", "White"), ("transparent", "Transparent")], "white")
        ol.addWidget(self.bg_seg)
        self.options.hide()
        self.body.addWidget(self.options)

        self.body.addWidget(hline())
        self.body.addSpacing(4)
        foot = hbox(spacing=8)
        self.regen_btn = Button("Regenerate", "ghost")
        self.regen_btn.clicked.connect(self.regenerate)
        self.close_btn = Button("Close", "")
        self.close_btn.clicked.connect(self.accept)
        foot.addWidget(self.regen_btn)
        foot.addStretch(1)
        foot.addWidget(self.close_btn)
        self.body.addLayout(foot)
        self.regenerate()

    # ---------------------------------------------------------------- state
    def size_px(self) -> int:
        return int(self.size_seg.value() or qrexport.DEFAULT_SIZE)

    def transparent(self) -> bool:
        return self.bg_seg.value() == "transparent"

    def _toggle_options(self, on: bool) -> None:
        self.options.setVisible(on)
        self.adjustSize()

    def regenerate(self) -> None:
        ok = self.preview.set_text(self._payload) if self._payload else False
        self.save_btn.setEnabled(ok)
        self.copy_btn.setEnabled(ok)
        self.status.setText("" if ok else
                            "The QR code could not be generated. Install the “qrcode” package "
                            "or check the link.")
        self.status.setVisible(not ok)

    def _busy(self, on: bool) -> None:
        for b in (self.save_btn, self.copy_btn, self.regen_btn):
            b.setEnabled(not on)
        if not on:
            self.regenerate()

    # -------------------------------------------------------------- actions
    def _ask_path(self) -> str:
        start = os.path.join(os.path.expanduser("~"), qrexport.DEFAULT_FILENAME)
        path, _ = QFileDialog.getSaveFileName(self, "Save QR Code", start,
                                              "PNG image (*.png)")
        return path or ""

    def save(self) -> None:
        if self._worker is not None:
            return
        path = self._ask_path()
        if not path:
            return                                # cancelled
        self._start("save", path)

    def copy(self) -> None:
        if self._worker is not None:
            return
        self._start("copy", "")

    def _start(self, mode: str, path: str) -> None:
        self._mode = mode
        self._busy(True)
        w = QrWorker(self._payload, self.size_px(), self.transparent(), path, self)
        w.done.connect(self._on_done)
        w.failed.connect(self._on_failed)
        w.finished.connect(self._on_finished)
        self._worker = w
        w.start()

    def _on_done(self, saved: str, png: bytes) -> None:
        if self._mode == "copy":
            img = QImage.fromData(png, "PNG")
            try:
                if img.isNull():
                    raise RuntimeError("decode")
                cb = QGuiApplication.clipboard()
                if cb is None:
                    raise RuntimeError("no clipboard")
                cb.setImage(img.convertToFormat(QImage.Format.Format_ARGB32))
            except Exception:  # noqa: BLE001
                self._notify("Couldn't copy the QR image to the clipboard.", "error")
                return
            self._notify("QR image copied to the clipboard.", "success")
        else:
            self._notify(f"QR code saved: {os.path.basename(saved)}", "success")

    def _on_failed(self, msg: str) -> None:
        self._notify(msg, "error")

    def _on_finished(self) -> None:
        w, self._worker = self._worker, None
        if w is not None:
            w.deleteLater()
        self._busy(False)

    # ------------------------------------------------------------- shutdown
    def _join(self) -> None:
        w = self._worker
        if w is not None and w.isRunning():
            w.wait(5000)

    def done(self, r: int) -> None:  # noqa: N802
        self._join()
        super().done(r)
