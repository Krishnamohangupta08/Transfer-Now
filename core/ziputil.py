"""Smart ZIP packaging (Qt-free).  Builds ONE archive from the selected files.

Guarantees:

* Originals are only ever read.  Folder structure (relative to the common
  parent folder), Unicode names and bytes are preserved; name collisions get
  `` (2)`` suffixes.
* Output goes to a private temp folder as ``*.part`` and is renamed on success;
  cancel / error / disk-full remove everything.
* Runs on a worker thread; ``progress`` and ``should_stop`` are callbacks.
"""
from __future__ import annotations

import errno
import os
import shutil
import stat
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from typing import Callable, Optional

from . import limits
_COMPRESSED_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp3", ".mp4", ".m4a", ".mkv", ".mov",
                    ".avi", ".webm", ".zip", ".gz", ".bz2", ".xz", ".7z", ".rar", ".docx", ".xlsx",
                    ".pptx", ".heic", ".flac", ".ogg", ".pdf", ".aac"}


def is_precompressed(name: str) -> bool:
    """Extensions whose bytes are already compressed (the packager STOREs them)."""
    return os.path.splitext(name.lower())[1] in _COMPRESSED_EXTS

CHUNK = 1024 * 1024
ARCHIVE_ROOT_NAME = "TransferNow-archives"


class ZipError(Exception):
    """Packaging failed; the message is user-facing."""


class ZipCancelled(Exception):
    pass


@dataclass
class ZipResult:
    path: str
    size: int
    entries: list[str] = field(default_factory=list)       # archive names, in order
    sources: list[str] = field(default_factory=list)


def archive_root() -> str:
    """Folder that holds generated archives (so cleanup can be restricted to it)."""
    return os.path.join(tempfile.gettempdir(), ARCHIVE_ROOT_NAME)


def is_managed_archive(path: str) -> bool:
    try:
        root = os.path.realpath(archive_root())
        return os.path.commonpath([os.path.realpath(path), root]) == root
    except (ValueError, OSError):
        return False


def cleanup_archive(path: str) -> None:
    """Delete a generated archive and its private folder.  No-op for any other path."""
    if not path or not is_managed_archive(path):
        return
    folder = os.path.dirname(os.path.realpath(path))
    if os.path.realpath(folder) != os.path.realpath(archive_root()):
        shutil.rmtree(folder, ignore_errors=True)


