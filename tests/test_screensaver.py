# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""The screensaver: sun and moon, the canvas, the ventilation drawing and every page in a running app."""

import asyncio
import time

from textual.content import Content

from knxscada.config import Config
from knxscada.tui.screensaver import (
    HEAT_COLORS,
    PAGES,
    Canvas,
    heat_color,
    hhmm,
    moon_glyph,
    moon_phase,
    sun_times,
    vent_lines,
)
from knxscada.tui.systems import Vent


def test_sun_times():
    # Oslo (almanac): 21 December 09:18–15:12 CET, 21 June 03:53–22:44 CEST. Five minutes allowed.
    rise, sset = sun_times(355, 59.91, 10.75, 1)
    assert abs(rise - (9 + 18 / 60)) < 0.09 and abs(sset - (15 + 12 / 60)) < 0.09
    rise, sset = sun_times(172, 59.91, 10.75, 2)
    assert abs(rise - (3 + 53 / 60)) < 0.09 and abs(sset - (22 + 44 / 60)) < 0.09
    assert sun_times(355, 78.2, 15.6, 1) is None            # Svalbard in December: polar night
    assert sun_times(172, 78.2, 15.6, 2) == (0.0, 24.0)     # and midnight sun in June
    assert hhmm(7.5) == "07:30" and hhmm(24.25) == "00:15"


def test_moon():
    full = 947182440 + 29.530588853 * 86400 / 2
    assert abs(moon_phase(full) - 0.5) < 1e-6
    assert moon_glyph(0.5) == ("●", "full moon") and moon_glyph(0.0)[1] == "new moon"
    assert moon_glyph(0.25)[1] == "waxing moon" and moon_glyph(0.75, ascii_only=True) == ("(", "waning moon")


def test_canvas_and_heat_colors():
    c = Canvas(6, 2)
    c.put(-1, 0, "abc", "b")          # cut at the left edge
    c.put(4, 1, "[xyz", "")           # cut at the right edge; "[" must be escaped
    assert c.get(0, 0) == "b" and c.get(5, 1) == "x" and c.get(9, 9) == ""
    markup = c.markup()
    assert Content.from_markup(markup).plain == "bc    \n    [x"
    assert heat_color(21.0, None) == HEAT_COLORS[3]
    assert heat_color(17.0, 21.0) == HEAT_COLORS[0] and heat_color(22.6, 21.0) == HEAT_COLORS[5]


def test_vent_lines_move():
    vent = Vent("AHU 1", "v", {"outdoor": "o", "supply": "s", "extract": "e", "supply_fan": "f", "running": "r",
                               "heating": "h", "mode": "m"}, mode_texts={1: "Comfort"})
    values = {"v/o": (-4.0, "°C", ""), "v/s": (19.5, "°C", ""), "v/e": (22.0, "°C", ""), "v/f": (60.0, "%", ""),
              "v/r": (True, "", ""), "v/h": (30.0, "%", ""), "v/m": (1, "", "")}
    a = [Content.from_markup(line).plain for line in vent_lines(vent, values.get, 0)]
    b = [Content.from_markup(line).plain for line in vent_lines(vent, values.get, 1)]
    assert "AHU 1" in a[0] and "running" in a[0] and "Comfort" in a[0]
    assert "-4.0 °C" in a[1] and "19.5 °C" in a[1] and "60 %" in a[1] and "30 %" in a[1] and "22.0 °C" in a[2]
    assert a[1] != b[1]                                       # the arrows and the fan move
    stopped = vent_lines(vent, {**values, "v/r": (False, "", ""), "v/f": (0.0, "%", "")}.get, 0)
    still = vent_lines(vent, {**values, "v/r": (False, "", ""), "v/f": (0.0, "%", "")}.get, 1)
    assert stopped[1] == still[1] and "stopped" in stopped[0]
    assert not set("─›◐⟳♨▥") & set(Content.from_markup(vent_lines(vent, {}.get, 0, ascii_only=True)[1]).plain)


