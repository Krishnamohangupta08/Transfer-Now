"""Local QR code generation and PNG export (Qt-free; stdlib + ``qrcode``).

Everything is generated on this machine — no remote QR API, and the payload
(a transfer link, which may carry a secret) is never logged or put in an
exception message.

Rendering uses *integer* module scaling: every QR module becomes an exact
``scale x scale`` block of pixels, so edges are perfectly crisp (no
interpolation).  The requested size is the exact output edge in pixels; any
remainder after integer scaling is added to the quiet zone (centred), which
keeps the image square and scannable.
"""
from __future__ import annotations

import os
import struct
import tempfile
import zlib

SIZES = (256, 512, 1024, 2048)
DEFAULT_SIZE = 1024
DEFAULT_FILENAME = "TransferNow_QR.png"
QUIET_MODULES = 4            # QR spec minimum quiet zone
MAX_PAYLOAD_BYTES = 1800     # comfortably inside QR capacity at level Q

_DARK = 17                   # #111111, same ink as the on-screen QrWidget
_LIGHT = 255


class QrExportError(Exception):
    """Base class: message is always safe to show (never contains the payload)."""


class InvalidPayloadError(QrExportError):
    pass


class ExportPathError(QrExportError):
    """Destination missing / not writable / not a file."""


def validate_payload(payload) -> str:
    if not isinstance(payload, str) or not payload.strip():
        raise InvalidPayloadError("There is nothing to encode in the QR code.")
    if len(payload.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise InvalidPayloadError("The link is too long to fit in a QR code.")
    return payload


def build_matrix(payload: str, border: int = 0) -> list[list[bool]]:
    """Module matrix (True = dark) at error-correction level Q."""
    validate_payload(payload)
    try:
        import qrcode
        qr = qrcode.QRCode(border=border, error_correction=qrcode.constants.ERROR_CORRECT_Q)
        qr.add_data(payload)
        qr.make(fit=True)
        matrix = [[bool(c) for c in row] for row in qr.get_matrix()]
    except ImportError as exc:
        raise QrExportError("The optional “qrcode” package is not installed.") from exc
    except Exception as exc:  # noqa: BLE001 — never echo the payload
        raise InvalidPayloadError("This link cannot be encoded as a QR code.") from exc
    if not matrix or any(len(r) != len(matrix) for r in matrix):
        raise QrExportError("The QR generator returned invalid data.")
    return matrix


def layout_for(modules: int, size: int) -> tuple[int, int]:
    """(scale, offset) so ``(modules + 2*quiet) * scale`` fits ``size`` exactly-ish."""
    if size not in SIZES:
        raise QrExportError("Unsupported image size.")
    scale = size // (modules + 2 * QUIET_MODULES)
    if scale < 1:
        raise QrExportError("The QR code is too dense for this image size.")
    offset = (size - modules * scale) // 2
    return scale, offset


def render_pixels(matrix: list[list[bool]], size: int, transparent: bool = False) -> bytes:
    """Raw rows: 8-bit gray (white bg) or gray+alpha (transparent bg)."""
    n = len(matrix)
    scale, off = layout_for(n, size)
    bpp = 2 if transparent else 1
    light = bytes((0, 0)) if transparent else bytes((_LIGHT,))
    dark = bytes((_DARK, 255)) if transparent else bytes((_DARK,))
    blank_row = b"\x00" + light * size
    out = bytearray()
    top = off
    bottom = size - off - n * scale
    out += blank_row * top
    for row in matrix:
        px = bytearray(light * off)
        for on in row:
            px += (dark if on else light) * scale
        px += light * (size - len(px) // bpp)
        out += (b"\x00" + bytes(px)) * scale
    out += blank_row * bottom
    if len(out) != size * (1 + size * bpp):
        raise QrExportError("Internal rendering error.")
    return bytes(out)


def _chunk(tag: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def encode_png(raw: bytes, size: int, transparent: bool) -> bytes:
    ctype = 4 if transparent else 0          # gray+alpha / gray, 8-bit
    ihdr = struct.pack(">IIBBBBB", size, size, 8, ctype, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", zlib.compress(raw, 6)) + _chunk(b"IEND", b""))


def render_png(payload: str, size: int = DEFAULT_SIZE, transparent: bool = False) -> bytes:
    """Validated payload -> PNG bytes of exactly ``size`` x ``size`` pixels."""
    matrix = build_matrix(payload)
    return encode_png(render_pixels(matrix, size, transparent), size, transparent)


def render_qr_image(payload: str, size: int = DEFAULT_SIZE, transparent: bool = False):
    """Same image as a ``QImage`` (PyQt6 imported lazily; for the clipboard)."""
    from PyQt6.QtGui import QImage
    img = QImage.fromData(render_png(payload, size, transparent), "PNG")
    if img.isNull():
        raise QrExportError("The QR image could not be created.")
    return img.convertToFormat(QImage.Format.Format_ARGB32)


def normalize_path(path: str) -> str:
    path = (path or "").strip()
    if not path:
        raise ExportPathError("No file name was chosen.")
    if not path.lower().endswith(".png"):
        path += ".png"
    return os.path.abspath(path)


def save_png(png: bytes, path: str, overwrite: bool = True) -> str:
    """Atomically write ``png`` to ``path`` (temp file in the same folder, then
    ``os.replace``).  A failure leaves neither a partial file nor a temp file,
    and an existing destination stays untouched.  Returns the final path."""
    if not png.startswith(b"\x89PNG\r\n\x1a\n"):
        raise QrExportError("Refusing to save invalid image data.")
    dest = normalize_path(path)
    folder = os.path.dirname(dest)
    if not os.path.isdir(folder):
        raise ExportPathError("The destination folder does not exist.")
    if os.path.isdir(dest):
        raise ExportPathError("The chosen name is a folder.")
    if os.path.exists(dest) and not overwrite:
        raise ExportPathError("A file with this name already exists.")
    tmp = ""
    try:
        fd, tmp = tempfile.mkstemp(prefix=".tn_qr_", suffix=".tmp", dir=folder)
        with os.fdopen(fd, "wb") as fh:
            fh.write(png)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, dest)
        tmp = ""
    except PermissionError as exc:
        raise ExportPathError("You don't have permission to save in that folder.") from exc
    except OSError as exc:
        raise ExportPathError("The image could not be saved "
                              f"({exc.strerror or 'disk error'}).") from exc
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    return dest


def export_png(payload: str, path: str, size: int = DEFAULT_SIZE,
               transparent: bool = False, overwrite: bool = True) -> str:
    return save_png(render_png(payload, size, transparent), path, overwrite)