def sweep_stale(max_age_s: float = 7 * 86400, keep: tuple[str, ...] = ()) -> int:
    """Remove old archive folders not referenced by ``keep``.  Returns how many were removed."""
    root = archive_root()
    removed = 0
    keep_dirs = {os.path.realpath(os.path.dirname(k)) for k in keep if k}
    try:
        names = os.listdir(root)
    except OSError:
        return 0
    for n in names:
        d = os.path.join(root, n)
        try:
            if os.path.realpath(d) in keep_dirs or not os.path.isdir(d) or os.path.islink(d):
                continue
            if time.time() - os.stat(d).st_mtime > max_age_s:
                shutil.rmtree(d, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    return removed


def plan_names(paths: list[str]) -> list[str]:
    """Archive names preserving folder structure relative to the common parent folder."""
    paths = [os.path.abspath(p) for p in paths]
    try:
        base = os.path.commonpath([os.path.dirname(p) for p in paths]) if paths else ""
    except ValueError:                                   # different Windows drives
        base = ""
    used: set[str] = set()
    out: list[str] = []
    for p in paths:
        rel = os.path.relpath(p, base) if base else os.path.basename(p)
        parts = [c for c in rel.replace("\\", "/").split("/") if c not in ("", ".", "..")]
        name = "/".join(parts) or os.path.basename(p)
        cand, n = name, 2
        stem, ext = os.path.splitext(name)
        while cand.casefold() in used:
            cand = f"{stem} ({n}){ext}"
            n += 1
        used.add(cand.casefold())
        out.append(cand)
    return out


def _zinfo(name: str, st: os.stat_result) -> zipfile.ZipInfo:
    zi = zipfile.ZipInfo(name, time.localtime(max(st.st_mtime, 315532800))[:6])
    zi.compress_type = zipfile.ZIP_STORED if is_precompressed(name) else zipfile.ZIP_DEFLATED
    zi.external_attr = (stat.S_IFREG | 0o644) << 16        # plain file, never executable/setuid
    zi.create_system = 3
    return zi


def build_archive(paths: list[str],
                  archive_name: str = limits.ARCHIVE_NAME,
                  progress: Optional[Callable[[int, int, str], None]] = None,
                  should_stop: Optional[Callable[[], bool]] = None,
                  dest_dir: Optional[str] = None) -> ZipResult:
    """Package ``paths`` into one ZIP.  Raises :class:`ZipError` / :class:`ZipCancelled`."""
    stop = should_stop or (lambda: False)
    if not paths:
        raise ZipError("There are no files to package.")
    paths = [os.path.abspath(p) for p in paths]
    names = plan_names(paths)

    root = dest_dir or archive_root()
    os.makedirs(root, mode=0o700, exist_ok=True)
    work = tempfile.mkdtemp(prefix="a-", dir=root)
    os.chmod(work, 0o700)
    final = os.path.join(work, os.path.basename(archive_name) or limits.ARCHIVE_NAME)
    part = final + ".part"
    real_out = {os.path.realpath(final), os.path.realpath(part)}
    ok = False
    try:
        # ---- pre-flight: exist, safe, sizes, disk space (never includes the output itself)
        sizes: list[int] = []
        for p in paths:
            if os.path.realpath(p) in real_out:
                raise ZipError("The archive cannot contain itself.")
            try:
                sizes.append(os.lstat(p).st_size)
            except OSError as exc:
                raise ZipError(f"File no longer exists: {os.path.basename(p)}") from exc
        total = sum(sizes)
        free = shutil.disk_usage(work).free
        if free < total + 8 * CHUNK:
            raise ZipError("Not enough free disk space to create the ZIP archive.")
        done = 0
        if progress:
            progress(0, total, "")
        with zipfile.ZipFile(part, "w", allowZip64=True, compresslevel=6) as zf:
            for p, name, expected in zip(paths, names, sizes):
                if stop():
                    raise ZipCancelled()
                try:
                    src = open(p, "rb")
                except FileNotFoundError as exc:
                    raise ZipError(f"File no longer exists: {os.path.basename(p)}") from exc
                except OSError as exc:
                    raise ZipError(f"Could not read {os.path.basename(p)}.") from exc
                with src:
                    st = os.fstat(src.fileno())
                    zi = _zinfo(name, st)
                    copied = 0
                    try:
                        with zf.open(zi, "w", force_zip64=True) as out:
                            while True:
                                if stop():
                                    raise ZipCancelled()
                                chunk = src.read(CHUNK)
                                if not chunk:
                                    break
                                out.write(chunk)
                                copied += len(chunk)
                                done += len(chunk)
                                if progress:
                                    progress(done, max(total, done), os.path.basename(p))
                    except OSError as exc:
                        if exc.errno == errno.ENOSPC:
                            raise ZipError("Not enough free disk space to create the ZIP archive.") from exc
                        raise ZipError(f"Could not read {os.path.basename(p)}.") from exc
                    if copied != st.st_size:
                        raise ZipError(f"{os.path.basename(p)} changed while it was being packaged.")
        # ---- verify the finished archive, then publish atomically
        with zipfile.ZipFile(part) as zf:
            got = [i.filename for i in zf.infolist()]
        if got != names:
            raise ZipError("The generated archive does not match the selection.")
        os.replace(part, final)
        ok = True
        return ZipResult(final, os.path.getsize(final), names, paths)
    except ZipCancelled:
        raise
    except ZipError:
        raise
    except OSError as exc:
        if exc.errno == errno.ENOSPC:
            raise ZipError("Not enough free disk space to create the ZIP archive.") from exc
        raise ZipError("The ZIP archive could not be created.") from exc
    finally:
        if not ok:
            shutil.rmtree(work, ignore_errors=True)
