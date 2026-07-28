"""Disk-vagt (SPEC §2 RECORDER + M1: 'disk-vagt testet'): simuleret lav
diskplads → skrivestop + sys-event; genoptag med hysterese."""
import collections
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import src.recorder as recorder_mod
from src.recorder import EventWriter

Usage = collections.namedtuple("usage", "total used free")


class TestDiskGuard(unittest.TestCase):
    def test_halt_and_resume(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            w = EventWriter(tmp / "ev.sqlite", tmp / "parquet",
                            disk_min_free_gb=5.0, disk_resume_free_gb=6.0)
            con = w._connect()

            with mock.patch.object(recorder_mod.shutil, "disk_usage",
                                   return_value=Usage(100e9, 98e9, 2e9)):
                w._disk_guard(con)
            self.assertTrue(w.disk_halted)
            w.put("f1", {"x": 1})           # droppes under halt
            self.assertEqual(w.dropped_disk, 1)
            self.assertEqual(w.q.qsize(), 0)
            evs = con.execute("SELECT payload FROM events").fetchall()
            self.assertIn("disk_halt", evs[0][0])

            # 5,5 GB fri: under resume-hysteresen → stadig halt
            with mock.patch.object(recorder_mod.shutil, "disk_usage",
                                   return_value=Usage(100e9, 94.5e9, 5.5e9)):
                w._disk_guard(con)
            self.assertTrue(w.disk_halted)

            with mock.patch.object(recorder_mod.shutil, "disk_usage",
                                   return_value=Usage(100e9, 90e9, 10e9)):
                w._disk_guard(con)
            self.assertFalse(w.disk_halted)
            w.put("f1", {"x": 2})
            self.assertEqual(w.q.qsize(), 1)
            evs = [r[0] for r in con.execute("SELECT payload FROM events").fetchall()]
            self.assertTrue(any("disk_resume" in e for e in evs))
            con.close()


if __name__ == "__main__":
    unittest.main()
