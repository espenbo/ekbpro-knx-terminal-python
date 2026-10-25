# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""Screensaver: slowly rotates between a few information pages.

Started by the app after ``ui.screensaver_minutes`` without input (or with
F9). Pages: clock & outdoor, home, temperatures, energy, indoor climate and
KNX traffic (pages without data are skipped).

* **Unacknowledged alarms take over** the whole screen with a slow red blink.
  Acknowledged-but-active alarms are only mentioned in the footer.
* **Burn-in protection:** the content is shifted a few cells at every page
  change, like old CRT screensavers.
* Any key or click returns to the normal UI.
"""

from __future__ import annotations

import random
import time
from typing import TYPE_CHECKING

from textual import events
from textual.app import ComposeResult
from textual.markup import escape
from textual.screen import Screen
from textual.widgets import Static

from ..alarms import is_critical
from ..i18n import N_, _
from .graphics import bar, big_text, resample, sparkline
from .home import HomeModel

if TYPE_CHECKING:
    from .app import ScadaApp

#: Weekday and month names in English; translated when shown.
DAYS = [N_("Monday"), N_("Tuesday"), N_("Wednesday"), N_("Thursday"), N_("Friday"), N_("Saturday"), N_("Sunday")]
MONTHS = [N_("January"), N_("February"), N_("March"), N_("April"), N_("May"), N_("June"), N_("July"), N_("August"),
          N_("September"), N_("October"), N_("November"), N_("December")]


def _num(v) -> float | None:
    """The value as float if it is a number (bools excluded), else None."""
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


class ScreenSaver(Screen):
    """Full-screen information display. Styling lives in app.tcss (ScreenSaver, #ss-body, #ss-foot)."""

    def __init__(self) -> None:
        super().__init__()
        self.page = 0
        self.last_switch = time.time()
        self._shown: dict[str | None, str] = {}  # widget id → markup last shown

    @property
    def scada(self) -> ScadaApp:
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        yield Static("", id="ss-body")
        yield Static("", id="ss-foot")

    def on_mount(self) -> None:
        self.set_interval(1, self._tick)
        self._tick()

    # -- events ------------------------------------------------------------------------
    def on_key(self, event: events.Key) -> None:
        event.stop()  # the key only wakes the UI; it must not trigger an action
        self.dismiss()

    def on_click(self) -> None:
        self.dismiss()

    # -- pages -------------------------------------------------------------------------
    def _pages(self) -> list[tuple[str, callable]]:
        """(English page name, render function) for every page that has data."""
        home = self.scada.home
        pages = [(N_("Clock"), self.page_clock), (N_("Home"), self.page_home), (N_("Temperatures"), self.page_temps)]
        if home.power_ga:
            pages.append((N_("Energy"), self.page_energy))
        if home.co2 or home.humidity:
            pages.append((N_("Indoor climate"), self.page_air))
        pages.append((N_("KNX"), self.page_knx))
        return pages

    def _tick(self) -> None:
        """Once per second: rotate pages, handle alarms, refresh the content (the clock moves)."""
        app = self.scada
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
        """Update *widget* only when its text changed (the page is rebuilt every second; the clock shows minutes)."""
        if self._shown.get(widget.id) != markup:
            self._shown[widget.id] = markup
            widget.update(markup)

    def _big(self, text: str, color: str = "$primary") -> str:
        """A large value: big block digits if enabled, otherwise bold text."""
        if not self.scada.big_digits:
            return f"[{color} b]{escape(text)}[/]"
        return "\n".join(f"[{color}]{escape(line)}[/]" for line in big_text(text, self.scada.ascii))

    def _spark(self, ga: str | None, width: int, hours: float = 24) -> str:
        if not ga:
            return ""
        now = time.time()
        return sparkline(resample(self.scada.history.get(ga, []), now - hours * 3600, now, width), self.scada.ascii)

    def page_clock(self) -> str:
        """Clock, date and outdoor temperature with a 24 h sparkline."""
        app = self.scada
        now = time.localtime()
        date = _("{weekday} {day} {month} {year}").format(
            weekday=_(DAYS[now.tm_wday]), day=now.tm_mday, month=_(MONTHS[now.tm_mon - 1]), year=now.tm_year)
        out = [self._big(time.strftime("%H:%M", now), "$primary"), "", f"[b]{date}[/b]", ""]
        home = app.home
        t = _num(HomeModel.value(app.state, home.outdoor_ga))
        if t is not None:
            pts = [p[1] for p in app.history.get(home.outdoor_ga, []) if p[0] > time.time() - 86400]
            rng = _("min {lo}°  max {hi}°").format(lo=f"{min(pts):.1f}", hi=f"{max(pts):.1f}") if pts else ""
            out += [f"[$text-muted]{_('OUTDOOR')}[/]", self._big(f"{t:.1f}°", "$accent"), "",
                    f"[$primary]{self._spark(home.outdoor_ga, 48)}[/]",
                    f"[$text-muted]{_('last 24 hours')}   {rng}[/]"]
        return "\n".join(out)

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
