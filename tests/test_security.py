"""Security regression tests: storage hardening (permissions, atomic writes,
corrupt-file backups, type validation), CSV formula-injection sanitisation,
the folder-upload screening policy, consent / browser hygiene and the
anonymous sender addresses.  Offline, Qt-free, no browser needed.

    python -m unittest tests.test_security -v
"""
from __future__ import annotations

import contextlib
import csv
import inspect
import io
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
DATA = tempfile.mkdtemp(prefix="tn-sec-data-")
os.environ["TN_DATA_DIR"] = DATA

from core import emailgen, engine, filesafety, store  # import after sys.path setup

POSIX = os.name != "nt"
AS_ROOT = POSIX and hasattr(os, "geteuid") and os.geteuid() == 0


def _mk(prefix: str) -> str:
    return tempfile.mkdtemp(prefix=prefix)


def _symlink_or_skip(test: unittest.TestCase, target: str, link: str) -> None:
    """Create a symlink or skip the test when the OS refuses (Windows)."""
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError, AttributeError):
        test.skipTest("symlinks not available on this platform")


# ------------------------------------------------------------- store storage
class TestStorePermissions(unittest.TestCase):
    def setUp(self):
        self.dir = _mk("tn-sec-store-")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    @unittest.skipUnless(POSIX, "POSIX permissions")
    def test_new_files_and_dir_are_owner_only(self):
        os.chmod(self.dir, 0o755)                       # simulate a loose dir
        h = store.History(os.path.join(self.dir, "history.json"))
        h.add({"kind": "upload", "link": "l", "files": []})
        self.assertEqual(stat.S_IMODE(os.stat(self.dir).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.dir, "history.json")).st_mode),
                         0o600)
        s = store.Settings(os.path.join(self.dir, "settings.json"))
        s.set("theme", "navy")
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.dir, "settings.json")).st_mode),
                         0o600)

    @unittest.skipUnless(POSIX, "POSIX permissions")
    def test_loose_files_are_tightened_on_load(self):
        path = os.path.join(self.dir, "settings.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"theme": "light", "parallel_uploads": 3}, f)
        os.chmod(path, 0o644)
        os.chmod(self.dir, 0o755)
        s = store.Settings(path)
        self.assertEqual(s.get("theme"), "light")       # valid data kept
        self.assertEqual(s.get("parallel_uploads"), 3)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.dir).st_mode), 0o700)

    def test_atomic_write_leaves_no_tmp_behind(self):
        path = os.path.join(self.dir, "x.json")
        store._atomic_write(path, "{}")
        self.assertEqual(os.listdir(self.dir), ["x.json"])
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "{}")

    @unittest.skipUnless(POSIX, "POSIX permissions")
    def test_atomic_write_file_is_0600(self):
        path = os.path.join(self.dir, "x.json")
        store._atomic_write(path, "{}")
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)


