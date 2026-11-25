"""Packaging / build tests — fast and fully offline unless a frozen build exists.

* build.py unit tests    — command() flags + --add-data destinations vs. the paths the
                           runtime (main.py / ui/about.py) actually reads, result_path()
                           per OS (mocked), Windows version-file generation.
* asset completeness     — every file the runtime reads from assets/ exists in the repo.
* requirements hygiene   — runtime vs. build vs. dev split, and a module↔requirement
                           mapping for every third-party import in core/ + ui/ + main.py.
* launcher scripts       — run.sh / build_exe.sh parse as bash, run.bat / build_exe.bat
                           carry the expected commands.
* frozen smoke (skipped without a build) — boots dist/onedir/.../TransferNow headless,
                           expects it alive after 6 s, then SIGTERM: no crash, no traceback,
                           and data files written under TN_DATA_DIR.  Opt-in full build
                           with TN_BUILD_SMOKE=1.

Run:  python -m unittest tests.test_packaging -v
"""
from __future__ import annotations

import ast
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
from typing import ClassVar

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import build  # repo-root import, mirrors how CI runs it

NAME = "TransferNow"
FONTS = ("Inter-Variable.ttf", "InterTight-Variable.ttf", "OFL.txt")


def read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


# ----------------------------------------------------------------- build.py
class TestCommand(unittest.TestCase):
    """build.command() must stay CLI-compatible and ship the right files."""

    @staticmethod
    def add_data_pairs(cmd: list[str]) -> list[tuple[str, str]]:
        pairs = []
        for i, part in enumerate(cmd):
            if part == "--add-data":
                src, dest = cmd[i + 1].split(os.pathsep, 1)
                pairs.append((src, dest))
        return pairs

    def test_fonts_land_where_main_py_reads_them(self):
        """main.py: _load_bundled_fonts() reads <MEIPASS>/assets/fonts/*.ttf.

        PyInstaller copies a *directory* source's contents into dest, so dest must
        already end in fonts (a bare assets dest once flattened the fonts into
        assets/ and the runtime silently skipped them).
        """
        pairs = self.add_data_pairs(build.command("onedir", False, True))
        fonts = [p for p in pairs if p[0].endswith(os.path.join("assets", "fonts"))]
        self.assertEqual(len(fonts), 1, f"one fonts --add-data expected, got {pairs}")
        self.assertEqual(fonts[0][1], os.path.join("assets", "fonts"))
        self.assertIn('"assets", "fonts"', read(os.path.join(ROOT, "main.py")))  # the reader

    def test_photo_lands_where_about_py_reads_it(self):
        """ui/about.py: asset_path() reads <MEIPASS>/assets/developer.png."""
        pairs = self.add_data_pairs(build.command("onedir", False, True))
        photo = [p for p in pairs if p[0].endswith("developer.png")]
        self.assertEqual(len(photo), 1, f"one developer.png --add-data expected, got {pairs}")
        self.assertEqual(photo[0][1], "assets")
        self.assertIn('PHOTO_FILE = "developer.png"', read(os.path.join(ROOT, "ui", "about.py")))

    def test_mode_and_flag_switches(self):
        onefile = build.command("onefile", False, True)
        onedir = build.command("onedir", False, True)
        console = build.command("onedir", True, True)
        self.assertIn("--onefile", onefile)
        self.assertIn("--onedir", onedir)
        self.assertIn("--console", console)
        self.assertIn("--windowed", onedir)
        self.assertIn("--clean", onedir)
        self.assertNotIn("--clean", build.command("onedir", False, False))   # --no-clean
        self.assertTrue(onedir[-1].endswith("main.py"))                      # entry point
        for cmd in (onefile, onedir, console):
            for flag, value in (("--name", NAME), ("--icon", build.icon_path())):
                self.assertEqual(cmd[cmd.index(flag) + 1], value)
            self.assertIn("selenium", cmd[cmd.index("--collect-all") + 1])   # selenium-manager
            hidden = {cmd[i + 1] for i, v in enumerate(cmd) if v == "--hidden-import"}
            self.assertIn("psutil", hidden)
            excluded = {cmd[i + 1] for i, v in enumerate(cmd) if v == "--exclude-module"}
            self.assertIn("tkinter", excluded)

    def test_windows_version_file_is_wired(self):
        with tempfile.TemporaryDirectory() as tmp, \
                unittest.mock.patch.object(build, "ROOT", tmp), \
                unittest.mock.patch("os.name", "nt"):
                cmd = build.command("onedir", False, True)
                self.assertIn("--version-file", cmd)
                self.assertEqual(cmd[cmd.index("--version-file") + 1],
                                 os.path.join(tmp, "build", "version_info.txt"))
                self.assertTrue(os.path.exists(cmd[cmd.index("--version-file") + 1]))


