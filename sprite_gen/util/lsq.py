# SPDX-License-Identifier: Apache-2.0
"""The least-squares line, the same to the last bit on every machine.

np.polyfit solves through LAPACK, and LAPACK builds round differently (Accelerate on macOS,
OpenBLAS on Linux): from the same points their lines lie a few 1e-13 px apart. Where a line
moves pixels or meets a threshold, that last bit can decide a resampled pixel's 8-bit level or
which side of zero a value falls on. Here the sums are exactly rounded (`math.fsum`) and the rest
is a fixed sequence of IEEE operations, so the line depends on the points alone: not on the
machine, nor on the order the points are given in. The lines that move the motion analysis's
frames (`auto_motion.analyse`) and the gait fallback's height and foot point
(`gait_fallback._trend`) are this line.
"""
from __future__ import annotations

import math


def line(t, values) -> tuple[float, float]:
    """(slope, intercept) of the least-squares line through (t, values), t whole numbers."""
    t = [int(v) for v in t]
    values = [float(v) for v in values]
    n, st, stt = len(t), sum(t), sum(v*v for v in t)
    sv, stv = math.fsum(values), math.fsum(a*b for a, b in zip(t, values))
    det = n*stt-st*st
    return (n*stv-st*sv)/det, (stt*sv-st*stv)/det
