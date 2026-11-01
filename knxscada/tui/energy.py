# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""The "Energy meters" view (Ctrl+G): the meters of ``ui.systems`` (``type: energy``).

A table with one row per meter; the meter under the cursor gets a power curve and the
consumption per hour or day, worked out from its energy counter. Both come from the log
(``log`` in protocols.yaml), so the power and energy tags of a meter should be logged.
"""

from __future__ import annotations

import itertools
import time
from collections.abc import Sequence
from typing import Any, ClassVar

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import DataTable, Static

from ..i18n import N_, _
from .charts import CachedPlot, draw, palette
from .graphics import resample, sparkline
from .systems import Meter, Value, fmt, parse_systems
from .views import View
from .widgets import FastTable, LiveStatic

#: Columns of the meter table: (role, English heading, width).
METER_COLUMNS = (
    ("power", N_("Power"), 10), ("energy", N_("Energy"), 14), ("i1", "I L1", 8), ("i2", "I L2", 8), ("i3", "I L3", 8),
    ("u1", "U L1", 8), ("u2", "U L2", 8), ("u3", "U L3", 8), ("pf", "cos φ", 6), ("freq", "Hz", 6),
)

#: Width of the 24-hour power curve in the meter table.
SPARK_W = 24

#: Periods of the charts: key → (hours, buckets of the consumption chart, English title).
PERIODS = {"d": (24, 24, N_("last 24 hours")), "w": (24 * 7, 7, N_("last 7 days")),
           "m": (24 * 30, 30, N_("last 30 days"))}


def meter_cells(m: Meter, get: Any) -> list[str]:
    """The cells of one row in the meter table (after the name)."""
    out = []
    for role, _heading, _w in METER_COLUMNS:
        v = get(m.key(role))
        out.append(fmt(v, {"energy": 1, "power": 2, "pf": 2, "freq": 1}.get(role, 1)))
    return out


def value_at(points: Sequence[tuple[float, float]], ts: float) -> float | None:
    """The counter reading at *ts*: interpolated between the points around it, the last one after them,
    None before the first. A counter is logged only when it changed enough (``log_deadband``), so
    without interpolation the consumption of a quiet hour would land in the next logged one."""
    prev = None
    for t, v in points:
        if t >= ts:
            if t == ts or prev is None:
                return v if t == ts else None
            t0, v0 = prev
            return v0 + (v - v0) * (ts - t0) / (t - t0) if t > t0 else v
        prev = (t, v)
    return prev[1] if prev else None


def consumption(points: Sequence[tuple[float, float]], start: float, end: float, n: int) -> list[float | None]:
    """Consumption per bucket from a counter (kWh): reading at the end of a bucket minus at its start.

    A bucket without a reading at both edges is None; a counter that went backwards (replaced
    meter, reset) gives None rather than a negative bar.
    """
    pts = sorted(points)
    step = (end - start) / n
    edges = [value_at(pts, start + i * step) for i in range(n + 1)]
    if pts and edges[-1] is None:
        edges[-1] = pts[-1][1]
    out: list[float | None] = []
    for a, b in itertools.pairwise(edges):
        out.append(b - a if a is not None and b is not None and b >= a else None)
    return out


class EnergyView(View):
    """Ctrl+G: energy meters – table, power curve and consumption of the selected meter."""

    BINDINGS: ClassVar = [
        Binding("d", "period('d')", _("24 hours")),
        Binding("w", "period('w')", _("7 days")),
        Binding("m", "period('m')", _("30 days")),
    ]

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self.meters: list[Meter] = []
        self.values: dict[str, Value] = {}
        self.spark: dict[str, str] = {}               # row key → 24-hour power curve
        self.power: list[tuple[float, float]] = []    # history of the selected meter
        self.energy: list[tuple[float, float]] = []
        self.selected = 0
        self.period = "d"
        self._dirty = True
        self._plot_dirty = False
        self._plot_at = 0.0
        self._spark_at = 0.0

    @property
    def meter(self) -> Meter | None:
        return self.meters[self.selected] if self.meters else None

    def compose(self) -> ComposeResult:
        _vents, self.meters, _problems = parse_systems(self.scada.ui.get("systems"))
        if not self.meters:
            yield Static(_("No energy meters set up. Add a ui.systems entry with type: energy to config.yaml – "
                           "see the user guide (docs/*/04-configuration.md, «Systems view»)."), classes="muted")
            return
        yield FastTable(id="en-meters", cursor_type="row", zebra_stripes=True)
        yield LiveStatic("", id="en-detail", classes="card")
        with Horizontal(id="en-charts"):
            yield CachedPlot(id="en-power-plot", classes="chart")
            yield CachedPlot(id="en-use-plot", classes="chart")

    def on_mount(self) -> None:
        if self.meters:
            t = self.query_one("#en-meters", FastTable)
            t.border_title = _("Energy meters")
            t.add_column(_("Meter"), key="name", width=34)
            for role, heading, width in METER_COLUMNS:
                t.add_column(_(heading), key=role, width=width)
            t.add_column(_("24 h"), key="spark", width=SPARK_W)
            for i, m in enumerate(self.meters):
                t.add_row(m.name, *["–"] * len(METER_COLUMNS), "", key=str(i))
            t.styles.height = len(self.meters) + 3
        self.set_interval(1, self._refresh_if_dirty)

    # -- data -------------------------------------------------------------------------------------
    def connections(self) -> list[str]:
        return list(dict.fromkeys(m.conn for m in self.meters))

    def on_show_view(self) -> None:
        self.load()

    @work(exclusive=True, group="energy")
    async def load(self) -> None:
        if not self.scada.client.connected:
            return
        for conn in self.connections():
            for p in await self.scada.request("points", conn=conn, limit=5000) or []:
                self.values[f"{conn}/{p['name']}"] = (p.get("value"), p.get("unit") or "", p.get("q") or "")
        self._dirty = True
        self._refresh_if_dirty()
        if time.time() - self._spark_at > 300:
            self._spark_at = time.time()
            await self._load_sparks()
        await self._load_history()

    async def _load_sparks(self) -> None:
        now = time.time()
        for i, m in enumerate(self.meters):
            key = m.key("power")
            if not key:
                continue
            pts = await self.scada.request("history", point=key, hours=24, max_points=600) or []
            values = resample([tuple(p) for p in pts], now - 86400, now, SPARK_W)
            self.spark[str(i)] = sparkline(values, bool(self.scada.ui.get("ascii"))) if pts else ""
        self._dirty = True

    async def _load_history(self) -> None:
        m = self.meter
        if m is None:
            return
        hours = PERIODS[self.period][0]
        out = []
        for role in ("power", "energy"):
            key = m.key(role)
            pts = await self.scada.request("history", point=key, hours=hours, max_points=2000) if key else []
            out.append([(float(p[0]), float(p[1])) for p in pts or [] if p[1] is not None])
        if m is self.meter:
            self.power, self.energy = out
            self._dirty = True
            self.replot()

    @work(exclusive=True, group="energy-history")
    async def reload_history(self) -> None:
        await self._load_history()

    def on_points(self, values: dict[str, list]) -> None:
        conns = self.connections()
        m = self.meter
        for key, (v, ts, q) in values.items():
            old = self.values.get(key)
            if old is None and key.split("/", 1)[0] not in conns:
                continue
            self.values[key] = (v, old[1] if old else "", q)
            self._dirty = True
            if m is not None and isinstance(v, (int, float)):
                if key == m.key("power"):
                    self.power.append((float(ts or time.time()), float(v)))
                    self._plot_dirty = True
                elif key == m.key("energy"):
                    self.energy.append((float(ts or time.time()), float(v)))
                    self._plot_dirty = True

    def get(self, key: str | None) -> Value | None:
        return self.values.get(key) if key else None

    # -- interaction ------------------------------------------------------------------------------
    @on(DataTable.RowHighlighted, "#en-meters")
    def _row(self, event: DataTable.RowHighlighted) -> None:
        index = int(event.row_key.value) if event.row_key and event.row_key.value else 0
        if index != self.selected or not (self.power or self.energy):
            self.selected = index
            self.power, self.energy = [], []
            self._dirty = True
            # The first row is highlighted when the table is built – long before the server is connected.
            if self.display and self.scada.client.connected:
                self.replot()
                self.reload_history()

    def action_period(self, period: str) -> None:
        if period in PERIODS and period != self.period:
            self.period = period
            self.reload_history()

    # -- drawing ----------------------------------------------------------------------------------
    def on_theme_changed(self) -> None:
        self.replot()

    def today(self) -> float | None:
        """Consumption since midnight from the energy counter (None without a reading before midnight)."""
        lt = time.localtime()
        midnight = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
        a = value_at(sorted(self.energy), midnight)
        b = self.energy[-1][1] if self.energy else None
        return b - a if a is not None and b is not None and b >= a else None

    def _detail(self) -> Text:
        m = self.meter
        t = Text()
        if m is None:
            return t
        t.append(m.name, style="bold")
        t.append(f"   ({m.conn})", style="dim")
        t.append(f"\n{_('Power')} {fmt(self.get(m.key('power')), 2)}   {_('Energy')} {fmt(self.get(m.key('energy')), 1)}")
        unit = (self.get(m.key("energy")) or (None, "kWh", ""))[1] or "kWh"
        if (today := self.today()) is not None:
            t.append(f"   {_('Today')} {today:.1f} {unit}")
        use = [x for x in consumption(self.energy, *self._span(), PERIODS[self.period][1]) if x is not None]
        if use:
            t.append(f"   {_('Period')} {sum(use):.1f} {unit}")
        if not (self.power or self.energy):
            t.append("\n" + _("No log for this meter – add its power and energy tags to log in protocols.yaml."),
                     style="dim")
        return t

    def _span(self) -> tuple[float, float]:
        now = time.time()
        return now - PERIODS[self.period][0] * 3600, now

    def _refresh_if_dirty(self) -> None:
        if not self.display or not self.meters:
            return
        if self._plot_dirty and time.time() - self._plot_at > 30:
            self.replot()
        if not self._dirty:
            return
        self._dirty = False
        t = self.query_one("#en-meters", FastTable)
        for i, m in enumerate(self.meters):
            row = str(i)
            for (role, _h, _w), text in zip(METER_COLUMNS, meter_cells(m, self.get)):
                t.update_cell(row, role, text)
            t.update_cell(row, "spark", self.spark.get(row, ""))
        detail = self.query_one("#en-detail", LiveStatic)
        detail.border_title = _("Selected meter")
        detail.set(self._detail())

    def replot(self) -> None:
        """Power curve and consumption bars of the selected meter."""
        m = self.meter
        if m is None or not self.display:
            return
        self._plot_at = time.time()
        self._plot_dirty = False
        start, end = self._span()
        hours, buckets, period = PERIODS[self.period]
        pal = palette(self.app)

        plot = self.query_one("#en-power-plot", CachedPlot)
        plt = plot.plt
        plt.clear_data()
        plt.clear_figure()
        unit = (self.get(m.key("power")) or (None, "", ""))[1]
        if self.power:
            draw(plt, [(_("Power"), self.power, pal[0])], "line", start, end, plot.size.width)
        plt.title(f"{_('Power')} – {_(period)}" + (f"  [{unit}]" if unit else ""))
        plot.changed()

        plot = self.query_one("#en-use-plot", CachedPlot)
        plt = plot.plt
        plt.clear_data()
        plt.clear_figure()
        unit = (self.get(m.key("energy")) or (None, "kWh", ""))[1] or "kWh"
        step = (end - start) / buckets
        use = consumption(self.energy, start, end, buckets)
        if any(x is not None for x in use):
            labels = [time.strftime("%H" if hours <= 24 else "%d.%m", time.localtime(start + i * step))
                      for i in range(buckets)]
            plt.bar(labels, [x or 0 for x in use], color=pal[1 % len(pal)], width=0.8)
        title = _("Consumption per hour") if hours <= 24 else _("Consumption per day")
        plt.title(f"{title} – {_(period)}  [{unit}]")
        plot.changed()
