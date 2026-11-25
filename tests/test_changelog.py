"""Changelog parser (Qt-free) + Changelog page / sidebar (Qt, offscreen).

    QT_QPA_PLATFORM=offscreen python -m unittest tests.test_changelog -v
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
DATA = tempfile.mkdtemp(prefix="tn-cl-data-")
os.environ["TN_DATA_DIR"] = DATA

from core import changelog as cl  # noqa: E402
from core.changelog import ChangelogParser, parse_text  # noqa: E402
from core.version import APP_VERSION  # noqa: E402

SAMPLE = """# Changelog

## 2.0.0 (2027-01-05)
Summary: Big **release**
that continues here.

### New Features
- Shiny `thing` that wraps
  onto a second line.
- Second feature

### Security
- Fix A

## 1.9.0
Bug Fixes:
- Plain heading style
Improvements:
* star bullet

## 1.8.0 (2026-02-03)
"""


def write(tmp: str, text: str, name: str = "CHANGELOG.md") -> str:
    p = os.path.join(tmp, name)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(text)
    return p


class TestParser(unittest.TestCase):
    def test_versions_dates_and_file_order(self):
        c = parse_text(SAMPLE)
        self.assertEqual(c.status, "ok")
        self.assertEqual([r.version for r in c.releases], ["2.0.0", "1.9.0", "1.8.0"])
        self.assertEqual([r.date for r in c.releases], ["2027-01-05", "", "2026-02-03"])
        self.assertIs(c.latest, c.releases[0])

    def test_summary_sections_and_inline_cleanup(self):
        r = parse_text(SAMPLE).releases[0]
        self.assertEqual(r.summary, "Big release that continues here.")
        self.assertEqual([s.title for s in r.sections], ["New Features", "Security"])
        self.assertEqual(r.sections[0].items[0], "Shiny thing that wraps onto a second line.")
        self.assertEqual(len(r.sections[0].items), 2)

    def test_plain_colon_headings_and_missing_date(self):
        r = parse_text(SAMPLE).releases[1]
        self.assertEqual(r.date, "")
        self.assertEqual([s.title for s in r.sections], ["Bug Fixes", "Improvements"])
        self.assertEqual(r.sections[1].items, ["star bullet"])

    def test_release_without_notes(self):
        self.assertTrue(parse_text(SAMPLE).releases[2].is_empty())

    def test_real_changelog_matches_app_version(self):
        c = ChangelogParser().load()
        self.assertEqual(c.status, "ok")
        self.assertEqual(c.releases[0].version, APP_VERSION)
        self.assertTrue(c.releases[0].date)
        self.assertGreaterEqual(len(c.releases), 2)
        titles = [s.title for s in c.releases[0].sections]
        for want in ("New Features", "Improvements", "Bug Fixes"):
            self.assertIn(want, titles)

    def test_empty_and_whitespace(self):
        self.assertEqual(parse_text("").status, "empty")
        self.assertEqual(parse_text("  \n\n").status, "empty")

    def test_invalid_markdown_never_raises(self):
        for junk in ("just words", "## not a version\n- x", "\x00\x01## 1.0\n", "# only title",
                     "## \n###\n- \n", None, 123, "## 1.0.0 (\n- a\n  \n\t- b"):
            c = parse_text(junk)                       # type: ignore[arg-type]
            self.assertIn(c.status, ("ok", "empty", "invalid"))

    def test_crlf_and_unicode(self):
        c = parse_text("## 3.0 (2027-02-02)\r\n### New Features\r\n- Größe 日本語 ✓\r\n")
        self.assertEqual(c.releases[0].sections[0].items, ["Größe 日本語 ✓"])

    def test_missing_file_and_cache_refresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "CHANGELOG.md")
            p = ChangelogParser(path)
            self.assertEqual(p.load().status, "missing")
            write(tmp, "## 1.0.0 (2026-01-01)\n- a\n")
            first = p.load()
            self.assertEqual(first.status, "ok")
            self.assertIs(p.load(), first)             # cached while the file is unchanged
            write(tmp, "## 1.0.1 (2026-01-02)\n- a\n- b\n")
            os.utime(path, (time.time() + 5, time.time() + 5))
            self.assertEqual(p.load().releases[0].version, "1.0.1")
            os.remove(path)
            self.assertEqual(p.load().status, "missing")

    def test_binary_file_does_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "CHANGELOG.md")
            with open(path, "wb") as fh:
                fh.write(os.urandom(4096))
            self.assertIn(ChangelogParser(path).load().status, ("invalid", "empty", "ok"))


# --------------------------------------------------------------------------- Qt
from PyQt6.QtWidgets import QApplication  # noqa: E402

from core.store import History, Settings  # noqa: E402
from ui.ctx import Ctx  # noqa: E402
from ui.pages.changelog import ChangelogPage, ReleaseCard  # noqa: E402
from ui.theme import theme  # noqa: E402
from ui.window import AppWindow  # noqa: E402

app = QApplication.instance() or QApplication([])


def pump(n: int = 4) -> None:
    for _ in range(n):
        app.processEvents()


class TestChangelogUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = Ctx(Settings(), History())
        cls.win = AppWindow(cls.ctx)
        cls.win.resize(1120, 760)
        cls.win.show()
        pump()

    @classmethod
    def tearDownClass(cls):
        cls.win.close()

    def page(self, text: str | None = None) -> ChangelogPage:
        self.win.navigate("changelog")
        pg = self.win.pages["changelog"]
        if text is not None:
            self._tmp = tempfile.TemporaryDirectory()
            self.addCleanup(self._tmp.cleanup)
            pg.parser = ChangelogParser(write(self._tmp.name, text))
            pg.reload()
        pump()
        return pg

    def test_sidebar_item_opens_page(self):
        btn = self.win.nav["changelog"]
        self.assertTrue(btn.isVisible())
        self.assertIn("Changelog", btn.text() if hasattr(btn, "text") else "Changelog")
        self.win.navigate("dashboard")
        btn.click()
        pump()
        self.assertEqual(self.win.current_key(), "changelog")
        self.assertTrue(btn.isChecked())
        self.assertIsInstance(self.win.pages["changelog"], ChangelogPage)

    def test_sidebar_order_above_settings(self):
        lay = self.win.sidebar.layout()
        idx = {self.win.nav[k]: i for k in ("changelog", "settings")
               for i in [lay.indexOf(self.win.nav[k])]}
        self.assertLess(idx[self.win.nav["changelog"]], idx[self.win.nav["settings"]])

    def test_other_navigation_still_works(self):
        for key in ("dashboard", "upload", "queue", "download", "history", "settings"):
            self.win.nav[key].click()
            pump()
            self.assertEqual(self.win.current_key(), key)
        self.assertEqual(self.win.nav["settings"].isChecked(), True)

    def test_real_file_renders_latest_and_collapsed_older(self):
        pg = self.page()
        pg.parser = ChangelogParser()
        pg.reload()
        pump()
        self.assertGreaterEqual(len(pg.cards), 2)
        self.assertTrue(pg.cards[0].latest and pg.cards[0].body.isVisibleTo(pg))
        self.assertEqual(pg.cards[0].version_label.text(), f"v{APP_VERSION}")
        for older in pg.cards[1:]:
            self.assertFalse(older.expanded)
            self.assertFalse(older.body.isVisibleTo(pg))

    def test_older_release_expands_and_collapses(self):
        pg = self.page(SAMPLE)
        self.assertEqual(len(pg.cards), 3)
        older = pg.cards[1]
        self.assertFalse(older.body.isVisibleTo(pg))
        older.header.clicked.emit()
        pump()
        self.assertTrue(older.expanded and older.body.isVisibleTo(pg))
        older.toggle()
        pump()
        self.assertFalse(older.expanded)
        pg.cards[0].toggle()                          # latest never collapses
        self.assertTrue(pg.cards[0].expanded)

    def test_undated_release_shows_no_date_label(self):
        pg = self.page(SAMPLE)
        self.assertEqual(pg.cards[1].date_label.text(), "")
        self.assertFalse(pg.cards[1].date_label.isVisibleTo(pg.cards[1]))
        self.assertEqual(pg.cards[0].date_label.text(), "2027-01-05")

    def test_missing_empty_and_invalid_states(self):
        pg = self.page()
        pg.parser = ChangelogParser(os.path.join(DATA, "does-not-exist.md"))
        pg.reload()
        pump()
        self.assertEqual(pg.cards, [])
        self.assertTrue(pg.empty_state.isVisibleTo(pg))
        for text in ("", "no headings at all", "## garbage heading\n- x"):
            pg = self.page(text)
            self.assertEqual(pg.cards, [], text)
            self.assertTrue(pg.empty_state.isVisibleTo(pg), text)

    def test_reload_recovers_after_empty_state(self):
        pg = self.page("")
        self.assertEqual(pg.cards, [])
        write(self._tmp.name, SAMPLE)
        os.utime(pg.parser.path, (time.time() + 9, time.time() + 9))
        pg.reload()
        self.assertEqual(len(pg.cards), 3)

    def test_both_themes_render_and_switch_repeatedly(self):
        pg = self.page(SAMPLE)
        for mode in ("dark", "light", "dark", "cream", "light", "navy"):
            theme.apply(app, mode, None)
            pump()
            self.assertFalse(self.win.grab().isNull(), mode)
            self.assertTrue(pg.cards[0].isVisibleTo(pg))
        theme.apply(app, "dark", None)

    def test_no_text_clipping_in_release_cards(self):
        pg = self.page(SAMPLE)
        pg.cards[1].set_expanded(True)
        pump()
        from PyQt6.QtWidgets import QLabel
        for lb in pg.findChildren(QLabel):
            if lb.isVisibleTo(pg) and lb.wordWrap() and lb.text():
                self.assertLessEqual(lb.heightForWidth(lb.width()), lb.height() + 1, lb.text())

    def test_release_card_standalone(self):
        r = parse_text(SAMPLE).releases[0]
        card = ReleaseCard(r, latest=False, expanded=True)
        self.assertTrue(card.expanded)
        card.set_expanded(False)
        self.assertFalse(card.expanded)

    def test_changelog_file_is_a_plain_module_dependency(self):
        self.assertTrue(os.path.exists(cl.default_path()))




class TestNoStrayWindows(unittest.TestCase):
    """Opening the Changelog page must not flash a stray top-level window
    (a parentless widget made visible pops up as its own window)."""

    def test_no_toplevel_shown_when_opening_page(self):
        from PyQt6.QtCore import QObject, QEvent
        from PyQt6.QtWidgets import QApplication
        import time as _t
        app = QApplication.instance()
        seen = []

        class F(QObject):
            def eventFilter(self, o, e):  # noqa: N802
                if e.type() == QEvent.Type.Show and o.isWidgetType() and o.isWindow():
                    seen.append(type(o).__name__)
                return False
        from tests.test_ui import make_window
        w, _ctx = make_window()
        f = F()
        app.installEventFilter(f)
        try:
            seen.clear()
            w.nav["changelog"].click()
            for _ in range(30):
                app.processEvents()
                _t.sleep(0.01)
            w.nav["settings"].click()
            w.nav["changelog"].click()
            for _ in range(30):
                app.processEvents()
                _t.sleep(0.01)
        finally:
            app.removeEventFilter(f)
            w.hide()
        self.assertEqual(seen, [])


class TestBannersAndRealFile(unittest.TestCase):
    def test_real_changelog_has_exactly_two_releases_with_banners(self):
        from core.changelog import ChangelogParser, default_path
        from ui.pages.changelog import banner_path
        data = ChangelogParser(default_path()).load()
        self.assertEqual([r.version for r in data.releases], ["1.0.0.1", "1.0.0.0"])
        self.assertEqual(data.releases[1].date, "")                 # never invented
        for r in data.releases:
            self.assertTrue(banner_path(r.version), r.version)
        self.assertGreater(len(data.releases[1].sections[0].items), 10)

    def test_page_shows_banners_and_expand_all(self):
        from tests.test_ui import make_window, wait_until
        w, _ctx = make_window()
        try:
            w.navigate("changelog")
            pg = w.pages["changelog"]
            self.assertEqual(len(pg.cards), 2)
            self.assertTrue(all(c.banner is not None for c in pg.cards))
            self.assertTrue(pg.toggle_btn.isVisible())
            self.assertFalse(pg.cards[1].expanded)
            pg.toggle_all()
            self.assertTrue(pg.cards[1].expanded)
            self.assertEqual(pg.toggle_btn.text(), "Collapse all")
            pg.toggle_all()
            self.assertFalse(pg.cards[1].expanded)
            for c in pg.cards:                                      # banner keeps its aspect ratio
                self.assertEqual(c.banner.heightForWidth(1000), round(1000 * c.banner._src.height() / c.banner._src.width()))
        finally:
            w.hide()


if __name__ == "__main__":
    unittest.main()
