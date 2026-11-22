"""Transfer Now 1.0.0.0 — create and fetch TransferNow links from your desktop.

    python main.py
"""
from __future__ import annotations

import atexit
import os
import signal
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt6.QtGui import QFont, QFontDatabase  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from core import procs  # noqa: E402
from core.store import DATA_DIR, History, Settings  # noqa: E402
from ui import icons  # noqa: E402
from ui.ctx import Ctx  # noqa: E402
from ui.i18n import i18n  # noqa: E402
from ui.sounds import sounds  # noqa: E402
from ui.theme import theme  # noqa: E402
from ui.tips import tips  # noqa: E402
from ui.window import AppWindow  # noqa: E402


def _install_excepthook() -> None:
    """Log unexpected errors instead of dying silently."""
    log = os.path.join(DATA_DIR, "error.log")

    def hook(exc_type, exc, tb) -> None:
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(log, "a", encoding="utf-8") as f:
                f.write(text + "\n")
        except OSError:
            pass
        if sys.stderr is not None:          # None in a windowed (no-console) .exe
            sys.stderr.write(text)

    sys.excepthook = hook


def _load_bundled_fonts() -> None:
    """Register the bundled Inter / Inter Tight variable fonts (Shade UI type)."""
    bases = []
    meipass = getattr(sys, "_MEIPASS", None)          # PyInstaller runtime
    if meipass:
        bases.append(os.path.join(meipass, "assets", "fonts"))
    bases.append(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "assets", "fonts"))
    for base in bases:
        if not os.path.isdir(base):
            continue
        for name in ("Inter-Variable.ttf", "InterTight-Variable.ttf"):
            path = os.path.join(base, name)
            if os.path.exists(path):
                QFontDatabase.addApplicationFont(path)
        break


def main() -> int:
    _install_excepthook()
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    app = QApplication(sys.argv)
    app.setApplicationName("Transfer Now")
    app.setOrganizationName("Transfer Now")
    app.setWindowIcon(icons.app_icon())
    _load_bundled_fonts()

    font = QFont()
    font.setFamilies(["Inter", "Inter Tight", "Segoe UI Variable Text", "Segoe UI",
                      "SF Pro Text", "Ubuntu", "Noto Sans", "DejaVu Sans",
                      # script fallbacks so the 8 UI languages render everywhere
                      "Noto Sans JP", "Noto Sans SC", "Noto Sans Devanagari"])
    font.setPixelSize(13)
    app.setFont(font)

    settings = Settings()
    history = History()
    ctx = Ctx(settings, history)
    sounds.setup(settings, DATA_DIR)
    tips.install(app)                       # macOS-style tooltips everywhere
    i18n.set_language(settings.get("language", "en"))

    theme.apply(app, settings.get("theme", "dark"), settings.get("accent"))

    def on_setting(key: str) -> None:
        if key in ("theme", "accent"):
            theme.apply(app, settings.get("theme", "dark"), settings.get("accent"))
        elif key == "language":
            i18n.set_language(settings.get("language", "en"))
    settings.on_change(on_setting)
    try:
        app.styleHints().colorSchemeChanged.connect(lambda *_: theme.refresh_system(app))
    except Exception:  # noqa: BLE001
        pass

    # Any way the app ends (window X, tray Quit, Ctrl+Q, OS shutdown): no browser is left.
    app.aboutToQuit.connect(procs.kill_all)
    atexit.register(procs.kill_all)

    AppWindow.watchdog_enabled = True
    win = AppWindow(ctx)
    win.show()
    try:
        from ui.wizard import run_wizard_if_needed
        run_wizard_if_needed(win, settings)   # first launch: guided setup
    except Exception:                          # noqa: BLE001 — never block startup
        traceback.print_exc()
    return app.exec()


if __name__ == "__main__":
    # Hard exit: once the event loop is done, nothing (a stuck thread, a browser helper)
    # may keep "Transfer Now" alive in Task Manager.
    procs.hard_exit(main())