class TestResultPath(unittest.TestCase):
    """One correct output path per OS x mode (mocked, no build needed)."""

    def check(self, os_name, platform, mode, tail):
        with unittest.mock.patch("os.name", os_name), \
             unittest.mock.patch("sys.platform", platform):
            out = build.result_path(mode)
        self.assertTrue(out.endswith(tail), f"{os_name}/{platform}/{mode}: {out} !~ {tail}")
        self.assertTrue(os.path.isabs(out))
        return out

    def test_linux(self):
        self.check("posix", "linux", "onefile", os.path.join("dist", "onefile", NAME))
        self.check("posix", "linux", "onedir", os.path.join("dist", "onedir", NAME, NAME))

    def test_windows(self):
        self.check("nt", "win32", "onefile", os.path.join("dist", "onefile", NAME + ".exe"))
        self.check("nt", "win32", "onedir", os.path.join("dist", "onedir", NAME, NAME + ".exe"))

    def test_macos_onefile_is_a_plain_executable(self):
        self.check("posix", "darwin", "onefile", os.path.join("dist", "onefile", NAME))

    def test_macos_onedir_is_a_bundle_directly_under_dist(self):
        out = self.check("posix", "darwin", "onedir", os.path.join("dist", "onedir", NAME + ".app"))
        self.assertNotIn(os.path.join(NAME, NAME + ".app"), out)   # not nested inside a folder


class TestVersionFile(unittest.TestCase):
    def test_exactly_four_parts_and_padding(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "version_info.txt")
            build.write_version_file(path)
            v = re.search(r"filevers=\((\d+), (\d+), (\d+), (\d+)\)", read(path))
            self.assertEqual(tuple(map(int, v.groups())), (1, 0, 0, 1))
            build.VERSION = "2.5"                                   # 2-part input -> padded
            try:
                build.write_version_file(path)
                v = re.search(r"filevers=\((\d+), (\d+), (\d+), (\d+)\)", read(path))
                self.assertEqual(tuple(map(int, v.groups())), (2, 5, 0, 0))
            finally:
                build.VERSION = "1.0.0.1"


# ------------------------------------------------------------------- assets
class TestAssets(unittest.TestCase):
    """Every file the runtime reads from assets/ must exist (and be non-empty)."""

    def test_runtime_assets_exist(self):
        fonts_dir = os.path.join(ROOT, "assets", "fonts")
        for name in FONTS:                                   # 2 fonts + the OFL license
            path = os.path.join(fonts_dir, name)
            self.assertTrue(os.path.isfile(path), f"missing {path}")
            self.assertGreater(os.path.getsize(path), 0)
        for name in ("developer.png", "icon.ico", "icon.icns", "icon.png"):
            path = os.path.join(ROOT, "assets", name)
            self.assertTrue(os.path.isfile(path), f"missing {path} (one per platform)")
            self.assertGreater(os.path.getsize(path), 0)

    def test_icon_paths_cover_all_platforms(self):
        self.assertEqual(os.path.basename(build.icon_path()), "icon.png")     # this sandbox
        with unittest.mock.patch("os.name", "nt"):
            self.assertEqual(os.path.basename(build.icon_path()), "icon.ico")
        with unittest.mock.patch("sys.platform", "darwin"):
            self.assertEqual(os.path.basename(build.icon_path()), "icon.icns")


