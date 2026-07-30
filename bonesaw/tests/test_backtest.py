"""Backtest-bootstrap (SPEC M2): unittest for de RENE funktioner i
research/backtest.py — ts-normalisering (ms/µs/header), label-regel
(tie -> Up), kvantil-bin-kanter, celle-P (Laplace) og Brier.

research/ er ikke en pakke (intet __init__.py); simplest robuste import er
at lægge research/ på sys.path og importere backtest.py som topmodul —
virker uanset cwd, også via `python3 -m unittest discover -s tests`.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "research"))

import numpy as np  # noqa: E402

import backtest  # noqa: E402


class TestTsNormalisering(unittest.TestCase):
    def test_has_header_row_true_for_text_header(self):
        self.assertTrue(backtest.has_header_row("open_time,open,high,low,close,volume"))

    def test_has_header_row_false_for_numeric_first_field(self):
        self.assertFalse(backtest.has_header_row("1772323200000000,66973.26,66973.26"))

    def test_has_header_row_false_for_negative_or_decimal_first_field(self):
        # forsvarer mod for naiv "kan ikke parses som int" -- float() skal bruges
        self.assertFalse(backtest.has_header_row("1772323200000000.0,1,2"))

    def test_normalize_open_time_milliseconds(self):
        ms = np.array([1772323200000, 1772323201000])
        got = backtest.normalize_open_time(ms)
        np.testing.assert_array_equal(got, np.array([1772323200, 1772323201]))

    def test_normalize_open_time_microseconds(self):
        us = np.array([1772323200000000, 1772323201000000])
        got = backtest.normalize_open_time(us)
        np.testing.assert_array_equal(got, np.array([1772323200, 1772323201]))

    def test_normalize_open_time_mixed_ms_and_us_elementwise(self):
        mixed = np.array([1772323200000, 1772323200000000])  # ms, saa us
        got = backtest.normalize_open_time(mixed)
        np.testing.assert_array_equal(got, np.array([1772323200, 1772323200]))

    def test_normalize_open_time_threshold_boundary(self):
        # praecis paa 1e15 skal IKKE regnes som mikrosekunder (">" ikke ">=")
        at_threshold = np.array([1_000_000_000_000_000])
        got = backtest.normalize_open_time(at_threshold)
        self.assertEqual(got[0], 1_000_000_000_000)  # tolket som ms

    def test_parse_kline_csv_without_header_ms(self):
        text = "1772323200000,66973.26,66980,66960,66975.5,1,1772323200999,0,0,0,0,0\n"
        df = backtest.parse_kline_csv(text)
        self.assertEqual(list(df.columns), ["ts", "close"])
        self.assertEqual(df["ts"].iloc[0], 1772323200)
        self.assertAlmostEqual(df["close"].iloc[0], 66975.5)

    def test_parse_kline_csv_with_header_us(self):
        text = ("open_time,open,high,low,close,volume,close_time,quote_volume,count,"
                "taker_buy_base,taker_buy_quote,ignore\n"
                "1772323200000000,66973.26,66980,66960,66975.5,1,1772323200999999,0,0,0,0,0\n")
        df = backtest.parse_kline_csv(text)
        self.assertEqual(df["ts"].iloc[0], 1772323200)
        self.assertAlmostEqual(df["close"].iloc[0], 66975.5)


class TestLabelRegel(unittest.TestCase):
    def test_tie_goes_up(self):
        # VERIFIED.md §2: "greater than or equal to" -> tie => Up
        self.assertTrue(bool(backtest.label_up(100.0, 100.0)))

    def test_close_above_open_is_up(self):
        self.assertTrue(bool(backtest.label_up(100.0, 100.01)))

    def test_close_below_open_is_down(self):
        self.assertFalse(bool(backtest.label_up(100.0, 99.99)))

    def test_vectorised_over_arrays(self):
        s_open = np.array([100.0, 100.0, 100.0])
        s_close = np.array([100.0, 100.1, 99.9])
        got = backtest.label_up(s_open, s_close)
        np.testing.assert_array_equal(got, np.array([True, True, False]))


class TestTauBand(unittest.TestCase):
    def test_band_boundaries(self):
        self.assertEqual(backtest.tau_band(240), "240-120")
        self.assertEqual(backtest.tau_band(150), "240-120")
        self.assertEqual(backtest.tau_band(120), "120-30")  # 120 hoerer til [120-30), ikke [240-120)
        self.assertEqual(backtest.tau_band(45), "120-30")
        self.assertEqual(backtest.tau_band(30), "30-0")
        self.assertEqual(backtest.tau_band(0), "30-0")

    def test_all_contract_taus_map_to_a_band(self):
        for tau in [240, 210, 180, 150, 120, 90, 60, 45, 30, 20, 10]:
            self.assertIn(backtest.tau_band(tau), backtest.BAND_NAMES)


class TestKvantilBinKanter(unittest.TestCase):
    def test_monotone_strictly_increasing(self):
        rng = np.random.default_rng(42)
        x = rng.normal(size=2000)
        edges = backtest.quantile_bin_edges(x, 15)
        diffs = np.diff(edges)
        self.assertTrue(np.all(diffs > 0), f"kanter ikke strengt stigende: {edges}")
        self.assertEqual(edges[0], -np.inf)
        self.assertEqual(edges[-1], np.inf)

    def test_duplicated_quantiles_are_deduped(self):
        # mange ens vaerdier -> flere kvantiler kolliderer; edges skal stadig
        # vaere strengt monotone (og dermed <= n_bins+1 kanter, ikke praecis)
        x = np.concatenate([np.zeros(500), np.linspace(1, 2, 20)])
        edges = backtest.quantile_bin_edges(x, 15)
        self.assertTrue(np.all(np.diff(edges) > 0))
        self.assertLessEqual(edges.size, 16)
        self.assertEqual(edges[0], -np.inf)
        self.assertEqual(edges[-1], np.inf)

    def test_constant_input_collapses_to_single_bin(self):
        x = np.full(100, 7.0)
        edges = backtest.quantile_bin_edges(x, 15)
        np.testing.assert_array_equal(edges, np.array([-np.inf, np.inf]))

    def test_empty_input(self):
        edges = backtest.quantile_bin_edges(np.array([]), 15)
        np.testing.assert_array_equal(edges, np.array([-np.inf, np.inf]))

    def test_bin_assignment_covers_extremes_via_digitize(self):
        x = np.array([-100.0, -1.0, 0.0, 1.0, 100.0, 5.0, 5.0, 5.0, 5.0, 5.0])
        edges = backtest.quantile_bin_edges(x, 4)
        bins = np.digitize(x, edges[1:-1])
        self.assertTrue(np.all(bins >= 0))
        self.assertTrue(np.all(bins <= edges.size - 2))


class TestCellePLaplace(unittest.TestCase):
    def test_formula(self):
        self.assertAlmostEqual(backtest.laplace_cell_prob(0, 0), 0.5)
        self.assertAlmostEqual(backtest.laplace_cell_prob(10, 20), 12 / 24)
        self.assertAlmostEqual(backtest.laplace_cell_prob(0, 100), 2 / 104)
        self.assertAlmostEqual(backtest.laplace_cell_prob(100, 100), 102 / 104)

    def test_bounded_strictly_between_0_and_1(self):
        for n_up, n in [(0, 0), (0, 1000), (1000, 1000), (5, 10)]:
            p = backtest.laplace_cell_prob(n_up, n)
            self.assertGreater(p, 0.0)
            self.assertLess(p, 1.0)

    def test_vectorised(self):
        n_up = np.array([0, 5, 10])
        n = np.array([0, 10, 20])
        got = backtest.laplace_cell_prob(n_up, n)
        expected = np.array([0.5, 7 / 14, 12 / 24])
        np.testing.assert_allclose(got, expected)


class TestBrier(unittest.TestCase):
    def test_perfect_predictions(self):
        y = np.array([1, 0, 1, 0])
        p = np.array([1.0, 0.0, 1.0, 0.0])
        self.assertAlmostEqual(backtest.brier_score(y, p), 0.0)

    def test_worst_predictions(self):
        y = np.array([1, 0, 1, 0])
        p = np.array([0.0, 1.0, 0.0, 1.0])
        self.assertAlmostEqual(backtest.brier_score(y, p), 1.0)

    def test_constant_half_on_balanced_labels(self):
        y = np.array([1, 0, 1, 0])
        p = np.full(4, 0.5)
        self.assertAlmostEqual(backtest.brier_score(y, p), 0.25)

    def test_matches_manual_mean_squared_error(self):
        y = np.array([1, 0, 1, 1, 0])
        p = np.array([0.9, 0.2, 0.6, 0.4, 0.5])
        expected = float(np.mean((p - y) ** 2))
        self.assertAlmostEqual(backtest.brier_score(y, p), expected)


if __name__ == "__main__":
    unittest.main()
