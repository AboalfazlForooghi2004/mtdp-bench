"""Sanity checks for ministats against hand-computed / textbook values.

Written as real unittest cases so that `python3 -m unittest discover -s nb/tests`
actually reports pass/fail. The sys.path insert is required because the tests
import the modules directly (ministats, isolation) rather than as a package.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from ministats import (  # noqa: E402
    _norm_sf,
    _rankdata,
    _student_t_sf,
    linregress,
    mannwhitneyu,
    spearmanr,
)


class TestRanking(unittest.TestCase):
    def test_ties_get_average_rank(self):
        r = _rankdata([10, 20, 20, 30])
        self.assertAlmostEqual(r[1], 2.5, places=12)
        self.assertAlmostEqual(r[2], 2.5, places=12)
        self.assertAlmostEqual(r[0], 1.0, places=12)
        self.assertAlmostEqual(r[3], 4.0, places=12)


class TestMannWhitney(unittest.TestCase):
    def test_complete_separation_gives_u1_zero(self):
        res = mannwhitneyu([1, 2, 3, 4, 5], [6, 7, 8, 9, 10])
        self.assertAlmostEqual(res.statistic, 0.0, places=12)
        # Exact two-sided p for n=5,5 full separation is 0.00794. The normal
        # approximation with continuity correction lands close but not on it;
        # we assert the neighbourhood rather than pretend it is exact.
        self.assertLess(res.pvalue, 0.02)

    def test_identical_samples_are_not_significant(self):
        res = mannwhitneyu([1, 2, 3, 4], [1, 2, 3, 4])
        self.assertAlmostEqual(res.statistic, 8.0, places=12)
        self.assertGreater(res.pvalue, 0.9)


class TestSpearman(unittest.TestCase):
    def test_perfect_monotone_increasing(self):
        self.assertAlmostEqual(
            spearmanr([1, 2, 3, 4, 5], [2, 4, 8, 16, 32]).statistic, 1.0, places=12
        )

    def test_perfect_monotone_decreasing(self):
        self.assertAlmostEqual(
            spearmanr([1, 2, 3, 4, 5], [5, 4, 3, 2, 1]).statistic, -1.0, places=12
        )

    def test_textbook_example(self):
        # rho = 1 - 6*sum(d^2)/(n(n^2-1))
        x = [86, 97, 99, 100, 101, 103, 106, 110, 112, 113]
        y = [0, 20, 28, 27, 50, 29, 7, 17, 6, 12]
        self.assertAlmostEqual(spearmanr(x, y).statistic, -0.17575757575, places=8)


class TestLinregress(unittest.TestCase):
    def test_exact_line(self):
        lr = linregress([0, 1, 2, 3, 4], [1, 4, 7, 10, 13])
        self.assertAlmostEqual(lr.slope, 3.0, places=10)
        self.assertAlmostEqual(lr.intercept, 1.0, places=10)
        self.assertAlmostEqual(lr.rvalue, 1.0, places=10)


class TestDistributionTails(unittest.TestCase):
    def test_student_t_critical_value(self):
        # t(0.025, df=10) = 2.228138852
        self.assertAlmostEqual(2 * _student_t_sf(2.228138852, 10), 0.05, places=6)

    def test_student_t_converges_to_normal(self):
        self.assertAlmostEqual(
            2 * _student_t_sf(1.959963985, 2_000_000), 0.05, places=4
        )

    def test_normal_sf(self):
        self.assertAlmostEqual(_norm_sf(1.959963985), 0.025, places=9)


if __name__ == "__main__":
    unittest.main(verbosity=2)
