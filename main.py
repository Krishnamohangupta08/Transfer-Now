"""Transfer Now 1.0.0.1 — create and fetch TransferNow links from your desktop.

    python main.py
"""
from __future__ import annotations

import atexit
import os
import signal
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt6.QtCore import QByteArray
from PyQt6.QtGui import QFont, QFontDatabase
from PyQt6.QtWidgets import QApplication

from core import procs
from core.store import DATA_DIR, History, Settings, apply_pending_reset
from ui import icons
from ui.ctx import Ctx
from ui.i18n import i18n
from ui.sounds import sounds
from ui.theme import theme
from ui.tips import tips
from ui.window import AppWindow


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
    meipass = getattr(sys, "_MEIPASS", None)          # PyInstaller data root:
    if meipass:                                       # onefile temp dir AND onedir _internal/
        bases.append(os.path.join(meipass, "assets", "fonts"))
    if getattr(sys, "frozen", False):
        bases.append(os.path.join(os.path.dirname(sys.executable), "assets", "fonts"))
    bases.append(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "assets", "fonts"))     # dev (source) tree
    for base in bases:
        found = False
        for name in ("Inter-Variable.ttf", "InterTight-Variable.ttf"):
            path = os.path.join(base, name)
            if os.path.exists(path):
                QFontDatabase.addApplicationFont(path)
                found = True
        if found:                                     # first base that really has the fonts wins
            return
    try:                                              # nothing on disk: use the copy embedded in the exe
        from ui import assets
        for name in ("Inter-Variable.ttf", "InterTight-Variable.ttf"):
            raw = assets.data("fonts/" + name)
            if raw:
                QFontDatabase.addApplicationFontFromData(QByteArray(raw))
    except Exception:  # noqa: BLE001
        pass


def _wait_for_previous_instance() -> None:
    """After 'Reset everything' the app relaunches itself: let the old process finish
    exiting first, so it cannot write its old files back after the wipe."""
    pid = os.environ.pop("TN_WAIT_PID", "")
    if not pid.isdigit():
        return
    import time
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            import psutil
            alive = psutil.pid_exists(int(pid))
        except ImportError:
            time.sleep(2.5)
            return
        if not alive:
            return
        time.sleep(0.15)


def main() -> int:
    _install_excepthook()
    _wait_for_previous_instance()
    apply_pending_reset()                  # "Reset everything" finishes here, before loading
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
    except Exception as exc:  # noqa: BLE001 — Qt < 6.5 has no colorSchemeChanged
        if sys.stderr is not None:      # None in a windowed (no-console) build
            sys.stderr.write(f"colorSchemeChanged unavailable: {exc}\n")

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
