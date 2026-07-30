import unittest

from src import clock


class TestClock(unittest.TestCase):
    def test_window_ts(self):
        self.assertEqual(clock.window_ts(300, 1785191400), 1785191400)
        self.assertEqual(clock.window_ts(300, 1785191400 + 123), 1785191400)
        self.assertEqual(clock.window_ts(300, 1785191400 + 299.9), 1785191400)
        self.assertEqual(clock.window_ts(900, 1785191400 + 456), 1785191400)

    def test_slug(self):
        self.assertEqual(clock.slug("btc", 300, 1785191400), "btc-updown-5m-1785191400")
        self.assertEqual(clock.slug("doge", 900, 1785191400), "doge-updown-15m-1785191400")

    def test_next_boundary(self):
        self.assertEqual(clock.next_boundary(300, 1785191400), 1785191700)
        self.assertEqual(clock.next_boundary(300, 1785191401.5), 1785191700)

    def test_window_dataclass(self):
        w = clock.Window("eth", 300, 1785191400)
        self.assertEqual(w.end_ts, 1785191700)
        self.assertEqual(w.slug, "eth-updown-5m-1785191400")

    def test_periods_flag(self):
        self.assertEqual(clock.periods(False), [300])
        self.assertEqual(clock.periods(True), [300, 900])

    def test_symbol_maps(self):
        self.assertEqual(clock.CHAINLINK_SYMBOL["btc"], "btc/usd")
        self.assertEqual(clock.BINANCE_SYMBOL["xrp"], "xrpusdt")
        self.assertEqual(clock.ASSET_BY_CHAINLINK["bnb/usd"], "bnb")


if __name__ == "__main__":
    unittest.main()
