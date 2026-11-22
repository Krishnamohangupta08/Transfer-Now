"""System-tray integration: quick actions, transfer alerts, minimize-to-tray.

The tray only becomes active when the desktop actually provides one
(``QSystemTrayIcon.isSystemTrayAvailable()``); on headless / offscreen
sessions every method degrades to a harmless no-op, so tests and VMs
keep working unchanged.
"""
from __future__ import annotations

from PyQt6.QtCore import QObject
from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import QSystemTrayIcon

from . import icons
from .i18n import tr
from .menus import RoundMenu
from .sounds import sounds
from .theme import theme


class TrayIcon(QObject):
    """Owns the QSystemTrayIcon; ``active`` tells whether the OS has a tray."""

    def __init__(self, win) -> None:            # win = AppWindow
        super().__init__(win)
        self.win = win
        self.active = False
        self._notify_suppressed = False
        try:
            self.available = bool(QSystemTrayIcon.isSystemTrayAvailable())
        except Exception:                        # noqa: BLE001 — exotic platforms
            self.available = False
        if not self.available:
            self.tray = None
            return
        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(icons.app_icon())
        self.tray.setToolTip("Transfer Now")
        self.tray.activated.connect(self._on_activated)
        self._build_menu()
        s = win.ctx.settings
        if bool(s.get("tray_icon", True)):
            self.tray.show()
            self.active = True

    # ------------------------------------------------------------------ menu
    def _build_menu(self) -> None:
        # RoundMenu (not QMenu): translucent frameless window so the app-wide
        # QMenu stylesheet's 14px rounded corners are actually round.
        m = RoundMenu()
        self._acts: list[tuple[QAction, str, str]] = []

        def act(text_key: str, icon_name: str, slot) -> None:
            a = QAction(tr(text_key), self)
            a.triggered.connect(lambda _=False: slot())
            m.addAction(a)
            self._acts.append((a, text_key, icon_name))

        act("tray.show", "grid", self.show_window)
        m.addSeparator()
        act("tray.new_upload", "cloud-upload", lambda: (self.show_window("upload")))
        act("tray.queue", "queue", lambda: (self.show_window("queue")))
        act("tray.download", "cloud-download", lambda: (self.show_window("download")))
        act("tray.check", "shield-check", self._check_links)
        m.addSeparator()
        act("tray.quit", "power", self.quit)
        self.menu = m
        self.tray.setContextMenu(m)
        self._refresh_menu()
        theme.changed.connect(self._refresh_menu)

    def _refresh_menu(self) -> None:
        """Retint the menu icons for the current theme + language."""
        for a, key, icon_name in getattr(self, "_acts", []):
            a.setText(tr(key))
            a.setIcon(QIcon(icons.pixmap(icon_name, theme.pal["text2"], 16, 1.6)))

    # --------------------------------------------------------------- actions
    def show_window(self, page: str = "") -> None:
        w = self.win
        if w.isMinimized():
            w.showNormal()
        else:
            w.show()
        w.raise_()
        w.activateWindow()
        if page:
            w.navigate(page)

    def _check_links(self) -> None:
        self.show_window("history")
        self.win.pages["history"].check_links()

    def quit(self) -> None:
        """Real quit: bypass the minimize-to-tray shortcut."""
        self._notify_suppressed = True
        self.win.close(force=True)

    def retranslate(self) -> None:
        """Language switched: refresh the tray menu texts."""
        self._refresh_menu()

    # ----------------------------------------------------------- tray state
    def set_visible(self, on: bool) -> None:
        if self.tray is None:
            return
        if on and not self.active:
            self.tray.show()
            self.active = True
        elif not on and self.active:
            self.tray.hide()
            self.active = False

    def refresh_visibility(self) -> None:
        self.set_visible(bool(self.win.ctx.settings.get("tray_icon", True)))

    # --------------------------------------------------------- notifications
    def notify(self, title: str, message: str) -> None:
        """Desktop notification through the tray (in-app toasts already exist)."""
        if not self.active or self._notify_suppressed:
            return
        if not bool(self.win.ctx.settings.get("tray_notifications", True)):
            return
        if not self.tray.supportsMessages():
            return
        try:
            self.tray.showMessage(title, message,
                                  QSystemTrayIcon.MessageIcon.Information, 4000)
        except Exception:                            # noqa: BLE001
            pass

    # ---------------------------------------------------------------- events
    def _on_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            sounds.play("nav")
            if self.win.isVisible() and not self.win.isMinimized():
                self.win.hide()
            else:
                self.show_window()