class TestStoreResilience(unittest.TestCase):
    def setUp(self):
        self.dir = _mk("tn-sec-res-")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_corrupt_history_backed_up_not_destroyed(self):
        path = os.path.join(self.dir, "history.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write('{"kind": "upload"')                # truncated mid-write
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            h = store.History(path)
        self.assertEqual(h.entries(), [])               # defaults, not a crash
        self.assertIsNotNone(h.last_error)
        self.assertIn("corrupt JSON", buf.getvalue())   # problem surfaced on stderr
        backups = [f for f in os.listdir(self.dir) if f.startswith("history.json.corrupt-")]
        self.assertEqual(len(backups), 1, os.listdir(self.dir))
        with open(os.path.join(self.dir, backups[0]), encoding="utf-8") as f:
            self.assertEqual(f.read(), '{"kind": "upload"')   # bytes preserved
        self.assertIn("history.json", h.last_error)

    def test_corrupt_settings_backed_up_and_defaults_used(self):
        path = os.path.join(self.dir, "settings.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write("{not json")
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            s = store.Settings(path)
        self.assertEqual(s.get("theme"), "dark")
        self.assertIsNotNone(s.last_error)
        self.assertIn("corrupt JSON", buf.getvalue())
        self.assertTrue(any(f.startswith("settings.json.corrupt-")
                            for f in os.listdir(self.dir)))

    def test_wrong_shape_reported_and_defaults_used(self):
        path = os.path.join(self.dir, "history.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"not": "a list"}, f)
        h = store.History(path)
        self.assertEqual(h.entries(), [])
        self.assertIsNotNone(h.last_error)

    def test_settings_type_validation_rejects_junk(self):
        path = os.path.join(self.dir, "settings.json")
        junk = {"parallel_uploads": "four", "auto_copy_link": "yes",
                "queue_cache": ["nope"], "theme": 42, "made_up_key": "x"}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(junk, f)
        s = store.Settings(path)
        self.assertEqual(s.get("parallel_uploads"), 2)  # default wins
        self.assertIs(s.get("auto_copy_link"), True)
        self.assertEqual(s.get("queue_cache"), [])
        self.assertEqual(s.get("theme"), "dark")
        self.assertNotIn("made_up_key", s._data)

    def test_settings_range_validation(self):
        path = os.path.join(self.dir, "settings.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"parallel_uploads": 9, "human_pace": 1}, f)   # out of range, int-as-bool
        s = store.Settings(path)
        self.assertEqual(s.get("parallel_uploads"), 2)
        self.assertIs(s.get("human_pace"), True)        # 1 is not a JSON bool

    def test_settings_valid_values_pass_through(self):
        path = os.path.join(self.dir, "settings.json")
        good = {"parallel_uploads": 4, "auto_copy_link": False,
                "queue_cache": [{"id": "a"}], "fixed_email": "me@example.com"}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(good, f)
        s = store.Settings(path)
        self.assertEqual(s.get("parallel_uploads"), 4)
        self.assertIs(s.get("auto_copy_link"), False)
        self.assertEqual(s.get("queue_cache"), [{"id": "a"}])
        self.assertEqual(s.get("fixed_email"), "me@example.com")

    def test_history_entries_get_ids_and_string_ids(self):
        path = os.path.join(self.dir, "history.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump([{"kind": "upload"}, {"id": 42, "kind": "download"},
                       "junk-not-a-dict", {"id": "", "kind": "download"}], f)
        entries = store.History(path).entries()
        self.assertEqual(len(entries), 3)
        ids = [e["id"] for e in entries]
        self.assertTrue(all(isinstance(i, str) and i for i in ids))
        self.assertEqual(ids[1], "42")                  # numeric id stringified

    @unittest.skipIf(os.name == "nt", "POSIX-only hardening detail")
    def test_save_failure_is_surfaced_never_raised(self):
        # The store re-tightens (and would re-open) its data dir on every
        # write, so simulate an unsaveable location with a directory sitting
        # where settings.json should be — os.replace onto it must fail.
        path = os.path.join(self.dir, "settings.json")
        os.makedirs(path)
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            s = store.Settings(path)
            s.set("theme", "navy")                      # must not raise
        self.assertIsNotNone(s.last_error)
        self.assertIn("could not save", buf.getvalue())


# ------------------------------------------------------------- CSV injection
class TestCsvInjection(unittest.TestCase):
    def test_sanitize_csv_cell_direct(self):
        f = store.sanitize_csv_cell
        self.assertEqual(f("=cmd|'/c calc'!A0"), "'=cmd|'/c calc'!A0")
        self.assertEqual(f("+1+1"), "'+1+1")
        self.assertEqual(f("@SUM(A1:A2)"), "'@SUM(A1:A2)")
        self.assertEqual(f("-2+3"), "'-2+3")
        self.assertEqual(f("\t=1"), "'\t=1")
        self.assertEqual(f("\r=1"), "'\r=1")
        self.assertEqual(f(" plain name"), " plain name")
        self.assertEqual(f("plain name"), "plain name")
        self.assertEqual(f("b"), "b")
        self.assertEqual(f("2026-11-23"), "2026-11-23")
        self.assertEqual(f("https://www.transfernow.net/dl/x"), "https://www.transfernow.net/dl/x")
        self.assertEqual(f(""), "")
        self.assertEqual(f(15), 15)                     # numbers untouched
        self.assertIsNone(f(None))

    def test_export_csv_read_back(self):
        entry = {"kind": "upload", "date": "2026-11-23T10:00:00+00:00",
                 "files": [{"name": "=cmd|'/c calc'!A0", "size": 10},
                           {"name": "plain.txt", "size": 5}],
                 "total_bytes": 15,
                 "link": "https://www.transfernow.net/dl/x",
                 "email": "=1+1@evil.example",
                 "expiry": "7 days",
                 "save_path": "@home/dest"}
        out = os.path.join(_mk("tn-sec-csv-"), "export.csv")
        self.addCleanup(shutil.rmtree, os.path.dirname(out), ignore_errors=True)
        self.assertEqual(store.export_csv([entry], out), 1)
        with open(out, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
        self.assertEqual(rows[0][0], "Type")
        row = rows[1]
        self.assertEqual(row[0], "upload")              # app-generated text untouched
        self.assertEqual(row[1], "2026-11-23T10:00:00+00:00")
        self.assertEqual(row[3], "'=cmd|'/c calc'!A0; plain.txt")   # hostile name neutralised
        self.assertEqual(row[5], "https://www.transfernow.net/dl/x")
        self.assertEqual(row[6], "'=1+1@evil.example")  # hostile sender neutralised
        self.assertEqual(row[8], "Active")
        self.assertEqual(row[9], "'@home/dest")
        self.assertEqual(int(row[4]), 15)               # sizes stay numbers
        self.assertEqual(int(row[2]), 2)
        with open(out, encoding="utf-8-sig") as f:      # raw text: numbers unquoted
            raw = f.read()
        self.assertNotIn('"15"', raw)
        self.assertNotIn('"2"', raw)
        self.assertIn(",15,", raw)


# --------------------------------------------------------- folder screening
class TestFileSafety(unittest.TestCase):
    def setUp(self):
        self.root = _mk("tn-sec-root-")
        self.outside = _mk("tn-sec-outside-")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.outside, ignore_errors=True)

    def _write(self, rel: str, content: str = "x") -> str:
        p = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
        return p

    def test_normal_files_ok(self):
        for rel in ("notes.txt", "a.tar.gz", "2026-report.pdf", "data.csv"):
            p = self._write(rel)
            self.assertEqual(filesafety.classify(p, self.root), filesafety.OK, rel)
            self.assertEqual(filesafety.flag_reason(p, self.root), "", rel)

    def test_env_and_key_files_flagged(self):
        cases = {".env": "secrets", ".env.local": "secrets", "deploy.env": "secrets",
                 "id_rsa": "private key", "id_ed25519": "private key",
                 "server.pem": "private key", "cert.pfx": "private key",
                 "backup.kdbx": "credentials", "credentials.json": "credentials",
                 "service-account-prod.json": "credentials",
                 "dump.sql": "database", "data.sqlite3": "database",
                 "Thumbs.db": "metadata", "desktop.ini": "metadata",
                 ".DS_Store": "metadata"}
        for rel, frag in cases.items():
            p = self._write(rel)
            self.assertEqual(filesafety.classify(p, self.root), filesafety.FLAG, rel)
            self.assertIn(frag, filesafety.flag_reason(p, self.root), rel)

    def test_hidden_file_flagged(self):
        p = self._write(".hidden")
        self.assertEqual(filesafety.classify(p, self.root), filesafety.FLAG)
        self.assertEqual(filesafety.flag_reason(p, self.root), "hidden file")

    def test_credential_and_browser_folders_flagged(self):
        p = self._write(".aws/credentials.txt")
        self.assertEqual(filesafety.classify(p, self.root), filesafety.FLAG)
        self.assertIn("credentials", filesafety.flag_reason(p, self.root))
        p = self._write("project/.ssh/id_rsa.pub")
        self.assertEqual(filesafety.classify(p, self.root), filesafety.FLAG)
        p = self._write(".config/google-chrome/cookies.bin")
        self.assertEqual(filesafety.classify(p, self.root), filesafety.FLAG)
        self.assertIn("browser profile", filesafety.flag_reason(p, self.root))

    def test_missing_file_skipped(self):
        p = os.path.join(self.root, "nope.txt")
        self.assertEqual(filesafety.classify(p, self.root), filesafety.SKIP)
        self.assertTrue(filesafety.flag_reason(p, self.root))
        self.assertEqual(filesafety.classify("", ""), filesafety.SKIP)   # never raises

    def test_directory_passed_as_file_skipped(self):
        d = os.path.join(self.root, "subdir")
        os.makedirs(d, exist_ok=True)
        self.assertEqual(filesafety.classify(d, self.root), filesafety.SKIP)
        self.assertIn("regular file", filesafety.flag_reason(d, self.root))

    def test_dotdot_traversal_path_skipped(self):
        outside = os.path.join(self.outside, "outside.txt")
        with open(outside, "w", encoding="utf-8") as f:
            f.write("s")
        sneaky = os.path.join(self.root, "..", os.path.basename(self.outside),
                              "outside.txt")
        self.assertEqual(filesafety.classify(sneaky, self.root), filesafety.SKIP)
        self.assertIn("outside", filesafety.flag_reason(sneaky, self.root))

    @unittest.skipIf(os.name == "nt", "needs POSIX symlinks")
    def test_escaping_symlink_skipped(self):
        target = os.path.join(self.outside, "secret.txt")
        with open(target, "w", encoding="utf-8") as f:
            f.write("s")
        link = os.path.join(self.root, "innocent.txt")
        _symlink_or_skip(self, target, link)
        self.assertEqual(filesafety.classify(link, self.root), filesafety.SKIP)
        self.assertIn("outside", filesafety.flag_reason(link, self.root))

    @unittest.skipIf(os.name == "nt", "needs POSIX symlinks")
    def test_inroot_symlink_allowed(self):
        real = self._write("real.txt")
        link = os.path.join(self.root, "alias.txt")
        _symlink_or_skip(self, real, link)
        self.assertEqual(filesafety.classify(link, self.root), filesafety.OK)
        self.assertEqual(filesafety.flag_reason(link, self.root), "")

    @unittest.skipIf(os.name == "nt", "needs POSIX symlinks")
    def test_broken_symlink_skipped(self):
        link = os.path.join(self.root, "dangling.txt")
        _symlink_or_skip(self, os.path.join(self.root, "gone.txt"), link)
        self.assertEqual(filesafety.classify(link, self.root), filesafety.SKIP)

    def test_screen_batch_splits_allowed_and_flagged(self):
        ok = self._write("a.txt")
        env = self._write(".env")
        allowed, flagged = filesafety.screen_batch([ok, env], self.root)
        self.assertEqual(allowed, [ok, env])            # FLAG stays; only SKIP is dropped
        self.assertEqual(len(flagged), 1)
        self.assertIn(env, flagged[0])
        self.assertIn("—", flagged[0])
        self.assertIn("secrets", flagged[0])

    @unittest.skipIf(os.name == "nt", "needs POSIX symlinks")
    def test_screen_batch_drops_escaping_symlinks_silently(self):
        ok = self._write("a.txt")
        target = os.path.join(self.outside, "secret.txt")
        with open(target, "w", encoding="utf-8") as f:
            f.write("s")
        link = os.path.join(self.root, "innocent.txt")
        _symlink_or_skip(self, target, link)
        allowed, flagged = filesafety.screen_batch([ok, link], self.root)
        self.assertEqual(allowed, [ok])
        self.assertEqual(flagged, [])                   # SKIP is silent, FLAG is not

    def test_individually_picked_files_without_root(self):
        p = self._write("loose.txt")                    # root="" → no folder rules
        self.assertEqual(filesafety.classify(p), filesafety.OK)
        env = self._write(".env")
        self.assertEqual(filesafety.classify(env), filesafety.FLAG)  # names still checked


# ------------------------------------------------------------ email identity
class TestEmailgen(unittest.TestCase):
    DOMAIN_RE = re.compile(r"@example\.(com|org|net)$")
    EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
    REAL_PROVIDERS = ("gmail.com", "outlook.com", "yahoo.com", "hotmail.com",
                      "protonmail.com", "icloud.com", "live.com", "mail.ru",
                      "qq.com", "163.com", "gmx.net", "proton.me")
    HUMAN_NAMES = ("alex", "sarah", "maria", "david", "james", "laura", "emily",
                   "michael", "chris", "jenny", "smith", "johnson", "brown",
                   "miller", "davis", "wilson")

    def test_200_generations_are_anonymous(self):
        seen_domains = set()
        for _ in range(200):
            e = emailgen.random_email()
            self.assertRegex(e, self.EMAIL_RE)
            self.assertRegex(e, self.DOMAIN_RE)
            local, _, domain = e.partition("@")
            self.assertNotIn(domain.lower(), self.REAL_PROVIDERS, e)
            self.assertNotIn(".", local, f"no first.last human pairs: {e}")
            self.assertFalse(local.lower().startswith(self.HUMAN_NAMES), e)
            seen_domains.add(domain)
        self.assertTrue(seen_domains)                   # sanity

    def test_signature_unchanged(self):
        self.assertTrue(callable(emailgen.random_email))
        self.assertIsInstance(emailgen.random_email(), str)


# ------------------------------------------------------------ consent policy
class TestConsentPolicy(unittest.TestCase):
    BANNED = ("fine with me", "i accept all", "accept all")

    def test_source_has_no_accept_labels(self):
        src = inspect.getsource(engine.TransferEngine._dismiss_consent).lower()
        for banned in self.BANNED:
            self.assertNotIn(banned, src, f"consent code still mentions {banned!r}")

    def test_reject_labels_are_reject_only(self):
        labels = list(engine.CONSENT_REJECT_LABELS)
        self.assertTrue(labels)
        self.assertIn("Reject all", labels)
        rejectish = ("reject", "refus", "ablehn", "rechaz", "rifiut",
                     "sans accepter", "without accepting", "necessary")
        for label in labels:
            low = label.lower()
            self.assertTrue(any(w in low for w in rejectish), label)
        for banned in self.BANNED:
            self.assertNotIn(banned, [l.lower() for l in labels])

    def _engine(self):
        return engine.TransferEngine(engine.EngineOptions(files=["x"], human_pace=False),
                                     lambda n, p: None)

    def test_dismiss_clicks_reject_only(self):
        class FakeDriver:
            def __init__(self):
                self.clicked = []

            def execute_script(self, js, *a):
                label = a[0] if a else None
                if label == "Reject all":           # the page offers this…
                    self.clicked.append(label)      # …so the JS clicks it
                    return True
                return False

        drv = FakeDriver()
        self._engine()._dismiss_consent(drv)
        self.assertEqual(drv.clicked, ["Reject all"])

    def test_nothing_clicked_when_only_accept_offered(self):
        class FakeDriver:
            def __init__(self):
                self.clicked = []

            def execute_script(self, js, *a):
                label = a[0] if a else None
                if label == "Accept all":           # the page ONLY offers accept…
                    self.clicked.append(label)      # …but the engine never sends it
                    return True
                return False

        drv = FakeDriver()
        self._engine()._dismiss_consent(drv)
        self.assertEqual(drv.clicked, [], "an accept-all button must never be clicked")

    def test_all_labels_tried_then_left_alone(self):
        tried: list[str] = []

        class FakeDriver:
            def execute_script(self, js, *a):
                tried.append(a[0] if a else None)
                return False

        self._engine()._dismiss_consent(FakeDriver())
        self.assertEqual(tried, list(engine.CONSENT_REJECT_LABELS))
        self.assertTrue(all(t in engine.CONSENT_REJECT_LABELS for t in tried))


# ------------------------------------------------------------ browser flags
class TestBrowserFlags(unittest.TestCase):
    BANNED_IN_SOURCE = ("AutomationControlled", "setUserAgentOverride",
                        "HeadlessChrome", "addScriptToEvaluateOnNewDocument",
                        "excludeSwitches", "enable-automation")

    def setUp(self):
        self.profile = _mk("tn-sec-profile-")

    def tearDown(self):
        shutil.rmtree(self.profile, ignore_errors=True)
        os.environ.pop("TN_CHROME_NO_SANDBOX", None)

    def _start(self):
        class FakeDriver:
            def __init__(self, options=None, service=None):
                self.options = options
                self.service = service

            def set_page_load_timeout(self, t):
                pass

            def set_script_timeout(self, t):
                pass

        fake_wd = types.SimpleNamespace(Chrome=FakeDriver, Edge=FakeDriver)
        return engine.TransferEngine._start_browser(None, fake_wd, "chrome", self.profile)

    def test_sandboxed_and_honest_by_default(self):
        drv = self._start()
        args = drv.options.arguments
        self.assertNotIn("--no-sandbox", args)
        self.assertNotIn("--disable-blink-features=AutomationControlled", args)
        self.assertIn("--window-size=1440,900", args)   # fixed, not randomised
        self.assertIn("--headless=new", args)
        self.assertIn("--disable-dev-shm-usage", args)
        self.assertTrue(any(a.startswith("--user-data-dir=") for a in args))
        self.assertEqual(drv.options.experimental_options, {})   # no excludeSwitches trick
        self.assertEqual(drv.options.page_load_strategy, "eager")

    def test_no_concealment_in_source(self):
        src = inspect.getsource(engine.TransferEngine._start_browser)
        for banned in self.BANNED_IN_SOURCE:
            self.assertNotIn(banned, src, f"concealment trick still present: {banned}")

    def test_no_sandbox_opt_in_for_containers(self):
        for value in ("1", "true", "TRUE"):
            os.environ["TN_CHROME_NO_SANDBOX"] = value
            drv = self._start()
            self.assertIn("--no-sandbox", drv.options.arguments, value)
        os.environ.pop("TN_CHROME_NO_SANDBOX", None)
        self.assertNotIn("--no-sandbox", self._start().options.arguments)


class TestRound3Audit(unittest.TestCase):
    """Regression tests for the round-3 audit fixes (A4-01..A4-13)."""

    def test_a4_01_02_history_type_normalization(self):
        # one corrupt record must never crash consumers: files/total_bytes
        # are normalized at load; string cells used to brick app startup
        entries = store._clean_history([
            {"id": "1", "kind": "upload", "files": "not-a-list",
             "total_bytes": "999", "link": 5, "date": None},
            {"id": "2", "kind": "download",
             "files": [{"name": b"bytes-name", "size": "7"},
                       {"name": "ok.bin", "size": 12},
                       "junk", {"size": 5}],
             "total_bytes": -3},
        ])
        e1, e2 = entries
        self.assertEqual(e1["files"], [])
        self.assertEqual(e1["total_bytes"], 0)
        self.assertEqual(e1["link"], "5")          # str-coerced
        self.assertEqual(e1["date"], "")
        self.assertEqual(e2["files"], [{"name": "ok.bin", "size": 12}])
        self.assertEqual(e2["total_bytes"], 0)
        # the consumers' assumptions hold: join + sum over every record
        for e in entries:
            names = "; ".join(x.get("name", "") for x in e["files"])
            total = sum(f.get("size", 0) for f in e["files"]) \
                    + e["total_bytes"]
            self.assertIsInstance(names, str)
            self.assertIsInstance(total, int)

    def test_a4_03_restored_links_validated(self):
        from core.downloader import parse_transfer_id
        # the exact guard _restore_queue applies to cached links
        self.assertFalse(parse_transfer_id("https://evil.example/payload.exe"))
        self.assertTrue(parse_transfer_id(
            "https://www.transfernow.net/dl/abc123xyz"))

    def test_a4_04_kill_tree_never_kills_recycled_pid(self):
        from core import procs
        killed = []
        rec = proc_mock = None

        class FakeProc:
            def __init__(self, name, create_time):
                self._name, self._ct = name, create_time
                self.children = lambda recursive=True: []
                self.kill = lambda: killed.append(self._name)
                self.name = lambda: self._name
                self.create_time = lambda: self._ct

        registered = FakeProc("chromedriver", 1000.0)
        recycled = FakeProc("python3", 9999.0)
        with mock.patch.object(procs, "psutil") as ps, \
                mock.patch.object(procs.psutil, "Process",
                                  side_effect=[registered, recycled]), \
                mock.patch.dict(procs._pid_ct, {4242: 1000.0}, clear=True):
            procs.kill_tree(4242)          # identity matches -> killed
            self.assertEqual(killed, ["chromedriver"])
            procs.kill_tree(4242)          # recycled pid, wrong identity
            self.assertEqual(killed, ["chromedriver"])   # untouched

    def test_a4_05_storage_log_written_and_last_error_readable(self):
        # _warn must leave a trace even when stderr is None (windowed exe)
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(store, "DATA_DIR", td), \
                    mock.patch.object(store.sys, "stderr", None):
                store._warn("unit-test storage failure")
            log = os.path.join(td, "storage.log")
            self.assertTrue(os.path.exists(log))
            self.assertIn("unit-test storage failure",
                          open(log, encoding="utf-8").read())
        self.assertTrue(hasattr(store.Settings(), "last_error"))
        self.assertTrue(hasattr(store.History(), "last_error"))

    def test_a4_10_stale_tmp_swept(self):
        with tempfile.TemporaryDirectory() as td:
            stale = os.path.join(td, "history.json.123.456.tmp")
            fresh = os.path.join(td, "settings.json.1.2.tmp")
            for p, age in ((stale, 7200), (fresh, 0)):
                open(p, "w").close()
                old = time.time() - age
                os.utime(p, (old, old))
            store._sweep_stale_tmp(td)
            self.assertFalse(os.path.exists(stale))
            self.assertTrue(os.path.exists(fresh))

    @unittest.skipUnless(POSIX, "chmod semantics")
    def test_a4_08_csv_export_owner_only(self):
        hist = store.History()
        hist.add({"kind": "download", "files": [{"name": "a.bin", "size": 3}]})
        out = os.path.join(_mk("tn-csv-"), "h.csv")
        n = store.export_csv(hist.entries(), out)
        self.assertEqual(n, 1)
        self.assertEqual(stat.S_IMODE(os.stat(out).st_mode), 0o600)

    def test_a4_06_frozen_build_ignores_env_hatches(self):
        # TN_SITE_URL / TN_CHROMEDRIVER / TN_CHROME_NO_SANDBOX are
        # source/CI-only; a frozen build must never honor them
        import importlib
        saved = {k: os.environ.get(k) for k in
                 ("TN_SITE_URL", "TN_CHROMEDRIVER", "TN_CHROME_NO_SANDBOX")}
        try:
            os.environ["TN_SITE_URL"] = "https://attacker.example/en"
            os.environ["TN_CHROMEDRIVER"] = "/tmp/evil-driver"
            with mock.patch.object(sys, "frozen", True, create=True):
                mod = importlib.reload(engine)
                self.assertEqual(mod.SITE_URL,
                                 "https://www.transfernow.net/en")
                self.assertIsNone(mod._env_driver_path())
            mod = importlib.reload(engine)      # unfrozen: hatches honored
            self.assertEqual(mod.SITE_URL, "https://attacker.example/en")
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            importlib.reload(engine)

    def test_a4_07_remote_strings_sanitized_on_all_parse_paths(self):
        from core import downloader as dl
        self.assertEqual(dl._display_safe("<img&x>"), "imgx")
        # the fetch_info parse paths route through _display_safe
        src = inspect.getsource(dl.fetch_info)
        self.assertIn('_display_safe((title_m.group(1)', src)
        self.assertIn('_display_safe(unquote(n))', src)

    def test_a4_12_sidebar_collapsed_persists(self):
        s = store.Settings()
        s.set("sidebar_collapsed", True)
        self.assertIn("sidebar_collapsed", store.DEFAULTS)
        self.assertTrue(store._setting_is_valid("sidebar_collapsed", True))
        self.assertTrue(store._setting_is_valid("sidebar_collapsed", False))
        self.assertFalse(store._setting_is_valid("sidebar_collapsed", "yes"))
        s2 = store.Settings()
        self.assertTrue(bool(s2.get("sidebar_collapsed", False)))

if __name__ == "__main__":
    unittest.main(verbosity=2)