# ------------------------------------------------------------- requirements
class TestRequirements(unittest.TestCase):
    """Runtime / build / dev deps stay split; imports map onto requirements.txt."""

    MAPPING: ClassVar[dict[str, str]] = {
        "PyQt6": "PyQt6", "selenium": "selenium", "requests": "requests",
        "urllib3": "urllib3",
        "psutil": "psutil", "qrcode": "qrcode"}              # qrcode: optional in code
    LOCAL: ClassVar[set[str]] = {"core", "ui", "tests", "tools", "main", "build"}

    @staticmethod
    def third_party_modules() -> set[str]:
        stdlib = set(getattr(sys, "stdlib_module_names", ()))
        found: set[str] = set()
        for base in ("core", "ui"):
            for base_dir, _dirs, files in os.walk(os.path.join(ROOT, base)):
                if "__pycache__" in base_dir:
                    continue
                for f in files:
                    if f.endswith(".py"):
                        found |= TestRequirements._imports(os.path.join(base_dir, f))
        found |= TestRequirements._imports(os.path.join(ROOT, "main.py"))
        return found - stdlib - TestRequirements.LOCAL - {"__future__"}

    @staticmethod
    def _imports(path: str) -> set[str]:
        top: set[str] = set()
        for node in ast.walk(ast.parse(read(path), filename=path)):
            if isinstance(node, ast.Import):
                top |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                top.add(node.module.split(".")[0])
        return top

    def test_runtime_imports_are_covered(self):
        self.assertEqual(self.third_party_modules(), set(self.MAPPING),
                         "a new third-party import needs a requirements.txt entry "
                         "and a MAPPING line here")

    def test_runtime_file_has_no_build_tools(self):
        text = read(os.path.join(ROOT, "requirements.txt")).lower()
        self.assertNotIn("pyinstaller", text)
        self.assertNotIn("pillow", text)
        for pkg in self.MAPPING.values():
            self.assertRegex(text, rf"(?m)^{pkg.lower()}[><=~\s]")

    def test_build_file_has_build_tools(self):
        text = read(os.path.join(ROOT, "requirements-build.txt")).lower()
        self.assertRegex(text, r"(?m)^pyinstaller[><=~\s]")
        self.assertRegex(text, r"(?m)^pillow[><=~\s]")

    def test_dev_file_has_dev_tools(self):
        text = read(os.path.join(ROOT, "requirements-dev.txt")).lower()
        for tool in ("ruff", "bandit", "pip-audit"):
            self.assertRegex(text, rf"(?m)^{tool}[><=~\s]")


# ------------------------------------------------------------------ scripts
class TestLauncherScripts(unittest.TestCase):
    def test_bash_scripts_parse(self):
        for name in ("run.sh", "build_exe.sh"):
            path = os.path.join(ROOT, name)
            self.assertTrue(os.path.isfile(path) and os.path.getsize(path) > 0, name)
            proc = subprocess.run(["bash", "-n", path], check=False,
                                  capture_output=True, text=True, timeout=60)
            self.assertEqual(proc.returncode, 0, f"{name} syntax error:\n{proc.stderr}")

    def test_sh_venvs_install_runtime_only(self):
        run = read(os.path.join(ROOT, "run.sh"))
        self.assertIn("requirements.txt", run)
        self.assertNotIn("requirements-build.txt", run)          # launcher stays light
        self.assertIn("requirements.txt", read(os.path.join(ROOT, "build_exe.sh")))
        self.assertIn("requirements-build.txt", read(os.path.join(ROOT, "build_exe.sh")))

    def test_bat_scripts_reference_expected_files(self):
        run = read(os.path.join(ROOT, "run.bat")).lower()
        self.assertIn("requirements.txt", run)
        self.assertIn("main.py", run)
        self.assertNotIn("requirements-build.txt", run)
        build_bat = read(os.path.join(ROOT, "build_exe.bat")).lower()
        self.assertIn("requirements-build.txt", build_bat)
        self.assertIn("build.py", build_bat)


# ------------------------------------------------------- frozen smoke tests
def frozen_app() -> str | None:
    """Path of a frozen binary on THIS OS, or None (env TN_FROZEN_APP overrides)."""
    env = os.environ.get("TN_FROZEN_APP")
    if env and os.path.exists(env):
        return env
    if os.name == "nt":
        cand = os.path.join(ROOT, "dist", "onedir", NAME, NAME + ".exe")
    elif sys.platform == "darwin":
        cand = os.path.join(ROOT, "dist", "onedir", NAME + ".app", "Contents", "MacOS", NAME)
    else:
        cand = os.path.join(ROOT, "dist", "onedir", NAME, NAME)
    return cand if os.path.exists(cand) else None


