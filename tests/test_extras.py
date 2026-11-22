"""1.0.0.0 extras: SVG icon engine + export sync, system tray fallback,
clipboard watcher, queue copy-all, version pin.

No network access: upload threads are never started (jobs are injected
directly, like TestRealtimeBytes), and the download fetch is stubbed.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
DATA = tempfile.mkdtemp(prefix="tn-extras-data-")
os.environ["TN_DATA_DIR"] = DATA

from PyQt6.QtCore import QByteArray  # noqa: E402
from PyQt6.QtGui import QGuiApplication  # noqa: E402
from PyQt6.QtSvg import QSvgRenderer  # noqa: E402
from PyQt6.QtWidgets import QApplication, QPushButton  # noqa: E402

from tests.test_ui import Base  # noqa: E402  (stub-safe app + window fixture)

app = QApplication.instance() or QApplication([])

import ui.dialogs as dialogs_mod  # noqa: E402
import ui.i18n as i18n_mod  # noqa: E402
from ui import icons  # noqa: E402
from ui.i18n import LANGS, STRINGS, i18n  # noqa: E402


class TestSvgIcons(unittest.TestCase):
    """Every icon in the app is renderable SVG; the exported files stay in sync."""

    def test_every_icon_renders(self):
        self.assertGreater(len(icons.ICONS), 70, "icon library shrank?")
        draw = ("<path", "<rect", "<circle", "<line", "<polygon", "<polyline")
        for name, body in icons.ICONS.items():
            svg = icons.icon_svg(name, "#FFFFFF", 24, 1.5)
            r = QSvgRenderer(QByteArray(svg.encode("utf-8")))
            self.assertTrue(r.isValid(), f"icon {name!r} is not valid SVG")
            self.assertTrue(any(tag in body for tag in draw),
                            f"icon {name!r} has no drawing elements")

    def test_duotone_table_is_valid(self):
        """Every duotone entry names a real icon and real element indices."""
        for name, idx in icons.DUO.items():
            self.assertIn(name, icons.ICONS)
            count = len(icons._ELEMENT.findall(icons.ICONS[name]))
            self.assertTrue(all(0 <= i < count for i in idx), f"{name}: bad duotone index")
        svg = icons.icon_svg("folder", "#000000", 24)
        self.assertIn("fill-opacity", svg)                       # tinted layer present
        self.assertNotIn("fill-opacity", icons.icon_svg("folder", "#000000", 12))   # too small
        self.assertNotIn("fill-opacity", icons.icon_svg("folder", "#000000", 24, duo=False))

    def test_new_feature_icons_exist(self):
        for name in ("power", "bell", "clipboard-check", "tray", "copy-check",
                     "check-circle", "x-circle"):
            self.assertIn(name, icons.ICONS)

    def test_exported_svg_files_in_sync(self):
        """assets/icons/*.svg must mirror ui/icons.py exactly."""
        icon_dir = os.path.join(ROOT, "assets", "icons")
        for name in icons.ICONS:
            path = os.path.join(icon_dir, f"{name}.svg")
            self.assertTrue(os.path.exists(path), f"missing exported {name}.svg")
            with open(path, encoding="utf-8") as f:
                self.assertEqual(f.read(), icons.icon_svg(name, "currentColor", 24, 1.5),
                                 f"{name}.svg is out of sync — rerun tools/export_icons.py")
        self.assertTrue(os.path.exists(os.path.join(icon_dir, "logo.svg")))
        flags = os.path.join(icon_dir, "flags")
        for code in ("gb", "in", "es", "fr", "de", "br", "jp", "cn"):
            self.assertTrue(os.path.exists(os.path.join(flags, f"{code}.svg")),
                            f"missing flag {code}.svg")

    def test_logo_matches_design(self):
        """Ink rounded square + ring-line-ring glyph; inverted on dark; files in sync."""
        light, dark = icons.logo_svg(64), icons.logo_svg(64, dark=True)
        for svg in (light, dark):
            self.assertTrue(QSvgRenderer(QByteArray(svg.encode("utf-8"))).isValid())
            self.assertEqual(svg.count("<circle"), 2)           # two nodes
        self.assertIn(icons.LOGO_INK, light.split("<g")[0])      # ink tile
        self.assertIn(icons.LOGO_PAPER, dark.split("<g")[0])     # inverted tile
        # small sizes get a bolder glyph
        self.assertGreater(icons.logo_scale(16), icons.logo_scale(256))
        icon_dir = os.path.join(ROOT, "assets", "icons")
        for fname, svg in (("logo.svg", light), ("logo-inverted.svg", dark)):
            with open(os.path.join(icon_dir, fname), encoding="utf-8") as f:
                self.assertEqual(f.read(), svg, f"{fname} out of sync — rerun tools/export_icons.py")

    def test_app_icon_files_exist(self):
        for name in ("icon.png", "icon.ico", "icon.icns"):
            self.assertTrue(os.path.exists(os.path.join(ROOT, "assets", name)), name)

    def test_kbd_accepts_icon_and_pill_renders(self):
        from ui.theme import theme
        from ui.widgets import IconPill, kbd
        theme.apply(app, "dark", None)
        k = kbd(icon_name="arrow-up")
        self.assertEqual(k.objectName(), "kbd")
        p = IconPill("check-circle", "Active", "success")
        self.assertEqual(p.objectName(), "pill")
        self.assertEqual(p.property("tone"), "success")
        p.set_text("Expired")
        p.set_tone("danger")
        self.assertEqual(p.text.text(), "Expired")
        self.assertEqual(p.property("tone"), "danger")

    def test_no_unicode_glyph_icons_in_strings(self):
        """Status marks must be SVG icons, never ✓/✕ unicode glyphs."""
        for lang, block in STRINGS.items():
            for key, value in block.items():
                self.assertNotIn("✓", value, f"{lang}:{key} still uses a unicode glyph")
                self.assertNotIn("✕", value, f"{lang}:{key} still uses a unicode glyph")


class TestVersion(unittest.TestCase):
    def test_version_is_100(self):
        self.assertEqual(dialogs_mod.VERSION, "1.0.0.0")


class TestTrayAndClipboard(Base):
    """Tray degrades to a no-op without a system tray; watcher toasts work."""

    def test_tray_safe_without_system_tray(self):
        w = self.w
        # offscreen sessions have no tray: must not raise anywhere
        self.assertIsInstance(w.tray.active, bool)
        w.tray.notify("Transfer Now", "done")          # no-op, no crash
        w.tray.set_visible(True)
        w.tray.set_visible(False)
        w.tray.refresh_visibility()
        w.tray.retranslate()
        self.assertTrue(w.isVisible())

    def test_clipboard_watcher_offers_link(self):
        w = self.w
        self.ctx.settings.set("clip_watch", True)
        self.assertTrue(w._clip_timer.isActive(), "watcher timer not started")
        clip = QGuiApplication.clipboard()
        clip.setText("https://www.transfernow.net/dl/abc123EXTRAS")
        w._watch_clipboard()
        app.processEvents()
        cards = [c for c in w.toast._cards if not c._closing]  # noqa: SLF001
        self.assertTrue(any(c.text == i18n_mod.tr("toast.clip_link") for c in cards),
                        "no clipboard toast shown")
        # the toast carries an action button that opens Download prefilled
        card = next(c for c in cards if c.text == i18n_mod.tr("toast.clip_link"))
        btn = card.findChild(QPushButton, "toastaction")
        self.assertIsNotNone(btn, "toast has no action button")
        dp = w.page("download")
        dp.fetch_now = lambda: None                   # block the real fetch
        btn.click()
        app.processEvents()
        self.assertEqual(w.current_key(), "download")
        self.assertEqual(dp.link.text(), "https://www.transfernow.net/dl/abc123EXTRAS")
        # non-links and repeats never toast
        w.toast._cards.clear()                        # noqa: SLF001
        clip.setText("just some text")
        w._watch_clipboard()
        clip.setText("https://example.com/nope")
        w._watch_clipboard()
        self.assertFalse(any(not c._closing for c in w.toast._cards))  # noqa: SLF001
        # our own links (already in history) never toast either
        self.ctx.add_history({"kind": "upload",
                              "link": "https://www.transfernow.net/dl/OWN1",
                              "files": [{"name": "a.txt", "size": 1}], "total_bytes": 1})
        clip.setText("https://www.transfernow.net/dl/OWN1")
        w._watch_clipboard()
        self.assertFalse(any(not c._closing for c in w.toast._cards))  # noqa: SLF001
        # disabling stops the timer
        self.ctx.settings.set("clip_watch", False)
        self.assertFalse(w._clip_timer.isActive())

    def test_copy_all_links(self):
        from ui.pages import upload as upm
        up = self.w.page("upload")
        self.w.navigate("queue")
        job = upm.UploadJob([self.f], {self.f: 1000}, "me@example.com")
        job.state, job.link, job.pct = "done", "https://www.transfernow.net/dl/XYZ1", 100
        up._add_job(job)
        app.processEvents()
        qp = self.w.page("queue")
        qp.rebuild()
        app.processEvents()
        self.assertTrue(qp.btn_copy_all.isEnabled())
        qp.copy_all()
        self.assertEqual(QGuiApplication.clipboard().text(),
                         "https://www.transfernow.net/dl/XYZ1")
        # second done job joins the clipboard list
        job2 = upm.UploadJob([self.f], {self.f: 1000}, "me@example.com")
        job2.state, job2.link = "done", "https://www.transfernow.net/dl/XYZ2"
        up._add_job(job2)
        qp.rebuild()
        app.processEvents()
        qp.copy_all()
        self.assertEqual(QGuiApplication.clipboard().text().splitlines(),
                         ["https://www.transfernow.net/dl/XYZ1",
                          "https://www.transfernow.net/dl/XYZ2"])

    def test_desktop_settings_section_exists(self):
        sp = self.w.page("settings")
        self.w.navigate("settings")
        for attr in ("tray_icon_sw", "minimize_to_tray_sw", "tray_notifications_sw",
                     "clip_watch_sw"):
            self.assertTrue(hasattr(sp, attr), f"settings misses {attr}")
        self.assertTrue(sp.tray_icon_sw.isChecked())
        self.assertFalse(sp.minimize_to_tray_sw.isChecked())
        self.assertFalse(sp.clip_watch_sw.isChecked())

    def test_i18n_keys_all_languages(self):
        keys = ("set.desktop", "set.tray_icon", "set.min_tray", "set.notify",
                "set.clip_watch", "tray.show", "tray.quit", "tray.done",
                "toast.clip_link", "toast.clip_open", "toast.links_copied",
                "toast.tray_hidden", "qu.copy_all")
        for lang in LANGS:
            block = STRINGS.get(lang, {})
            for k in keys:
                self.assertIn(k, block, f"language {lang} misses {k}")
                self.assertTrue(block[k].strip(), f"language {lang} has empty {k}")


class TestWizard(unittest.TestCase):
    """First-launch setup wizard: pages, navigation, live settings, done-flag."""

    def _mk(self):
        from core.store import Settings
        from ui.wizard import SetupWizard
        w = SetupWizard(None, Settings())
        self.addCleanup(w.deleteLater)
        w.show()
        app.processEvents()
        return w

    def test_five_pages_and_navigation(self):
        w = self._mk()
        self.assertEqual(w.stack.count(), 5)
        self.assertFalse(w.back.isVisible())           # welcome: no back button
        self.assertTrue(w.skip.isVisible())
        self.assertIn(w.next.text(), ("Get started", "Empezar", "Commencer"))
        for _ in range(4):
            w._go_next()                               # noqa: SLF001
            app.processEvents()
        self.assertEqual(w.stack.currentIndex(), 4)
        self.assertTrue(w.back.isVisible())
        self.assertFalse(w.skip.isVisible())           # last step: no skip
        w._go_back()                                   # noqa: SLF001
        app.processEvents()
        self.assertEqual(w.stack.currentIndex(), 3)
        self.assertTrue(w.skip.isVisible())

    def test_choices_apply_live(self):
        from ui.i18n import i18n
        w = self._mk()
        s = w._s                                       # noqa: SLF001
        w._pick_theme("black", True)                   # noqa: SLF001
        w._pick_accent("violet", True)                 # noqa: SLF001
        w._pick_lang("de", True)                       # noqa: SLF001
        app.processEvents()
        self.assertEqual(s.get("theme"), "black")
        self.assertEqual(s.get("accent"), "violet")
        self.assertEqual(s.get("language"), "de")
        self.assertEqual(i18n.lang, "de")
        i18n.set_language("en")                        # restore for other tests
        w._pick_theme("dark", True)                    # noqa: SLF001

    def test_finish_marks_wizard_done(self):
        w = self._mk()
        s = w._s                                       # noqa: SLF001
        s.set("wizard_done", False)
        w.accept()
        app.processEvents()
        self.assertTrue(bool(s.get("wizard_done", False)))

    def test_skip_still_marks_wizard_done(self):
        w = self._mk()
        s = w._s                                       # noqa: SLF001
        s.set("wizard_done", False)
        w.skip.click()
        app.processEvents()
        self.assertTrue(bool(s.get("wizard_done", False)))

    def test_summary_shows_choices(self):
        w = self._mk()
        w.stack.setCurrentIndex(4)
        w._sync()                                      # noqa: SLF001
        app.processEvents()
        self.assertIn("·", w.sum_theme_val.text())
        self.assertTrue(w.sum_lang_val.text())
        self.assertTrue(w.sum_dl_val.full_text())

    def test_wizard_i18n_complete_all_languages(self):
        for lang, block in STRINGS.items():
            for k in [x for x in STRINGS["en"] if x.startswith("wiz.")]:
                self.assertTrue(block.get(k, "").strip(), f"language {lang} misses {k}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
