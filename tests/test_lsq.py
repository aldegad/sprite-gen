# SPDX-License-Identifier: Apache-2.0
"""The exact least-squares line (`sprite_gen.util.lsq`)."""
import numpy as np

from sprite_gen.util import lsq


def test_line_is_the_least_squares_line_and_the_same_in_any_order():
    """np.polyfit's line, but written out with exactly rounded sums, so the order the points are
    summed in — which differs between machines' LAPACK — cannot reach its last bit."""
    rng = np.random.default_rng(3)
    t = np.arange(-12, 13)
    values = rng.normal(40, 9, len(t)).round(1)*0.7
    slope, intercept = lsq.line(t, values)
    np.testing.assert_allclose([slope, intercept], np.polyfit(t, values, 1), rtol=0, atol=1e-12)
    for seed in range(20):
        order = np.random.default_rng(seed).permutation(len(t))
        assert lsq.line(t[order], values[order]) == (slope, intercept)


def test_a_line_through_equal_whole_or_half_pixels_is_that_value():
    """Box edges are whole pixels and their middles half pixels: a line through equal ones is flat
    at exactly that value."""
    t = np.arange(73)
    assert lsq.line(t, np.full(73, 640.0)) == (0.0, 640.0)
    assert lsq.line(t, np.full(73, 171.5)) == (0.0, 171.5)
