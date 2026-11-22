"""Small, fast building blocks.  Chrome is styled by the global QSS in
``theme.py``; only genuinely custom visuals (switch, sparkline, charts, QR)
paint themselves."""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import (
    QAbstractAnimation, QPointF, QPropertyAnimation, pyqtProperty,
    QRectF, QSize, Qt, QVariantAnimation, pyqtSignal,
)
from PyQt6.QtCore import QPoint
from PyQt6.QtGui import (
    QColor, QFont, QFontMetrics, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
)
from PyQt6.QtWidgets import (
    QAbstractButton, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QProgressBar,
    QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from . import icons, motion
from .i18n import LANGS, flag_pixmap, i18n, tr
from .sounds import sounds
from .theme import theme


# ----------------------------------------------------------------- helpers
def repolish(w: QWidget) -> None:
    w.style().unpolish(w)
    w.style().polish(w)
    w.update()


def label(text: str = "", role: str = "", wrap: bool = False,
          selectable: bool = False) -> QLabel:
    lb = QLabel(text)
    if role:
        lb.setProperty("role", role)
    lb.setWordWrap(wrap)
    if selectable:
        lb.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return lb


def hline() -> QFrame:
    f = QFrame()
    f.setObjectName("divider")
    f.setFixedHeight(1)
    return f


def vbox(parent: Optional[QWidget] = None, margins=(0, 0, 0, 0), spacing: int = 0) -> QVBoxLayout:
    lay = QVBoxLayout(parent) if parent is not None else QVBoxLayout()
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    return lay


def hbox(parent: Optional[QWidget] = None, margins=(0, 0, 0, 0), spacing: int = 0) -> QHBoxLayout:
    lay = QHBoxLayout(parent) if parent is not None else QHBoxLayout()
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    return lay


def clear_layout(lay) -> None:
    while lay.count():
        item = lay.takeAt(0)
        w = item.widget()
        if w is not None:
            w.setParent(None)
            w.deleteLater()
        elif item.layout() is not None:
            clear_layout(item.layout())


# ------------------------------------------------------------------- icons
class IconLabel(QLabel):
    """A tinted SVG icon that re-colours itself on theme change."""

    def __init__(self, name: str, size: int = 20, color: str = "text2",
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._name, self._size, self._color = name, size, color
        self.setFixedSize(size, size)
        self.setScaledContents(False)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        theme.changed.connect(self._refresh)
        self._refresh()

    def set_icon(self, name: str, color: Optional[str] = None) -> None:
        self._name = name
        if color:
            self._color = color
        self._refresh()

    def _refresh(self) -> None:
        self.setPixmap(icons.pixmap(self._name, theme.pal.get(self._color, self._color),
                                    self._size))


class Badge(QLabel):
    """Round, softly tinted tile with a centred icon (stat icons, status icons)."""

    def __init__(self, name: str, size: int = 40, color: str = "accent",
                 parent: Optional[QWidget] = None, round_: bool = True) -> None:
        super().__init__(parent)
        self.setObjectName("badge")
        self._name, self._color, self._isz = name, color, max(12, int(size * 0.48))
        self._round = round_
        self._pm = None
        self.setFixedSize(size, size)
        theme.changed.connect(self._refresh)
        self._refresh()

    def set_icon(self, name: str) -> None:
        self._name = name
        self._refresh()

    def _refresh(self) -> None:
        self._pm = icons.pixmap(self._name, theme.pal.get(self._color, self._color), self._isz, 1.7)
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = theme.pal
        if self._color == "accent":
            fill = QColor(c["accent_soft"])
        else:
            fill = _blend(QColor(c["surface"]), QColor(c.get(self._color, self._color)),
                          0.20 if theme.dark else 0.11)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(fill)
        rad = self.width() / 2 if self._round else self.width() * 0.3
        p.drawRoundedRect(QRectF(self.rect()), rad, rad)
        if self._pm is not None:
            p.drawPixmap(QPoint((self.width() - self._isz) // 2, (self.height() - self._isz) // 2),
                         self._pm)


class ExtTile(QWidget):
    """Small outlined file-type tile showing the extension (ZIP, IMG, MP4)."""

    def __init__(self, name_or_ext: str = "", size: int = 36, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._text = ""
        self.set_name(name_or_ext)
        theme.changed.connect(self.update)

    @staticmethod
    def label_for(name: str) -> str:
        import os
        ext = os.path.splitext(name)[1].lstrip(".").upper() if "." in name else name.upper()
        alias = {"JPG": "IMG", "JPEG": "IMG", "PNG": "IMG", "GIF": "IMG", "WEBP": "IMG",
                 "HEIC": "IMG", "SVG": "IMG", "BMP": "IMG"}
        return (alias.get(ext, ext) or "FILE")[:4]

    def set_name(self, name: str) -> None:
        self._text = self.label_for(name)
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = theme.pal
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor(c["border_strong"]), 1))
        p.setBrush(QColor(c["surface3"]))
        p.drawRoundedRect(r, 11, 11)
        f = QFont(self.font())
        f.setPixelSize(10)
        f.setWeight(QFont.Weight.Bold)
        p.setFont(f)
        p.setPen(QColor(c["text2"]))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, self._text)


class Button(QPushButton):
    """QPushButton with a theme-aware icon and QSS ``variant`` / ``size``."""

    def __init__(self, text: str = "", variant: str = "", icon: str = "",
                 size: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        if variant:
            self.setProperty("variant", variant)
        if size:
            self.setProperty("size", size)
        self._icon = icon
        self._text_key = ""
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setIconSize(QSize(17, 17))
        theme.changed.connect(self._refresh_icon)
        i18n.changed.connect(self._retranslate)
        self._refresh_icon()

    def set_icon_name(self, name: str) -> None:
        self._icon = name
        self._refresh_icon()

    def set_text_key(self, key: str, **kw) -> None:
        """Bind this button's text to a translation key (auto-updates)."""
        self._text_key = key
        self.setText(tr(key, **kw))

    def _retranslate(self) -> None:
        if self._text_key:
            self.setText(tr(self._text_key))

    def _refresh_icon(self) -> None:
        if not self._icon:
            return
        variant = self.property("variant")
        color = (theme.pal["on_ink"] if variant == "primary" else
                 theme.pal["on_accent"] if variant == "signal" else
                 theme.pal["danger"] if variant == "danger" else theme.pal["text"])
        gap = 7 if self.text() else 0          # Qt only leaves ~4 px between icon and label
        self._icon_gap = gap
        self.setIconSize(QSize(17 + gap, 17))
        ic = QIcon()
        ic.addPixmap(self._padded(icons.pixmap(self._icon, color, 17, 1.6), gap), QIcon.Mode.Normal)
        ic.addPixmap(self._padded(icons.pixmap(self._icon, theme.pal["muted"], 17, 1.6), gap),
                     QIcon.Mode.Disabled)
        self.setIcon(ic)

    @staticmethod
    def _padded(pm: QPixmap, gap: int) -> QPixmap:
        """The icon pixmap with ``gap`` transparent px on its right edge."""
        if not gap:
            return pm
        dpr = pm.devicePixelRatio() or 1.0
        out = QPixmap(int(pm.width() + gap * dpr), pm.height())
        out.setDevicePixelRatio(dpr)
        out.fill(Qt.GlobalColor.transparent)
        q = QPainter(out)
        q.drawPixmap(0, 0, pm)
        q.end()
        return out

    def setText(self, text: str) -> None:  # noqa: N802
        had = bool(self.text())
        super().setText(text)
        if self._icon and had != bool(text):
            self._refresh_icon()


class NavButton(QPushButton):
    """Sidebar entry, Windows 11 NavigationView style.

    Active = white tile with a hairline border, a 3 px Signal bar on its left
    edge and a Signal-tinted icon.  An optional Signal count badge sits right.
    """
    HEIGHT = 38

    def __init__(self, text: str, icon: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(text.replace("&", "&&"), parent)
        self.setObjectName("nav")
        self._icon = icon
        self._text = text
        self._badge = 0
        self._collapse = 0.0                 # 0 = expanded row, 1 = icon-only rail
        self.setCheckable(True)
        self.setFixedHeight(self.HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        theme.changed.connect(self.update)
        i18n.changed.connect(self._retranslate)

    def _retranslate(self) -> None:
        self.setText(self._text.replace("&", "&&"))
        self.update()

    def set_text(self, text: str) -> None:
        self._text = text
        self._retranslate()

    def set_badge(self, n: int) -> None:
        n = max(0, int(n))
        if n != self._badge:
            self._badge = n
            self.update()

    def set_collapse(self, t: float) -> None:
        """Sidebar animation hook: icon slides to the centre, label fades out."""
        if abs(t - self._collapse) > 1e-4:
            self._collapse = t
            self.update()

    def _refresh_icon(self) -> None:      # kept for API compatibility
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
        r = QRectF(0.5, 0.5, w - 1, h - 1)
        on = self.isChecked()
        if on:
            p.setPen(QPen(QColor(c["border"]), 1))
            p.setBrush(QColor(c["surface"] if not theme.dark else c["surface_hi"]))
            p.drawRoundedRect(r, 10, 10)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(c["accent"]))
            p.drawRoundedRect(QRectF(1.0, h / 2 - 8, 3, 16), 1.5, 1.5)
        elif self.underMouse() and self.isEnabled():
            hov = QColor(c["text"])
            hov.setAlpha(14)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(hov)
            p.drawRoundedRect(r, 10, 10)
        t = self._collapse
        ix = 14 + ((w - 17) / 2 - 14) * t               # icon glides to the rail centre
        pm = icons.pixmap(self._icon, c["accent"] if on else c["text2"], 17, 1.6)
        p.drawPixmap(QPoint(round(ix), (h - 17) // 2), pm)
        label_op = max(0.0, 1.0 - t * 2.4)
        f = QFont(self.font())
        f.setPixelSize(13)
        f.setWeight(QFont.Weight.DemiBold if on else QFont.Weight.Medium)
        right = 12
        if self._badge:
            if t > 0.4:                                  # rail: a small dot on the icon corner
                p.setOpacity(min(1.0, (t - 0.4) * 2.5))
                p.setPen(QPen(QColor(c["sidebar"]), 2))
                p.setBrush(QColor(c["accent"]))
                p.drawEllipse(QRectF(ix + 10, (h - 17) / 2 - 3, 9, 9))
                p.setOpacity(1.0)
            if label_op > 0.01:
                txt = str(self._badge) if self._badge < 100 else "99+"
                bf = QFont(f)
                bf.setPixelSize(11)
                bf.setWeight(QFont.Weight.DemiBold)
                bw = max(20, QFontMetrics(bf).horizontalAdvance(txt) + 12)
                br = QRectF(w - 12 - bw, (h - 18) / 2, bw, 18)
                p.setOpacity(label_op)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(c["accent"]))
                p.drawRoundedRect(br, 9, 9)
                p.setFont(bf)
                p.setPen(QColor(c["on_accent"]))
                p.drawText(br, Qt.AlignmentFlag.AlignCenter, txt)
                p.setOpacity(1.0)
                right = bw + 20
        if label_op > 0.01:
            p.setFont(f)
            p.setPen(QColor(c["text"] if on else c["text2"]))
            p.setOpacity(label_op)
            tx = 42
            shown = QFontMetrics(f).elidedText(self._text, Qt.TextElideMode.ElideRight,
                                               max(10, int(w - tx - right)))
            p.drawText(QRectF(tx, 0, max(10, w - tx - right), h),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, shown)
            p.setOpacity(1.0)
        if self.hasFocus():
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(c["accent"]), 2))
            p.drawRoundedRect(QRectF(1, 1, w - 2, h - 2), 10, 10)


class ThemeToggle(QWidget):
    """Sun / moon segmented toggle at the foot of the sidebar."""
    picked = pyqtSignal(str)            # "light" | "dark"

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._dark = theme.dark
        self.setFixedSize(68, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Light / dark")
        theme.changed.connect(self._sync)

    def _sync(self) -> None:
        self._dark = theme.dark
        self.update()

    def set_dark(self, on: bool) -> None:
        self._dark = on
        self.update()

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.picked.emit("light" if e.position().x() < self.width() / 2 else "dark")
        super().mouseReleaseEvent(e)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = theme.pal
        w, h = self.width(), self.height()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(c["surface3"] if theme.dark else c["surface_hi"]))
        p.drawRoundedRect(QRectF(0, 0, w, h), h / 2, h / 2)
        half = w / 2
        tile = QRectF((half if self._dark else 0) + 2.5, 2.5, half - 5, h - 5)
        p.setPen(QPen(QColor(c["border"]), 1))
        p.setBrush(QColor(c["surface_hi"] if theme.dark else c["surface"]))
        p.drawRoundedRect(tile, tile.height() / 2, tile.height() / 2)
        for i, name in enumerate(("sun", "moon")):
            active = (i == 1) == self._dark
            pm = icons.pixmap(name, c["text"] if active else c["muted"], 15, 1.6)
            p.drawPixmap(QPoint(int(i * half + half / 2 - 7.5), int((h - 15) / 2)), pm)


class Segmented(QFrame):
    """Compact segmented control (exclusive)."""
    changed = pyqtSignal(str)

    def __init__(self, options: list[tuple[str, str]], current: str,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("segwrap")
        lay = hbox(self, (3, 3, 3, 3), 2)
        self._btns: dict[str, QPushButton] = {}
        for key, text in options:
            b = QPushButton(text)
            b.setObjectName("seg")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self.set_value(k, True))
            self._btns[key] = b
            lay.addWidget(b)
        self.set_value(current, False)

    def set_option_text(self, key: str, text: str) -> None:
        b = self._btns.get(key)
        if b is not None:
            b.setText(text)

    def value(self) -> str:
        return next((k for k, b in self._btns.items() if b.isChecked()), "")

    def set_value(self, key: str, emit: bool = False) -> None:
        for k, b in self._btns.items():
            b.setChecked(k == key)
        if emit:
            self.changed.emit(key)


class Pill(QLabel):
    def __init__(self, text: str = "", tone: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("pill")
        self.set_tone(tone)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

    def set_tone(self, tone: str) -> None:
        self.setProperty("tone", tone)
        repolish(self)


class IconPill(QFrame):
    """Pill with a leading SVG icon — verified/checked status chips.

    Shares the ``#pill`` QSS with :class:`Pill` (same tones), so both look
    identical except for the icon.
    """

    _TONES = {"success": "success", "danger": "danger", "warning": "warning",
              "accent": "accent"}

    def __init__(self, icon_name: str = "", text: str = "", tone: str = "",
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("pill")
        lay = hbox(self, (9, 2, 10, 2), 5)
        self.icon = IconLabel(icon_name, 13, "text2")
        lay.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignVCenter)
        self.text = label(text)
        lay.addWidget(self.text, 0, Qt.AlignmentFlag.AlignVCenter)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.set_tone(tone)

    def set_text(self, text: str) -> None:
        self.text.setText(text)

    def set_tone(self, tone: str) -> None:
        self.setProperty("tone", tone)
        self.icon._color = self._TONES.get(tone, "text2")  # noqa: SLF001
        self.icon._refresh()                               # noqa: SLF001
        repolish(self)


class ElidedLabel(QLabel):
    """Single-line label that ellipsises instead of forcing the layout wider."""

    def __init__(self, text: str = "", role: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        if role:
            self.setProperty("role", role)
        self._full = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(10)
        self.set_full_text(text)

    def set_full_text(self, text: str) -> None:
        self._full = text or ""
        self._elide()

    def full_text(self) -> str:
        return self._full

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._elide()

    def _elide(self) -> None:
        fm = QFontMetrics(self.font())
        shown = fm.elidedText(self._full, Qt.TextElideMode.ElideMiddle, max(10, self.width()))
        if shown != self.text():
            self.setText(shown)
        self.setToolTip(self._full if shown != self._full else "")


# ------------------------------------------------------------------ switch
def _blend(a: QColor, b: QColor, t: float) -> QColor:
    """Linear blend of two colours (t = 0 → a, t = 1 → b)."""
    return QColor(
        round(a.red() + (b.red() - a.red()) * t),
        round(a.green() + (b.green() - a.green()) * t),
        round(a.blue() + (b.blue() - a.blue()) * t))


class Switch(QAbstractButton):
    """macOS-style animated toggle.

    The OFF track is blended from ``surface3`` toward ``text2``/``muted`` so
    it stays clearly visible on every theme — including Deep Black, where the
    old bare ``surface3`` track vanished into the pure-black background.
    Adds a hairline glass border, a floating knob with a soft drop shadow,
    hover/press feedback and a 130 ms slide animation.
    """

    SIZE = QSize(48, 30)          # 42x24 track + 3 px all round for the focus ring
    PAD = 3.0

    def __init__(self, checked: bool = False, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setFixedSize(self.SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._pos = 1.0 if checked else 0.0          # knob position 0..1
        self._anim: Optional[QVariantAnimation] = None
        self.toggled.connect(self._slide)
        theme.changed.connect(self.update)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.SIZE)

    # ------------------------------------------------------------- animation
    def _slide(self, on: bool) -> None:
        self._animate(1.0 if on else 0.0)

    def _animate(self, target: float) -> None:
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(motion.DUR)          # 150 ms, cubic-bezier(0.4,0,0.2,1)
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(target)
        self._anim.setEasingCurve(motion.ease())
        self._anim.valueChanged.connect(self._tick)
        self._anim.finished.connect(self._anim_done)
        self._anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)

    def _tick(self, v) -> None:
        self._pos = float(v)
        self.update()

    def _anim_done(self) -> None:
        self._pos = 1.0 if self.isChecked() else 0.0
        self._anim = None
        self.update()

    def _snap(self) -> None:
        """Jump to the end state (theme switch, programmatic set)."""
        self._pos = 1.0 if self.isChecked() else 0.0
        self.update()

    def setChecked(self, on: bool) -> None:  # noqa: N802
        super().setChecked(on)
        self._snap()

    # ------------------------------------------------------------- repainting
    def enterEvent(self, e) -> None:  # noqa: N802
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        self.update()
        super().leaveEvent(e)

    def _track_colors(self) -> tuple[QColor, QColor, QColor]:
        """(off, off-hover, edge) — visible on every palette, incl. OLED black."""
        c = theme.pal
        if theme.dark:
            base = _blend(QColor(c["surface3"]), QColor(c["text2"]), 0.32)
            hov = _blend(QColor(c["surface3"]), QColor(c["text2"]), 0.44)
            edge = QColor(255, 255, 255, 38)
        else:
            base = _blend(QColor(c["surface3"]), QColor(c["muted"]), 0.36)
            hov = _blend(QColor(c["surface3"]), QColor(c["muted"]), 0.50)
            edge = QColor(13, 22, 48, 36)
        return base, hov, edge

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = theme.pal
        t = self._pos
        hov = self.underMouse() and self.isEnabled()

        off, off_hov, edge = self._track_colors()
        on = QColor(c["accent_hover"] if hov else c["accent"])
        track = _blend(off_hov if hov else off, on, t)

        # ---- track (inset 0.5 px so the hairline never clips)
        pad = self.PAD
        rect = QRectF(pad + 0.5, pad + 0.5, 41, 23)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(rect, 11.75, 11.75)
        if t < 0.97:                      # border fades out as the track turns accent
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(edge, 1))
            p.drawRoundedRect(rect, 11.75, 11.75)

        # ---- knob: soft drop shadow + floating white circle
        kx = pad + 12.5 + 17.0 * t          # 3.5 px of track visible around the knob
        ky = pad + 12.0
        if self.isEnabled():
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 70 if theme.dark else 52))
            p.drawEllipse(QPointF(kx, ky + 1.2), 8.0, 8.0)
        if not self.isEnabled():
            p.setOpacity(0.45)
        p.setBrush(QColor("#FFFFFF"))
        kr = 8.0 if self.isDown() else 8.5
        p.drawEllipse(QPointF(kx, ky), kr, kr)

        # ---- focus ring
        if self.hasFocus():
            p.setOpacity(1.0)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(c["accent"]), 2))
            p.drawRoundedRect(QRectF(0.75, 0.75, 46.5, 28.5), 14.5, 14.5)


