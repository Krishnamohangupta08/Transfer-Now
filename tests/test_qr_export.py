"""QR export tests: local generation, exact-size PNGs, atomic saves, clipboard,
cancelled dialogs and scan-readability (decoded back with OpenCV when present).

    python -m unittest tests.test_qr_export -v
"""
from __future__ import annotations

import os
import shutil
import struct
import sys
import tempfile
import unittest
import zlib
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("TN_DATA_DIR", tempfile.mkdtemp(prefix="tn-qr-data-"))

from core import qrexport  # noqa: E402

try:
    import cv2
    import numpy as np
    HAVE_CV = True
except Exception:  # noqa: BLE001
    HAVE_CV = False

try:
    import qrcode  # noqa: F401
    HAVE_QR = True
except Exception:  # noqa: BLE001
    HAVE_QR = False

LINK = "https://www.transfernow.net/dl/2026112400abcdef1234?k=secret"
UNI = "https://example.com/dl/ü-日本語-🙂"


def png_info(png: bytes):
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    w, h, depth, ctype = struct.unpack(">IIBB", png[16:26])
    return w, h, depth, ctype


def decode(png: bytes) -> str:
    """Decode a PNG back to its payload (transparent pixels composited on white)."""
    a = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_UNCHANGED)
    if a.ndim == 3 and a.shape[2] == 4:
        alpha = a[..., 3:4] / 255.0
        g = (a[..., 0:1] * alpha + 255 * (1 - alpha)).astype(np.uint8)[..., 0]
    else:
        g = a
    # The legacy cv2.QRCodeDetector is known to miss large, perfectly clean
    # codes; the Aruco-based detector is robust.  Fall back to the legacy one.
    det = getattr(cv2, "QRCodeDetectorAruco", None)
    if det is not None:
        text, _, _ = det().detectAndDecode(g)
        if text:
            return text
    text, _, _ = cv2.QRCodeDetector().detectAndDecode(g)
    return text


@unittest.skipUnless(HAVE_QR, "qrcode package not installed")
class TestGeneration(unittest.TestCase):
    def test_matrix_square_and_has_finder_pattern(self):
        m = qrexport.build_matrix(LINK)
        self.assertEqual(len(m), len(m[0]))
        self.assertTrue(all(m[0][i] for i in range(7)))        # top-left finder bar

    def test_invalid_payloads(self):
        for bad in ("", "   ", None, 123, "x" * 5000):
            with self.assertRaises(qrexport.InvalidPayloadError):
                qrexport.build_matrix(bad)

    def test_error_never_contains_payload(self):
        with self.assertRaises(qrexport.QrExportError) as cm:
            qrexport.build_matrix("x" * 5000)
        self.assertNotIn("xxxx", str(cm.exception))

    def test_each_size_exact_dimensions_both_backgrounds(self):
        for size in qrexport.SIZES:
            for transparent in (False, True):
                png = qrexport.render_png(LINK, size, transparent)
                w, h, depth, ctype = png_info(png)
                self.assertEqual((w, h, depth), (size, size, 8))
                self.assertEqual(ctype, 4 if transparent else 0)

    def test_unsupported_size(self):
        with self.assertRaises(qrexport.QrExportError):
            qrexport.render_png(LINK, 300)

    def test_quiet_zone_and_integer_scaling(self):
        for size in qrexport.SIZES:
            m = qrexport.build_matrix(LINK)
            scale, off = qrexport.layout_for(len(m), size)
            self.assertGreaterEqual(off, qrexport.QUIET_MODULES * scale)
            raw = qrexport.render_pixels(m, size, False)
            rows = [raw[i * (1 + size):(i + 1) * (1 + size)][1:] for i in range(size)]
            # only pure ink / pure paper — no interpolated grey
            self.assertEqual(set(b"".join(rows)), {17, 255})
            self.assertTrue(all(set(r) == {255} for r in rows[:off]))     # top quiet zone

    def test_transparent_background_pixels(self):
        png = qrexport.render_png(LINK, 256, True)
        raw = zlib.decompress(png[png.index(b"IDAT") + 4:png.index(b"IEND") - 8])
        px = raw[1:1 + 256 * 2]                                  # first row
        self.assertEqual(set(px[1::2]), {0})                      # fully transparent
        self.assertIn(255, raw[1 + 256 * 2 * 100 + 1::2][:512])   # opaque ink exists

    def test_unicode_payload_roundtrip_generation(self):
        self.assertTrue(qrexport.render_png(UNI, 512).startswith(b"\x89PNG"))


