"""Gate-mekanik (SPEC §7): LIVE nægter start uden gyldige gates. exit(1)."""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from src.modes import check_gates


def setup_gates(root: Path, *, a=True, b=True, arm=True, arm_age_days=0.0,
                a_pass=True, b_pass=True):
    g = root / "gates"
    g.mkdir(exist_ok=True)
    if a:
        (g / "gate_a.json").write_text(json.dumps({"pass": a_pass}))
    if b:
        (g / "gate_b.json").write_text(json.dumps({"pass": b_pass}))
    if arm:
        p = g / "ARM"
        p.write_text("armed af menneske\n")
        if arm_age_days:
            t = time.time() - arm_age_days * 86400
            os.utime(p, (t, t))


class TestGates(unittest.TestCase):
    def _expect_exit(self, root):
        with self.assertRaises(SystemExit) as cm:
            check_gates(root)
        self.assertEqual(cm.exception.code, 1)

    def test_missing_everything(self):
        with tempfile.TemporaryDirectory() as td:
            self._expect_exit(Path(td))

    def test_missing_gate_b(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            setup_gates(root, b=False)
            self._expect_exit(root)

    def test_pass_false(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            setup_gates(root, b_pass=False)
            self._expect_exit(root)

    def test_arm_too_old(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            setup_gates(root, arm_age_days=8)
            self._expect_exit(root)

    def test_arm_missing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            setup_gates(root, arm=False)
            self._expect_exit(root)

    def test_all_valid(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            setup_gates(root, arm_age_days=6.5)
            check_gates(root)  # maa ikke raise


if __name__ == "__main__":
    unittest.main()
