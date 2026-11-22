"""Transfer Now 1.0.0.0 — "Porcelain & Signal" design system.

Every colour and control metric lives here.  ``theme.apply()`` rebuilds one
application stylesheet (only when the theme/accent really changes).

Language: premium, quiet, precise.  Windows 11 Mica shell (title bar + sidebar
sit on a tinted backdrop), the page lives on a rounded "sheet", content is laid
out as bento tiles.  One accent (Signal) reserved for live / active state; the
primary button is solid ink.  No gradients, no glow, no glass.

    light  — Porcelain          dark   — Graphite
    black / navy / cream are tonal variants; ``system`` follows the OS.
"""
from __future__ import annotations

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

THEME_LABELS = {
    "dark": "Graphite", "black": "Graphite Black", "navy": "Graphite Blue",
    "cream": "Porcelain Warm", "light": "Porcelain", "system": "System",
}

# bg = Mica backdrop (title bar / sidebar), bg2 = the page "sheet", surface = panels.
LIGHT = {
    "bg": "#E7EAF1", "bg2": "#F7F8FB", "sheet": "#F7F8FB", "sidebar": "#E7EAF1", "chrome": "#E7EAF1",
    "surface": "#FFFFFF", "surface_hi": "#E9ECF1",
    "surface2": "#FFFFFF", "surface3": "#EEF0F4", "surface_solid": "#FFFFFF",
    "border": "#E4E7EC", "border_strong": "#D3D8E0",
    "text": "#0F1218", "text2": "#4A5262", "muted": "#7A8294",
    "success": "#0E8F55", "success_bg": "#E1F3EA", "warning": "#B86E00",
    "warning_bg": "#FBEED6", "danger": "#C9372C", "danger_bg": "#FBE7E5",
    "on_accent": "#FFFFFF", "shadow": "#0F1218",
    "ink": "#0F1218", "on_ink": "#FFFFFF",
    "hero_bg": "#0E1117", "hero_fg": "#FFFFFF", "hero_fg2": "#9AA2B1", "hero_chip": "#232733",
    "glass_hi": "#FFFFFF", "hero_a": "#EEF0FF", "hero_b": "#F7F8FB",
}

DARK = {
    "bg": "#07090C", "bg2": "#0D1015", "sheet": "#0D1015", "sidebar": "#07090C", "chrome": "#07090C",
    "surface": "#12151B", "surface_hi": "#212630",
    "surface2": "#12151B", "surface3": "#1A1E26", "surface_solid": "#12151B",
    "border": "#212630", "border_strong": "#2D3340",
    "text": "#ECEEF2", "text2": "#AAB1C0", "muted": "#6F7788",
    "success": "#3CCF8E", "success_bg": "#12291F", "warning": "#F0A93A",
    "warning_bg": "#2B2210", "danger": "#FF6B5E", "danger_bg": "#301816",
    "on_accent": "#0B0D11", "shadow": "#000000",
    "ink": "#ECEEF2", "on_ink": "#0B0D11",
    "hero_bg": "#EEF0F5", "hero_fg": "#0F1218", "hero_fg2": "#6A7183", "hero_chip": "#DDE0E8",
    "glass_hi": "#FFFFFF", "hero_a": "#161A2A", "hero_b": "#0D1015",
}

BLACK = dict(DARK, **{
    "bg": "#030405", "bg2": "#08090C", "sheet": "#08090C", "sidebar": "#030405", "chrome": "#030405",
    "surface": "#0E1015", "surface3": "#161920", "surface_hi": "#1C2028", "surface2": "#0E1015",
    "surface_solid": "#0E1015", "border": "#1B1F27", "border_strong": "#272C36",
})

NAVY = dict(DARK, **{
    "bg": "#080B12", "bg2": "#0D121B", "sheet": "#0D121B", "sidebar": "#080B12", "chrome": "#080B12",
    "surface": "#131924", "surface3": "#1B2230", "surface_hi": "#232C3D", "surface2": "#131924",
    "surface_solid": "#131924", "border": "#222B3B", "border_strong": "#2E394D",
})

