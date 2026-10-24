# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""Shared plotext helpers: colour palette from the theme, time axis and chart kinds.

Charts are drawn with `textual-plotext <https://github.com/Textualize/textual-plotext>`_.
Its ``PlotextPlot`` widget already follows the Textual theme for background
and text; this module adds series colours taken from the theme as well, so a
chart looks right in every colour scheme.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import TYPE_CHECKING

from rich.text import Text
from textual.color import Color
from textual_plotext import PlotextPlot

from ..i18n import N_
from .graphics import resample

if TYPE_CHECKING:
    from textual.app import App
    from textual.app import RenderResult

#: (id, English label) of the chart types offered in the trend view.
CHART_KINDS = [("line", N_("Line")), ("step", N_("Step")), ("filled", N_("Filled")), ("bars", N_("Bars"))]

#: Extra series colours used after the theme's own colours are exhausted.
EXTRA = ["#56b6c2", "#c678dd", "#e5c07b", "#98c379", "#61afef", "#e06c75"]

#: A time series for :func:`draw`: (legend name, [(timestamp, value)], RGB colour).
Series = tuple[str, list[tuple[float, float]], tuple[int, int, int]]


class CachedPlot(PlotextPlot):
    """A ``PlotextPlot`` that builds the figure only when the data, size or theme changed.

    ``PlotextPlot.render()`` rebuilds the whole figure on every repaint –
    also for repaints caused by hover, focus or a dialog closing. Building a
    braille chart costs up to a few hundred milliseconds on a small CPU, so
    the result is cached. Call :meth:`changed` instead of ``refresh()``
    after drawing new data into :attr:`plt`.
    """

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self._version = 0
        self._cache_key: tuple | None = None
        self._cache: Text | None = None

    def changed(self) -> None:
        """The figure was redrawn: rebuild it at the next repaint."""
        self._version += 1
        self.refresh()

    def render(self) -> RenderResult:
        key = (self._version, self.size, self.app.theme)
        if key != self._cache_key or self._cache is None:
            self._cache = super().render()  # type: ignore[assignment]
            self._cache_key = key
        return self._cache


def rgb(color: str) -> tuple[int, int, int]:
    """CSS colour string → ``(r, g, b)`` as plotext expects it."""
    return Color.parse(color).rgb


def palette(app: App) -> list[tuple[int, int, int]]:
    """Distinct series colours for the current theme (theme colours first)."""
    t = app.current_theme
    base = [t.primary, t.accent, t.success, t.warning, t.error, t.secondary]
    out: list[tuple[int, int, int]] = []
    for c in [*base, *EXTRA]:
        if not c:
            continue
        v = rgb(c)
        # Skip colours that are nearly identical to one already chosen
        # (e.g. primary == success in the green themes).
        if all(sum(abs(a - b) for a, b in zip(v, o)) > 90 for o in out):
            out.append(v)
    return out


def time_axis(plt, start: float, end: float, n: int = 6) -> None:
    """Put *n* evenly spaced time labels on the x axis (clock time or date)."""
    ticks = [start + i * (end - start) / (n - 1) for i in range(n)]
    span = end - start
    fmt = "%H:%M" if span <= 26 * 3600 else "%d.%m"
    plt.xticks(ticks, [time.strftime(fmt, time.localtime(x)) for x in ticks])
    plt.xlim(start, end)


def decimate(points: Sequence[tuple[float, float]], start: float, end: float, buckets: int) -> list[tuple[float, float]]:
    """Reduce *points* to what can be seen with *buckets* pixel columns between *start* and *end*.

    Plotext rasterises every point, which takes seconds for a day of power
    meter readings (thousands of telegrams). Per column only the first,
    lowest, highest and last point are kept – in time order – so peaks and
    the step shape stay exactly as they would look with all points.

    The value in force at *start* (the last point before it) is included at
    *start*, so a step line does not begin in the middle of the chart.
    *points* must be sorted by time.
    """
    out: list[tuple[float, float]] = []
    if not points or buckets <= 0 or end <= start:
        return out
    width = (end - start) / buckets
    before = None
    current = -1
    group: list[tuple[float, float]] = []

    def flush() -> None:
        if len(group) <= 4:
            out.extend(group)
            return
        lo = min(group, key=lambda p: p[1])
        hi = max(group, key=lambda p: p[1])
        keep = {group[0], lo, hi, group[-1]}
        out.extend(sorted(keep))

    for p in points:
        if p[0] < start:
            before = p
            continue
        b = min(int((p[0] - start) / width), buckets - 1)
        if b != current:
            flush()
            group, current = [], b
        group.append(p)
    flush()
    if before is not None and (not out or out[0][0] > start):
        out.insert(0, (start, before[1]))
    return out


def step_xy(points: Sequence[tuple[float, float]], end: float) -> tuple[list[float], list[float]]:
    """Convert points to a step line: a KNX value holds until the next telegram."""
    xs: list[float] = []
    ys: list[float] = []
    for i, (x, y) in enumerate(points):
        if i:
            xs.append(x)
            ys.append(points[i - 1][1])  # horizontal segment up to the new value
        xs.append(x)
        ys.append(y)
    if points:
        xs.append(end)  # extend the last value to "now"
        ys.append(points[-1][1])
    return xs, ys


def draw(plt, series: list[Series], kind: str, start: float, end: float, width: int = 200) -> None:
    """Draw several time series into a plotext figure *width* character cells wide.

    *kind* is one of the ids in :data:`CHART_KINDS`:

    ``line``    straight lines between the received values
    ``step``    step line (most faithful for KNX, which sends on change)
    ``filled``  area down to the x axis (one bar per column, average value)
    ``bars``    averages per hour (≤ 1 day) or per day, grouped per series
    """
    if kind == "bars":
        buckets = 24 if end - start <= 26 * 3600 else 30
        step = (end - start) / buckets
        centers = [start + (i + 0.5) * step for i in range(buckets)]
        labels = [time.strftime("%H" if step < 86400 else "%d.%m", time.localtime(c - step / 2)) for c in centers]
        data, names, colors = [], [], []
        for name, pts, color in series:
            vals = resample(pts, start, end, buckets)
            data.append([v if v is not None else 0 for v in vals])
            names.append(name)
            colors.append(color)
        if not data:
            return
        if len(data) == 1:
            plt.bar(labels, data[0], color=colors[0], label=names[0], width=0.8)
        else:
            plt.multiple_bar(labels, data, labels=names, color=colors, width=0.8)
        return
    if kind == "filled":
        # Plotext's own fill (fillx) takes several seconds for a day of data. One bar per
        # character column gives the same area look in a fraction of the time.
        n = max(20, width)
        step = (end - start) / n
        centers = [start + (i + 0.5) * step for i in range(n)]
        for name, pts, color in series:
            vals = resample(pts, start, end, n)
            if any(v is not None for v in vals):
                plt.bar(centers, [v if v is not None else 0 for v in vals], color=color, label=name, width=1, marker="sd")
        time_axis(plt, start, end)
        return
    # Braille markers give 2 "pixels" per cell horizontally: more points than that cannot be seen.
    columns = max(20, 2 * width)
    for name, pts, color in series:
        pts = decimate(pts, start, end, columns)
        if not pts:
            continue
        if kind == "line":
            xs = [p[0] for p in pts] + [end]
            ys = [p[1] for p in pts] + [pts[-1][1]]
        else:
            xs, ys = step_xy(pts, end)
        plt.plot(xs, ys, label=name, color=color, marker="braille", fillx=(kind == "filled"))
    time_axis(plt, start, end)