@unittest.skipUnless(HAVE_QR and HAVE_CV, "qrcode and opencv (decoder) are required")
class TestReadability(unittest.TestCase):
    def test_every_size_and_background_decodes(self):
        for payload in (LINK, UNI):
            for size in qrexport.SIZES:
                for transparent in (False, True):
                    png = qrexport.render_png(payload, size, transparent)
                    self.assertEqual(decode(png), payload, (size, transparent))

    def test_saved_file_decodes(self):
        with tempfile.TemporaryDirectory() as d:
            out = qrexport.export_png(LINK, os.path.join(d, "q"), 512, True)
            self.assertTrue(out.endswith("q.png"))
            with open(out, "rb") as fh:
                self.assertEqual(decode(fh.read()), LINK)


@unittest.skipUnless(HAVE_QR, "qrcode package not installed")
class TestSave(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="tn-qr-")
        self.addCleanup(shutil.rmtree, self.d, True)

    def listing(self):
        return sorted(os.listdir(self.d))

    def test_save_adds_extension_and_leaves_no_temp(self):
        out = qrexport.export_png(LINK, os.path.join(self.d, "code"), 256)
        self.assertEqual(self.listing(), ["code.png"])
        self.assertEqual(png_info(open(out, "rb").read())[:2], (256, 256))

    def test_existing_file_overwritten_atomically(self):
        p = os.path.join(self.d, "a.png")
        open(p, "wb").write(b"old")
        qrexport.export_png(LINK, p, 256)
        self.assertTrue(open(p, "rb").read().startswith(b"\x89PNG"))
        self.assertEqual(self.listing(), ["a.png"])

    def test_existing_file_refused_when_not_overwriting(self):
        p = os.path.join(self.d, "a.png")
        open(p, "wb").write(b"old")
        with self.assertRaises(qrexport.ExportPathError):
            qrexport.export_png(LINK, p, 256, overwrite=False)
        self.assertEqual(open(p, "rb").read(), b"old")

    def test_missing_folder_and_directory_target(self):
        with self.assertRaises(qrexport.ExportPathError):
            qrexport.export_png(LINK, os.path.join(self.d, "nope", "a.png"), 256)
        os.mkdir(os.path.join(self.d, "dir.png"))
        with self.assertRaises(qrexport.ExportPathError):
            qrexport.export_png(LINK, os.path.join(self.d, "dir.png"), 256)

    def test_permission_failure_leaves_nothing_behind(self):
        p = os.path.join(self.d, "a.png")
        open(p, "wb").write(b"keep")
        with mock.patch("core.qrexport.os.replace", side_effect=PermissionError):
            with self.assertRaises(qrexport.ExportPathError) as cm:
                qrexport.export_png(LINK, p, 256)
        self.assertIn("permission", str(cm.exception).lower())
        self.assertEqual(self.listing(), ["a.png"])           # no partial / temp file
        self.assertEqual(open(p, "rb").read(), b"keep")        # original intact

    def test_temp_creation_denied(self):
        with mock.patch("core.qrexport.tempfile.mkstemp", side_effect=PermissionError):
            with self.assertRaises(qrexport.ExportPathError):
                qrexport.export_png(LINK, os.path.join(self.d, "a.png"), 256)
        self.assertEqual(self.listing(), [])

    def test_disk_full_mid_write_cleans_temp(self):
        real_fsync = os.fsync

        def boom(fd):
            real_fsync(fd)
            raise OSError(28, "No space left on device")
        with mock.patch("core.qrexport.os.fsync", side_effect=boom):
            with self.assertRaises(qrexport.ExportPathError):
                qrexport.export_png(LINK, os.path.join(self.d, "a.png"), 256)
        self.assertEqual(self.listing(), [])

    def test_real_readonly_dir(self):
        if os.geteuid() == 0:
            self.skipTest("root bypasses directory permissions (patched variant covers it)")
        os.chmod(self.d, 0o500)
        try:
            with self.assertRaises(qrexport.ExportPathError):
                qrexport.export_png(LINK, os.path.join(self.d, "a.png"), 256)
        finally:
            os.chmod(self.d, 0o700)

    def test_invalid_png_refused(self):
        with self.assertRaises(qrexport.QrExportError):
            qrexport.save_png(b"not a png", os.path.join(self.d, "a.png"))
        self.assertEqual(self.listing(), [])


