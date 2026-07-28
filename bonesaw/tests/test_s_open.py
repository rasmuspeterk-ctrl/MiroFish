"""S_open-fangst og gap-regel (SPEC §2 F1, M1: 'simulér manglende tick')."""
import unittest

from src.feeds.f1_rtds import SOpenTracker

B = 1_785_191_400  # boundary (unix s)


class FakeWriter:
    def __init__(self):
        self.events = []

    def put(self, source, payload, **kw):
        self.events.append((source, payload))

    def by(self, source):
        return [p for s, p in self.events if s == source]


def make(**kw):
    w = FakeWriter()
    t = SOpenTracker(w, ["btc"], [300], gap_s=2.0, grace_ms=500, **kw)
    t.register_boundary(300, B)
    return w, t


class TestSOpen(unittest.TestCase):
    def test_capture_first_tick_at_boundary(self):
        w, t = make()
        t.on_tick("btc", B * 1000, 118_000.0)
        ev = w.by("s_open")
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["s_open"], 118_000.0)
        self.assertEqual(ev[0]["window_ts"], B)
        self.assertEqual(ev[0]["lag_ms"], 0)
        self.assertEqual(t.state[("btc", 300, B)], "open")

    def test_pre_boundary_ticks_ignored_first_in_window_wins(self):
        w, t = make()
        t.on_tick("btc", B * 1000 - 800, 117_990.0)   # foer boundary: ikke kvalificerende
        t.on_tick("btc", B * 1000 + 1500, 118_005.0)  # foerste ts >= boundary, inde i gap
        ev = w.by("s_open")
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["s_open"], 118_005.0)
        self.assertEqual(ev[0]["lag_ms"], 1500)
        self.assertFalse(w.by("unpriceable"))

    def test_gap_rule_first_tick_after_2s(self):
        w, t = make()
        t.on_tick("btc", B * 1000 + 3000, 118_010.0)  # simuleret manglende tick i [B, B+2s]
        self.assertFalse(w.by("s_open"))
        un = w.by("unpriceable")
        self.assertEqual(len(un), 1)
        self.assertEqual(un[0]["reason"], "first_tick_after_gap")
        self.assertEqual(t.state[("btc", 300, B)], "unpriceable")

    def test_gap_rule_silent_feed_watchdog(self):
        w, t = make()
        t.watchdog(now_wall=B + 2.4)   # foer gap+grace: ingen afgoerelse
        self.assertFalse(w.by("unpriceable"))
        t.watchdog(now_wall=B + 2.6)   # efter gap+grace
        un = w.by("unpriceable")
        self.assertEqual(len(un), 1)
        self.assertEqual(un[0]["reason"], "no_tick_by_deadline")

    def test_late_tick_after_unpriceable_stays_unpriceable(self):
        w, t = make()
        t.watchdog(now_wall=B + 2.6)
        t.on_tick("btc", B * 1000 + 1000, 118_001.0)  # sen levering af in-window-tick
        t.note_late("btc", B * 1000 + 1000, 118_001.0)
        self.assertEqual(t.state[("btc", 300, B)], "unpriceable")
        self.assertTrue(w.by("s_open_late"))
        self.assertFalse(w.by("s_open"))

    def test_cleanup_after_window_end(self):
        w, t = make()
        t.on_tick("btc", B * 1000, 118_000.0)
        t.watchdog(now_wall=B + 300 + 61)
        self.assertNotIn(("btc", 300, B), t.state)

    def test_multiple_assets_independent(self):
        w = FakeWriter()
        t = SOpenTracker(w, ["btc", "eth"], [300], gap_s=2.0, grace_ms=500)
        t.register_boundary(300, B)
        t.on_tick("btc", B * 1000 + 100, 118_000.0)
        t.watchdog(now_wall=B + 2.6)
        self.assertEqual(t.state[("btc", 300, B)], "open")
        self.assertEqual(t.state[("eth", 300, B)], "unpriceable")


if __name__ == "__main__":
    unittest.main()
