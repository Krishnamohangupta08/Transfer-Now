"""Build Transfer Now into a Windows .exe (or a macOS / Linux app) with PyInstaller.

    python build.py              # both: single file AND folder
    python build.py onefile      # dist/onefile/TransferNow.exe          (one portable file)
    python build.py onedir       # dist/onedir/TransferNow/TransferNow.exe (folder, starts faster)
    python build.py both --zip   # also zip the folder build for sharing

Options:  --console  keep a console window (debugging)   --dry-run  print the commands only
          --no-clean keep PyInstaller's cache             --skip-icon  don't (re)generate the icon

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
VERSION = "1.0.0.0"
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
            "--add-data", os.path.join(ASSETS, "fonts") + os.pathsep + "assets",
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
    exe = NAME + (".exe" if os.name == "nt" else ".app" if sys.platform == "darwin" and mode == "onedir" else "")
    if mode == "onefile":
        return os.path.join(ROOT, "dist", "onefile", exe)
    return os.path.join(ROOT, "dist", "onedir", NAME, exe if os.name == "nt" else NAME)


def size_text(path: str) -> str:
    total = 0
    if os.path.isdir(path):
        for base, _d, files in os.walk(path):
            total += sum(os.path.getsize(os.path.join(base, f)) for f in files)
    elif os.path.exists(path):
        total = os.path.getsize(path)
    return f"{total / 1024 / 1024:.1f} MB"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("mode", nargs="?", default="both", choices=("onefile", "onedir", "both"))
    ap.add_argument("--console", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-clean", action="store_true")
    ap.add_argument("--skip-icon", action="store_true")
    ap.add_argument("--zip", action="store_true", help="zip the folder build")
    a = ap.parse_args()

    os.chdir(ROOT)
    ensure_tools(a.skip_icon, a.dry_run)
    modes = ("onefile", "onedir") if a.mode == "both" else (a.mode,)
    built: list[str] = []
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
        print(f"[{mode:7s}] {out}  ({size_text(out if mode == 'onefile' else os.path.dirname(out))})")
        if mode == "onedir" and a.zip:
            z = shutil.make_archive(os.path.join(ROOT, "dist", f"{NAME}-folder"), "zip",
                                    os.path.join(ROOT, "dist", "onedir"), NAME)
            print(f"[zip    ] {z}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