class Swatch(QAbstractButton):
    """Round accent colour chooser."""

    def __init__(self, color: str, name: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._color = QColor(color)
        self.setCheckable(True)
        self.setFixedSize(32, 32)
        self.setToolTip(name)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_color(self, color: str) -> None:
        self._color = QColor(color)
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._color)
        p.drawEllipse(QRectF(5, 5, 22, 22))
        if self.isChecked():
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(self._color, 2))
            p.drawEllipse(QRectF(1.5, 1.5, 29, 29))
            p.setPen(QPen(QColor("#FFFFFF" if self._color.lightnessF() < 0.62 else "#111"), 2,
                          Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                          Qt.PenJoinStyle.RoundJoin))
            path = QPainterPath()
            path.moveTo(11, 16.5)
            path.lineTo(14.5, 20)
            path.lineTo(21, 12.5)
            p.drawPath(path)


class FlagButton(QPushButton):
    """Language chooser tile: flag + native name."""

    def __init__(self, code: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.code = code
        name = LANGS[code][1]
        self.setText("  " + name)
        self.setObjectName("flag")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setIconSize(QSize(24, 16))
        self.setIcon(QIcon(flag_pixmap(LANGS[code][2], 24, 16)))
        self.setToolTip(LANGS[code][0])


# ----------------------------------------------------------------- charts
class Sparkline(QWidget):
    """Tiny trend line: 1.5 px Signal stroke, no fill, no end dot."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._vals: list[float] = []
        self.setFixedSize(66, 26)

    def set_values(self, vals: list[float]) -> None:
        self._vals = list(vals)
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        n = len(self._vals)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = theme.pal
        w, h = self.width(), self.height()
        if n < 2:
            return
        top, low = max(self._vals), min(self._vals)
        if top <= 0:
            p.setPen(QPen(QColor(c["border_strong"]), 1.5, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(1, h - 3), QPointF(w - 1, h - 3))
            return
        span = (top - low) or 1.0
        pts = [QPointF(1 + i * (w - 2) / (n - 1), h - 3 - ((v - low) / span) * (h - 6))
               for i, v in enumerate(self._vals)]
        line = QPainterPath(pts[0])
        for pt in pts[1:]:
            line.lineTo(pt)
        p.setPen(QPen(QColor(c["accent"]), 1.5, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.drawPath(line)


class SpeedGraph(QWidget):
    """Live rolling speed chart for transfers (bytes/s samples)."""

    CAP = 48

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._vals: list[float] = []
        self.setMinimumHeight(44)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def push(self, bps: float) -> None:
        self._vals.append(max(0.0, float(bps)))
        if len(self._vals) > self.CAP:
            self._vals = self._vals[-self.CAP:]
        self.update()

    def clear(self) -> None:
        self._vals.clear()
        self.update()

    def set_values(self, vals: list[float]) -> None:
        self._vals = [max(0.0, float(v)) for v in vals][-self.CAP:]
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = theme.pal
        w, h = self.width(), self.height()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(c["surface2"]))
        p.drawRoundedRect(QRectF(0, 0, w, h), 8, 8)
        if len(self._vals) < 2:
            p.setPen(QPen(QColor(c["border_strong"]), 1.5, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(6, h / 2), QPointF(w - 6, h / 2))
            return
        top = max(self._vals) or 1.0
        n = len(self._vals)
        step = (w - 10) / (self.CAP - 1)
        x0 = w - 5 - (n - 1) * step           # right-aligned: newest at the right edge
        pts = [QPointF(x0 + i * step, h - 5 - (v / top) * (h - 12)) for i, v in enumerate(self._vals)]
        area = QPainterPath(QPointF(pts[0].x(), h))
        for pt in pts:
            area.lineTo(pt)
        area.lineTo(QPointF(pts[-1].x(), h))
        area.closeSubpath()
        grad = QLinearGradient(0, 0, 0, h)
        top_col = QColor(c["accent"])
        top_col.setAlpha(70)
        bot_col = QColor(c["accent"])
        bot_col.setAlpha(6)
        grad.setColorAt(0.0, top_col)
        grad.setColorAt(1.0, bot_col)
        p.fillPath(area, grad)
        line = QPainterPath(pts[0])
        for pt in pts[1:]:
            line.lineTo(pt)
        p.setPen(QPen(QColor(c["accent"]), 1.8, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.drawPath(line)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(c["accent"]))
        p.drawEllipse(pts[-1], 2.6, 2.6)


class BarChart(QWidget):
    """Seven capsule bars — muted tint, today in Signal with an ink value bubble."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._vals: list[float] = []
        self._labels: list[str] = []
        self._bubble = ""
        self.setMinimumHeight(190)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_values(self, vals: list[float], labels: list[str], bubble: str = "") -> None:
        self._vals = list(vals)
        self._labels = list(labels)
        self._bubble = bubble
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = theme.pal
        w, h = self.width(), self.height()
        n = len(self._vals)
        if not n:
            return
        label_h, top_pad, gap = 24, 38, 12
        chart_h = max(40, h - label_h - top_pad)
        base = top_pad + chart_h
        bw = min(66.0, (w - gap * (n - 1)) / n)
        span = n * bw + (n - 1) * gap
        x0 = (w - span) / 2
        top = max(self._vals) or 1.0
        min_h = min(40.0, chart_h * 0.3)
        muted_fill = QColor(c["surface_hi"] if not theme.dark else c["surface3"])
        f = QFont(self.font())
        f.setPixelSize(12)
        p.setFont(f)
        for i, v in enumerate(self._vals):
            today = i == n - 1
            x = x0 + i * (bw + gap)
            bh = min_h + (chart_h - min_h) * (v / top if top else 0.0)
            r = QRectF(x, base - bh, bw, bh)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(c["accent"]) if today else muted_fill)
            rad = min(bw, bh) / 2
            p.drawRoundedRect(r, rad, rad)
            if today and self._bubble:
                bf = QFont(f)
                bf.setPixelSize(12)
                bf.setWeight(QFont.Weight.DemiBold)
                bwid = QFontMetrics(bf).horizontalAdvance(self._bubble) + 22
                br = QRectF(x + bw / 2 - bwid / 2, r.top() - 30, bwid, 24)
                br.moveLeft(max(0.0, min(br.left(), w - bwid)))
                p.setBrush(QColor(c["ink"]))
                p.drawRoundedRect(br, 12, 12)
                p.setFont(bf)
                p.setPen(QColor(c["on_ink"]))
                p.drawText(br, Qt.AlignmentFlag.AlignCenter, self._bubble)
                p.setFont(f)
            lf = QFont(f)
            lf.setWeight(QFont.Weight.DemiBold if today else QFont.Weight.Normal)
            p.setFont(lf)
            p.setPen(QColor(c["text"] if today else c["muted"]))
            p.drawText(QRectF(x - 6, h - label_h, bw + 12, label_h),
                       Qt.AlignmentFlag.AlignCenter,
                       self._labels[i] if i < len(self._labels) else "")


