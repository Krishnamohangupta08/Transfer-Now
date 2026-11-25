"""Custom caption bar for the frameless window: logo, menus, min/max/close."""
from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QFrame, QMenuBar, QPushButton, QWidget

from . import icons
from .i18n import i18n, tr
from .iconfx import AnimatedLogo
from .sounds import sounds
from .theme import theme
from .widgets import hbox, kbd, label


class CaptionButton(QPushButton):
    """Minimise / maximise / close — native-feeling 46 x 40 buttons, flush to the corner.

    Painted by hand so the 10 px / 1 px-stroke glyphs are pixel-crisp.  Hover is a
    hairline tint; close hovers #C42B1C with a white glyph; pressed darkens.
    """
    WIDTH, HEIGHT = 46, 40

    def __init__(self, icon: str, close: bool = False) -> None:
        super().__init__()
        self.setObjectName("capclose" if close else "cap")
        self._icon, self._close = icon, close
        self._round = True               # top-right corner follows the window radius
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        theme.changed.connect(self.update)

    def set_icon_name(self, name: str) -> None:
        self._icon = name
        self.update()

    def set_round(self, on: bool) -> None:
        if on != self._round:
            self._round = on
            self.update()

    def refresh(self) -> None:
        self.update()

    def enterEvent(self, e) -> None:  # noqa: N802
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = theme.pal
        w, h = self.width(), self.height()
        hover = self.underMouse()
        down = self.isDown()
        glyph = QColor(c["text"])
        if hover or down:
            if self._close:
                fill = QColor("#B32619" if down else "#C42B1C")
                glyph = QColor("#FFFFFF")
            else:
                fill = QColor(c["text"])
                fill.setAlpha(34 if down else 20)
            path = QPainterPath()
            if self._close and self._round:
                path.addRoundedRect(QRectF(-24, 0, w + 24, h), 8, 8)
                clip = QPainterPath()
                clip.addRect(QRectF(0, 0, w, h))
                path = path.intersected(clip)
            else:
                path.addRect(QRectF(0, 0, w, h))
            p.fillPath(path, fill)
        pen = QPen(glyph, 1.0)
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        cx, cy = w / 2, h / 2
        if self._icon == "cap-min":
            p.drawLine(QPointF(cx - 5, cy + 0.5), QPointF(cx + 5, cy + 0.5))
        elif self._icon == "cap-max":
            p.drawRect(QRectF(cx - 4.5, cy - 4.5, 9, 9))
        elif self._icon == "cap-restore":
            p.drawRect(QRectF(cx - 5, cy - 2.5, 7.5, 7.5))
            p.drawPolyline([QPointF(cx - 2.5, cy - 2.5), QPointF(cx - 2.5, cy - 5.0),
                            QPointF(cx + 5.0, cy - 5.0), QPointF(cx + 5.0, cy + 2.5),
                            QPointF(cx + 2.5, cy + 2.5)])
        else:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.drawLine(QPointF(cx - 5, cy - 5), QPointF(cx + 5, cy + 5))
            p.drawLine(QPointF(cx + 5, cy - 5), QPointF(cx - 5, cy + 5))


