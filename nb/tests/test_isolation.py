"""Property and boundary tests for the three isolation metrics.

These are the tests referenced by Section 3.5 of the paper. They check analytic
boundaries that must hold by construction, so a failure here means the reported
metric values are wrong, not merely different.

Stdlib unittest on purpose: a reproducibility artifact should run with a bare
python3 and no pip install.

    python3 -m unittest discover -s nb/tests -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from isolation import (  # noqa: E402
    blast_radius,
    fairness_deviation,
    interference_ratio,
    jain_index,
)


class TestJainAndFD(unittest.TestCase):
    def test_perfect_equality_is_one(self):
        self.assertAlmostEqual(jain_index([5.0] * 100), 1.0, places=12)

    def test_single_winner_is_one_over_n(self):
        self.assertAlmostEqual(jain_index([0.0] * 99 + [1.0]), 0.01, places=12)

    def test_all_zero_is_perfectly_equal(self):
        # Degenerate but well defined: nobody got anything, nobody was favoured.
        self.assertEqual(jain_index([0.0] * 10), 1.0)

    def test_negative_allocation_rejected(self):
        with self.assertRaises(ValueError):
            jain_index([1.0, -1.0])

    def test_fd_is_exactly_one_minus_jain(self):
        achieved = [9.0, 4.0, 1.0, 7.0]
        requested = [10.0] * 4
        ratios = [a / r for a, r in zip(achieved, requested)]
        self.assertAlmostEqual(
            fairness_deviation(achieved, requested),
            1.0 - jain_index(ratios),
            places=12,
        )

    def test_fd_perfect_equality_is_zero(self):
        self.assertAlmostEqual(fairness_deviation([3.0] * 8, [3.0] * 8), 0.0, places=12)

    def test_fd_single_winner_matches_theory(self):
        # 99 starved + 1 satisfied => FD = 1 - 1/100 = 0.99
        self.assertAlmostEqual(
            fairness_deviation([0.0] * 99 + [10.0], [10.0] * 100), 0.99, places=12
        )

    def test_fd_half_starved_matches_theory(self):
        # 50 at ratio 1, 50 at ratio 0 => Jain = 50^2/(100*50) = 0.5 => FD = 0.5
        self.assertAlmostEqual(
            fairness_deviation([10.0] * 50 + [0.0] * 50, [10.0] * 100), 0.5, places=12
        )

    def test_fd_ignores_tenants_that_requested_nothing(self):
        self.assertAlmostEqual(
            fairness_deviation([10.0, 10.0, 0.0], [10.0, 10.0, 0.0]), 0.0, places=12
        )

    def test_fd_clamps_overachievement(self):
        # A tenant served above its request must not register as "extra fair".
        self.assertAlmostEqual(
            fairness_deviation([20.0, 10.0], [10.0, 10.0]), 0.0, places=12
        )

    def test_fd_rejects_mismatched_lengths(self):
        with self.assertRaises(ValueError):
            fairness_deviation([1.0, 2.0], [1.0])


class TestInterferenceRatio(unittest.TestCase):
    def test_no_interference_is_zero(self):
        self.assertAlmostEqual(
            interference_ratio([2.0, 3.0, 4.0], [2.0, 3.0, 4.0]), 0.0, places=12
        )

    def test_doubling_is_one(self):
        self.assertAlmostEqual(interference_ratio([4.0, 6.0], [2.0, 3.0]), 1.0, places=12)

    def test_uses_median_not_mean(self):
        # A single catastrophic outlier must not drag the statistic.
        self.assertAlmostEqual(
            interference_ratio([1.0, 1.0, 1.0, 1.0, 1000.0], [1.0] * 5), 0.0, places=12
        )

    def test_can_be_negative(self):
        # Victims sometimes get faster when an aggressor shifts placement.
        # Silently clamping to 0 would hide a real effect.
        self.assertAlmostEqual(interference_ratio([1.0], [2.0]), -0.5, places=12)

    def test_rejects_zero_baseline(self):
        with self.assertRaises(ValueError):
            interference_ratio([1.0], [0.0])

    def test_rejects_mismatched_lengths(self):
        with self.assertRaises(ValueError):
            interference_ratio([1.0, 2.0], [1.0])


def _mk(n, degraded, factor=2.0):
    out = {}
    for i in range(n):
        out["t%d" % i] = {
            "p99_baseline": 1.0,
            "p99_aggressed": factor if i < degraded else 1.0,
        }
    return out


class TestBlastRadius(unittest.TestCase):
    def test_none_degraded_is_zero(self):
        self.assertAlmostEqual(blast_radius(_mk(10, 0), aggressors=[]), 0.0, places=12)

    def test_all_degraded_is_one(self):
        self.assertAlmostEqual(blast_radius(_mk(10, 10), aggressors=[]), 1.0, places=12)

    def test_excludes_aggressors_from_denominator(self):
        # 2 aggressors excluded => denominator 8, all 8 degraded => 1.0
        self.assertAlmostEqual(
            blast_radius(_mk(10, 10), aggressors=["t0", "t1"]), 1.0, places=12
        )

    def test_is_strictly_above_threshold(self):
        at = {"a": {"p99_baseline": 1.0, "p99_aggressed": 1.20}}
        just_over = {"a": {"p99_baseline": 1.0, "p99_aggressed": 1.2001}}
        self.assertEqual(blast_radius(at, aggressors=[], threshold=0.20), 0.0)
        self.assertEqual(blast_radius(just_over, aggressors=[], threshold=0.20), 1.0)

    def test_monotone_nonincreasing_in_threshold(self):
        d = {
            "t%d" % i: {"p99_baseline": 1.0, "p99_aggressed": 1.0 + i * 0.05}
            for i in range(20)
        }
        prev = 1.1
        for thr in (0.10, 0.15, 0.20, 0.25, 0.30, 0.50):
            cur = blast_radius(d, aggressors=[], threshold=thr)
            self.assertLessEqual(cur, prev + 1e-12)
            prev = cur

    def test_requires_a_non_aggressor(self):
        with self.assertRaises(ValueError):
            blast_radius(_mk(2, 2), aggressors=["t0", "t1"])

    def test_rejects_zero_baseline(self):
        with self.assertRaises(ValueError):
            blast_radius(
                {"a": {"p99_baseline": 0.0, "p99_aggressed": 1.0}}, aggressors=[]
            )


class TestCrossMetric(unittest.TestCase):
    def test_metrics_are_not_redundant(self):
        """IR and BR must be able to disagree.

        This is the empirical claim in Section 3.4.1 that justifies reporting
        three numbers instead of one: a modest median interference can coexist
        with a wide blast radius. If this ever fails, the paper's argument for
        keeping BR separate from IR collapses.
        """
        d, agg_p99, base_p99 = {}, [], []
        for i in range(100):
            mult = 1.25 if i < 70 else 1.0
            d["t%d" % i] = {"p99_baseline": 1.0, "p99_aggressed": mult}
            agg_p99.append(mult)
            base_p99.append(1.0)
        self.assertAlmostEqual(interference_ratio(agg_p99, base_p99), 0.25, places=12)
        self.assertAlmostEqual(blast_radius(d, aggressors=[]), 0.70, places=12)


if __name__ == "__main__":
    unittest.main(verbosity=2)