CREAM = dict(LIGHT, **{
    "bg": "#ECE8DF", "bg2": "#FAF8F4", "sheet": "#FAF8F4", "sidebar": "#ECE8DF", "chrome": "#ECE8DF",
    "surface": "#FFFFFF", "surface3": "#F0ECE4", "surface_hi": "#E8E3D8", "surface2": "#FFFFFF",
    "surface_solid": "#FFFFFF", "border": "#E6E1D7", "border_strong": "#D5CFC2",
    "text": "#1E1B16", "text2": "#575246", "muted": "#857F70", "ink": "#1E1B16",
    "hero_bg": "#1B1813", "shadow": "#1E1B16",
})

THEMES = {"dark": DARK, "black": BLACK, "navy": NAVY, "cream": CREAM, "light": LIGHT}
DARK_THEMES = {"dark", "black", "navy"}

# name -> (label, dark-mode colour, light-mode colour).  Signal is the default.
ACCENTS = {
    "signal": ("Signal", "#7189FF", "#2A3FFF"),
    "violet": ("Violet", "#9B83FF", "#6D4AE8"),
    "teal": ("Teal", "#3CCFC0", "#0E8F85"),
    "green": ("Green", "#3CCF8E", "#0E8F55"),
    "amber": ("Amber", "#F0A93A", "#B86E00"),
    "red": ("Red", "#FF6B5E", "#C9372C"),
    "graphite": ("Graphite", "#AAB1C0", "#4A5262"),
}
DEFAULT_ACCENT = "signal"


def _mix(a: str, b: str, t: float) -> str:
    ca, cb = QColor(a), QColor(b)
    return QColor(
        round(ca.red() + (cb.red() - ca.red()) * t),
        round(ca.green() + (cb.green() - ca.green()) * t),
        round(ca.blue() + (cb.blue() - ca.blue()) * t)).name()


def _luminance(hex_color: str) -> float:
    """WCAG relative luminance (0..1)."""
    def ch(v: float) -> float:
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    c = QColor(hex_color)
    return 0.2126 * ch(c.redF()) + 0.7152 * ch(c.greenF()) + 0.0722 * ch(c.blueF())


def rgba(hex_color: str, alpha: float) -> str:
    """'#RRGGBB' + alpha 0..1 -> 'rgba(r, g, b, a)' for QSS."""
    c = QColor(hex_color)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {round(alpha * 255)})"