class DonutRing(QWidget):
    """Thin segmented ring (12 px) with the percentage in the centre."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._segs: list[tuple[float, str]] = []
        self._text = ""
        self.setMinimumSize(120, 120)

    def set_segments(self, segs: list[tuple[float, str]], text: str = "") -> None:
        self._segs = [(max(0.0, float(f)), k) for f, k in segs]
        self._text = text
        self.update()

    def set_value(self, frac: float, text: str = "", sub: str = "") -> None:
        frac = max(0.0, min(1.0, frac))
        self.set_segments([(frac, "success"), (1.0 - frac, "surface_hi")], text)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = theme.pal
        side = min(self.width(), self.height())
        thick = 12.0
        rect = QRectF((self.width() - side) / 2 + thick / 2 + 1, (self.height() - side) / 2 + thick / 2 + 1,
                      side - thick - 2, side - thick - 2)
        total = sum(f for f, _k in self._segs)
        if total <= 0:
            p.setPen(QPen(QColor(c["surface_hi"]), thick, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
            p.drawArc(rect, 0, 360 * 16)
        else:
            live = [(f, k) for f, k in self._segs if f > 0]
            gap = 3.0 if len(live) > 1 else 0.0
            ang = 90.0
            for f, k in live:
                sweep = 360.0 * f / total
                p.setPen(QPen(QColor(c.get(k, k)), thick, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
                p.drawArc(rect, int((ang - gap / 2) * 16), int(-(sweep - gap) * 16))
                ang -= sweep
        f = QFont("Inter Tight", 12)
        f.setPixelSize(30)
        f.setWeight(QFont.Weight.Normal)
        p.setFont(f)
        p.setPen(QColor(c["text"]))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._text)


class QrWidget(QWidget):
    """Draws a QR code if the optional ``qrcode`` package is installed."""

    def __init__(self, size: int = 132, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._m: list[list[bool]] = []
        self.setFixedSize(size, size)

    @staticmethod
    def available() -> bool:
        try:
            import qrcode  # noqa: F401
            return True
        except Exception:  # noqa: BLE001
            return False

    def set_text(self, text: str) -> bool:
        try:
            import qrcode
            qr = qrcode.QRCode(border=0, error_correction=qrcode.constants.ERROR_CORRECT_M)
            qr.add_data(text)
            qr.make(fit=True)
            self._m = [list(map(bool, row)) for row in qr.get_matrix()]
        except Exception:  # noqa: BLE001
            self._m = []
        self.update()
        return bool(self._m)

    def paintEvent(self, _e) -> None:  # noqa: N802
        if not self._m:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        p.fillRect(self.rect(), QColor("#FFFFFF"))
        n = len(self._m)
        pad = 8
        cell = (self.width() - 2 * pad) / n
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#111111"))
        for y, row in enumerate(self._m):
            for x, on in enumerate(row):
                if on:
                    p.drawRect(QRectF(pad + x * cell, pad + y * cell, cell + 0.4, cell + 0.4))


def kbd(text: str = "", icon_name: str = "") -> QFrame:
    """Compact keyboard-key pill: fixed 22 px tall, never stretches with its row.

    Pass ``icon_name`` to put a small SVG icon inside the keycap instead of
    text (e.g. arrow keys) — every glyph in the UI is real SVG.
    """
    pill = QFrame()
    pill.setObjectName("kbd")
    pill.setFixedHeight(20)
    pill.setMinimumWidth(20)
    pill.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
    pl = hbox(pill, (6, 0, 6, 0), 2)
    if icon_name:
        pl.addWidget(IconLabel(icon_name, 13, "text2"))
    else:
        lb = label(text, "caption")
        lb.setStyleSheet("font-size: 11px; font-weight: 500;")
        lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pl.addWidget(lb)
    return pill


# ------------------------------------------------------------------- toast
_TOAST_KINDS = {            # kind -> (icon, colour token, kind label, tone)
    "success": ("check-circle", "success", "SUCCESS", "success"),
    "error":   ("alert-circle", "danger", "ERROR", "danger"),
    "warning": ("alert", "warning", "WARNING", "warning"),
    "info":    ("info", "accent", "INFO", "info"),
}
_DURATIONS = {"short": 2600, "normal": 4200, "long": 6800}   # base auto-dismiss
_DUR_FACTOR = {"success": 1.0, "info": 1.0, "warning": 1.15, "error": 1.35}


class ToastCard(QFrame):
    """One modern notification: tinted icon tile, kind line with timestamp,
    message, optional action, close button, optional countdown line.

    Enters with a fade + unfold, leaves with a fade + collapse.  Duration
    (short / normal / long / sticky), countdown bar and sound all follow the
    user's notification settings (Settings → Notifications).
    """
    dismissed = pyqtSignal(object)

    def __init__(self, text: str, kind: str, parent: QWidget,
                 action_text: str = "", on_action=None,
                 settings=None) -> None:
        super().__init__(parent)
        kind = kind if kind in _TOAST_KINDS else "info"
        icon, color, kind_label, tone = _TOAST_KINDS[kind]
        self.text, self.kind = text, kind
        self._closing = False
        self._revealed = False
        self._bar = None
        self._count = None
        self.setObjectName("toast")
        self.setProperty("kind", kind)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Click to dismiss")

        outer = vbox(self, (0, 0, 0, 0), 0)
        row = hbox(margins=(12, 12, 10, 10), spacing=12)
        outer.addLayout(row)

        tile = QFrame()
        tile.setObjectName("toasticon")
        tile.setProperty("kind", kind)
        tile.setFixedSize(36, 36)
        tl = hbox(tile)
        tl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon = IconLabel(icon, 17, color)
        tl.addWidget(self._icon)
        row.addWidget(tile, 0, Qt.AlignmentFlag.AlignTop)

        col = vbox(spacing=3)
        meta = hbox(spacing=6)
        kl = label(kind_label, "toastkind")
        kl.setProperty("kind", kind)
        meta.addWidget(kl)
        meta.addWidget(label("·", "caption"))
        self._time = label(tr("toast.now"), "caption")
        meta.addWidget(self._time)
        meta.addStretch(1)
        for w_ in (kl, self._time):
            w_.hide()
        self._msg = label(text, "", wrap=True)
        self._msg.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        col.addWidget(self._msg)
        row.addLayout(col, 1)

        if action_text:                              # optional inline action
            act = QPushButton(action_text)
            act.setObjectName("toastaction")
            act.setCursor(Qt.CursorShape.PointingHandCursor)
            act.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            act.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

            def run_action() -> None:
                if not self._closing and on_action is not None:
                    on_action()
                self.dismiss()

            act.clicked.connect(run_action)
            row.addWidget(act, 0, Qt.AlignmentFlag.AlignVCenter)

        close = QPushButton()
        close.setObjectName("toastclose")
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        close.setIconSize(QSize(12, 12))
        close.setIcon(QIcon(icons.pixmap("cap-close", theme.pal["muted"], 12, 1.8)))
        close.clicked.connect(self.dismiss)
        row.addWidget(close, 0, Qt.AlignmentFlag.AlignTop)

        # ---- duration: user setting × kind factor ("sticky" never times out)
        dur_key = str(settings.get("toast_dur", "normal")) if settings else "normal"
        self._sticky = dur_key == "sticky"
        base = _DURATIONS.get(dur_key, 4200)
        self._ms = int(base * _DUR_FACTOR.get(kind, 1.0))

        if not self._sticky:
            self._bar = QProgressBar()
            self._bar.setObjectName("toastbar")
            self._bar.setProperty("tone", tone)
            self._bar.setRange(0, 1000)
            self._bar.setValue(1000)
            self._bar.setTextVisible(False)
            bl = hbox(margins=(14, 0, 14, 10))
            bl.addWidget(self._bar)
            outer.addLayout(bl)
            if not (settings.get("toast_bar", False) if settings else False):
                self._bar.hide()
            self._count = QPropertyAnimation(self._bar, b"value", self)
            self._count.setStartValue(1000)
            self._count.setEndValue(0)
            self._count.setDuration(self._ms)
            self._count.finished.connect(self.dismiss)

        # fade in — Shade motion: 200 ms on the signature curve
        self._fx = QGraphicsOpacityEffect(self)
        self._fx.setOpacity(0.0)
        self.setGraphicsEffect(self._fx)
        self._fade = QPropertyAnimation(self._fx, b"opacity", self)
        self._fade.setEasingCurve(motion.ease())
        self._fade.setDuration(motion.DUR + 50)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)

    # ------------------------------------------------------------ reveal/exit
    def _get_reveal(self) -> int:
        return self.height()

    def _set_reveal(self, v: int) -> None:
        self.setFixedHeight(max(1, int(v)))

    revealHeight = pyqtProperty(int, _get_reveal, _set_reveal)

    def start_reveal(self, height: int) -> None:
        """Called by the stack once the card is measured: fade + unfold."""
        self._revealed = True
        self.setFixedHeight(height)
        self._fade.start()
        rev = QPropertyAnimation(self, b"revealHeight", self)
        rev.setDuration(motion.DUR + 50)
        rev.setStartValue(1)
        rev.setEndValue(max(2, height))
        rev.setEasingCurve(motion.ease_out())
        rev.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
        if self._count is not None:
            self._count.start()

    def restart(self) -> None:
        """Same message fired again: refresh instead of stacking a duplicate."""
        if self._closing:
            return
        if self._count is not None:
            self._count.stop()
            self._bar.setValue(1000)
            self._count.start()

    def dismiss(self) -> None:
        if self._closing:
            return
        self._closing = True
        if self._count is not None:
            self._count.stop()
        self._fade.stop()
        self._fade.setDuration(motion.DUR_EXIT)
        self._fade.setEasingCurve(motion.ease_in())
        self._fade.setStartValue(self._fx.opacity())
        self._fade.setEndValue(0.0)
        self._fade.start()
        coll = QPropertyAnimation(self, b"revealHeight", self)
        coll.setDuration(motion.DUR_EXIT + 20)
        coll.setStartValue(max(1, self.height()))
        coll.setEndValue(1)
        coll.setEasingCurve(motion.ease_in())
        coll.finished.connect(lambda: self.dismissed.emit(self))
        coll.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)

    def enterEvent(self, e) -> None:  # noqa: N802
        if (not self._closing and self._count is not None
                and self._count.state() == QPropertyAnimation.State.Running):
            self._count.pause()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        if (not self._closing and self._count is not None
                and self._count.state() == QPropertyAnimation.State.Paused):
            self._count.resume()
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self.dismiss()
        super().mouseReleaseEvent(e)


class Toast(QWidget):
    """Stack of toast notifications pinned to a corner of the parent window.

    Position (bottom/top × right/centre), duration (short / normal / long /
    sticky), countdown bar and sound follow the user's notification settings.
    """
    announced = pyqtSignal(str, str)
    WIDTH = 360
    MAX_VISIBLE = 4
    MARGIN_X = 20
    TOP_INSET = 58                  # clears the title bar

    def __init__(self, parent: QWidget, settings=None) -> None:
        super().__init__(parent)
        self.setObjectName("toasthost")
        self.settings = settings
        self.setFixedWidth(self.WIDTH)
        self.bottom_inset = 44        # keeps toasts above the status bar
        self._lay = vbox(self, (0, 0, 0, 0), 8)   # insertion order, no re-align
        self._cards: list[ToastCard] = []
        self.hide()

    # ------------------------------------------------------------ placement
    @property
    def pos_key(self) -> str:
        k = str(self.settings.get("toast_pos", "br")) if self.settings else "br"
        return k if k in ("br", "bc", "tr", "tc") else "br"

    def reposition(self) -> None:
        """Notification settings changed: re-fit + re-place the stack."""
        self._fit()
        self.place()

    def show_message(self, text: str, kind: str = "success", action_text: str = "",
                     on_action=None) -> None:
        kind = kind if kind in _TOAST_KINDS else "info"
        if self.settings is None or bool(self.settings.get("toast_sound", True)):
            sounds.play(kind)
        self.announced.emit(text, kind)
        for c in self._cards:
            if c.text == text and c.kind == kind and not c._closing:  # noqa: SLF001
                c.restart()
                self.place()
                return
        card = ToastCard(text, kind, self, action_text, on_action, self.settings)
        card.dismissed.connect(self._remove)
        self._cards.append(card)
        self._lay.addWidget(card)
        while len([c for c in self._cards if not c._closing]) > self.MAX_VISIBLE:  # noqa: SLF001
            oldest = next(c for c in self._cards if not c._closing)  # noqa: SLF001
            oldest.dismiss()
        self._fit()
        self.show()
        self.place()
        self.raise_()

    def _remove(self, card: ToastCard) -> None:
        if card in self._cards:
            self._cards.remove(card)
        self._lay.removeWidget(card)
        card.hide()
        card.deleteLater()
        if not self._cards:
            self.hide()
        else:
            self._fit()

    def _fit(self) -> None:
        # Give every card its real height (word-wrapped text needs
        # height-for-width) so cards never get squashed into each other.
        total = 0
        for c in self._cards:
            if c.parent() is not self:  # noqa: SLF001
                continue
            if c._closing:  # noqa: SLF001
                total += c.height()          # collapsing: keep shrinking space
                continue
            c.setFixedWidth(self.WIDTH)
            lay = c.layout()
            lay.activate()
            h = lay.totalHeightForWidth(self.WIDTH) if lay.hasHeightForWidth() else 0
            h = max(h, lay.sizeHint().height(), c.sizeHint().height(), 66)
            if c._revealed:  # noqa: SLF001
                c.setFixedHeight(h)
            else:
                c.start_reveal(h)
            total += h
        total += self._lay.spacing() * max(0, len(self._cards) - 1)
        self.setFixedHeight(max(0, total))
        self.place()

    def place(self) -> None:
        par = self.parentWidget()
        if par is None:
            return
        k = self.pos_key
        x = (par.width() - self.width()) // 2 if k in ("bc", "tc") \
            else par.width() - self.width() - self.MARGIN_X
        y = self.TOP_INSET if k.startswith("t") \
            else par.height() - self.height() - self.bottom_inset
        self.move(x, y)


# ------------------------------------------------------------------ format
def eta_text(seconds: float) -> str:
    seconds = int(max(0, seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60:02d}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60:02d}m"


def speed_text(bps: float) -> str:
    from core.store import human_size
    return human_size(bps) + "/s"
