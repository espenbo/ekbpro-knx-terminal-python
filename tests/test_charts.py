# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""Chart helpers: point reduction keeps what is visible."""

import random

from knxscada.tui.charts import decimate, step_xy


def test_decimate_keeps_extremes_and_order():
    """Reducing 10 000 points to a 100-column chart keeps time order, the peaks and the latest value."""
    random.seed(3)
    pts = sorted((random.uniform(0, 1000), random.uniform(0, 100)) for _ in range(10000))
    out = decimate(pts, 0, 1000, 100)
    assert len(out) <= 4 * 100
    assert out == sorted(out)                                    # still in time order
    assert max(p[1] for p in out) == max(p[1] for p in pts)      # peaks survive
    assert min(p[1] for p in out) == min(p[1] for p in pts)
    assert out[-1] == pts[-1]                                    # the current value survives


def test_decimate_keeps_extremes_per_column():
    """Min and max survive in every column, so the reduced line looks the same as the full one."""
    pts = [(i / 10, float(i % 7)) for i in range(1000)]          # 10 points per time unit
    out = decimate(pts, 0, 100, 10)
    for col in range(10):
        inside = [p[1] for p in pts if col * 10 <= p[0] < (col + 1) * 10]
        kept = [p[1] for p in out if col * 10 <= p[0] < (col + 1) * 10]
        assert (min(kept), max(kept)) == (min(inside), max(inside))


def test_decimate_small_input_unchanged():
    """Fewer points than columns are returned as they are."""
    pts = [(1.0, 5.0), (2.0, 6.0), (3.0, 4.0)]
    assert decimate(pts, 0, 10, 100) == pts


def test_decimate_value_in_force_at_start():
    """A value from before the period is moved to its start, because KNX only sends on change and that value is still
    valid.
    """
    # KNX sends on change: the last value before the period is valid at its start.
    out = decimate([(-50.0, 7.0), (10.0, 8.0)], 0, 100, 10)
    assert out == [(0, 7.0), (10.0, 8.0)]
    assert decimate([(-50.0, 7.0)], 0, 100, 10) == [(0, 7.0)]


def test_decimate_empty():
    """No points, or a chart without width, gives no points instead of an error."""
    assert decimate([], 0, 100, 10) == []
    assert decimate([(1.0, 1.0)], 0, 100, 0) == []


def test_step_xy():
    """Values are drawn as steps (held until the next change) and the last one is extended to the end of the period."""
    xs, ys = step_xy([(0, 1.0), (10, 2.0)], 20)
    assert list(zip(xs, ys)) == [(0, 1.0), (10, 1.0), (10, 2.0), (20, 2.0)]