def test_tui_every_page(tmp_path, monkeypatch):
    """Every page renders valid markup with rooms, units and meters; ui.screensaver_pages picks the pages."""
    from knxscada.tui.app import ScadaApp
    from knxscada.tui.screensaver import ScreenSaver

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    (tmp_path / "protocols.yaml").write_text(
        "connections:\n  gw:\n    protocol: mem\n    tags:\n"
        "      Light Status: {object: 'bv:5'}\n      RT201_PV: {object: 'av:53', unit: °C}\n"
        "      RT201_SP: {object: 'av:54', unit: °C}\n      RT202_PV: {object: 'av:55', unit: °C}\n",
        encoding="utf-8")
    rooms = [{"building": "Annex", "floor": "2nd floor", "name": f"Office {n}", "cards": [
        {"name": "Light", "usage": "light", "rows": [{"point": "gw/Light Status", "label": "Light"}]},
        {"name": "Heating", "usage": "heating", "rows": [{"point": f"gw/RT{n}_PV", "role": "temp"},
                                                         {"point": f"gw/RT{n}_SP", "role": "setpoint"}]}]}
             for n in (201, 202)]
    systems = [{"name": "AHU 1", "connection": "vent", "tags": {"outdoor": "RT900", "supply": "RT401",
                                                                "supply_fan": "JV401", "running": "Run"}},
               {"name": "Energy", "type": "energy", "meters": [{"name": "Main", "connection": "m1"},
                                                               {"name": "Heat pump", "connection": "m2"}]}]
    cfg = Config(path=tmp_path / "config.yaml", server={"socket": "s.sock", "protocols": "protocols.yaml"},
                 knx={"enabled": False}, project={"point_rooms": rooms},
                 ui={"splash": False, "language": "en", "building": "Main", "systems": systems,
                     "screensaver_minutes": 0})
    app = ScadaApp(cfg)
    now = time.time()
    points = {"vent": [{"name": "RT900", "value": -3.5, "unit": "°C"}, {"name": "RT401", "value": 19.0, "unit": "°C"},
                       {"name": "JV401", "value": 55, "unit": "%"}, {"name": "Run", "value": True}],
              "m1": [{"name": "Total active power", "value": 4.2, "unit": "kW"},
                     {"name": "Total active energy", "value": 12345.6, "unit": "kWh"}],
              "m2": [{"name": "Total active power", "value": 1.1, "unit": "kW"}]}

    async def fake_request(cmd, **kw):
        if cmd == "points":
            return points.get(kw["conn"], [])
        if cmd == "history":
            return [[now - 3600 * h, 1.0 + h % 3] for h in range(24, 0, -1)]
        return []

    async def fake_connect():
        app._handle({"type": "hello", "state": {}, "status": {"connected": True, "connection": "x"},
                     "alarms": [], "stats": {}})

    monkeypatch.setattr(app.client, "request", fake_request)
    monkeypatch.setattr(app.client, "connect", fake_connect)
    monkeypatch.setattr(type(app.client), "connected", property(lambda self: True))
    monkeypatch.setattr(app.client, "close_nowait", lambda: None)

    async def run():
        async with app.run_test(size=(160, 48)) as pilot:
            await pilot.pause(0.2)
            for key, value in (("RT201_PV", 23.4), ("RT201_SP", 21.0), ("RT202_PV", 19.0), ("Light Status", True)):
                app._set_point(f"gw/{key}", value, now, "good")
            app.telegrams.extend({"ts": now - i, "type": "write", "dst": f"1/2/{i % 3}", "text": f"{i} %"}
                                 for i in range(20))
            app.action_screensaver()
            await pilot.pause(0.3)
            ss = app.screen
            assert isinstance(ss, ScreenSaver)
            assert [k for k, _f in ss._available().items()] == [k for k in PAGES if k not in ("energy", "air")]
            assert ss.values["m1/Total active power"][0] == 4.2 and ss._outdoor()[0] == -3.5
            pages = dict(ss._available())
            for step in range(3):                               # the animations advance without errors
                ss.step = step
                for key, render in pages.items():
                    plain = Content.from_markup(render()).plain
                    assert plain.strip(), key
            assert "Office 201" in Content.from_markup(pages["heatmap"]()).plain
            assert "Heat pump" in Content.from_markup(pages["meters"]()).plain
            assert "5.30 kW" in Content.from_markup(pages["meters"]()).plain   # the total of both meters
            assert "Warmest room" in Content.from_markup(pages["records"]()).plain
            assert "AHU 1" in Content.from_markup(pages["ventilation"]()).plain
            assert "Outdoor -3.5 °C" in Content.from_markup(pages["sky"]()).plain
            app.ui["screensaver_pages"] = ["sky", "nonsense", "bounce"]
            assert [name for name, _f in ss._pages()] == ["Sky", "Bouncing clock"]
            await pilot.press("x")
            assert not isinstance(app.screen, ScreenSaver)

    asyncio.run(run())