@unittest.skipUnless(HAVE_QR, "qrcode package not installed")
class TestDialog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_ui import app, wait_until
        from PyQt6.QtWidgets import QWidget
        cls.app, cls.wait = app, staticmethod(wait_until)
        cls.parent = QWidget()
        cls.parent.show()

    @classmethod
    def tearDownClass(cls):
        cls.parent.close()

    def setUp(self):
        from ui.qrdialog import QrExportDialog
        self.msgs: list[tuple[str, str]] = []
        self.dlg = QrExportDialog(self.parent, LINK, lambda m, k="info": self.msgs.append((m, k)))
        self.d = tempfile.mkdtemp(prefix="tn-qr-dlg-")
        self.addCleanup(shutil.rmtree, self.d, True)
        self.addCleanup(self.dlg.deleteLater)

    def settle(self):
        self.assertTrue(self.wait(lambda: self.dlg._worker is None, 20))

    def test_defaults_and_preview(self):
        self.assertEqual(self.dlg.size_px(), qrexport.DEFAULT_SIZE)
        self.assertFalse(self.dlg.transparent())
        self.assertTrue(self.dlg.save_btn.isEnabled())
        self.assertTrue(self.dlg.preview._m)
        self.assertTrue(self.dlg.options.isHidden())          # progressive disclosure

    def test_options_toggle(self):
        self.dlg.opt_toggle.setChecked(True)
        self.assertFalse(self.dlg.options.isHidden())

    def test_default_filename_offered_to_dialog(self):
        with mock.patch("ui.qrdialog.QFileDialog.getSaveFileName", return_value=("", "")) as g:
            self.dlg.save()
        self.assertTrue(g.call_args[0][2].endswith(qrexport.DEFAULT_FILENAME))

    def test_cancelled_dialog_does_nothing(self):
        with mock.patch("ui.qrdialog.QFileDialog.getSaveFileName", return_value=("", "")):
            self.dlg.save()
        self.assertIsNone(self.dlg._worker)
        self.assertEqual(self.msgs, [])

    def test_save_each_size_and_background(self):
        for size in qrexport.SIZES:
            for bg in ("white", "transparent"):
                self.dlg.size_seg.set_value(str(size))
                self.dlg.bg_seg.set_value(bg)
                path = os.path.join(self.d, f"q{size}{bg}.png")
                with mock.patch("ui.qrdialog.QFileDialog.getSaveFileName",
                                return_value=(path, "")):
                    self.dlg.save()
                self.settle()
                png = open(path, "rb").read()
                self.assertEqual(png_info(png)[:2], (size, size))
                if HAVE_CV:
                    self.assertEqual(decode(png), LINK)
        self.assertTrue(all(k == "success" for _, k in self.msgs))
        self.assertNotIn(LINK, " ".join(m for m, _ in self.msgs))

    def test_save_failure_reports_error(self):
        path = os.path.join(self.d, "missing", "q.png")
        with mock.patch("ui.qrdialog.QFileDialog.getSaveFileName", return_value=(path, "")):
            self.dlg.save()
        self.settle()
        self.assertEqual(self.msgs[-1][1], "error")
        self.assertTrue(self.dlg.save_btn.isEnabled())

    def test_copy_puts_real_image_on_clipboard(self):
        from PyQt6.QtGui import QGuiApplication
        QGuiApplication.clipboard().clear()
        self.dlg.size_seg.set_value("512")
        self.dlg.copy()
        self.settle()
        img = QGuiApplication.clipboard().image()
        self.assertFalse(img.isNull())
        self.assertEqual((img.width(), img.height()), (512, 512))
        self.assertEqual(self.msgs[-1][1], "success")

    def test_copy_failure_is_handled(self):
        with mock.patch("ui.qrdialog.QGuiApplication.clipboard", return_value=None):
            self.dlg.copy()
            self.settle()
        self.assertEqual(self.msgs[-1][1], "error")

    def test_regenerate_and_bad_payload(self):
        self.dlg.regenerate()
        self.assertTrue(self.dlg.save_btn.isEnabled())
        self.dlg._payload = ""
        self.dlg.regenerate()
        self.assertFalse(self.dlg.save_btn.isEnabled())
        self.assertFalse(self.dlg.status.isHidden())

    def test_close_joins_worker(self):
        self.dlg.copy()
        self.dlg.reject()
        self.assertTrue(self.wait(lambda: self.dlg._worker is None, 20))


if __name__ == "__main__":
    unittest.main()
