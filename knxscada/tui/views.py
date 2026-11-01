# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""The main views of the TUI (one per menu entry, except the floor plan).

Every view derives from :class:`View` and lives inside the app's
``ContentSwitcher``; only one is visible at a time. The app forwards events
to all views:

* :meth:`View.on_show_view` – the view became visible (rebuild if needed),
* :meth:`View.on_telegram` – a telegram arrived (update live data),
* :meth:`View.on_theme_changed` – colours changed (re-render cached text).

Performance notes (these views run over SSH, often for hours):

* Values that change every second are shown with ``LiveStatic`` and
  ``FastTable.update_cell`` only when they really changed.
* Logs use ``LineLog`` which renders only the visible lines.
* Hidden views skip their periodic work (``if not self.display: return``).

Data comes from the app's local copy (``app.state``, ``app.telegrams``, ``app.events``, ``app.alarms``,
``app.history``, ``app.model``) and from server requests (``history``, ``telegrams``, ``alarm_history``,
``logged_points``). Writes go through ``ScadaApp.write``/``run_request`` to the server; no view changes a value
on screen before the bus (or the protocol connection) reports it. Config keys read here: ``ui.export_dir``
(bus monitor export folder). The floor plan, systems, energy, gateways and protocol monitor views live in
their own modules and also derive from :class:`View`.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, ItemGrid, Vertical, VerticalScroll
from textual.markup import escape
from textual.widgets import Button, DataTable, Input, Label, Static, Tree

from .. import dpt as dptlib
from ..alarms import is_critical, normalize_severity
from ..i18n import N_, _
from ..project import split_key
from .charts import CHART_KINDS, CachedPlot, draw, palette
from .graphics import big_text, resample, sparkline
from .home import HomeModel
from .widgets import FastTable, FunctionCard, GAWatcher, LineLog, LiveStatic, columns

if TYPE_CHECKING:
    from .app import ScadaApp


def hms(ts: float) -> str:
    """Clock time ``HH:MM:SS``."""
    return time.strftime("%H:%M:%S", time.localtime(ts))


def dhms(ts: float) -> str:
    """Date and clock time ``DD.MM HH:MM:SS``."""
    return time.strftime("%d.%m %H:%M:%S", time.localtime(ts))


class View(Vertical):
    """Base class of all views: a ``Vertical`` in the app's ``ContentSwitcher`` with no-op event hooks.

    The app calls the hooks on every view, visible or not (``ScadaApp._handle`` for telegrams, the theme signal for
    colours, ``action_menu`` when a view is selected), so subclasses override only what they need and must keep the
    hidden-view work cheap.
    """

    @property
    def scada(self) -> ScadaApp:
        """The running app, typed as :class:`ScadaApp` (``self.app`` is only typed as ``App``)."""
        return self.app  # type: ignore[return-value]

    def on_show_view(self) -> None:
        """Called by the app when the view is selected in the menu or with its key.

        Views that skip work while hidden (logs, tables) rebuild or flush here.
        """

    def focus_target(self) -> Any:
        """The widget to focus when the view is opened with its key (None: the first list or input).

        ``ScadaApp`` falls back to the first focusable ``LineLog`` of the view when this returns None.
        """
        return None

    def on_telegram(self, t: dict) -> None:
        """Called by the app for every telegram *t* (a dict from the server: ``type``, ``src``, ``dst``, ``value``,
        ``text``, ``ts`` …), also while the view is hidden – keep it cheap.
        """

    def on_theme_changed(self) -> None:
        """Called after the colour theme changed: re-render text and charts whose colours were cached."""


# ============================================================================ Overview
class Tile(LiveStatic):
    """Status tile: a value with unit, a sparkline and a coloured state.

    *state* is ``ok`` (normal), ``warn``, ``bad`` (critical) or ``idle``
    (no data); the CSS colours the tile accordingly.
    """

    #: CSS class of every tile (``app.tcss``).
    DEFAULT_CLASSES = "tile"

    def show(self, value: str, unit: str = "", sub: str = "", state: str = "ok", spark: str = "",
             old: bool = False) -> None:
        """Show *value* with *unit* in the tile and colour it by *state*.

        *sub* goes on the bottom border, *spark* is a sparkline (inside the tile, or on the border when big digits fill
        it). *state* sets one of the CSS classes ``ok``/``warn``/``bad``/``idle``. *old*: the value is from the database
        of the last run (shown dimmed and in italics) – such values are only displayed, never sent anywhere. Called once
        a second by :meth:`OverviewView._tiles`; classes are only touched on change, since every ``set_class`` restyles.
        """
        app: ScadaApp = self.app  # type: ignore[assignment]
        if app.big_digits:
            lines = big_text(value, app.ascii)
            if unit and lines:
                lines[0] = f"{lines[0]}  {unit}"
            self.set(Text("\n".join(lines) if lines and any(lines) else value))
        else:
            # Plain, readable text: value + unit, sparkline below.
            t = Text(justify="center", no_wrap=True, overflow="ellipsis")
            t.append(value, style="bold")
            if unit:
                t.append(f" {unit}")
            if spark:
                t.append("\n" + spark, style=app.color("muted"))
            self.set(t)
        if app.big_digits and spark:
            sub = spark  # no room for the sparkline inside the tile: put it on the border
        if self.border_subtitle != sub:
            self.border_subtitle = sub
        for s in ("ok", "warn", "bad", "idle"):
            if self.has_class(s) != (s == state):
                self.set_class(s == state, s)
        if self.has_class("old") != old:
            self.set_class(old, "old")


class RoomTile(LiveStatic):
    """Room status card in the overview: name, temperature → setpoint, lights, heating, alarm."""

    #: CSS class of every room card (``app.tcss``).
    DEFAULT_CLASSES = "room"

    def __init__(self, room) -> None:
        """*room* is the :class:`~knxscada.tui.home.Room` this card shows (its ``id`` is used when clicked)."""
        super().__init__()
        self.room = room

    def show(self, st: dict, spark: str) -> None:
        """Render the card from *st* (``HomeModel.room_status``) with *spark*, a short temperature sparkline.

        Called by :meth:`OverviewView._update_rooms` when something watched changed. Fields listed in ``st["old"]`` come
        from the database of the last run and are drawn dimmed/italic so the operator does not mistake them for live
        values.
        """
        name = escape(self.room.name)
        old = st.get("old", ())
        # Values from the database of the last run: dimmed and in italics until the bus has sent a new one.
        dim = "$text-muted i"
        flag = {"critical": "  [$error b]!![/]", "warning": "  [$warning b]![/]"}.get(st["alarm"] or "", "")
        if st["temp"] is None:
            temp = "[$text-muted]–[/]"
        else:
            temp = f"[{dim if 'temp' in old else 'b'}]{st['temp']:.1f}°[/]"
        sp = ""
        if isinstance(st["sp"], (int, float)) and not isinstance(st["sp"], bool):
            sp = f"[{dim}] → {st['sp']:.1f}°[/]" if "sp" in old else f" → {st['sp']:.1f}°"
        heat = f"  [{dim if 'heating' in old else '$warning'}]♨[/]" if st["heating"] else ""
        if st["lights_total"]:
            if st["lights_on"]:
                lit = f"[{dim if 'lights' in old else '$accent b'}]● {st['lights_on']}/{st['lights_total']}[/]"
            else:
                lit = f"[$text-muted{' i' if 'lights' in old else ''}]○ 0/{st['lights_total']}[/]"
        else:
            lit = "   "
        self.set(f"[b]{name}[/b]{flag}\n{temp}{sp}{heat}\n{lit}  [$text-muted]{spark}[/]")
        for cls, on_ in (
            ("bad", st["alarm"] == "critical"),
            ("warn", st["alarm"] == "warning"),
            ("lit", not st["alarm"] and st["lights_on"] > 0),
            ("idle", st["temp"] is None and not st["lights_on"] and not st["alarm"]),
        ):
            if self.has_class(cls) != on_:  # set_class only on change (avoids restyling)
                self.set_class(on_, cls)

    def on_click(self) -> None:
        """Clicking the card opens the room (F2) with its function cards."""
        app: ScadaApp = self.app  # type: ignore[assignment]
        app.show_room_id(self.room.id)


