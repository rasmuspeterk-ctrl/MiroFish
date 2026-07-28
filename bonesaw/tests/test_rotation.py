"""SQLite→parquet-rotation: hele timer flyttes, seneste time bliver i WAL."""
import json
import tempfile
import unittest
from pathlib import Path

from src.recorder import EventWriter


class TestRotation(unittest.TestCase):
    def test_rotate_moves_whole_hours(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            w = EventWriter(tmp / "ev.sqlite", tmp / "parquet")
            con = w._connect()
            hour0 = 1_785_189_600            # en hel time i fortiden (aligned)
            now = hour0 + 2 * 3600 + 120     # to timer senere + lidt
            rows = []
            for i in range(50):              # time 0 (skal roteres)
                rows.append((i, hour0 + i * 60 % 3600, "f1", json.dumps({"i": i})))
            for i in range(20):              # indevaerende time (skal blive)
                rows.append((100 + i, now - 30 + i, "f3", json.dumps({"i": i})))
            con.executemany(
                "INSERT INTO events (ts_mono, ts_wall, source, payload) VALUES (?,?,?,?)", rows)
            con.commit()

            stats = w.rotate(con, now=now)

            files = list((tmp / "parquet").rglob("*.parquet"))
            self.assertEqual(len(files), 1)
            import pyarrow.parquet as pq
            t = pq.read_table(files[0])
            self.assertEqual(t.num_rows, 50)
            left = con.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            self.assertEqual(left, 20)
            self.assertEqual(sum(n for _, _, n, _ in stats), 50)
            hs = con.execute("SELECT source, n, uploaded FROM hourly_stats").fetchall()
            self.assertEqual(hs, [("f1", 50, 0)])
            con.close()

    def test_rotate_idempotent_when_empty(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            w = EventWriter(tmp / "ev.sqlite", tmp / "parquet")
            con = w._connect()
            self.assertEqual(w.rotate(con, now=1_785_189_600), [])
            con.close()


if __name__ == "__main__":
    unittest.main()