class Theme(QObject):
    changed = pyqtSignal()
    about_to_change = pyqtSignal()      # emitted *before* the restyle (window snapshots itself for a cross-fade)

    def __init__(self) -> None:
        super().__init__()
        self.mode = "dark"          # dark | black | navy | cream | light | system
        self.accent_key = DEFAULT_ACCENT
        self.pal: dict[str, str] = {}
        self.dark = True
        self._qss_cache: dict[tuple, str] = {}
        self._build()

    # ---------------------------------------------------------------- state
    def _resolve_dark(self) -> bool:
        if self.mode == "system":
            app = QApplication.instance()
            try:
                scheme = app.styleHints().colorScheme()
                return scheme != Qt.ColorScheme.Light
            except Exception:  # noqa: BLE001
                return True
        return self.mode in DARK_THEMES

    @property
    def black(self) -> bool:
        return self.mode == "black"

    def _build(self) -> None:
        self.dark = self._resolve_dark()
        if self.mode == "system":
            base = dict(DARK if self.dark else LIGHT)
        else:
            base = dict(THEMES.get(self.mode, DARK))
        key = self.accent_key if self.accent_key in ACCENTS else DEFAULT_ACCENT
        _label, dk, lt = ACCENTS[key]
        acc = dk if self.dark else lt
        base["accent"] = acc
        # Shade button states: hover lifts toward white on dark, dips toward
        # black on light (shadcn ``hover:bg-primary/90`` behaves the same way).
        base["accent_hover"] = _mix(acc, "#FFFFFF" if self.dark else "#000000",
                                    0.12 if self.dark else 0.08)
        base["accent_press"] = _mix(acc, "#000000", 0.22 if self.dark else 0.16)
        base["accent_soft"] = _mix(base["surface"], acc, 0.20 if self.dark else 0.10)
        base["flight"] = _mix(acc, "#FFFFFF", 0.0) if self.dark else _mix(acc, "#FFFFFF", 0.42)
        base["accent_line"] = rgba(acc, 0.45 if self.dark else 0.40)
        base["glow"] = rgba(acc, 0.45 if self.dark else 0.30)
        base["glow_soft"] = rgba(acc, 0.16 if self.dark else 0.10)
        base["hero_a"] = _mix(base["hero_a"], acc, 0.30 if self.dark else 0.16)
        base["hero_b"] = base["hero_b"]
        # text on top of the accent must stay readable for light accents
        base["on_accent"] = "#0B0D11" if _luminance(acc) > 0.40 else "#FFFFFF"
        base["dot"] = rgba(base["text"], 0.11 if self.dark else 0.13)
        base["shadow_rgba"] = rgba(base["shadow"], 0.10 if not self.dark else 0.45)
        # hairline separators (Shade draws 1px #30343B on dark, #DFE1E4 on light)
        base["hairline"] = base["border"]
        self.pal = base

    # ------------------------------------------------------------------ api
    def apply(self, app: QApplication, mode: str, accent: str | None) -> None:
        mode = mode if mode in list(THEMES) + ["system"] else "dark"
        accent = accent if accent in ACCENTS else DEFAULT_ACCENT
        if (mode, accent) == (self.mode, self.accent_key) and app.styleSheet():
            return
        self.about_to_change.emit()
        self._swap(app, lambda: setattr(self, "mode", mode) or setattr(self, "accent_key", accent))

    def refresh_system(self, app: QApplication) -> None:
        """Re-resolve when the OS flips light/dark while mode == system."""
        if self.mode == "system":
            was = self.dark
            if self._resolve_dark() != was:
                self.about_to_change.emit()
                self._swap(app, lambda: None)

    def _swap(self, app: QApplication, set_state) -> None:
        """Palette + stylesheet + every listener, with repainting paused (one repaint at the end)."""
        tops = [w for w in app.topLevelWidgets() if w.isVisible()]
        for w in tops:
            w.setUpdatesEnabled(False)
        try:
            set_state()
            self._build()
            if app.style().objectName().lower() != "fusion":
                app.setStyle("Fusion")
            self._palette(app)
            key = (self.mode, self.dark, self.accent_key)
            sheet = self._qss_cache.get(key)
            if sheet is None:
                sheet = self._qss_cache[key] = self.qss()
            if sheet != app.styleSheet():
                app.setStyleSheet(sheet)
            self.changed.emit()
        finally:
            for w in tops:
                w.setUpdatesEnabled(True)

    def _palette(self, app: QApplication) -> None:
        p, pal = self.pal, QPalette()
        for role, key in (
            (QPalette.ColorRole.Window, "bg"), (QPalette.ColorRole.WindowText, "text"),
            (QPalette.ColorRole.Base, "surface2"), (QPalette.ColorRole.AlternateBase, "surface3"),
            (QPalette.ColorRole.Text, "text"), (QPalette.ColorRole.Button, "surface2"),
            (QPalette.ColorRole.ButtonText, "text"), (QPalette.ColorRole.ToolTipBase, "surface3"),
            (QPalette.ColorRole.ToolTipText, "text"), (QPalette.ColorRole.Highlight, "accent"),
            (QPalette.ColorRole.HighlightedText, "on_accent"),
            (QPalette.ColorRole.PlaceholderText, "muted"),
        ):
            pal.setColor(role, QColor(p[key]))
        for grp in (QPalette.ColorGroup.Disabled,):
            pal.setColor(grp, QPalette.ColorRole.Text, QColor(p["muted"]))
            pal.setColor(grp, QPalette.ColorRole.ButtonText, QColor(p["muted"]))
        app.setPalette(pal)

    # ------------------------------------------------------------------ qss
    def qss(self) -> str:
        c = self.pal
        dark = self.dark
        card_border = f"1px solid {c['border']}"
        if dark:
            tip_bg, tip_border, tip_text = rgba("#1A1E26", 0.98), rgba("#FFFFFF", 0.10), "#ECEEF2"
        else:
            tip_bg, tip_border, tip_text = rgba("#FFFFFF", 0.99), rgba("#0F1218", 0.12), "#0F1218"
        ui_font = '"Inter", "Inter Tight", "Segoe UI Variable", "Segoe UI", "SF Pro Text", "Ubuntu", "Noto Sans", sans-serif'
        disp_font = '"Inter Tight", "Inter", "Segoe UI Variable Display", "Segoe UI", "SF Pro Display", sans-serif'
        pill = "999px"
        return f"""
* {{ outline: 0; }}
QWidget {{ color: {c['text']}; font-size: 13px; font-family: {ui_font}; }}
QMainWindow {{ background: transparent; }}
QWidget#page, QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; }}

/* ---- Windows 11 Mica shell ---- */
QFrame#winframe {{ background: {c['bg']}; border: 1px solid {c['border_strong']}; border-radius: 9px; }}
QFrame#winframe[max="true"] {{ border-radius: 0px; border: none; }}
QFrame#titlebar {{ background: transparent; border: none; }}
QFrame#statusbar {{ background: transparent; border: none; }}
QFrame#sheet {{ background: {c['sheet']}; border: 1px solid {c['border']}; border-radius: 22px; }}
QFrame#sheet[max="true"] {{ border-radius: 22px; }}
QMenuBar#tbmenu {{ background: transparent; border: none; padding: 0; }}
QFrame#sidebar {{ background: transparent; border: none; }}

QDialog#fdialog {{ background: transparent; }}
QFrame#dlgframe {{ background: {c['surface_solid']}; border: {card_border}; border-radius: 18px; }}
QScrollArea {{ border: none; }}
QLabel {{ background: transparent; }}
QLabel[role="h1"] {{ font-family: {disp_font}; font-size: 38px; font-weight: 500; letter-spacing: -1.5px; }}
QLabel[role="h2"] {{ font-family: {disp_font}; font-size: 15px; font-weight: 600; letter-spacing: -0.2px; }}
QLabel[role="stat"] {{ font-family: {disp_font}; font-size: 24px; font-weight: 600; letter-spacing: -0.6px; }}
QLabel[role="big"] {{ font-family: {disp_font}; font-size: 34px; font-weight: 500; letter-spacing: -1px; }}
QLabel[role="muted"] {{ color: {c['text2']}; }}
QLabel[role="subtitle"] {{ color: {c['muted']}; font-size: 14px; }}
QLabel[role="caption"] {{ color: {c['muted']}; font-size: 12px; }}
QLabel[role="overline"] {{ color: {c['muted']}; font-size: 12px; font-weight: 500; }}
QLabel[role="danger"] {{ color: {c['danger']}; }}
QLabel[role="success"] {{ color: {c['success']}; }}
QLabel[role="link"] {{ color: {c['accent']}; }}

/* ---- panels / bento tiles ---- */
QFrame#card {{ background: {c['surface']}; border: {card_border}; border-radius: 20px; }}
QFrame#card[flat="true"] {{ background: {c['bg2']}; }}
QFrame#tile {{ background: {c['surface']}; border: {card_border}; border-radius: 22px; }}
QFrame#heroCard {{ background: {c['surface']}; border: {card_border}; border-radius: 22px; }}
QFrame#inner {{ background: {c['surface']}; border: {card_border}; border-radius: 14px; }}
QFrame#minibar {{ background: {c['surface']}; border: {card_border}; border-radius: 14px; }}
QFrame#row {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 14px; }}
QFrame#frow {{ background: transparent; border: none; }}
QFrame#row[drag="true"] {{ background: {rgba(c['accent'], 0.08)}; border: 1px solid {c['accent']}; }}
QFrame#divider {{ background: {c['hairline']}; border: none; max-height: 1px; min-height: 1px; }}
QFrame#vdivider {{ background: {c['hairline']}; border: none; }}
QFrame#palbtn {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 14px;
    min-height: 28px; max-height: 28px; }}
QFrame#palbtn:hover {{ border-color: {c['border_strong']}; }}
QFrame#palbtn QLabel {{ color: {c['muted']}; font-size: 12.5px; font-weight: 400; background: transparent; }}
QListWidget#palette {{ background: transparent; border: none; }}
QListWidget#palette::item {{ border-radius: 10px; margin: 1px 0; }}
QListWidget#palette::item:selected {{ background: {c['accent_soft']}; }}
QListWidget#palette::item:hover:!selected {{ background: {c['surface3']}; }}
QLineEdit#paletteinput {{ background: transparent; border: none; font-size: 15px; min-height: 40px; padding: 0; }}
QLineEdit#paletteinput:hover, QLineEdit#paletteinput:focus {{ border: none; }}

/* ---- schedule dialog ---- */
QCalendarWidget {{ background: transparent; }}
QCalendarWidget QWidget {{ alternate-background-color: {c['bg2']}; }}
QCalendarWidget #qt_calendar_navigationbar {{ background: {c['surface']}; border-radius: 12px; padding: 4px; }}
QCalendarWidget QToolButton {{ background: transparent; color: {c['text']}; border: none; border-radius: 8px; padding: 5px 10px; font-weight: 600; }}
QCalendarWidget QToolButton:hover {{ background: {c['surface3']}; }}
QCalendarWidget QToolButton::menu-indicator {{ image: none; }}
QCalendarWidget QSpinBox {{ background: {c['surface3']}; border: 1px solid {c['border']}; border-radius: 8px;
    padding: 2px 8px; selection-background-color: {c['accent']}; selection-color: {c['on_accent']}; }}
QCalendarWidget QAbstractItemView {{ background: {c['surface_solid']}; color: {c['text']}; outline: 0;
    selection-background-color: {c['accent']}; selection-color: {c['on_accent']}; }}
QCalendarWidget QAbstractItemView:disabled {{ color: {c['muted']}; }}
QFrame#stepper {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 12px; }}
QFrame#stepper:hover {{ border-color: {c['border_strong']}; }}
QLabel#stepval {{ font-size: 20px; font-weight: 600; color: {c['text']}; background: transparent; }}

/* ---- drop zone (painted by DropZone; QSS only sets the base) ---- */
QFrame#dropzone {{ background: transparent; border: none; }}

/* ---- toast ---- */
QFrame#toast {{ background: {c['surface_solid']}; border: 1px solid {c['border']}; border-radius: 16px; }}
QFrame#toasticon {{ background: {c['accent_soft']}; border: none; border-radius: 18px; }}
QFrame#toasticon[kind="success"] {{ background: {c['success_bg']}; }}
QFrame#toasticon[kind="error"] {{ background: {c['danger_bg']}; }}
QFrame#toasticon[kind="warning"] {{ background: {c['warning_bg']}; }}
QLabel[role="toastkind"] {{ color: {c['accent']}; font-size: 12px; font-weight: 600; }}
QLabel[role="toastkind"][kind="success"] {{ color: {c['success']}; }}
QLabel[role="toastkind"][kind="error"] {{ color: {c['danger']}; }}
QLabel[role="toastkind"][kind="warning"] {{ color: {c['warning']}; }}
QPushButton#toastclose {{ background: transparent; border: none; border-radius: 11px; padding: 0;
    min-width: 22px; max-width: 22px; min-height: 22px; max-height: 22px; }}
QPushButton#toastclose:hover {{ background: {c['surface3']}; }}
QPushButton#toastaction {{ background: {c['surface']}; color: {c['text']}; border: 1px solid {c['border_strong']};
    border-radius: 15px; padding: 0 16px; min-height: 30px; max-height: 30px; font-size: 13px; font-weight: 500; }}
QPushButton#toastaction:hover {{ background: {c['surface3']}; }}
QPushButton#toastaction:pressed {{ background: {c['surface_hi']}; }}
QProgressBar#toastbar {{ background: transparent; border-radius: 1px; min-height: 2px; max-height: 2px; }}
QProgressBar#toastbar::chunk {{ background: {c['accent']}; border-radius: 1px; }}
QProgressBar#toastbar[tone="success"]::chunk {{ background: {c['success']}; }}
QProgressBar#toastbar[tone="danger"]::chunk {{ background: {c['danger']}; }}
QProgressBar#toastbar[tone="warning"]::chunk {{ background: {c['warning']}; }}

/* ---- sidebar mini progress + (hidden) status bar ---- */
QProgressBar#actbar {{ background: {c['surface3']}; border-radius: 1px; min-height: 3px; max-height: 3px; }}
QProgressBar#actbar::chunk {{ background: {c['accent']}; border-radius: 1px; }}
QProgressBar#statusprog {{ background: {c['surface3']}; border-radius: 3px; min-height: 6px; max-height: 6px; min-width: 190px; max-width: 190px; }}
QProgressBar#statusprog::chunk {{ background: {c['accent']}; border-radius: 3px; }}
QProgressBar#statusprog[tone="success"]::chunk {{ background: {c['success']}; }}
QProgressBar#statusprog[tone="danger"]::chunk {{ background: {c['danger']}; }}
QLabel#statuslabel {{ color: {c['text2']}; font-size: 12px; }}
QLabel#statuslabel[active="true"] {{ color: {c['text']}; }}
QLabel#statuspct {{ color: {c['text2']}; font-size: 12px; font-weight: 600; }}
QFrame#statusdot {{ background: {c['success']}; border-radius: 4px; min-width: 8px; max-width: 8px; min-height: 8px; max-height: 8px; }}
QFrame#statusdot[active="true"] {{ background: {c['accent']}; }}
QLabel#actlabel {{ color: {c['text2']}; font-size: 12px; }}
QLabel#actpct {{ color: {c['text']}; font-size: 12px; font-weight: 600; }}

/* ---- badges + pills (status only) ---- */
QLabel#badge {{ background: {c['accent_soft']}; border-radius: 12px; }}
QLabel#pill {{ background: {c['surface3']}; border: 1px solid {c['surface3']}; border-radius: 10px; padding: 2px 10px; color: {c['text2']}; font-size: 12px; font-weight: 500; }}
QLabel#pill[tone="success"] {{ background: {c['success_bg']}; border-color: {c['success_bg']}; color: {c['success']}; }}
QLabel#pill[tone="warning"] {{ background: {c['warning_bg']}; border-color: {c['warning_bg']}; color: {c['warning']}; }}
QLabel#pill[tone="danger"] {{ background: {c['danger_bg']}; border-color: {c['danger_bg']}; color: {c['danger']}; }}
QLabel#pill[tone="accent"] {{ background: {c['accent_soft']}; border-color: {c['accent_soft']}; color: {c['accent']}; }}
QFrame#pill {{ background: {c['surface3']}; border: 1px solid {c['surface3']}; border-radius: 10px; color: {c['text2']}; font-size: 12px; }}
QFrame#pill QLabel {{ background: transparent; color: {c['text2']}; font-size: 12px; font-weight: 500; }}
QFrame#pill[tone="success"] {{ background: {c['success_bg']}; border-color: {c['success_bg']}; }}
QFrame#pill[tone="success"] QLabel {{ color: {c['success']}; }}
QFrame#pill[tone="warning"] {{ background: {c['warning_bg']}; border-color: {c['warning_bg']}; }}
QFrame#pill[tone="warning"] QLabel {{ color: {c['warning']}; }}
QFrame#pill[tone="danger"] {{ background: {c['danger_bg']}; border-color: {c['danger_bg']}; }}
QFrame#pill[tone="danger"] QLabel {{ color: {c['danger']}; }}
QFrame#pill[tone="accent"] {{ background: {c['accent_soft']}; border-color: {c['accent_soft']}; }}
QFrame#pill[tone="accent"] QLabel {{ color: {c['accent']}; }}

/* ---- buttons: pills ---- */
QPushButton {{ background: {c['surface']}; border: 1px solid {c['border_strong']}; border-radius: 18px;
    padding: 0 18px; min-height: 36px; font-weight: 500; font-size: 13px; }}
QPushButton:hover {{ background: {c['surface3']}; }}
QPushButton:pressed {{ background: {c['surface_hi']}; }}
QPushButton:focus {{ border: 1.5px solid {c['accent']}; }}
QPushButton:disabled {{ color: {c['muted']}; background: {c['surface3']}; border-color: {c['border']}; }}
QPushButton[variant="primary"] {{ background: {c['ink']}; color: {c['on_ink']}; border: 1px solid {c['ink']}; font-weight: 500; }}
QPushButton[variant="primary"]:hover {{ background: {_mix(c['ink'], c['bg'], 0.14)}; border-color: {_mix(c['ink'], c['bg'], 0.14)}; }}
QPushButton[variant="primary"]:pressed {{ background: {_mix(c['ink'], c['bg'], 0.28)}; border-color: {_mix(c['ink'], c['bg'], 0.28)}; }}
QPushButton[variant="primary"]:disabled {{ background: {c['surface_hi']}; color: {c['muted']}; border-color: {c['surface_hi']}; }}
QPushButton[variant="signal"] {{ background: {c['accent']}; color: {c['on_accent']}; border: 1px solid {c['accent']}; font-weight: 600; }}
QPushButton[variant="signal"]:hover {{ background: {c['accent_hover']}; border-color: {c['accent_hover']}; }}
QPushButton[variant="signal"]:pressed {{ background: {c['accent_press']}; border-color: {c['accent_press']}; }}
QPushButton[variant="signal"]:disabled {{ background: {c['surface_hi']}; color: {c['muted']}; border-color: {c['surface_hi']}; }}
QPushButton[variant="secondary"] {{ background: {c['surface3']}; border: 1px solid {c['surface3']}; }}
QPushButton[variant="ghost"] {{ background: transparent; border: 1px solid transparent; color: {c['text2']}; }}
QPushButton[variant="ghost"]:hover {{ background: {c['surface3']}; color: {c['text']}; }}
QPushButton[variant="danger"] {{ background: transparent; color: {c['danger']}; border: 1px solid {c['border_strong']}; }}
QPushButton[variant="danger"]:hover {{ background: {c['danger_bg']}; border-color: {c['danger']}; }}
QPushButton[size="xl"] {{ min-height: 52px; max-height: 52px; font-size: 15px; padding: 0 26px; border-radius: 26px; }}
QPushButton[size="lg"] {{ min-height: 42px; max-height: 42px; font-size: 14px; padding: 0 22px; border-radius: 21px; }}
QPushButton[size="sm"] {{ min-height: 30px; max-height: 30px; padding: 0 14px; font-size: 12.5px; border-radius: 15px; }}
QPushButton[size="icon"] {{ min-height: 32px; min-width: 32px; max-width: 32px; max-height: 32px; padding: 0; border-radius: 16px; }}

QPushButton#nav {{ background: transparent; border: none; }}
QPushButton#seg {{ background: transparent; border: 1px solid transparent; border-radius: 14px; min-height: 28px; max-height: 28px; padding: 0 14px; color: {c['muted']}; font-size: 13px; font-weight: 500; }}
QPushButton#seg:hover {{ color: {c['text']}; }}
QPushButton#seg:checked {{ background: {c['surface'] if not dark else c['surface_hi']}; border-color: {c['border']}; color: {c['text']}; font-weight: 600; }}
QFrame#segwrap {{ background: {c['surface3']}; border: none; border-radius: 17px; }}
QPushButton#theme {{ background: {c['surface']}; border: 1.5px solid {c['border']}; border-radius: 16px; padding: 12px; min-height: 64px; text-align: center; }}
QPushButton#theme:checked {{ border-color: {c['accent']}; background: {c['accent_soft']}; }}
QPushButton#flag {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 14px;
    min-height: 42px; text-align: left; padding: 0 14px; font-weight: 500; }}
QPushButton#flag:hover {{ border-color: {c['border_strong']}; background: {c['surface3']}; }}
QPushButton#flag:checked {{ border: 1.5px solid {c['accent']}; background: {c['accent_soft']}; }}

QLineEdit {{ background: {c['surface']}; border: 1px solid {c['border_strong']}; border-radius: 18px;
    padding: 0 16px; min-height: 36px; selection-background-color: {c['accent']}; selection-color: {c['on_accent']}; font-size: 13px; }}
QLineEdit:hover {{ border-color: {c['muted']}; }}
QLineEdit:focus {{ border: 1.5px solid {c['accent']}; background: {c['surface']}; }}
QLineEdit[state="error"] {{ border: 1.5px solid {c['danger']}; }}
QLineEdit[state="ok"] {{ border: 1px solid {c['success']}; }}
QLineEdit:disabled {{ color: {c['muted']}; }}

QDateTimeEdit {{ background: {c['surface']}; border: 1px solid {c['border_strong']}; border-radius: 18px;
    padding: 0 14px; min-height: 36px; }}
QDateTimeEdit:hover {{ border-color: {c['muted']}; }}
QDateTimeEdit:focus {{ border: 1.5px solid {c['accent']}; }}
QDateTimeEdit::drop-down {{ border: none; width: 22px; }}

QProgressBar {{ background: {c['surface3']}; border: none; border-radius: 3px; max-height: 6px; min-height: 6px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {c['accent']}; border-radius: 3px; }}
QProgressBar[tone="success"]::chunk {{ background: {c['success']}; }}
QProgressBar[tone="danger"]::chunk {{ background: {c['danger']}; }}

QTableWidget {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 16px; gridline-color: transparent;
    selection-background-color: {c['accent_soft']}; selection-color: {c['text']}; alternate-background-color: transparent; }}
QTableWidget::item {{ padding: 0 10px; border-bottom: 1px solid {c['hairline']}; }}
QTableWidget::item:selected {{ background: {c['accent_soft']}; color: {c['text']}; }}
QTableWidget::item:hover {{ background: {c['surface3']}; }}
QTableWidget#hist {{ background: transparent; border: none; border-radius: 0; }}
QTableWidget#hist::item, QTableWidget#hist::item:selected, QTableWidget#hist::item:hover,
QTableWidget#hist::item:selected:active, QTableWidget#hist::item:selected:!active {{
    background: transparent; border: none; padding: 0; color: {c['text']}; outline: 0; }}
QTableWidget#hist QHeaderView::section {{ padding: 6px 8px 10px 8px; font-size: 11px; font-weight: 600; letter-spacing: 0.8px;
    border-bottom: none; background: transparent; }}
QTableWidget#hist QHeaderView::section:first {{ padding-left: 20px; border-top-left-radius: 0; }}
QTableWidget#hist QHeaderView::section:last {{ border-top-right-radius: 0; }}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{ background: transparent; color: {c['muted']}; border: none; border-bottom: 1px solid {c['hairline']};
    padding: 10px 10px; font-size: 12px; font-weight: 500; text-align: left; }}
QTableCornerButton::section {{ background: transparent; border: none; }}

QScrollBar:vertical {{ background: transparent; width: 12px; border-radius: 6px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {c['border_strong']}; border-radius: 3px; min-height: 36px; margin: 3px 3px; }}
QScrollBar::handle:vertical:hover {{ background: {c['muted']}; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; border-radius: 6px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {c['border_strong']}; border-radius: 3px; min-width: 36px; margin: 3px 3px; }}
QScrollBar::handle:horizontal:hover {{ background: {c['muted']}; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ background: none; border: none; width: 0; height: 0; }}
QAbstractScrollArea::corner {{ background: transparent; border: none; width: 0; height: 0; }}

QMenu {{ background: {c['surface_solid']}; border: 1px solid {c['border']}; border-radius: 14px; padding: 6px; }}
QMenu::item {{ padding: 8px 34px 8px 12px; border-radius: 9px; margin: 1px 2px; }}
QMenu::indicator {{ width: 14px; height: 14px; margin-left: 8px; }}
QMenu::right-arrow {{ margin-right: 10px; }}
QMenu::icon {{ padding-left: 8px; }}
QMenu::item:selected {{ background: {c['surface3']}; color: {c['text']}; }}
QMenu::item:disabled {{ color: {c['muted']}; }}
QMenu::separator {{ height: 1px; background: {c['hairline']}; margin: 5px 8px; }}
QToolTip {{ background: {tip_bg}; color: {tip_text}; border: 1px solid {tip_border};
    padding: 7px 11px; border-radius: 9px; font-size: 12px; }}
QFrame#mactip {{ background: {tip_bg}; border: 1px solid {tip_border}; border-radius: 10px; }}
QLabel#tiplbl {{ background: transparent; color: {tip_text}; font-size: 12px; }}
QMenuBar {{ spacing: 2px; }}
QMenuBar::item {{ background: transparent; padding: 5px 11px; margin: 4px 0; border-radius: 6px;
    color: {c['text']}; font-size: 13px; font-weight: 400; }}
QMenuBar::item:selected {{ background: {c['surface_hi'] if not dark else c['surface_hi']}; color: {c['text']}; }}
QMenuBar::item:pressed {{ background: {c['surface_hi']}; color: {c['text']}; }}
QHeaderView::section:first {{ border-top-left-radius: 15px; }}
QHeaderView::section:last {{ border-top-right-radius: 15px; }}
QFrame#kbd {{ background: {c['surface']}; border: 1px solid {c['border_strong']}; border-radius: 5px; }}
QKeySequenceEdit, QComboBox {{ background: {c['surface']}; border: 1px solid {c['border_strong']}; border-radius: 12px; padding: 6px 12px; }}

QFrame#wizdot {{ background: {c['border_strong']}; border-radius: 3px;
    min-width: 6px; max-width: 6px; min-height: 6px; max-height: 6px; }}
QFrame#wizdot[on="true"] {{ background: {c['accent']}; min-width: 20px; max-width: 20px; }}
"""


theme = Theme()