class OverviewView(GAWatcher, View):
    """F1: status tiles, climate table, temperature chart, room status, activity, traffic, lights."""

    #: Lines kept in the activity log (older ones are dropped).
    ACTIVITY_ROWS = 500
    #: Seconds each energy meter is shown on the POWER tile (meters from ui.systems, no KNX power address).
    METER_ROTATE_S = 5
    #: Column (English title, width) of the activity log.
    ACT_COLS = ((N_("Time"), 8), (N_("GA"), 8), (N_("Name"), 40), (N_("Value"), 16))

    def __init__(self, **kw: Any) -> None:
        """Set up the caches that keep the once-a-second refresh cheap (see the comments on each attribute)."""
        super().__init__(**kw)
        self._dirty = True  # some watched value changed since the last refresh
        self.prev: dict[str, Any] = {}  # last value per GA, to mark changes in the activity log
        self.alarm_gas: set[str] = set()
        self._cells: dict[tuple, Any] = {}  # last value written to each climate table cell
        self._sparks: dict[tuple, tuple] = {}  # (ga, bars) → (cache key, sparkline)
        self._light_rows: list = []
        self._status: dict[str, dict] = {}  # room id → room_status(), recomputed when dirty
        # While hidden, the activity log is not touched (appending scrolls it, which makes
        # Textual lay out the whole screen); it is rebuilt from the telegram buffer when shown.
        self._activity_stale = False
        self.lights_on: list[dict] = []

    def compose(self) -> ComposeResult:
        """Tiles on top; climate table, temperature chart and room cards in the middle; activity, traffic, lights below.
        """
        with ItemGrid(id="tiles", min_column_width=20):
            for tid, title in (
                ("t-out", N_("OUTDOOR")),
                ("t-in", N_("INDOOR AVG")),
                ("t-power", N_("POWER")),
                ("t-light", N_("LIGHTS ON")),
                ("t-knx", N_("KNX TLG/S")),
                ("t-alarm", N_("ALARMS")),
            ):
                tile = Tile(id=tid)
                tile.border_title = _(title)
                yield tile
        with Horizontal(id="ov-mid"):
            with Vertical(id="ov-left"):
                climate = FastTable(id="ov-climate", cursor_type="row", zebra_stripes=True)
                climate.border_title = _("Climate")
                yield climate
                temp_plot = CachedPlot(id="ov-temp-plot", classes="chart")
                temp_plot.border_title = _("Temperature °C – last 24 hours")
                yield temp_plot
            rooms = VerticalScroll(id="ov-rooms")
            rooms.border_title = _("Room status   ● 1/4 = lights on/total  ♨ heating  ! alarm  italic = not read yet")
            with rooms:
                yield ItemGrid(id="ov-room-grid", min_column_width=26)
        with Horizontal(id="ov-bottom"):
            with Vertical(id="ov-activity", classes="logbox") as act:
                act.border_title = _("Bus activity")
                yield LiveStatic("", id="ov-act-head", classes="loghead")
                yield LineLog(self._activity_line, sum(w + 1 for _title, w in self.ACT_COLS),
                              max_lines=self.ACTIVITY_ROWS, id="ov-act-log")
            traffic = CachedPlot(id="ov-traffic-plot", classes="chart")
            traffic.border_title = _("KNX traffic – telegrams per minute")
            yield traffic
            lights = FastTable(id="ov-lights", cursor_type="row", zebra_stripes=True)
            lights.border_title = _("Lights on  (Enter = switch off)")
            yield lights

    def on_mount(self) -> None:
        """Fill the table columns, mount the room cards, register the watched addresses and start the timers.

        ``climate_rooms`` (rooms with a temperature) and ``watched`` are built here because ``app.home`` exists only
        once the app is mounted. ``super().on_mount()`` (:class:`GAWatcher`) registers ``watched`` with the app.
        """
        app = self.scada
        home = app.home
        self.climate_rooms = [r for r in home.rooms if r.temp_ga]
        # Watch everything the overview shows, so on_state marks it dirty.
        watched = set(home.trend_gas())
        for r in home.rooms:
            watched |= {g for g in (r.sp_ga, r.mode_ga, r.heat_ga) if g}
            watched |= {light["status"] for light in r.lights if light["status"]}
        self.watched = tuple(watched)

        c = self.query_one("#ov-climate", DataTable)
        for key, label, width in (("room", N_("Room"), 18), ("temp", N_("Actual"), 7), ("sp", N_("Setp."), 7),
                                  ("mode", N_("Mode"), 10), ("heat", N_("Heat"), 5), ("spark", N_("Last 24 h"), 24)):
            c.add_column(_(label), key=key, width=width)
        for r in self.climate_rooms:
            c.add_row(r.name, "", "", "", "", "", key=r.id)

        grid = self.query_one("#ov-room-grid", ItemGrid)
        self.room_tiles = [RoomTile(r) for r in home.rooms if r.temp_ga or r.lights]
        grid.mount_all(self.room_tiles)

        self.query_one("#ov-act-head", LiveStatic).set(columns(*((_(t), w, "bold") for t, w in self.ACT_COLS)))
        lt = self.query_one("#ov-lights", DataTable)
        for key, label, width in (("room", N_("Room"), 12), ("name", N_("Light"), 18), ("level", N_("Level"), 5)):
            lt.add_column(_(label), key=key, width=width)
        super().on_mount()
        self.set_interval(1, self._refresh)
        self.set_interval(30, self.replot)

    def on_state(self) -> None:
        """A watched address changed (or the history was loaded): rebuild the tables at the next 1 s tick.

        Only a flag is set – with a busy bus this is called many times per second.
        """
        self._dirty = True

    def on_alarms(self) -> None:
        """The alarm list changed: alarms colour the activity log and the room status."""
        self.alarm_gas = {a["ga"] for a in self.scada.alarms if a["cleared_ts"] is None}
        self._dirty = True

    def on_show_view(self) -> None:
        """Refresh the tables, and rebuild the activity log if telegrams arrived while hidden."""
        self._dirty = True
        if self._activity_stale:
            self.load_activity()

    def on_theme_changed(self) -> None:
        """Drop the cached cells and light rows (their colours are baked in) and redraw the charts."""
        self._cells.clear()
        self._light_rows = []
        self._dirty = True
        self.replot()

    def replot(self) -> None:
        """Redraw the two charts (every 30 s and after the history has loaded)."""
        if not self.display:
            return
        app = self.scada
        home = app.home
        pal = palette(app)
        now = time.time()
        start = now - 86400

        # Temperature chart: outdoor and the average of all rooms.
        plot = self.query_one("#ov-temp-plot", CachedPlot)
        plt = plot.plt
        plt.clear_data()
        plt.clear_figure()
        series = []
        if home.outdoor_ga and app.history.get(home.outdoor_ga):
            series.append((_("Outdoor"), app.history[home.outdoor_ga], pal[1 % len(pal)]))
        n = 96  # 15-minute buckets
        cols = [resample(app.history.get(r.temp_ga, []), start, now, n) for r in self.climate_rooms]
        step = (now - start) / n
        avg = []
        for i in range(n):
            vals = [c[i] for c in cols if c[i] is not None]
            if vals:
                avg.append((start + (i + 1) * step, sum(vals) / len(vals)))
        if avg:
            series.append((_("Indoor avg"), avg, pal[0]))
        draw(plt, series, "line", start, now, plot.size.width)
        plot.changed()

        # Traffic chart: telegrams per minute for the last hour.
        tp = self.query_one("#ov-traffic-plot", CachedPlot)
        plt = tp.plt
        plt.clear_data()
        plt.clear_figure()
        if app.traffic:
            xs = list(range(-len(app.traffic) + 1, 1))
            plt.bar(xs, app.traffic, color=pal[0], width=0.9)
            plt.xticks([-60, -45, -30, -15, 0], ["-60", "-45", "-30", "-15", _("now")])
            plt.xlim(-60.5, 0.5)
        tp.changed()

    # -- activity log ---------------------------------------------------------------------
    def on_telegram(self, t: dict) -> None:
        """Append *t* to the activity log while visible; read requests carry no value and are skipped.

        While hidden only a flag is set, and :meth:`load_activity` rebuilds the log from ``app.telegrams`` when shown.
        """
        if t["type"] == "read":
            return
        if not self.display:
            self._activity_stale = True
            return
        self.add_activity(t)

    def _activity_kind(self, t: dict) -> tuple[dict, str]:
        """Tag a telegram as alarm / changed / same. Must run in arrival order (uses ``prev``)."""
        changed = self.prev.get(t["dst"], object()) != t.get("value")
        self.prev[t["dst"]] = t.get("value")
        kind = "alarm" if t["dst"] in self.alarm_gas else ("changed" if changed else "same")
        return (t, kind)

    def _activity_line(self, item: tuple[dict, str]) -> Text:
        """LineLog formatter: changes in normal colour, repeats muted, alarms red."""
        t, kind = item
        app = self.scada
        style = {"alarm": f"bold {app.color('error')}", "changed": app.color("foreground")}.get(kind, app.color("muted"))
        vals = (hms(t["ts"]), t["dst"], app.ga(t["dst"]).get("name", ""), t.get("text") or "")
        return columns(*((v, w, style) for v, (_title, w) in zip(vals, self.ACT_COLS)))

    def add_activity(self, t: dict) -> None:
        """Append one telegram to the activity log (classified by :meth:`_activity_kind`)."""
        self.query_one("#ov-act-log", LineLog).append(self._activity_kind(t))

    def load_activity(self) -> None:
        """Fill the activity log from the telegram buffer (after connecting, or when shown again)."""
        self._activity_stale = False
        self.alarm_gas = {a["ga"] for a in self.scada.alarms if a["cleared_ts"] is None}
        self.prev.clear()
        items = [self._activity_kind(t) for t in self.scada.telegrams if t["type"] != "read"]
        self.query_one("#ov-act-log", LineLog).set_items(items[-self.ACTIVITY_ROWS:])

    # -- periodic refresh -------------------------------------------------------------------
    def _refresh(self) -> None:
        """Once per second: tiles always; table, room cards and lights only when something changed."""
        if not self.display or self.app.screen is not self.screen:
            return  # hidden, or covered by the screensaver or a dialog
        if self._dirty:
            self._dirty = False
            self._update_rooms()
        self._tiles()

    def _spark(self, ga: str | None, n: int, now: float) -> str:
        """24-hour sparkline of *ga* with *n* bars, cached.

        Resampling a day of history for every room each second cost up to
        150 ms per second; the line only changes when a point arrives or a new
        bar begins, so it is rebuilt only then.
        """
        hist = self.scada.history.get(ga or "", [])
        key = (ga, n, int(now // (86400 / n)), len(hist), hist[-1][0] if hist else None, self.scada.ascii)
        cached = self._sparks.get((ga, n))
        if cached and cached[0] == key:
            return cached[1]
        line = sparkline(resample(hist, now - 86400, now, n), self.scada.ascii)
        self._sparks[(ga, n)] = (key, line)
        return line

    def _update_rooms(self) -> None:
        """Climate table, room cards and the list of lights that are on."""
        app = self.scada
        home = app.home
        state = app.state
        now = time.time()
        # Room status is needed by the table, the cards and the tiles: compute it once.
        self._status = {r.id: home.room_status(r, state, app.alarms) for r in home.rooms}

        c = self.query_one("#ov-climate", DataTable)
        for r in self.climate_rooms:
            st = self._status[r.id]
            old = st["old"]
            old_style = f"italic {app.color('muted')}"  # value from the last run, not read since the start
            temp = Text(f"{st['temp']:.1f}°" if st["temp"] is not None else "–",
                        style=old_style if "temp" in old else "bold")
            sp = st["sp"]
            sp_text = Text(f"{sp:.1f}°" if isinstance(sp, (int, float)) and not isinstance(sp, bool) else "–",
                           style=old_style if "sp" in old else "")
            if "temp" not in old and st["temp"] is not None and isinstance(sp, (int, float)) and st["temp"] < sp - 1.5:
                temp.stylize(app.color("warning"))  # clearly below setpoint
            mode = Text(st["mode"], style=old_style if "mode" in old else "")
            heat = Text(_("♨ on"), style=app.color("warning")) if st["heating"] else Text(_("off"), style=app.color("muted"))
            if "heating" in old:
                heat.stylize(old_style)
            spark = self._spark(r.temp_ga, 24, now)
            cells = (("temp", temp), ("sp", sp_text), ("mode", mode), ("heat", heat),
                     ("spark", Text(spark, style=app.color("primary"))))
            for col, val in cells:
                # update_cell is relatively expensive: only call it on real change.
                key = (r.id, col)
                sig = (val.plain, str(val.style)) if isinstance(val, Text) else val
                if self._cells.get(key) != sig:
                    self._cells[key] = sig
                    c.update_cell(r.id, col, val)

        for tile in self.room_tiles:
            st = self._status[tile.room.id]
            spark = ""
            if tile.room.temp_ga:
                spark = self._spark(tile.room.temp_ga, 8, now)
            tile.show(st, spark)

        lt = self.query_one("#ov-lights", DataTable)
        rows = []
        self.lights_on = []
        for r in home.rooms:
            for light in r.lights:
                if state.get(light["status"] or "", {}).get("value") is True:
                    lvl = HomeModel.text(state, light["level"]) if light["level"] else ""
                    self.lights_on.append(light)
                    rows.append((f"{r.id}|{light['ga']}", (r.name, light["name"], lvl)))
        if rows != self._light_rows:  # rebuild the small table only when the set changed
            self._light_rows = rows
            row = lt.cursor_row
            lt.clear()
            for key, cells in rows:
                lt.add_row(*cells, key=key)
            if rows:
                lt.move_cursor(row=min(row, len(rows) - 1))

    def _tiles(self) -> None:
        """Update the six status tiles."""
        app = self.scada
        home = app.home
        state = app.state
        now = time.time()
        q = self.query_one
        no_data = _("no data")

        out = HomeModel.value(state, home.outdoor_ga)
        if isinstance(out, (int, float)):
            spark = self._spark(home.outdoor_ga, 18, now)
            q("#t-out", Tile).show(f"{out:.1f}", "°C", "", "ok", spark, HomeModel.is_old(state, home.outdoor_ga))
        else:
            q("#t-out", Tile).show("-", "", no_data, "idle")

        rooms = [st for r in self.climate_rooms if (st := self._status.get(r.id)) and st["temp"] is not None]
        temps = [st["temp"] for st in rooms]
        if temps:
            q("#t-in", Tile).show(f"{sum(temps) / len(temps):.1f}", "°C", f"{min(temps):.1f}–{max(temps):.1f}°", "ok",
                                  old=any("temp" in st["old"] for st in rooms))
        else:
            q("#t-in", Tile).show("-", "", no_data, "idle")

        pw = HomeModel.value(state, home.power_ga)
        if home.meters and not home.power_ga:
            self._meter_tile(now)
        elif isinstance(pw, (int, float)):
            spark = self._spark(home.power_ga, 18, now)
            text = f"{pw / 1000:.2f}" if abs(pw) >= 10000 else f"{pw:.0f}"
            q("#t-power", Tile).show(text, "kW" if abs(pw) >= 10000 else "W", "", "ok", spark,
                                     HomeModel.is_old(state, home.power_ga))
        else:
            q("#t-power", Tile).show("-", "", _("no power meter") if not home.power_ga else no_data, "idle")

        total = sum(len(r.lights) for r in home.rooms)
        on = len(self.lights_on)
        q("#t-light", Tile).show(str(on), f"/ {total}", "", "ok" if on else "idle")

        st = app.status
        per_s = app.stats.get("per_min", 0) / 60
        traffic = sparkline(app.traffic[-18:], app.ascii) if app.traffic else ""
        q("#t-knx", Tile).show(f"{per_s:.1f}", _("tlg/s"), "" if st.get("connected") else _(str(st.get("connection", ""))),
                               "ok" if st.get("connected") else "bad", traffic)

        active = [a for a in app.alarms if a["cleared_ts"] is None]
        unacked = [a for a in app.alarms if a["acked_ts"] is None]
        crit = any(is_critical(a) for a in active)
        state_name = "bad" if crit else ("warn" if active or unacked else "ok")
        sub = _("{n} unacknowledged").format(n=len(unacked)) if unacked else _("all normal")
        q("#t-alarm", Tile).show(str(len(active)), _("active"), sub, state_name)

    def _meter_tile(self, now: float) -> None:
        """The POWER tile without a KNX power address: one meter from ui.systems at a time, its name in the title."""
        app = self.scada
        meters = app.home.meters
        i = int(now // self.METER_ROTATE_S) % len(meters)
        name, ga = meters[i]
        tile = self.query_one("#t-power", Tile)
        short = re.sub(r"\s*\(.*\)\s*$", "", name)          # "Main (riser from the board)" → "Main"
        title = f"{_('POWER')} · {short}"
        if tile.border_title != title:
            tile.border_title = title
        pw = HomeModel.value(app.state, ga)
        where = f"{i + 1}/{len(meters)}"
        if isinstance(pw, (int, float)) and not isinstance(pw, bool):
            unit = app.ga(ga).get("unit", "")
            tile.show(f"{pw:.2f}" if unit == "kW" else f"{pw:.0f}", unit, where, "ok", self._spark(ga, 18, now),
                      HomeModel.is_old(app.state, ga))
        else:
            tile.show("-", "", f"{where} {_('no data')}", "idle")

    @on(DataTable.RowSelected, "#ov-lights")
    def _light_off(self, event: DataTable.RowSelected) -> None:
        """Enter on a row of the lights table switches that light off (DPT 1.001 ``False`` to its control address).

        The row key is ``"room id|ga"``; the table updates itself when the status telegram arrives.
        """
        _room, ga = str(event.row_key.value).split("|", 1)
        self.scada.write(ga, False, "1.001")

    @on(DataTable.RowSelected, "#ov-climate")
    def _goto_room(self, event: DataTable.RowSelected) -> None:
        """Enter on a climate table row opens that room (the row key is the room id)."""
        self.scada.show_room_id(str(event.row_key.value))

    @on(LineLog.Selected, "#ov-act-log")
    def _activity(self, event: LineLog.Selected) -> None:
        """Enter or click on an activity line opens the group address dialog of its destination."""
        self.scada.open_ga(event.data[0]["dst"])


# ============================================================================ Rooms
class RoomView(View):
    """F2: the cards of one room, or of all rooms of a floor."""

    def compose(self) -> ComposeResult:
        """A title line and a scrolling grid that :meth:`show` fills with function cards."""
        yield Label("", id="room-title")
        with VerticalScroll(id="room-scroll"):
            yield ItemGrid(id="room-grid", min_column_width=46, stretch_height=False)

    async def show(self, title: str, functions: list[dict], show_room: bool = False) -> None:
        """Replace the cards with one :class:`FunctionCard` per entry of *functions* and set *title*.

        Called by the app when a room or a whole floor is chosen in the menu. *show_room* adds the room name to every
        card title (used for a floor, where cards of several rooms are mixed). Async because removing and mounting
        widgets is awaited – the old cards must be gone before the new ones (with the same watched addresses) are
        mounted.
        """
        self.query_one("#room-title", Label).update(title)
        grid = self.query_one("#room-grid", ItemGrid)
        await grid.remove_children()
        await grid.mount_all([FunctionCard(f, self.scada.ui, show_room) for f in functions])
        self.query_one("#room-scroll").scroll_home(animate=False)


# ============================================================================ Group addresses
class GroupAddressView(View):
    """F3: all group addresses with live values, filtered by tree and search text."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("u", "toggle_unused", _("Show/hide unused")),
        Binding("slash", "focus_filter", _("Search")),
    ]

    def __init__(self, **kw: Any) -> None:
        """Start with all used addresses, no tree selection and an empty batch of pending changes."""
        super().__init__(**kw)
        self.show_unused = False
        # Selected main/middle group, e.g. "1" or "1/2", or ("project", prefix) for a project node.
        self.prefix: str | tuple[str, str] | None = None
        self.rows: set[str] = set()     # addresses currently in the table
        self._pending: dict[str, dict] = {}  # last telegram per row, applied in batches by _flush
        self._filter_timer = None

    def compose(self) -> ComposeResult:
        """The tree of main/middle groups on the left, the filter field and the address table on the right."""
        with Horizontal():
            tree: Tree = Tree(_("All"), id="ga-tree")
            tree.border_title = _("Main groups")
            yield tree
            with Vertical():
                yield Input(placeholder=_("Filter by address, name or DPT …  (/)"), id="ga-filter")
                yield FastTable(id="ga-table", cursor_type="row", zebra_stripes=True)

    def on_mount(self) -> None:
        """Build the group tree from ``model["ranges"]``, add the table columns, fill the table and start the flush
        timer.
        """
        tree = self.query_one("#ga-tree", Tree)
        tree.show_root = True
        tree.root.data = None
        tree.root.expand()

        def add(node, ranges) -> None:
            """Add *ranges* (and recursively their children) below tree *node*; leaves cannot be expanded."""
            for r in ranges:
                if "project" in r:  # several projects: one top node per project, filtered by prefix
                    child = node.add(r["name"], data=("project", r["project"]), expand=False)
                else:
                    child = node.add(f"{r['address']}  {r['name']}", data=r["address"], expand=False)
                if r["children"]:
                    add(child, r["children"])
                else:
                    child.allow_expand = False

        add(tree.root, self.scada.model["ranges"])
        t = self.query_one("#ga-table", DataTable)
        for key, label, width in (("addr", N_("Address"), 9), ("name", N_("Name"), 46), ("dpt", N_("DPT"), 7),
                                  ("value", N_("Value"), 16), ("ts", N_("Changed"), 8), ("src", N_("Source"), 8)):
            t.add_column(_(label), key=key, width=width)
        self.rebuild()
        self.set_interval(1, self._flush)

    def _matches(self, ga: dict, words: list[str]) -> bool:
        """True if group address record *ga* passes the unused toggle, the tree selection and all filter *words*.

        The tree selection is either an address prefix (``"1"`` or ``"1/2"``) or ``("project", prefix)`` when several
        ETS projects are loaded; their keys carry the project prefix (``split_key``).
        """
        if not self.show_unused and (ga["placeholder"] or not ga["used"]):
            return False
        if isinstance(self.prefix, tuple):
            if split_key(ga["address"])[0] != self.prefix[1]:
                return False
        elif self.prefix and not ga["address"].startswith(self.prefix + "/") and ga["address"] != self.prefix:
            return False
        hay = f"{ga['address']} {ga['name']} {ga['dpt'] or ''} {ga['description']}".lower()
        return all(w in hay for w in words)  # every word must match (AND)

    def rebuild(self) -> None:
        """Refill the table from the model and the current state (after a filter, tree or toggle change).

        Values from the database of the last run are shown dimmed/italic. The pending batch is dropped because the rows
        are built from the newest state anyway.
        """
        t = self.query_one("#ga-table", DataTable)
        t.clear()
        self.rows.clear()
        self._pending.clear()  # the rows are rebuilt from the current state
        words = self.query_one("#ga-filter", Input).value.lower().split()
        state = self.scada.state
        for addr, ga in self.scada.model["gas"].items():
            if not self._matches(ga, words):
                continue
            st = state.get(addr, {})
            text = Text(st.get("text", ""), style="dim italic") if st.get("old") else st.get("text", "")
            t.add_row(addr, ga["name"], ga["dpt"] or "", text,
                      hms(st["ts"]) if st.get("ts") else "", st.get("src", ""), key=addr)
            self.rows.add(addr)
        title = _("{n} group addresses").format(n=len(self.rows))
        t.border_title = title + ("" if self.show_unused else "  " + _("(u: show unused)"))

    def on_telegram(self, t: dict) -> None:
        """Remember the newest telegram per shown row; :meth:`_flush` applies them once per second."""
        # Every update_cell makes the DataTable re-render all visible rows, which is
        # expensive. Collect the changes and apply them once per second (see _flush).
        if t["type"] != "read" and t["dst"] in self.rows:
            self._pending[t["dst"]] = t

    def _flush(self) -> None:
        """Apply the collected value changes (only while the view is visible)."""
        if not self.display or not self._pending:
            return
        pending, self._pending = self._pending, {}
        for t in pending.values():
            if t["dst"] in self.rows:
                self._update_row(t)

    def _update_row(self, t: dict) -> None:
        """Write value text, time and source of telegram *t* into its row."""
        table = self.query_one("#ga-table", DataTable)
        table.update_cell(t["dst"], "value", t["text"])
        table.update_cell(t["dst"], "ts", hms(t["ts"]))
        table.update_cell(t["dst"], "src", t["src"])

    def on_show_view(self) -> None:
        """Apply the changes collected while the view was hidden."""
        self._flush()

    @on(Input.Changed, "#ga-filter")
    def _filter(self) -> None:
        """Filter text changed: rebuild after 0.2 s without further typing (debounced)."""
        # Rebuilding hundreds of rows on every key press is slow: wait for a short pause in typing.
        if self._filter_timer is not None:
            self._filter_timer.stop()
        self._filter_timer = self.set_timer(0.2, self.rebuild)

    @on(Tree.NodeSelected, "#ga-tree")
    def _tree(self, event: Tree.NodeSelected) -> None:
        """A tree node was selected: show only the addresses below it (the root shows all)."""
        self.prefix = event.node.data
        self.rebuild()

    @on(DataTable.RowSelected, "#ga-table")
    def _row(self, event: DataTable.RowSelected) -> None:
        """Enter on a row opens the group address dialog (read, write, trend)."""
        self.scada.open_ga(str(event.row_key.value))

    def action_toggle_unused(self) -> None:
        """Key ``u``: show or hide addresses without links in ETS and placeholders."""
        self.show_unused = not self.show_unused
        self.rebuild()

    def action_focus_filter(self) -> None:
        """Key ``/``: jump to the filter field."""
        self.query_one("#ga-filter", Input).focus()


# ============================================================================ Bus monitor
class MonitorView(View):
    """F4: every telegram live, with filter and pause."""

    #: Lines kept in the bus monitor; the client buffer is larger (``app.telegrams``).
    MAX_ROWS = 2000
    #: (English column title, width).
    COLS = ((N_("Time"), 8), ("", 1), (N_("Source"), 8), (N_("Dest."), 8), (N_("Name"), 40), (N_("Type"), 8),
            (N_("DPT"), 7), (N_("Raw"), 12), (N_("Value"), 18))
    #: With several KNX interfaces: an extra column with the interface the telegram came through.
    COLS_VIA = COLS[:2] + ((N_("Via"), 8),) + COLS[2:4] + ((N_("Name"), 32),) + COLS[5:]
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("p", "pause", _("Pause/resume")),
        Binding("c", "clear", _("Clear")),
        Binding("e", "export", _("Export for ETS")),
        Binding("slash", "focus_filter", _("Filter")),
    ]

    def __init__(self, **kw: Any) -> None:
        """Live, unfiltered, and with the normal columns until a telegram with an interface (``via``) arrives."""
        super().__init__(**kw)
        self.paused = False
        self.words: list[str] = []
        self.cols = self.COLS

    def compose(self) -> ComposeResult:
        """Toolbar (filter, export, live/paused state) above the log with its fixed column header."""
        with Horizontal(classes="toolbar"):
            yield Input(placeholder=_("Filter: address, name, source or value  (/)"), id="mon-filter")
            yield Button(_("Export (e)"), id="mon-export", compact=True)
            yield Static("", id="mon-state")
        with Vertical(id="mon-box", classes="logbox"):
            yield LiveStatic("", id="mon-head", classes="loghead")
            yield LineLog(self._line, sum(w + 1 for _title, w in self.COLS), max_lines=self.MAX_ROWS, id="mon-log")

    def on_mount(self) -> None:
        """Set the column header, the border title and the live/paused indicator."""
        self._set_columns(self.COLS)
        self.query_one("#mon-box").border_title = _("Bus monitor")
        self._update_state()

    def _set_columns(self, cols: tuple) -> None:
        """Switch between the normal columns and the ones with the interface (*Via*)."""
        self.cols = cols
        self.query_one("#mon-head", LiveStatic).set(columns(*((_(t) if t else "", w, "bold") for t, w in cols)))
        self.query_one("#mon-log", LineLog).line_width = sum(w + 1 for _title, w in cols)

    def _match(self, t: dict) -> bool:
        """True if telegram *t* contains every filter word (address, source, name, value, type in English and
        translated, interface).
        """
        if not self.words:
            return True
        ga = self.scada.ga(t["dst"])
        hay = (f"{t['dst']} {t['src']} {ga.get('name', '')} {t.get('text', '')} {t['type']} {_(t['type'])}"
               f" {t.get('via') or ''}").lower()
        return all(w in hay for w in self.words)

    def _line(self, t: dict) -> Text:
        """LineLog formatter for one telegram."""
        app = self.scada
        ga = app.ga(t["dst"])
        kind = {"write": _("Write"), "response": _("Response"), "read": _("Read")}.get(t["type"], t["type"])
        out = t["dir"] == "out"
        muted = app.color("muted")
        vals = (hms(t["ts"]), "→" if out else "←", t["src"], t["dst"], ga.get("name", ""), kind,
                ga.get("dpt") or "", t.get("raw") or "", t.get("text") or "")
        styles = (muted, f"bold {app.color('accent')}" if out else muted, muted, "", "",
                  muted if t["type"] == "read" else "", muted, muted, "bold")
        if self.cols is self.COLS_VIA:
            vals = vals[:2] + (t.get("via") or "",) + vals[2:]
            styles = styles[:2] + (app.color("accent"),) + styles[2:]
        return columns(*((v, w, s) for v, (_title, w), s in zip(vals, self.cols, styles)))

    def rebuild(self) -> None:
        """Re-apply the filter to the client's telegram buffer (newest MAX_ROWS matches)."""
        if len(self.scada.status.get("interfaces") or []) > 1 and self.cols is not self.COLS_VIA:
            self._set_columns(self.COLS_VIA)
        self.words = self.query_one("#mon-filter", Input).value.lower().split()
        items: list[dict] = []
        for t in reversed(self.scada.telegrams):
            if self._match(t):
                items.append(t)
                if len(items) >= self.MAX_ROWS:
                    break
        items.reverse()
        log = self.query_one("#mon-log", LineLog)
        log.set_items(items)
        self.query_one("#mon-box").border_subtitle = _("{n} telegrams").format(n=log.line_count)

    def on_show_view(self) -> None:
        """Rebuild from the client's telegram buffer – lines are not appended while hidden."""
        self.rebuild()

    def on_telegram(self, t: dict) -> None:
        """Append *t* to the log if it is visible, not paused and matches the filter.

        The first telegram with ``via`` (several KNX interfaces) switches to the columns with the interface.
        """
        # While hidden we skip appending; the view is rebuilt from the buffer when shown.
        if t.get("via") and self.cols is not self.COLS_VIA:
            self._set_columns(self.COLS_VIA)
            if self.display:
                self.rebuild()  # the lines already shown need the new column too
                return
        if not self.paused and self.display and self._match(t):
            self.query_one("#mon-log", LineLog).append(t)

    def _update_state(self) -> None:
        """Show ``PAUSED`` or ``LIVE`` in the toolbar."""
        self.query_one("#mon-state", Static).update(
            Text(" ⏸ " + _("PAUSED") + " " if self.paused else " ● " + _("LIVE") + " ", style="reverse bold")
        )

    @on(Input.Changed, "#mon-filter")
    def _filter(self) -> None:
        """Filter text changed: rebuild at once (the buffer is small enough to filter on every key)."""
        self.rebuild()

    @on(Input.Submitted, "#mon-filter")
    def _to_log(self) -> None:
        """Enter in the filter field moves the focus to the log."""
        self.query_one("#mon-log", LineLog).focus()

    @on(LineLog.Selected, "#mon-log")
    def _open(self, event: LineLog.Selected) -> None:
        """Enter or click on a line opens the group address dialog of its destination."""
        self.scada.open_ga(event.data["dst"])

    def action_pause(self) -> None:
        """Key ``p``: freeze the log; resuming rebuilds it so telegrams received meanwhile are not lost."""
        self.paused = not self.paused
        self._update_state()
        if not self.paused:
            self.rebuild()

    def action_clear(self) -> None:
        """Key ``c``: empty the shown log (the client buffer and the server log are kept)."""
        self.query_one("#mon-log", LineLog).clear()

    @on(Button.Pressed, "#mon-export")
    def action_export(self) -> None:
        """Ask for format and period, then save the telegrams that match the filter (see knxscada.export)."""
        from .dialogs import ExportDialog

        def chosen(choice: dict | None) -> None:
            """Dialog result: ``{"fmt", "hours"}`` starts the export, None (cancel) does nothing."""
            if choice:
                self._export(choice["fmt"], choice["hours"])

        self.app.push_screen(ExportDialog(" ".join(self.words)), chosen)

    #: Most telegrams fetched from the server for one export, and the page size of each request.
    EXPORT_MAX = 500_000
    EXPORT_PAGE = 5000

    @work(exclusive=True, group="export")
    async def _export(self, fmt: str, hours: int | None) -> None:
        """Save the telegrams matching the filter as *fmt* (``xml``, ``csv``, ``json``) in ``ui.export_dir`` (default
        ``~``).

        *hours* None exports the lines in the log; otherwise the last *hours* are fetched from the server's telegram
        log, page by page (newest first, at most :attr:`EXPORT_MAX`). Runs as an exclusive worker so a second export
        cancels the first. The file name has a timestamp, so nothing is overwritten. Errors are shown as notifications.
        """
        from ..export import FORMATS, render

        app = self.scada
        if hours is None:
            items = self.query_one("#mon-log", LineLog).items
        else:
            # Page backwards through the server's log; the filter is applied to every page.
            since, before, items = time.time() - hours * 3600, None, []
            self.notify(_("Fetching telegrams from the server …"))
            while len(items) < self.EXPORT_MAX:
                page = await app.request("telegrams", limit=self.EXPORT_PAGE, before_id=before)
                if not page:
                    break
                for t in page:  # the log stores the decoded value; the display text is made here
                    if t.get("type") != "read":
                        t.setdefault("text", dptlib.fmt(app.ga(t["dst"]).get("dpt"), t.get("value")))
                # Pages come oldest first within the page, newest page first: prepend to keep time order.
                items[:0] = [t for t in page if t["ts"] >= since and self._match(t)]
                # Stop once the page reaches before the period, or the server had no more (a short page).
                if page[0]["ts"] < since or len(page) < self.EXPORT_PAGE:
                    break
                before = page[0]["id"]  # the next page ends just before the oldest telegram of this one
        if not items:
            self.notify(_("No telegrams to export"), severity="warning")
            return
        folder = Path(os.path.expanduser(str(app.cfg.ui.get("export_dir") or "~")))
        path = folder / time.strftime(f"knx-busmonitor-%Y%m%d-%H%M%S{FORMATS[fmt]}")
        try:
            text, n = render(fmt, items, app.model["gas"], app.title)
            folder.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        except OSError as exc:
            self.notify(_("Export failed: {error}").format(error=exc), severity="error")
            return
        hint = _(" – open it in ETS: Diagnostics → Group Monitor → Open") if fmt == "xml" else ""
        self.notify(_("{n} telegrams saved to {path}").format(n=n, path=path) + hint, timeout=12)

    def action_focus_filter(self) -> None:
        """Key ``/``: jump to the filter field."""
        self.query_one("#mon-filter", Input).focus()


# ============================================================================ Alarms
#: Rich style of the priority badge per normalized severity (others: bold).
SEVERITY_STYLE = {"critical": "bold white on red", "warning": "bold black on yellow"}


class AlarmView(View):
    """F5: open alarms (active or unacknowledged) on top, history below."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("k", "ack", _("Acknowledge selected")),
        Binding("K", "ack_all", _("Acknowledge all")),
        Binding("h", "history", _("Refresh history")),
    ]

    def compose(self) -> ComposeResult:
        """The open alarms table on top, the history table below."""
        active = FastTable(id="al-active", cursor_type="row")
        active.border_title = _("Active and unacknowledged alarms   (k: acknowledge · K: acknowledge all)")
        yield active
        hist = FastTable(id="al-history", cursor_type="row", zebra_stripes=True)
        hist.border_title = _("Alarm history")
        yield hist

    def on_mount(self) -> None:
        """Add the same columns to both tables and show the open alarms."""
        for tid in ("#al-active", "#al-history"):
            t = self.query_one(tid, DataTable)
            for key, label, width in (("raised", N_("Raised"), 14), ("sev", N_("Priority"), 10), ("text", N_("Text"), 50),
                                      ("ga", N_("GA"), 8), ("state", N_("State"), 14), ("ack", N_("Acknowledged"), 30)):
                t.add_column(_(label), key=key, width=width)
        self.refresh_alarms()

    @staticmethod
    def _row(a: dict) -> tuple:
        """Table cells for alarm *a*: raised time, priority badge, text, address, state and who acknowledged it."""
        sev = normalize_severity(a["severity"])
        sev_cell = Text(f" {_(sev).upper()} ", style=SEVERITY_STYLE.get(sev, "bold"))
        if a["cleared_ts"] is None:
            state = Text(_("ACTIVE"), style="bold red")
        else:
            state = Text(f"{_('normal')} {hms(a['cleared_ts'])}", style="dim")
        ack = f"{a['acked_by']} {hms(a['acked_ts'])}" if a["acked_ts"] else Text(_("unacknowledged"), style="bold")
        return (dhms(a["raised_ts"]), sev_cell, a["text"], a["ga"], state, ack)

    def refresh_alarms(self) -> None:
        """Rebuild the open-alarm table: unacknowledged first, newest first.

        Called by the app whenever the alarm list changes; the cursor stays on the same row number.
        """
        t = self.query_one("#al-active", DataTable)
        row = t.cursor_row
        t.clear()
        # False sorts before True: unacknowledged first; within each group the newest first.
        for a in sorted(self.scada.alarms, key=lambda a: (a["acked_ts"] is not None, -a["raised_ts"])):
            t.add_row(*self._row(a), key=str(a["id"]))
        if t.row_count:
            t.move_cursor(row=min(row, t.row_count - 1))

    def on_show_view(self) -> None:
        """Refresh the open alarms and fetch the history from the server."""
        self.refresh_alarms()
        self.action_history()

    @work(exclusive=True, group="alarm-history")
    async def action_history(self) -> None:
        """Key ``h``: fetch the newest 300 alarms (open and closed) from the server's database."""
        rows = await self.scada.request("alarm_history", limit=300)
        t = self.query_one("#al-history", DataTable)
        t.clear()
        for a in rows or []:
            t.add_row(*self._row(a), key=str(a["id"]))

    def action_ack(self) -> None:
        """Key ``k``: acknowledge the alarm under the cursor (the server logs the user and sends the new list)."""
        t = self.query_one("#al-active", DataTable)
        if not t.row_count:
            return
        key = t.coordinate_to_cell_key(t.cursor_coordinate).row_key.value
        self.scada.run_request("ack", alarm_id=int(key))

    def action_ack_all(self) -> None:
        """Key ``K``: acknowledge all alarms."""
        self.scada.run_request("ack", all=True)


# ============================================================================ Event log
#: Event levels; "feil" is the Norwegian level name used by older databases.
LEVEL_STYLE = {"alarm": "bold red", "error": "bold yellow", "feil": "bold yellow", "info": ""}


class EventView(View):
    """F6: operator actions, alarm transitions and system events."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("slash", "focus_filter", _("Filter"))]

    def compose(self) -> ComposeResult:
        """The filter field above the event table."""
        yield Input(placeholder=_("Filter: user, text, category …  (/)"), id="ev-filter")
        t = FastTable(id="ev-table", cursor_type="row", zebra_stripes=True)
        t.border_title = _("Event log – operation, alarms and system")
        yield t

    def on_mount(self) -> None:
        """Add the table columns; the rows are filled by :meth:`rebuild` when the view is shown."""
        t = self.query_one("#ev-table", DataTable)
        for key, label, width in (("ts", N_("Time"), 14), ("level", N_("Level"), 6), ("kind", N_("Category"), 10),
                                  ("user", N_("User"), 24), ("text", N_("Event"), 80)):
            t.add_column(_(label), key=key, width=width)

    def _match(self, ev: dict) -> bool:
        """True if event *ev* contains every word of the filter field (level, category in English and translated, user,
        text).
        """
        words = self.query_one("#ev-filter", Input).value.lower().split()
        hay = f"{ev['level']} {ev['kind']} {_(ev['kind'])} {ev.get('user') or ''} {ev['text']}".lower()
        return all(w in hay for w in words)

    def _add(self, ev: dict) -> None:
        """Add event *ev* as a row unless its id is already in the table (live and loaded events overlap)."""
        t = self.query_one("#ev-table", DataTable)
        key = str(ev["id"])
        if key in t.rows:
            return
        style = LEVEL_STYLE.get(ev["level"], "")
        t.add_row(dhms(ev["ts"]), Text(_(ev["level"]), style=style), _(ev["kind"]), ev.get("user") or "",
                  Text(ev["text"], style=style), key=key)

    def rebuild(self) -> None:
        """Refill the table from the client's event buffer and put the cursor on the newest row."""
        t = self.query_one("#ev-table", DataTable)
        t.clear()
        for ev in self.scada.events:
            if self._match(ev):
                self._add(ev)
        t.move_cursor(row=t.row_count - 1)

    def on_show_view(self) -> None:
        """Rebuild – events are not added while hidden."""
        self.rebuild()

    def add_event(self, ev: dict) -> None:
        """Append a live event (follow the end if the cursor is at the bottom)."""
        if self.display and self._match(ev):
            t = self.query_one("#ev-table", DataTable)
            follow = t.cursor_row >= t.row_count - 2
            self._add(ev)
            if follow:
                t.move_cursor(row=t.row_count - 1)

    @on(Input.Changed, "#ev-filter")
    def _filter(self) -> None:
        """Filter text changed: rebuild at once."""
        self.rebuild()

    def action_focus_filter(self) -> None:
        """Key ``/``: jump to the filter field."""
        self.query_one("#ev-filter", Input).focus()


# ============================================================================ Trends
#: (English button label, hours).
PERIODS = [(N_("1h"), 1), (N_("6h"), 6), (N_("24h"), 24), (N_("7d"), 168), (N_("30d"), 720)]


#: Prefix of a logged protocol tag in the trend view (``pt:connection/tag``); group addresses have none.
POINT = "pt:"


class TrendView(View):
    """F7: search addresses on the left, up to six curves on the right.

    Logged tags of protocols.yaml (``log``) are in the list too, with the connection as address and the key
    ``pt:conn/tag``. History comes from the server (``history`` request), live values from telegrams and ``points``
    messages; replots are throttled by period (:attr:`LIVE_REPLOT_S`).
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("slash", "focus_search", _("Search")),
        Binding("delete", "clear", _("Remove all curves")),
    ]

    def __init__(self, **kw: Any) -> None:
        """24 hours as step lines and no curves to begin with."""
        super().__init__(**kw)
        self.hours = 24
        self.kind = "step"
        self.series: dict[str, list[tuple[float, float]]] = {}  # GA → points (insertion order = colour)
        self.points: dict[str, dict] = {}   # "pt:conn/tag" → tag info (logged tags, from the server)
        self.rows: set[str] = set()
        self._pending: dict[str, str] = {}  # value text per list row, applied in batches by _flush
        self._dirty = False
        self._last_plot = 0.0  # time of the last replot (live updates are throttled)

    def compose(self) -> ComposeResult:
        """Search field and result list on the left; period and chart-kind buttons, legend and chart on the right."""
        with Horizontal():
            with Vertical(id="tr-side"):
                yield Input(placeholder=_("Search: temp, kitchen, co2, 3/0 …  (/)"), id="tr-search")
                yield FastTable(id="tr-list", cursor_type="row", zebra_stripes=True)
            with Vertical():
                with Horizontal(id="tr-periods"):
                    for label, hours in PERIODS:
                        yield Button(_(label), id=f"p-{hours}", compact=True,
                                     classes="period" + (" -active" if hours == self.hours else ""))
                    yield Static("  ", classes="spacer")
                    for kid, label in CHART_KINDS:
                        yield Button(_(label), id=f"k-{kid}", compact=True,
                                     classes="kind" + (" -active" if kid == self.kind else ""))
                yield Static("", id="tr-series", classes="muted")
                yield CachedPlot(id="tr-plot")

    def on_mount(self) -> None:
        """Collect the plottable addresses, fill the list, ask for the logged tags and start the replot/flush timers."""
        # Only numeric and on/off addresses can be plotted.
        self.candidates = [
            (a, g) for a, g in self.scada.model["gas"].items()
            if g["used"] and not g["placeholder"] and (dptlib.is_numeric(g["dpt"]) or dptlib.split(g["dpt"])[0] == 1)
        ]
        t = self.query_one("#tr-list", DataTable)
        t.add_column("", key="sel", width=1)
        t.add_column(_("Address"), key="addr", width=12)
        t.add_column(_("Name"), key="name", width=30)
        t.add_column(_("Value"), key="value", width=10)
        self.rebuild_list()
        self.load_points()
        self.set_interval(2, self._replot_if_dirty)
        self.set_interval(2, self._flush)
        self.replot()

    # -- search list -----------------------------------------------------------------
    def rebuild_list(self) -> None:
        """Refill the result list with the addresses and logged tags matching all words of the search field."""
        t = self.query_one("#tr-list", DataTable)
        t.clear()
        self.rows.clear()
        self._pending.clear()
        words = self.query_one("#tr-search", Input).value.lower().split()
        for addr, g in self.candidates:
            hay = f"{addr} {g['name']} {g['description']} {g['range']}".lower()
            if all(w in hay for w in words):
                st = self.scada.state.get(addr, {})
                t.add_row(self._mark(addr), addr, g["name"], st.get("text", ""), key=addr)
                self.rows.add(addr)
        for key, p in self.points.items():
            hay = f"{p['key']} {p['description']} {p['unit']}".lower()
            if all(w in hay for w in words):
                t.add_row(self._mark(key), p["conn"], p["description"] or p["name"], self._point_text(p["value"], p),
                          key=key)
                self.rows.add(key)
        t.border_title = _("{n} matches – Enter adds/removes").format(n=len(self.rows))

    @work(exclusive=True, group="trend-points")
    async def load_points(self) -> None:
        """Fetch the logged tags of protocols.yaml (they can be plotted like group addresses).

        Text tags are left out. The list is rebuilt only when the set of tags changed, so it is not cleared (and the
        cursor reset) on every visit.
        """
        if not self.scada.client.connected:
            return              # called at start-up too; on_show_view asks again
        try:
            rows = await self.scada.request("logged_points")
        except Exception:  # noqa: BLE001 - an older server without the command
            return
        points = {POINT + p["key"]: p for p in rows or [] if p.get("type") != "str"}  # text cannot be plotted
        if points.keys() != self.points.keys():
            self.points = points
            self.rebuild_list()

    @staticmethod
    def _point_text(value: Any, p: dict) -> str:
        """Display text of a tag *value* with the unit of tag info *p* (floats with at most two decimals, None → "")."""
        if value is None:
            return ""
        if isinstance(value, float):
            value = f"{value:.2f}".rstrip("0").rstrip(".")
        return f"{value} {p.get('unit') or ''}".strip()

    def on_points(self, values: dict[str, list]) -> None:
        """Live values of protocol tags (``points`` messages: ``conn/tag → [value, ts, quality]``).

        Only good, numeric values are added to a curve; the list text is batched like for telegrams.
        """
        for key, (v, ts, q) in values.items():
            k = POINT + key
            if k in self.points:
                self.points[k]["value"] = v
                if k in self.rows:
                    self._pending[k] = self._point_text(v, self.points[k])
                if k in self.series and q == "good" and isinstance(v, (int, float)):
                    self.series[k].append((ts, float(v)))
                    self._dirty = True

    def _label(self, key: str) -> tuple[str, str, str]:
        """(address, name, unit) of a curve: a group address or a logged tag."""
        if key.startswith(POINT):
            p = self.points.get(key) or {}
            return p.get("conn", ""), p.get("description") or p.get("name") or key[len(POINT):], p.get("unit", "")
        g = self.scada.ga(key)
        return key, g.get("name", ""), dptlib.unit(g.get("dpt"))

    def _color(self, addr: str) -> tuple[int, int, int]:
        """Series colour: position in self.series → palette entry."""
        pal = palette(self.scada)
        return pal[list(self.series).index(addr) % len(pal)]

    def _mark(self, addr: str) -> Text:
        """Coloured ● in the list for addresses that are plotted."""
        if addr not in self.series:
            return Text("")
        r, g, b = self._color(addr)
        return Text("●", style=f"bold rgb({r},{g},{b})")

    def _refresh_marks(self) -> None:
        """Update the colour marks of all rows (after a curve was added or removed, or the theme changed)."""
        t = self.query_one("#tr-list", DataTable)
        for addr in self.rows:
            t.update_cell(addr, "sel", self._mark(addr))

    @on(Input.Changed, "#tr-search")
    def _search(self) -> None:
        """Search text changed: rebuild the list."""
        self.rebuild_list()

    @on(Input.Submitted, "#tr-search")
    def _to_list(self) -> None:
        """Enter in the search field moves the focus to the list."""
        self.query_one("#tr-list", DataTable).focus()

    def on_key(self, event) -> None:
        """Arrow down in the search field moves the focus to the list."""
        # Arrow down from the search field jumps into the result list.
        if event.key == "down" and self.screen.focused is self.query_one("#tr-search", Input):
            self.query_one("#tr-list", DataTable).focus()
            event.stop()

    @on(DataTable.RowSelected, "#tr-list")
    def _toggle(self, event: DataTable.RowSelected) -> None:
        """Enter on a list row adds that curve or removes it."""
        addr = str(event.row_key.value)
        if addr in self.series:
            del self.series[addr]
            self.replot()
        else:
            self.add_series(addr)
        self._refresh_marks()

    # -- curves ----------------------------------------------------------------------------
    def add_series(self, ga: str) -> None:
        """Add a curve (max 6 – the oldest is dropped) and load its history.

        Also called by the app when "Trend" is chosen in the group address dialog.
        """
        if ga not in self.series:
            if len(self.series) >= 6:
                self.series.pop(next(iter(self.series)))
            self.series[ga] = []
            self._refresh_marks()
        self.load()

    @work(exclusive=True, group="trend")
    async def load(self) -> None:
        """Load the history of every curve for the chosen period from the server, then replot.

        An exclusive worker: changing the period again cancels a load still running. A curve removed while loading is
        not brought back.
        """
        for ga in list(self.series):
            if ga.startswith(POINT):
                pts = await self.scada.request("history", point=ga[len(POINT):], hours=self.hours)
            else:
                pts = await self.scada.request("history", ga=ga, hours=self.hours)
            if ga in self.series:
                self.series[ga] = [tuple(p) for p in pts or []]
        self.replot()

    def on_telegram(self, t: dict) -> None:
        """Update the list value and append numeric values to a plotted curve (the replot is throttled)."""
        if t["type"] == "read":
            return
        if t["dst"] in self.rows:
            # Batched: every update_cell re-renders all visible rows of the list (see _flush).
            self._pending[t["dst"]] = t.get("text") or ""
        if t["dst"] in self.series:
            v = t.get("value")
            if isinstance(v, (int, float)):
                self.series[t["dst"]].append((t["ts"], float(v)))
                self._dirty = True  # replotted by _replot_if_dirty (throttled)

    #: Minimum seconds between live replots per period (hours). A pixel column of a
    #: 30-day chart is hours wide, so redrawing it every 2 s only costs CPU.
    LIVE_REPLOT_S = ((1, 2), (6, 5), (24, 15), (10**9, 60))

    def _replot_if_dirty(self) -> None:
        """Every 2 s: replot if new live points arrived, but not more often than :attr:`LIVE_REPLOT_S` allows."""
        if not (self._dirty and self.display):
            return
        min_gap = next(s for hours, s in self.LIVE_REPLOT_S if self.hours <= hours)
        if time.time() - self._last_plot >= min_gap:
            self._dirty = False
            self.replot()

    def on_theme_changed(self) -> None:
        """Recolour the list marks and the chart."""
        self._refresh_marks()
        self.replot()

    def replot(self) -> None:
        """Draw all curves for the chosen period and kind, and update the legend line above the chart.

        Points that scrolled out of the period are dropped from ``series`` here (except the value in force at the
        start), so live curves do not grow without bound.
        """
        self._last_plot = time.time()
        plot = self.query_one(CachedPlot)
        plt = plot.plt
        plt.clear_data()
        plt.clear_figure()
        now = time.time()
        start = now - self.hours * 3600
        series, names, units = [], [], set()
        for ga, pts in self.series.items():
            # Forget live points that have scrolled out of the period (keep the one in force at start).
            first = next((i for i, p in enumerate(pts) if p[0] >= start), len(pts))
            if first > 1:
                del pts[: first - 1]
            addr, name, unit = self._label(ga)
            units.add(unit)
            color = self._color(ga)
            series.append((f"{addr} {name[:30]}", pts, color))
            r, gg, b = color
            n = sum(1 for p in pts if p[0] >= start)
            unit_txt = escape(f"[{unit}]") if unit else ""
            names.append(f"[rgb({r},{gg},{b})]●[/] {escape(addr)} {escape(name)} "
                         f"[dim]{unit_txt} {_('{n} pts').format(n=n)}[/dim]")
        draw(plt, series, self.kind, start, now, plot.size.width)
        units.discard("")
        unit = f"  [{units.pop()}]" if len(units) == 1 else ""  # one common unit: show it in the title
        if self.hours < 48:
            title = _("Last {n} hours").format(n=self.hours)
        else:
            title = _("Last {n} days").format(n=self.hours // 24)
        plt.title(title + unit)
        plot.changed()
        self.query_one("#tr-series", Static).update(
            "   ".join(names) if names else _("No curves selected – search on the left and press Enter on an address.")
        )

    @on(Button.Pressed, ".period")
    def _period(self, event: Button.Pressed) -> None:
        """A period button (``p-<hours>``) was pressed: mark it and reload the history."""
        self.hours = int(str(event.button.id).split("-")[1])
        for b in self.query(".period"):
            b.set_class(b is event.button, "-active")
        self.load()

    @on(Button.Pressed, ".kind")
    def _kind(self, event: Button.Pressed) -> None:
        """A chart-kind button (``k-<kind>``) was pressed: mark it and redraw (the data stays the same)."""
        self.kind = str(event.button.id).split("-", 1)[1]
        for b in self.query(".kind"):
            b.set_class(b is event.button, "-active")
        self.replot()

    def action_clear(self) -> None:
        """Key Delete: remove all curves."""
        self.series.clear()
        self._refresh_marks()
        self.replot()

    def action_focus_search(self) -> None:
        """Key ``/``: jump to the search field."""
        self.query_one("#tr-search", Input).focus()

    def _flush(self) -> None:
        """Apply the collected value changes to the address list (only while visible)."""
        if not self.display or not self._pending:
            return
        pending, self._pending = self._pending, {}
        table = self.query_one("#tr-list", DataTable)
        for addr, text in pending.items():
            if addr in self.rows:
                table.update_cell(addr, "value", text)

    def on_show_view(self) -> None:
        """Apply pending list values, look for new logged tags and reload the curves (history may have grown)."""
        self._flush()
        self.load_points()
        if self.series:
            self.load()