@unittest.skipIf(frozen_app() is None, "no frozen build (run python build.py onedir first)")
class TestFrozenSmoke(unittest.TestCase):
    """Boot the frozen app headless: alive after 6 s, clean kill, data written."""

    def test_bundle_carries_runtime_assets(self):
        app = frozen_app()
        internal = os.path.join(os.path.dirname(os.path.abspath(app)), "_internal")
        if not os.path.isdir(internal):
            self.skipTest("TN_FROZEN_APP is not an onedir bundle -- fonts live inside the exe")
        for name in FONTS:
            self.assertTrue(os.path.isfile(os.path.join(internal, "assets", "fonts", name)), name)
        self.assertTrue(os.path.isfile(os.path.join(internal, "assets", "developer.png")))

    def test_app_boots_headless_and_writes_data(self):
        app = frozen_app()
        if os.name == "posix" and sys.platform != "darwin" and \
                shutil.which("ldconfig") and not _libegl_present():
            self.skipTest("libEGL.so.1 missing on this host (install libegl1) -- "
                          "PyQt6.QtGui cannot load, frozen or not")
        with tempfile.TemporaryDirectory(prefix="tn-smoke-") as data_dir, \
                tempfile.TemporaryDirectory(prefix="tn-cwd-") as cwd:
            env = dict(os.environ, QT_QPA_PLATFORM="offscreen", TN_DATA_DIR=data_dir)
            proc = subprocess.Popen([app], cwd=cwd, env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, errors="replace")
            deadline = time.monotonic() + 6.0                  # must survive 6 s
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    self.fail(f"frozen app died after {6.0 - (deadline - time.monotonic()):.1f}s "
                              f"(rc={proc.returncode})\n{proc.communicate(timeout=10)[0]}")
                time.sleep(0.25)
            self.assertIsNone(proc.poll(), "app exited before the 6 s deadline")
            proc.terminate()                                   # SIGTERM: clean kill
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
                self.fail("frozen app ignored SIGTERM for 15 s")
            allowed = {0, -signal.SIGTERM}                     # clean exit or the TERM kill
            if os.name == "nt":
                allowed |= {1, 143, 3221225786}                # TerminateProcess / STATUS_*_EXIT
            self.assertIn(proc.returncode, allowed, f"unexpected exit code {proc.returncode}")
            out = proc.communicate(timeout=10)[0]
            self.assertNotIn("Traceback (most recent call last)", out,
                             f"crashed at runtime:\n{out}")
            sounds = os.path.join(data_dir, "sounds")          # first-start synthesis proves
            self.assertTrue(os.path.isdir(sounds),             # Settings + writes work frozen
                            f"no sounds/ written to TN_DATA_DIR ({list(os.listdir(data_dir))})")
            self.assertTrue([f for f in os.listdir(sounds) if f.endswith(".wav")],
                            "no .wav files synthesised")


def _libegl_present() -> bool:
    """True if libEGL is loadable (needed by PyQt6.QtGui even headless).

    Checks ldconfig first, then any LD_LIBRARY_PATH entries (lets a host that
    cannot install libegl1 provide a shim directory instead).
    """
    if _libegl_present._cached is not None:
        return _libegl_present._cached
    ok = False
    try:
        import ctypes.util
        ok = ctypes.util.find_library("EGL") is not None
        if not ok:
            for entry in (os.environ.get("LD_LIBRARY_PATH") or "").split(os.pathsep):
                if entry and os.path.exists(os.path.join(entry, "libEGL.so.1")):
                    ok = True
                    break
    except Exception:  # noqa: BLE001
        ok = True
    _libegl_present._cached = ok
    return ok


_libegl_present._cached = None


@unittest.skipIf(os.environ.get("TN_BUILD_SMOKE") != "1",
                 "opt-in full build: set TN_BUILD_SMOKE=1 (CI does this in the package jobs)")
class TestFrozenBuild(unittest.TestCase):
    """Full PyInstaller run -- slow, network-independent, opt-in."""

    def test_onedir_build_succeeds_and_carries_assets(self):
        subprocess.run([sys.executable, os.path.join(ROOT, "build.py"), "onedir", "--skip-icon"],
                       cwd=ROOT, check=True, timeout=1200)
        out = build.result_path("onedir")
        self.assertTrue(os.path.exists(out), f"expected build output at {out}")


if __name__ == "__main__":
    unittest.main()
