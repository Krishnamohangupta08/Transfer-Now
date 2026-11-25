"""Build Transfer Now into a Windows .exe (or a macOS / Linux app) with PyInstaller.

    python build.py              # both: single file AND folder
    python build.py onefile      # dist/onefile/TransferNow(.exe)            (one portable file)
    python build.py onedir       # Windows/Linux: dist/onedir/TransferNow/TransferNow(.exe)
                                 # macOS:         dist/onedir/TransferNow.app  (bundle)
    python build.py both         # always ends with ready-to-upload .zip file(s) in dist/

Options:  --console  keep a console window (debugging)   --dry-run  print the commands only
          --no-clean keep PyInstaller's cache             --skip-icon  don't (re)generate the icon
          --no-zip   skip the zip step (zips are ALWAYS made by default)

PyInstaller does not cross-compile: build the .exe on Windows.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
NAME = "TransferNow"
from core.version import FILE_VERSION_STR as VERSION  # single source of truth
AUTHOR = "Krishna Mohan Gupta"
ASSETS = os.path.join(ROOT, "assets")


def icon_path() -> str:
    if os.name == "nt":
        return os.path.join(ASSETS, "icon.ico")
    if sys.platform == "darwin":
        return os.path.join(ASSETS, "icon.icns")
    return os.path.join(ASSETS, "icon.png")


def ensure_tools(skip_icon: bool, dry: bool) -> None:
    """Install PyInstaller / Pillow if missing and create the icon if it is missing."""
    missing = []
    for mod, pip_name in (("PyInstaller", "pyinstaller>=6.6"), ("PIL", "pillow>=10.0")):
        try:
            __import__(mod)
        except ImportError:
            missing.append(pip_name)
    if missing:
        print("Installing build tools:", ", ".join(missing))
        if not dry:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", *missing])
    if not skip_icon and not os.path.exists(icon_path()):
        print("Creating the app icon...")
        if not dry:
            subprocess.check_call([sys.executable, os.path.join(ROOT, "tools", "make_icon.py")])


def write_version_file(path: str) -> None:
    """Windows 'Details' tab: product name, version, author."""
    v = (tuple(int(x) for x in VERSION.split(".")) + (0, 0, 0, 0))[:4]   # exactly 4 parts
    text = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={v}, prodvers={v}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', '{AUTHOR}'),
      StringStruct('FileDescription', 'Transfer Now - create and fetch TransferNow links'),
      StringStruct('FileVersion', '{VERSION}'),
      StringStruct('InternalName', '{NAME}'),
      StringStruct('LegalCopyright', 'Copyright (c) 2026 {AUTHOR}'),
      StringStruct('OriginalFilename', '{NAME}.exe'),
      StringStruct('ProductName', 'Transfer Now'),
      StringStruct('ProductVersion', '{VERSION}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ])
"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def command(mode: str, console: bool, clean: bool) -> list[str]:
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm"]
    if clean:
        cmd.append("--clean")
    cmd += ["--onefile" if mode == "onefile" else "--onedir"]
    cmd += ["--console" if console else "--windowed"]
    cmd += ["--name", NAME, "--icon", icon_path(),
            "--distpath", os.path.join(ROOT, "dist", mode),
            "--workpath", os.path.join(ROOT, "build", mode),
            "--specpath", os.path.join(ROOT, "build", mode),
            "--collect-all", "selenium",                 # ships selenium-manager (auto chromedriver)
            # Runtime asset paths must match what main.py (_load_bundled_fonts) and
            # ui/about.py (asset_path) look up under sys._MEIPASS:
            #   fonts + license -> <bundle>/assets/fonts/   photo -> <bundle>/assets/
            # NOTE: for a directory source PyInstaller copies its *contents* into dest,
            # so dest must already end in "fonts".  The window/taskbar icon needs no
            # data file: ui/icons.py renders it from inline SVG; icon.ico/.icns/.png
            # are only passed to --icon (embedded in the binary / .app bundle).
            "--add-data", os.path.join(ASSETS, "fonts") + os.pathsep + os.path.join("assets", "fonts"),
            "--add-data", os.path.join(ASSETS, "developer.png") + os.pathsep + "assets",
            "--add-data", os.path.join(ASSETS, "banners") + os.pathsep + os.path.join("assets", "banners"),
            "--add-data", os.path.join(os.path.dirname(os.path.abspath(__file__)), "CHANGELOG.md") + os.pathsep + ".",
            "--add-data", ASSETS + os.pathsep + "assets",     # whole folder: nothing visible gets left behind
            "--hidden-import", "ui.embedded_assets",       # embedded fallback copy of photo/banners/fonts
            "--hidden-import", "ui.assets",
            "--hidden-import", "ui.iconfx",
            "--hidden-import", "PyQt6.QtSvg",
            "--hidden-import", "psutil",
            "--hidden-import", "PyQt6.QtMultimedia",     # sound effects
            "--exclude-module", "tkinter"]
    if os.name == "nt":
        vf = os.path.join(ROOT, "build", "version_info.txt")
        write_version_file(vf)
        cmd += ["--version-file", vf]
    cmd.append(os.path.join(ROOT, "main.py"))
    return cmd


def result_path(mode: str) -> str:
    """Where PyInstaller puts the built app for this OS / mode.

    Windows onefile  dist/onefile/TransferNow.exe      Linux onefile  dist/onefile/TransferNow
    Windows onedir   dist/onedir/TransferNow/TransferNow.exe          Linux onedir  .../TransferNow
    macOS onefile    dist/onefile/TransferNow (plain Unix executable, *not* a .app)
    macOS onedir     dist/onedir/TransferNow.app  (bundle directly under dist/onedir)
    """
    exe = NAME + (".exe" if os.name == "nt" else "")
    if sys.platform == "darwin" and mode == "onedir":
        return os.path.join(ROOT, "dist", "onedir", NAME + ".app")
    if mode == "onefile":
        return os.path.join(ROOT, "dist", "onefile", exe)
    return os.path.join(ROOT, "dist", "onedir", NAME, exe)


def size_text(path: str) -> str:
    total = 0
    if os.path.isdir(path):
        for base, _d, files in os.walk(path):
            total += sum(os.path.getsize(os.path.join(base, f)) for f in files)
    elif os.path.exists(path):
        total = os.path.getsize(path)
    return f"{total / 1024 / 1024:.1f} MB"


def make_zip(mode: str) -> str:
    """Zip a finished build -> dist/ (always done, so there is one file to upload).

    onefile -> dist/TransferNow-portable.zip   (the single TransferNow executable)
    onedir  -> dist/TransferNow-folder.zip     (the whole TransferNow folder / .app)
    """
    dist = os.path.join(ROOT, "dist")
    if mode == "onefile":
        out = os.path.join(dist, "onefile")
        z = shutil.make_archive(os.path.join(dist, f"{NAME}-portable"), "zip", out,
                                os.path.basename(result_path("onefile")))
    else:
        z = shutil.make_archive(os.path.join(dist, f"{NAME}-folder"), "zip",
                                os.path.join(dist, "onedir"),
                                NAME + ".app" if sys.platform == "darwin" else NAME)
    print(f"[zip    ] {z}")
    return z


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("mode", nargs="?", default="both", choices=("onefile", "onedir", "both"))
    ap.add_argument("--console", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-clean", action="store_true")
    ap.add_argument("--skip-icon", action="store_true")
    ap.add_argument("--zip", action="store_true", help="(default, kept for compatibility) zip the build")
    ap.add_argument("--no-zip", action="store_true", help="do not create the .zip files")
    a = ap.parse_args()

    os.chdir(ROOT)
    ensure_tools(a.skip_icon, a.dry_run)
    if not a.dry_run:                       # refresh the embedded copy of photo / banners / fonts / changelog
        subprocess.check_call([sys.executable, os.path.join(ROOT, "tools", "embed_assets.py")])
    modes = ("onefile", "onedir") if a.mode == "both" else (a.mode,)
    built: list[str] = []
    zips: list[str] = []
    for mode in modes:
        cmd = command(mode, a.console, not a.no_clean)
        print(f"\n=== {mode} ===\n" + " ".join(f'"{c}"' if " " in c else c for c in cmd) + "\n")
        if a.dry_run:
            continue
        if subprocess.call(cmd) != 0:
            print(f"\nBuild failed ({mode}).")
            return 1
        built.append(mode)

    for mode in built:
        out = result_path(mode)
        size_of = out if (mode == "onefile" or sys.platform == "darwin") else os.path.dirname(out)
        print(f"[{mode:7s}] {out}  ({size_text(size_of)})")
        if not a.no_zip:
            zips.append(make_zip(mode))
    if zips:
        print("\nReady to upload:")
        for z in zips:
            print(f"  {z}  ({size_text(z)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
