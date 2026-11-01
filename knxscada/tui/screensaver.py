# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""Screensaver: slowly rotates between information pages.

Started by the app after ``ui.screensaver_minutes`` without input (or with
F9). Pages (``PAGES``; ``ui.screensaver_pages`` picks and orders them): clock
and outdoor, sky with sun/moon and lit windows, home, heat map, temperatures,
ventilation, energy, energy meters, indoor climate, today's records, KNX
traffic, telegram rain and a bouncing clock. Pages without data are skipped.

* **Unacknowledged alarms take over** the whole screen with a slow red blink.
  Acknowledged-but-active alarms are only mentioned in the footer.
* **Burn-in protection:** the content is shifted a few cells at every page
  change, like old CRT screensavers. The animated pages move by themselves.
* Any key or click returns to the normal UI.

The drawings are pure functions (``sun_times``, ``moon_phase``, ``Canvas``,
``heat_color``, ``vent_lines``), so they are tested without a terminal.
"""

from __future__ import annotations

import math
import random
import re
import time
from collections import Counter
from typing import TYPE_CHECKING, Any

from textual import events, work
from textual.app import ComposeResult
from textual.markup import escape
from textual.screen import Screen
from textual.widgets import Static

from ..alarms import is_critical
from ..i18n import N_, _
from .graphics import bar, big_text, resample, sparkline
from .home import HomeModel
from .systems import Value, Vent, fmt, on, parse_systems

if TYPE_CHECKING:
    from collections.abc import Callable

    from .app import ScadaApp

#: Weekday and month names in English; translated when shown.
DAYS = [N_("Monday"), N_("Tuesday"), N_("Wednesday"), N_("Thursday"), N_("Friday"), N_("Saturday"), N_("Sunday")]
MONTHS = [N_("January"), N_("February"), N_("March"), N_("April"), N_("May"), N_("June"), N_("July"), N_("August"),
          N_("September"), N_("October"), N_("November"), N_("December")]

#: Every page: key → English name, in the default order (moving pages spread out between the calm ones).
PAGES = {
    "clock": N_("Clock"), "home": N_("Home"), "sky": N_("Sky"), "temps": N_("Temperatures"),
    "ventilation": N_("Ventilation"), "heatmap": N_("Heat map"), "energy": N_("Energy"),
    "meters": N_("Energy meters"), "rain": N_("Telegram rain"), "air": N_("Indoor climate"),
    "records": N_("Today"), "knx": N_("KNX"), "bounce": N_("Bouncing clock"),
}

#: Pages that move: redrawn twice a second (more traffic over a slow SSH link – leave them out there).
ANIMATED = {"ventilation", "rain", "bounce"}

#: Default position for the sun and moon (Fredrikstad) when ``ui.latitude``/``ui.longitude`` are not set.
LATITUDE, LONGITUDE = 59.22, 10.93

#: A known new moon (2000-01-06 18:14 UTC) and the mean length of a lunar month in days.
NEW_MOON = 947182440
SYNODIC = 29.530588853

#: Heat map colours by deviation from the setpoint (or from 21 °C), −3 … +3 K: cold blue → green → hot red.
HEAT_COLORS = ("#1d4ed8", "#3b82f6", "#06b6d4", "#16a34a", "#ca8a04", "#ea580c", "#dc2626")


def _num(v) -> float | None:
    """The value as float if it is a number (bools excluded), else None."""
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def sun_times(day_of_year: int, lat: float, lon: float, tz_hours: float) -> tuple[float, float] | None:
    """Sunrise and sunset in local hours (NOAA approximation, within a few minutes).

    None during the polar night, ``(0, 24)`` under the midnight sun.
    """
    g = 2 * math.pi / 365 * (day_of_year - 1)    # fractional year (NOAA)
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
            + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    phi = math.radians(lat)
    cos_h = (math.sin(math.radians(-0.833)) - math.sin(phi) * math.sin(decl)) / (math.cos(phi) * math.cos(decl))
    if cos_h > 1:
        return None
    if cos_h < -1:
        return 0.0, 24.0
    half = math.degrees(math.acos(cos_h)) / 15
    eot = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g) - 0.014615 * math.cos(2 * g)
                    - 0.040849 * math.sin(2 * g))     # equation of time, minutes
    noon = 12 - lon / 15 + tz_hours - eot / 60
    return noon - half, noon + half


def moon_phase(ts: float) -> float:
    """Age of the moon as a fraction of the lunar month: 0 = new, 0.5 = full."""
    return ((ts - NEW_MOON) / 86400 / SYNODIC) % 1.0


def moon_glyph(phase: float, ascii_only: bool = False) -> tuple[str, str]:
    """(character, English name) of the moon for *phase*."""
    if phase < 0.0625 or phase >= 0.9375:
        return ("o" if ascii_only else "○"), N_("new moon")
    if phase < 0.4375:
        return (")" if ascii_only else "◑"), N_("waxing moon")
    if phase < 0.5625:
        return ("O" if ascii_only else "●"), N_("full moon")
    return ("(" if ascii_only else "◐"), N_("waning moon")


def heat_color(temp: float, setpoint: float | None) -> str:
    """Background colour of a heat map tile: the deviation from the setpoint (or 21 °C) in whole kelvins."""
    d = temp - (setpoint if setpoint is not None else 21.0)
    return HEAT_COLORS[max(-3, min(3, round(d))) + 3]


def hhmm(hours: float) -> str:
    """Hours as a clock time: 7.5 → "07:30" (wraps at midnight, for sunrise/sunset)."""
    hours %= 24
    return f"{int(hours):02d}:{round(hours % 1 * 60) % 60:02d}"


class Canvas:
    """A grid of characters with a style each, rendered as Textual markup (one tag per run of equal style)."""

    def __init__(self, width: int, height: int) -> None:
        self.w, self.h = max(1, width), max(1, height)
        self.cells = [[(" ", "")] * self.w for _ in range(self.h)]

    def put(self, x: int, y: int, text: str, style: str = "") -> None:
        """Write *text* from (x, y); what falls outside is cut."""
        if not 0 <= y < self.h:
            return
        for i, ch in enumerate(text):
            if 0 <= x + i < self.w:
                self.cells[y][x + i] = (ch, style)

    def get(self, x: int, y: int) -> str:
        """The character at (x, y); "" outside the canvas (so callers need not check the edges)."""
        return self.cells[y][x][0] if 0 <= y < self.h and 0 <= x < self.w else ""

    def markup(self) -> str:
        """The canvas as Textual markup: one tag per run of equal style, keeps the markup short to parse."""
        lines = []
        for row in self.cells:
            out: list[str] = []
            run, style = "", ""
            for ch, st in row:
                if st != style and run:
                    out.append(f"[{style}]{escape(run)}[/]" if style else escape(run))
                    run = ""
                style = st
                run += ch
            if run:
                out.append(f"[{style}]{escape(run)}[/]" if style else escape(run))
            lines.append("".join(out))
        return "\n".join(lines)


def _duct(n: int, t: int, right: bool, moving: bool, ascii_only: bool) -> str:
    """A duct of *n* characters with arrows that walk along it while the air moves."""
    line, arrow = ("-", ">" if right else "<") if ascii_only else ("─", "›" if right else "‹")
    if not moving:
        return line * n
    return "".join(arrow if ((i - t) if right else (i + t)) % 4 == 0 else line for i in range(n))


def vent_lines(s: Vent, get: Callable[[str | None], Value | None], t: int, ascii_only: bool = False) -> list[str]:
    """Three lines of markup for one air handling unit: name/state, supply air (→) and extract air (←).

    *t* is the animation step: arrows move along the ducts and the fans turn while the unit runs.
    """
    def v(role: str, decimals: int = 1) -> str:
        return fmt(get(s.key(role)), decimals)

    def level(role: str) -> float | None:
        val = get(s.key(role))
        return _num(val[0]) if val else None

    running = on(get(s.key("running")))
    alarm = on(get(s.key("alarm")))
    sf, ef = level("supply_fan"), level("extract_fan")
    supply_on = bool(on(get(s.key("supply_fan_run"))) or (sf or 0) > 0 or (running and sf is None))
    extract_on = bool(on(get(s.key("extract_fan_run"))) or (ef or 0) > 0 or (running and ef is None))
    fans = "|/-\\" if ascii_only else "◐◓◑◒"

    def fan(turning: bool, role: str, reverse: bool = False) -> str:
        glyph = fans[(-t if reverse else t) % 4] if turning else fans[0]
        return f"[b]({glyph} {v(role, 0):>5})[/b]"

    def box(icon: str, role: str, style: str = "") -> str:
        text = f"[{icon} {v(role, 0):>5}]"
        return f"[{style}]{escape(text)}[/]" if style else escape(text)

    heat = level("heating")
    rec_icon, heat_icon, filt_icon = ("R", "H", "F") if ascii_only else ("⟳", "♨", "▥")
    filt = get(s.key("filter_supply"))
    filt_text = escape(f"[{filt_icon} {('!' if on(filt) else 'ok') if filt and isinstance(filt[0], bool) else fmt(filt, 0):>5}]")
    if filt and isinstance(filt[0], bool) and filt[0]:
        filt_text = f"[$warning]{filt_text}[/]"
    blank = "─" * 9 if not ascii_only else "-" * 9

    def sduct(n: int = 4) -> str:
        return f"[$accent]{_duct(n, t, True, supply_on, ascii_only)}[/]"

    def educt(n: int = 4) -> str:
        return f"[$warning]{_duct(n, t, False, extract_on, ascii_only)}[/]"

    state = (f"[$error b]⚠ {_('ALARM')}[/]" if alarm else
             f"[$success]● {_('running')}[/]" if running else
             f"[$text-muted]○ {_('stopped')}[/]" if running is False else "")
    mode = get(s.key("mode"))
    mode_text = ""
    if mode and mode[0] is not None:
        mode_text = s.mode_texts.get(int(mode[0]), fmt(mode, 0)) if isinstance(mode[0], (int, float)) else fmt(mode)
    head = "  ".join(x for x in (f"[b]{escape(s.name)}[/b]", state,
                                 f"[$text-muted]{escape(mode_text)}[/]" if mode_text else "") if x)
    supply = (f"{escape(v('outdoor')):>9} {sduct()}{filt_text}{sduct()}{box(rec_icon, 'recovery')}{sduct()}"
              f"{box(heat_icon, 'heating', '$error' if (heat or 0) > 0 else '')}{sduct()}{fan(supply_on, 'supply_fan')}"
              f"{sduct()} [b]{escape(v('supply'))}[/b] [$text-muted]{_('supply')}[/]")
    extract = (f"{escape(v('exhaust')):>9} {educt()}{educt(9)}{educt()}{box(rec_icon, 'recovery')}{educt()}"
               f"[$warning]{blank}[/]{educt()}{fan(extract_on, 'extract_fan', True)}"
               f"{educt()} [b]{escape(v('extract'))}[/b] [$text-muted]{_('extract')}[/]")
    return [head, supply, extract]


class ScreenSaver(Screen):
    """Full-screen information display. Styling lives in app.tcss (ScreenSaver, #ss-body, #ss-foot)."""

    def __init__(self) -> None:
        super().__init__()
        self.page = 0
        self.last_switch = time.time()
        self._shown: dict[str | None, str] = {}  # widget id → markup last shown
        self.step = 0                             # animation step (half seconds)
        self.values: dict[str, Value] = {}        # "conn/tag" → value of the units and meters in ui.systems
        self.series: dict[str, list[tuple[float, float]]] = {}  # 24 h history of the meters' power
        self._series_at = 0.0
        self._drops: list[list[Any]] = []         # telegram rain: [x, y, speed, text]
        self._rain_seen = time.time() - 60
        self._ball = [2.0, 1.0, 1.0, 0.5, 0]      # bouncing clock: x, y, dx, dy, colour index

    @property
    def scada(self) -> ScadaApp:
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        yield Static("", id="ss-body")
        yield Static("", id="ss-foot")

    def on_mount(self) -> None:
        vents, meters, _problems = parse_systems(self.scada.ui.get("systems"))
        self.vents = [v for v in vents if v.kind == "ventilation"]
        self.meters = meters
        self.set_interval(0.5, self._tick)
        self.set_interval(15, self._load)
        self._load()
        self._tick()

    @work(exclusive=True, group="screensaver")
    async def _load(self) -> None:
        """Values of the units and meters in ``ui.systems``: polled, the screensaver is shown now and then."""
        app = self.scada
        if not (self.vents or self.meters) or not app.client.connected:
            return
        for conn in dict.fromkeys([v.conn for v in self.vents] + [m.conn for m in self.meters]):
            for p in await app.request("points", conn=conn, limit=5000) or []:
                self.values[f"{conn}/{p['name']}"] = (p.get("value"), p.get("unit") or "", p.get("q") or "")
        if time.time() - self._series_at > 300:
            self._series_at = time.time()
            for key in [k for m in self.meters if (k := m.key("power"))]:
                pts = await app.request("history", point=key, hours=24, max_points=300) or []
                self.series[key] = [(float(p[0]), float(p[1])) for p in pts if p[1] is not None]

    # -- events ------------------------------------------------------------------------
    def on_key(self, event: events.Key) -> None:
        event.stop()  # the key only wakes the UI; it must not trigger an action
        self.dismiss()

    def on_click(self) -> None:
        self.dismiss()

    # -- pages -------------------------------------------------------------------------
    def _available(self) -> dict[str, Callable[[], str]]:
        """Page key → render function for every page that has data."""
        home = self.scada.home
        temps = any(r.temp_ga for r in home.rooms)
        pages: dict[str, Any] = {
            "clock": self.page_clock, "home": self.page_home, "sky": self.page_sky,
            "temps": self.page_temps, "ventilation": self.page_vent if self.vents else None,
            "heatmap": self.page_heatmap if temps else None,
            "energy": self.page_energy if home.power_ga else None,
            "meters": self.page_meters if self.meters else None, "rain": self.page_rain,
            "air": self.page_air if home.co2 or home.humidity else None,
            "records": self.page_records, "knx": self.page_knx, "bounce": self.page_bounce,
        }
        return {k: f for k, f in pages.items() if f is not None}

    def _pages(self) -> list[tuple[str, Callable[[], str]]]:
        """(English page name, render function) in the order of ``ui.screensaver_pages`` (default: all)."""
        available = self._available()
        wanted = self.scada.ui.get("screensaver_pages")
        keys = [str(k) for k in wanted] if isinstance(wanted, list) and wanted else list(PAGES)
        pages = [(PAGES[k], available[k]) for k in keys if k in available]
        return pages or [(PAGES["clock"], self.page_clock)]

    def _tick(self) -> None:
        """Twice a second: rotate pages, handle alarms, refresh the content (the clock and animations move)."""
        app = self.scada
        self.step += 1
        body = self.query_one("#ss-body", Static)
        unacked = [a for a in app.alarms if a["acked_ts"] is None]
        acked_active = [a for a in app.alarms if a["acked_ts"] is not None and a["cleared_ts"] is None]
        rotate = float(app.ui.get("screensaver_rotate_s", 20))
        pages = self._pages()
        if time.time() - self.last_switch > rotate:
            self.last_switch = time.time()
            self.page = (self.page + 1) % len(pages)
            body.styles.offset = (random.randint(-6, 6), random.randint(-2, 2))  # burn-in protection
        current = self.page % len(pages)
        if unacked:
            self.add_class("alarm")
            self.set_class(int(time.time()) % 2 == 0, "blink")
            self._set(body, self.page_alarm(unacked))
        else:
            self.remove_class("alarm", "blink")
            self._set(body, pages[current][1]())
        dots = "  ".join(("●" if i == current else "○") for i in range(len(pages)))
        extra = ""
        if acked_active:
            extra = "   ·   [$warning]⚠ " + _("{n} active alarms (acknowledged)").format(n=len(acked_active)) + "[/]"
        self._set(self.query_one("#ss-foot", Static),
                  f"{dots}    {_(pages[current][0])}  ·  {_('press any key to return')}{extra}")

    def _set(self, widget: Static, markup: str) -> None:
        """Update *widget* only when its text changed (the page is rebuilt twice a second)."""
        if self._shown.get(widget.id) != markup:
            self._shown[widget.id] = markup
            widget.update(markup)

    def _area(self) -> tuple[int, int]:
        """Width and height for a drawing: the screen minus padding, footer and room for the burn-in shift."""
        return max(40, self.size.width - 8 - 14), max(10, self.size.height - 1 - 2 - 6)

    def _outdoor(self) -> tuple[float | None, list[tuple[float, float]]]:
        """Outdoor temperature and its 24 h history: the KNX address, else the outdoor sensor of a unit (the app
        registers it as ``pt:conn/tag``)."""
        app = self.scada
        ga = app.home.outdoor_ga
        return _num(HomeModel.value(app.state, ga)), app.history.get(ga or "", [])

    def _big(self, text: str, color: str = "$primary") -> str:
        """A large value: big block digits if enabled, otherwise bold text."""
        if not self.scada.big_digits:
            return f"[{color} b]{escape(text)}[/]"
        return "\n".join(f"[{color}]{escape(line)}[/]" for line in big_text(text, self.scada.ascii))

    def _spark(self, ga: str | None, width: int, hours: float = 24) -> str:
        if not ga:
            return ""
        return self._spark_of(self.scada.history.get(ga, []), width, hours)

    def _spark_of(self, points: list[tuple[float, float]], width: int, hours: float = 24) -> str:
        """Sparkline of *points* over the last *hours* with *width* characters ("" without data)."""
        now = time.time()
        return sparkline(resample(points, now - hours * 3600, now, width), self.scada.ascii) if points else ""

    def page_clock(self) -> str:
        """Clock, date and outdoor temperature with a 24 h sparkline."""
        now = time.localtime()
        out = [self._big(time.strftime("%H:%M", now), "$primary"), "", f"[b]{self._date(now)}[/b]", ""]
        t, hist = self._outdoor()
        if t is not None:
            pts = [p[1] for p in hist if p[0] > time.time() - 86400]
            rng = _("min {lo}°  max {hi}°").format(lo=f"{min(pts):.1f}", hi=f"{max(pts):.1f}") if pts else ""
            out += [f"[$text-muted]{_('OUTDOOR')}[/]", self._big(f"{t:.1f}°", "$accent"), "",
                    f"[$primary]{self._spark_of(hist, 48)}[/]",
                    f"[$text-muted]{_('last 24 hours')}   {rng}[/]"]
        return "\n".join(out)

    @staticmethod
    def _date(now: time.struct_time) -> str:
        """ "Sunday 1 November 2026", translated (weekday and month names from DAYS/MONTHS)."""
        return _("{weekday} {day} {month} {year}").format(
            weekday=_(DAYS[now.tm_wday]), day=now.tm_mday, month=_(MONTHS[now.tm_mon - 1]), year=now.tm_year)

    def page_home(self) -> str:
        """One line per room: temperature, heating, lights, alarm."""
        app = self.scada
        home = app.home
        lines = [f"[b]{_('HOME NOW')}[/b]", ""]
        total_on = 0
        for r in home.rooms:
            st = home.room_status(r, app.state, app.alarms)
            if st["temp"] is None and not r.lights:
                continue
            total_on += st["lights_on"]
            temp = f"{st['temp']:5.1f}°" if st["temp"] is not None else "    – "
            if st["lights_total"]:
                lit = (f"[$accent]● {st['lights_on']}/{st['lights_total']}[/]" if st["lights_on"]
                       else f"[$text-muted]○ 0/{st['lights_total']}[/]")
            else:
                lit = "     "
            heat = "[$warning]♨[/]" if st["heating"] else " "
            flag = "[$error b]![/]" if st["alarm"] else " "
            lines.append(f"{flag} {escape(r.name):<20} [b]{temp}[/b]  {heat}  {lit}")
        lines += ["", f"[$text-muted]{_('{n} lights on').format(n=total_on)}[/]"]
        return "\n".join(lines)

    def page_temps(self) -> str:
        """Bars per room from 15 to 26 °C with a marker at the setpoint."""
        app = self.scada
        home = app.home
        lo, hi, width = 15.0, 26.0, 34
        lines = [f"[b]{_('TEMPERATURES')}[/b]", f"[$text-muted]{'':22}{lo:<6.0f}{'':{width - 10}}{hi:>4.0f} °C[/]"]
        for r in home.rooms:
            st = home.room_status(r, app.state, app.alarms)
            if st["temp"] is None:
                continue
            frac = (st["temp"] - lo) / (hi - lo)
            b = list(bar(frac, width, app.ascii))
            sp = _num(st["sp"])
            if sp is not None:
                pos = int(max(0, min(width - 1, (sp - lo) / (hi - lo) * width)))
                b[pos] = "|" if app.ascii else "┃"
            color = "$warning" if sp is not None and st["temp"] < sp - 1.5 else ("$error" if st["temp"] > 27 else "$primary")
            lines.append(f"{escape(r.name):<20}  [{color}]{escape(''.join(b))}[/] [b]{st['temp']:5.1f}°[/b]")
        lines += ["", f"[$text-muted]┃ = {_('setpoint')}[/]"]
        return "\n".join(lines)

    def page_energy(self) -> str:
        """Current power, bar relative to the 24 h peak, sparkline and meter reading."""
        app = self.scada
        home = app.home
        pw = _num(HomeModel.value(app.state, home.power_ga))
        lines = [f"[b]{_('ENERGY')}[/b]", ""]
        if pw is None:
            return "\n".join(lines + [f"[$text-muted]{_('no data from the power meter')}[/]"])
        pts = [p[1] for p in app.history.get(home.power_ga, []) if p[0] > time.time() - 86400]
        peak = max(pts + [pw, 1.0])
        lines += [self._big(f"{pw:.0f}", "$accent") + "  W", "",
                  f"[$primary]{escape(bar(pw / peak, 48, app.ascii))}[/]",
                  f"[$text-muted]{_('now / peak last 24 h ({peak} W)').format(peak=f'{peak:.0f}')}[/]", "",
                  f"[$primary]{self._spark(home.power_ga, 48)}[/]", f"[$text-muted]{_('last 24 hours')}[/]"]
        if home.energy_ga:
            lines += ["", f"{_('Meter:')} [b]{escape(HomeModel.text(app.state, home.energy_ga))}[/b]"]
        return "\n".join(lines)

    def page_air(self) -> str:
        """CO₂ (green < 1000, yellow < 1400, red above) and humidity bars."""
        app = self.scada
        home = app.home
        lines = [f"[b]{_('INDOOR CLIMATE')}[/b]", ""]
        for ga, name in home.co2:
            v = _num(HomeModel.value(app.state, ga))
            if v is None:
                continue
            color = "$error" if v > 1400 else ("$warning" if v > 1000 else "$primary")
            lines.append(f"{escape(name[:30]):<30} [{color}]{escape(bar((v - 400) / 1200, 24, app.ascii))}[/] [b]{v:5.0f}[/b] ppm")
        if home.humidity:
            lines.append("")
            for ga, name in home.humidity:
                v = _num(HomeModel.value(app.state, ga))
                if v is None:
                    continue
                lines.append(f"{escape(name[:30]):<30} [$primary]{escape(bar(v / 100, 24, app.ascii))}[/] [b]{v:5.0f}[/b] %")
        return "\n".join(lines)

    def page_knx(self) -> str:
        """Connection state, telegram rate, traffic sparkline and the last telegrams."""
        app = self.scada
        st = app.status
        per_s = app.stats.get("per_min", 0) / 60
        status = f"[$success]● {_('connected')}[/]" if st.get("connected") else f"[$error b]● {_('disconnected')}[/]"
        lines = [f"[b]{_('KNX TRAFFIC')}[/b]  " + status, "", self._big(f"{per_s:.1f}", "$accent") + "  " + _("telegrams/s"), ""]
        if app.traffic:
            lines += [f"[$primary]{sparkline(app.traffic, app.ascii)}[/]",
                      f"[$text-muted]{_('telegrams per minute, last hour')}[/]", ""]
        for t in [t for t in app.telegrams if t["type"] != "read"][-6:]:
            name = app.ga(t["dst"]).get("name", "")
            lines.append(f"[$text-muted]{time.strftime('%H:%M:%S', time.localtime(t['ts']))}[/]  {t['dst']:<8} "
                         f"{escape(name[:34]):<34} [b]{escape(t.get('text') or '')}[/b]")
        return "\n".join(lines)

    # -- the newer pages: drawings ---------------------------------------------------------
    def page_sky(self) -> str:
        """The sky over the buildings: the sun (or the moon and stars) on its arc, one window per room, lit when a
        light in the room is on."""
        app = self.scada
        a = app.ascii
        w, h = self._area()
        w, h = min(w, 110), min(h, 24)
        c = Canvas(w, h)
        now = time.time()
        lt = time.localtime(now)
        hour = lt.tm_hour + lt.tm_min / 60
        lat = float(app.ui.get("latitude", LATITUDE))
        lon = float(app.ui.get("longitude", LONGITUDE))
        times = sun_times(lt.tm_yday, lat, lon, lt.tm_gmtoff / 3600)
        # Buildings first: their height decides how much sky there is.
        buildings = self._buildings()
        bh = max((len(rows) for _name, rows in buildings), default=0) + 2
        ground = h - 1
        sky_h = max(3, ground - bh)
        day = times is not None and times[0] <= hour <= times[1]
        if day:
            rise, sset = times  # type: ignore[misc]
            frac = (hour - rise) / max(0.1, sset - rise)
            x = int(2 + frac * (w - 5))
            y = round(sky_h - 1 - math.sin(math.pi * frac) * (sky_h - 1))
            c.put(x, y, "*" if a else "●", "$warning b")
            if not a:
                for dx, dy, ray in ((-2, 0, "─"), (2, 0, "─"), (0, -1, "│"), (0, 1, "│")):
                    if y + dy < sky_h:
                        c.put(x + dx, y + dy, ray, "$warning")
        else:
            rnd = random.Random(int(now // 86400))
            for i in range(w * sky_h // 18):
                sx, sy = rnd.randrange(w), rnd.randrange(sky_h)
                twinkle = (self.step // 4 + i) % 7 == 0
                c.put(sx, sy, ("+" if twinkle else ".") if a else ("✦" if twinkle else "·"),
                      "$foreground" if twinkle else "$text-muted")
            glyph, _name = moon_glyph(moon_phase(now), a)
            # The moon follows the night from sunset to sunrise.
            if times:
                night = 24 - (times[1] - times[0])
                frac = ((hour - times[1]) % 24) / max(0.1, night)
            else:
                frac = hour / 24
            x = int(2 + frac * (w - 5))
            y = round(sky_h - 1 - math.sin(math.pi * frac) * (sky_h - 1))
            c.put(x, y, glyph, "$foreground b")
        # Buildings side by side on the ground.
        total = sum(len(rows[0]) if rows else 0 for _n, rows in buildings) + 4 * max(0, len(buildings) - 1)
        x = max(0, (w - total) // 2)
        for name, rows in buildings:
            bw = len(rows[0]) if rows else 0
            top = ground - len(rows) - 1
            c.put(x, top, ("_" if a else "▄") * bw, "$primary")
            for i, row in enumerate(rows):
                for j, (ch, style) in enumerate(row):
                    c.put(x + j, top + 1 + i, ch, style)
            c.put(x + max(0, (bw - len(name)) // 2), ground, name[:bw], "$text-muted")
            x += bw + 4
        for gx in range(w):
            if c.get(gx, ground) == " ":
                c.put(gx, ground, "_" if a else "▔", "$text-muted")
        # Text in the sky: time, sun or moon, outdoor temperature.
        c.put(1, 0, time.strftime("%H:%M", lt), "b")
        if times is None:
            info = _("polar night")
        elif times == (0.0, 24.0):
            info = _("midnight sun")
        else:
            info = _("sunrise {rise} · sunset {set}").format(rise=hhmm(times[0]), set=hhmm(times[1]))
        if not day:
            info += "  ·  " + _(moon_glyph(moon_phase(now), a)[1])
        c.put(8, 0, info, "$text-muted")
        t, _hist = self._outdoor()
        if t is not None:
            text = f"{_('Outdoor')} {t:.1f} °C"
            c.put(w - len(text) - 1, 0, text, "$accent b")
        lit = sum(1 for r in app.home.rooms if any(app.state.get(light["status"] or "", {}).get("value") is True
                                                    for light in r.lights))
        return f"[b]{_('SKY')}[/b]   [$text-muted]{_('{n} rooms with the light on').format(n=lit)}[/]\n\n" + c.markup()

    def _buildings(self) -> list[tuple[str, list[list[tuple[str, str]]]]]:
        """(name, rows of window cells) per building: the floors stacked, lowest floor at the bottom.

        A window is a room with lights or a temperature: lit when a light is on, red on an alarm."""
        app = self.scada
        a = app.ascii
        rooms = {r.id: r for r in app.home.rooms}
        main = str(app.ui.get("building") or _("Main building"))
        by_building: dict[str, list[tuple[float, list]]] = {}
        for i, floor in enumerate(app.model["floors"]):
            rs = [rooms[r["id"]] for r in floor["rooms"] if r["id"] in rooms
                  and (rooms[r["id"]].lights or rooms[r["id"]].temp_ga)]
            if not rs:
                continue
            m = re.search(r"(-?\d+)", floor["name"])
            level = float(m.group(1)) if m else -100 + i * 0.01   # unnumbered floors (e.g. "outdoor") at the bottom
            by_building.setdefault(floor.get("building") or main, []).append((level, rs))
        out = []
        width = max(12, min(40, (self._area()[0] - 4 * max(0, len(by_building) - 1)) // max(1, len(by_building)) - 2))
        per_row = max(4, width // 2)
        for name, floors in by_building.items():
            rows: list[list[tuple[str, str]]] = []
            for _level, rs in sorted(floors, key=lambda f: -f[0]):
                for k in range(0, len(rs), per_row):
                    cells = []
                    for r in rs[k:k + per_row]:
                        st = app.home.room_status(r, app.state, app.alarms)
                        if st["alarm"]:
                            cells.append(("#" if a else "█", "$error"))
                        elif st["lights_on"]:
                            cells.append(("#" if a else "█", "$warning"))
                        else:
                            cells.append(("." if a else "▒", "$text-muted"))
                    rows.append(cells)
            bw = max(len(r) for r in rows) * 2 + 3
            drawn = []
            for cells in rows:
                line = [("|" if a else "▌", "$primary"), (" ", "")]
                for cell in cells:
                    line += [cell, (" ", "")]
                line += [(" ", "")] * (bw - 1 - len(line)) + [("|" if a else "▐", "$primary")]
                drawn.append(line)
            out.append((name, drawn))
        return out

    def page_heatmap(self) -> str:
        """Every room as a tile coloured by how far it is from its setpoint, floor by floor."""
        app = self.scada
        w, _h = self._area()
        tile = 19
        per_row = max(1, (w + 1) // (tile + 1))
        lines = [f"[b]{_('HEAT MAP')}[/b]  [$text-muted]{_('deviation from setpoint')}[/]", ""]
        rooms = {r.id: r for r in app.home.rooms}
        main = str(app.ui.get("building") or _("Main building"))
        several = len({f.get("building") or main for f in app.model["floors"]}) > 1
        for floor in app.model["floors"]:
            tiles = []
            for r in floor["rooms"]:
                room = rooms.get(r["id"])
                if room is None or not room.temp_ga:
                    continue
                st = app.home.room_status(room, app.state, app.alarms)
                if st["temp"] is None:
                    continue
                name = room.name.split(" · ")[0]          # "Erik · RT6201": the regulator code does not fit
                text = f" {name[:11]:<11} {st['temp']:4.1f}°"[:tile].ljust(tile)
                style = f"#000000 on {heat_color(st['temp'], _num(st['sp']))}" + (" i" if "temp" in st["old"] else "")
                tiles.append(f"[{style}]{escape(text)}[/]")
            if not tiles:
                continue
            title = f"{floor.get('building') or main} · {_(floor['name'])}" if several else _(floor["name"])
            lines.append(f"[$text-muted]{escape(title)}[/]")
            for k in range(0, len(tiles), per_row):
                lines.append(" ".join(tiles[k:k + per_row]))
            lines.append("")
        legend = " ".join(f"[#000000 on {col}] {d:+d} [/]" if d else f"[#000000 on {col}]  0 [/]"
                          for d, col in zip(range(-3, 4), HEAT_COLORS))
        lines.append(f"{legend}  [$text-muted]K[/]")
        return "\n".join(lines)

    def page_vent(self) -> str:
        """Every air handling unit as a line drawing: the air moves along the ducts and the fans turn."""
        lines = [f"[b]{_('VENTILATION')}[/b]", ""]
        for s in self.vents:
            lines += vent_lines(s, lambda k: self.values.get(k) if k else None, self.step, self.scada.ascii) + [""]
        return "\n".join(lines)

    def page_meters(self) -> str:
        """All energy meters: power now as a bar against the largest, 24 h curve and the counter."""
        app = self.scada
        lines = [f"[b]{_('ENERGY METERS')}[/b]", ""]
        rows = []
        for m in self.meters:
            p = self.values.get(m.key("power") or "")
            rows.append((m, p, _num(p[0]) if p else None))
        peak = max([abs(v) for _m, _p, v in rows if v is not None] + [1e-9])
        width = max(len(m.name) for m in self.meters)
        units = {p[1] for _m, p, v in rows if p and v is not None}
        for m, p, v in rows:
            b = bar(abs(v) / peak, 20, app.ascii) if v is not None else " " * 20
            spark = self._spark_of(self.series.get(m.key("power") or "", []), 24)
            lines.append(f"{escape(m.name):<{width}}  [$primary]{escape(b)}[/] [b]{fmt(p, 2):>10}[/b]  "
                         f"[$accent]{escape(spark):<24}[/]  [$text-muted]{fmt(self.values.get(m.key('energy') or ''), 0)}[/]")
        known = [v for _m, _p, v in rows if v is not None]
        if len(known) > 1 and len(units) == 1:
            lines += ["", f"{_('Total'):<{width}}  {'':20} [b]{sum(known):>8.2f} {units.pop()}[/b]"]
        lines += ["", f"[$text-muted]{_('power now · last 24 hours · counter')}[/]"]
        return "\n".join(lines)

    def page_records(self) -> str:
        """Today in numbers: warmest and coldest room, outdoor min/max, lights, the busiest addresses."""
        app = self.scada
        now = time.time()
        lines = [f"[b]{_('TODAY')}[/b]  [$text-muted]{self._date(time.localtime(now))}[/]", ""]
        temps = []
        lights_on = lights = 0
        for r in app.home.rooms:
            st = app.home.room_status(r, app.state, app.alarms)
            lights_on += st["lights_on"]
            lights += st["lights_total"]
            if st["temp"] is not None:
                temps.append((st["temp"], r.name))
        if temps:
            hi, lo = max(temps), min(temps)
            lines.append(f"{_('Warmest room'):<22} [$error b]{hi[0]:5.1f}°[/]  {escape(hi[1])}")
            lines.append(f"{_('Coldest room'):<22} [$accent b]{lo[0]:5.1f}°[/]  {escape(lo[1])}")
            lines.append(f"{_('Average indoors'):<22} [b]{sum(t for t, _n in temps) / len(temps):5.1f}°[/]")
        t, hist = self._outdoor()
        pts = [p[1] for p in hist if p[0] > now - 86400]
        if t is not None:
            rng = f"  [$text-muted]({_('min {lo}°  max {hi}°').format(lo=f'{min(pts):.1f}', hi=f'{max(pts):.1f}')})[/]" \
                if pts else ""
            lines.append(f"{_('Outdoor'):<22} [b]{t:5.1f}°[/]{rng}")
        if lights:
            lines.append(f"{_('Lights on'):<22} [$warning b]{lights_on:5d}[/]  / {lights}")
        active = [a for a in app.alarms if a["cleared_ts"] is None]
        lines.append(f"{_('Active alarms'):<22} [{'$error' if active else '$success'} b]{len(active):5d}[/]")
        recent = [t for t in app.telegrams if t["ts"] > now - 3600 and t["type"] != "read"]
        if recent:
            lines += ["", (f"[b]{_('Busiest addresses last hour')}[/b]  [$text-muted]"
                          f"{_('{n} telegrams').format(n=len(recent))}[/]")]
            top = Counter(t["dst"] for t in recent).most_common(5)
            most = top[0][1]
            for dst, n in top:
                name = app.ga(dst).get("name", "")
                lines.append(f"{dst:<9} {escape(name[:30]):<30} [$primary]{escape(bar(n / most, 16, app.ascii))}[/] {n:4d}")
        return "\n".join(lines)

    # -- the newer pages: animations -------------------------------------------------------
    def page_rain(self) -> str:
        """Telegrams falling down the screen like digital rain: the address and value of each new telegram."""
        app = self.scada
        a = app.ascii
        w, h = self._area()
        w, h = min(w, 120), min(h, 30)
        new = [t for t in app.telegrams if t["ts"] > self._rain_seen and t["type"] != "read"]
        if new:
            self._rain_seen = new[-1]["ts"]
        used = {int(d[0]) for d in self._drops if d[1] - len(d[3]) < 2}
        free = [x for x in range(0, w, 2) if x not in used]
        rnd = random.Random()
        for t in new[-max(1, w // 6):]:
            if not free:
                break
            x = free.pop(rnd.randrange(len(free)))
            self._drops.append([x, 0.0, rnd.choice((1.0, 1.5, 2.0)), f"{t['dst']} {t.get('text') or ''}".strip()])
        # Quiet bus: some random rain so the page is never empty.
        while len(self._drops) < w // 5 and free:
            x = free.pop(rnd.randrange(len(free)))
            text = "".join(rnd.choice("0123456789ABCDEF/.") for _i in range(rnd.randint(4, 12)))
            self._drops.append([x, float(-rnd.randint(0, h)), rnd.choice((1.0, 1.5, 2.0)), text])
        c = Canvas(w, h)
        for d in self._drops:
            d[1] += d[2]
            head, text = int(d[1]), d[3]
            n = len(text)
            for k, ch in enumerate(text):
                y = head - (n - 1 - k)
                if k == n - 1:
                    style = "$foreground b"
                elif k > n * 2 // 3:
                    style = "$success"
                else:
                    style = "dim $success"
                c.put(int(d[0]), y, ch if not (a and ord(ch) > 127) else "?", style)
        self._drops = [d for d in self._drops if d[1] - len(d[3]) < h]
        return c.markup()

    def page_bounce(self) -> str:
        """The time in big digits bouncing around the screen; it changes colour at every wall."""
        app = self.scada
        w, h = self._area()
        lt = time.localtime()
        rows = big_text(time.strftime("%H:%M", lt), app.ascii)
        date = self._date(lt)
        bw = max(max(len(r) for r in rows), len(date))
        bh = len(rows) + 2
        x, y, dx, dy, col = self._ball
        x, y = x + dx, y + dy
        if not 0 <= x <= w - bw:
            dx, col = -dx, col + 1
            x = max(0, min(w - bw, x))
        if not 0 <= y <= h - bh:
            dy, col = -dy, col + 1
            y = max(0, min(h - bh, y))
        self._ball = [x, y, dx, dy, col]
        color = ("$primary", "$accent", "$success", "$warning", "$secondary")[col % 5]
        c = Canvas(w, h)
        for i, row in enumerate(rows):
            c.put(int(x) + (bw - len(row)) // 2, int(y) + i, row, color)
        c.put(int(x) + (bw - len(date)) // 2, int(y) + len(rows) + 1, date, "$text-muted")
        return c.markup()

    def page_alarm(self, alarms: list[dict]) -> str:
        """The alarm takeover page (newest first, max 8)."""
        crit = any(is_critical(a) for a in alarms)
        title = _("⚠  CRITICAL ALARM  ⚠") if crit else _("⚠  ALARM  ⚠")
        lines = [f"[$error b]{title}[/]", ""]
        for a in sorted(alarms, key=lambda a: a["raised_ts"], reverse=True)[:8]:
            when = time.strftime("%H:%M:%S", time.localtime(a["raised_ts"]))
            state = _("ACTIVE") if a["cleared_ts"] is None else _("normal – not acknowledged")
            lines.append(f"[b]{when}[/b]  {escape(a['text'])}  [$text-muted]({a['ga']}, {state})[/]")
        lines += ["", f"[$text-muted]{_('Press a key and go to F5 to acknowledge')}[/]"]
        return "\n".join(lines)