class PaletteButton(QFrame):
    """Centred search pill (28 px): magnifier · placeholder · [Ctrl] [K] keycaps."""
    clicked = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("palbtn")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = hbox(self, (12, 0, 6, 0), 8)
        self.icon_lb = label("")
        self.icon_lb.setFixedSize(15, 15)
        lay.addWidget(self.icon_lb, 0, Qt.AlignmentFlag.AlignVCenter)
        self.text = label(tr("tb.search"))
        lay.addWidget(self.text, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addStretch(1)
        self.keys = QWidget()
        kl = hbox(self.keys, (0, 0, 0, 0), 4)
        kl.addWidget(kbd("Ctrl"), 0, Qt.AlignmentFlag.AlignVCenter)
        kl.addWidget(kbd("K"), 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self.keys, 0, Qt.AlignmentFlag.AlignVCenter)
        self.setToolTip("Command palette (Ctrl+K)")
        theme.changed.connect(self.refresh)
        i18n.changed.connect(self._on_language)   # bound method: auto-disconnects on delete
        self.refresh()

    def _on_language(self) -> None:
        self.text.setText(tr("tb.search"))

    def mousePressEvent(self, e) -> None:  # noqa: N802
        # swallow the press so the title bar never starts a window drag (which eats the click)
        e.accept()

    def mouseDoubleClickEvent(self, e) -> None:  # noqa: N802
        e.accept()

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        e.accept()

    def refresh(self) -> None:
        self.icon_lb.setPixmap(icons.pixmap("search", theme.pal["muted"], 14, 1.8))


class TitleBar(QFrame):
    HEIGHT = 40

    def __init__(self, win: QWidget) -> None:
        super().__init__()
        self._win = win
        self.setObjectName("titlebar")
        self.setFixedHeight(self.HEIGHT)
        lay = hbox(self, (10, 0, 0, 0), 8)
        lay.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self.logo = AnimatedLogo(20, pad=2, drag_through=True)
        lay.addWidget(self.logo)
        self.title = label("Transfer Now", "h2")
        self.title.setStyleSheet("font-size: 13px; font-weight: 600; letter-spacing: 0px;")
        lay.addWidget(self.title)
        lay.addSpacing(10)
        self.menubar = QMenuBar()
        self.menubar.setObjectName("tbmenu")
        self.menubar.setNativeMenuBar(False)
        self.menubar.setFixedHeight(self.HEIGHT)
        lay.addWidget(self.menubar, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addStretch(1)
        self.btn_min = CaptionButton("cap-min")
        self.btn_max = CaptionButton("cap-max")
        self.btn_close = CaptionButton("cap-close", close=True)
        self.btn_min.setToolTip("Minimize")
        self.btn_close.setToolTip("Close")
        self.btn_min.clicked.connect(self._minimize)
        self.btn_max.clicked.connect(self.toggle_max)
        self.btn_close.clicked.connect(win.close)
        for b in (self.btn_min, self.btn_max, self.btn_close):
            lay.addWidget(b, 0, Qt.AlignmentFlag.AlignTop)
        # the search pill floats, truly centred in the bar (not in the leftover space)
        self.palette_btn = PaletteButton()
        self.palette_btn.setParent(self)
        self.palette_btn.raise_()
        self.sync()

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._place_palette()

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        self._place_palette()

    def _place_palette(self) -> None:
        w = self.width()
        left = self.layout().contentsMargins().left() + 24 + 8 + self.title.sizeHint().width() + 10 \
            + self.menubar.sizeHint().width()
        right = CaptionButton.WIDTH * 3
        avail = w / 2 - max(left, right) - 16          # half-width free on each side of centre
        pw = int(max(180, min(360, avail * 2)))
        self.palette_btn.setFixedSize(pw, 28)
        self.palette_btn.move(int((w - pw) / 2), int((self.height() - 28) / 2))
        self.palette_btn.keys.setVisible(pw >= 260)

    def _minimize(self) -> None:
        sounds.play("minimize")
        self._win.showMinimized()

    def toggle_max(self) -> None:
        w = self._win
        if w.isMaximized() or w.isFullScreen():
            sounds.play("restore")
            w.showNormal()
        else:
            sounds.play("maximize")
            w.showMaximized()

    def sync(self) -> None:
        maxed = self._win.isMaximized()
        self.btn_max.set_icon_name("cap-restore" if maxed else "cap-max")
        self.btn_close.set_round(not maxed)
        self.btn_max.setToolTip("Restore Down" if maxed else "Maximize")

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and not self._win.isMaximized():
            h = self._win.windowHandle()
            if h is not None:
                h.startSystemMove()
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self.toggle_max()
