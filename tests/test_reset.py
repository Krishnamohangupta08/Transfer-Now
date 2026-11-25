"""'Reset everything' — wipes every stored file at the next start, nothing else."""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from core import store


class TestFullReset(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tn-reset-")
        p = mock.patch.object(store, "DATA_DIR", self.tmp)
        p.start()
        self.addCleanup(p.stop)
        self.settings_path = os.path.join(self.tmp, "settings.json")
        self.history_path = os.path.join(self.tmp, "history.json")

    def _fill(self):
        s = store.Settings(self.settings_path)
        h = store.History(self.history_path)
        s.set("theme", "light")
        s.set("wizard_done", True)
        s.set("queue_cache", [{"files": ["/tmp/x"]}])
        h.add({"kind": "upload", "name": "a", "files": []})
        for name in ("storage.log", "error.log", "settings.json.corrupt-1"):
            with open(os.path.join(self.tmp, name), "w") as f:
                f.write("x")
        os.makedirs(os.path.join(self.tmp, "sounds"))
        with open(os.path.join(self.tmp, "sounds", "a.wav"), "w") as f:
            f.write("x")

    def test_nothing_happens_without_a_request(self):
        self._fill()
        self.assertFalse(store.apply_pending_reset())
        self.assertTrue(os.path.exists(self.settings_path))

    def test_request_does_not_wipe_until_next_start(self):
        self._fill()
        self.assertTrue(store.request_reset())
        self.assertTrue(os.path.exists(self.settings_path))      # still there: wipe is deferred

    def test_reset_clears_everything_and_defaults_return(self):
        self._fill()
        store.request_reset()
        self.assertTrue(store.apply_pending_reset())
        s = store.Settings(self.settings_path)
        h = store.History(self.history_path)
        self.assertEqual(s.get("theme"), store.DEFAULTS["theme"])
        self.assertFalse(s.get("wizard_done"))                    # first-run wizard runs again
        self.assertEqual(s.get("queue_cache"), [])
        self.assertEqual(h.entries(), [])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "sounds")))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "storage.log")))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, store.RESET_MARKER)))

    def test_reset_runs_once_and_keeps_unrelated_files(self):
        self._fill()
        keep = os.path.join(self.tmp, "my-notes.txt")
        with open(keep, "w") as f:
            f.write("mine")
        store.request_reset()
        self.assertTrue(store.apply_pending_reset())
        self.assertFalse(store.apply_pending_reset())             # marker consumed
        self.assertTrue(os.path.exists(keep))


if __name__ == "__main__":
    unittest.main()
