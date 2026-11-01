# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""The systems view (Ctrl+E): ui.systems, the air-flow drawing and the meter table against a hub."""

import asyncio

from knxscada.config import Config
from knxscada.protocols.hub import Hub
from knxscada.tui.energy import consumption, meter_cells, value_at
from knxscada.tui.systems import (
    Colors,
    Meter,
    Vent,
    common_title,
    group_tags,
    parse_systems,
    render_vent,
    strip_title,
)
from tests.test_protocols import FakeServer, Mem, until

SYSTEMS = [
    {"name": "AHU 1", "type": "ventilation", "connection": "vent", "prefix": "U1_", "meter": "meter",
     "tags": {"outdoor": "RT900", "supply": "RT401", "extract": "RT501", "supply_fan": "JV401", "alarm": "AL",
              "filter_extract": "QD501", "mode": "SD"},
     "mode_texts": {0: "Auto", "1": "Manual off"}, "settings": ["U1_RT*"]},
    {"name": "Energy", "type": "energy", "meters": [{"name": "Main", "connection": "meter",
                                                      "tags": {"pf": "cos phi"}}, {"name": "broken"}]},
    {"name": "x", "type": "boiler", "connection": "b"},
    {"name": "y", "connection": "vent", "tags": {"nonsense": "a"}},
]


def test_parse_systems():
    vents, meters, problems = parse_systems(SYSTEMS)
    assert [v.name for v in vents] == ["AHU 1", "y"]
    assert vents[0].tags["supply"] == "U1_RT401" and vents[0].key("supply") == "vent/U1_RT401"
    assert vents[0].key("heating") is None
    assert vents[0].mode_texts == {0: "Auto", 1: "Manual off"}
    assert vents[0].label() == "AHU 1" and vents[1].label() == "y"
    assert Vent("=360.001 Ventilation", "v").label() == "360.001" and Vent("x", "v", short="A").label() == "A"
    assert vents[0].shows_setting("U1_RT401") and not vents[0].shows_setting("U1_JV401")
    assert vents[1].shows_setting("anything")
    assert [m.name for m in meters] == ["Main"]
    assert meters[0].tags["power"] == "Total active power" and meters[0].tags["pf"] == "cos phi"
    assert len(problems) == 3          # meter without connection, unknown type, unknown role
    assert parse_systems(None) == ([], [], [])


def test_render_vent_and_meter_cells():
    vent = Vent("AHU", "v", {"outdoor": "o", "supply": "s", "supply_fan": "f", "alarm": "a", "filter_extract": "q",
                             "mode": "m"}, mode_texts={1: "Manual off"})
    values = {"v/o": (-5.25, "°C", "good"), "v/s": (21.0, "°C", "good"), "v/f": (55.0, "%", "good"),
              "v/a": (True, "", "good"), "v/q": (True, "", "good"), "v/m": (1, "", "good")}
    text = render_vent(vent, values.get, Colors(), (1.5, "kW", "good")).plain
    assert "running" in text and "ALARM" in text and "Mode: Manual off" in text and "Power 1.50 kW" in text
    assert "-5.2 °C" in text and "21.0 °C" in text and "55 %" in text and "Filter" in text and "⚠ Alarm" in text
    assert "Heat" not in text           # no heating tag: plain duct instead of a coil
    wide = max(len(line) for line in text.splitlines())
    narrow = render_vent(vent, values.get, Colors(), width=90).plain
    assert max(len(line) for line in narrow.splitlines()) < wide and "Filter" in narrow and "21.0 °C" in narrow
    assert "no contact" not in text
    values["v/s"] = (21.0, "°C", "old")
    text = render_vent(vent, values.get, Colors()).plain
    assert "no contact" in text
    stopped = render_vent(vent, {"v/f": (0, "%", "good")}.get, Colors()).plain
    assert "stopped" in stopped and "–" in stopped and "ALARM" not in stopped
    m = Meter("M", "e", {"power": "P", "energy": "E"})
    assert meter_cells(m, {"e/P": (1.234, "kW", "good"), "e/E": (147858.6, "kWh", "good")}.get)[:3] == \
        ["1.23 kW", "147858.6 kWh", "–"]


def test_tui_systems_view(tmp_path, monkeypatch):
    """Ctrl+E shows one unit with its settings tab, Ctrl+G the meter table, with live values from the hub."""
    from knxscada.tui.app import ScadaApp
    from knxscada.tui.energy import EnergyView
    from knxscada.tui.systems import SystemsView
    from knxscada.tui.widgets import LiveStatic

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cfg = Config(path=tmp_path / "config.yaml", server={"socket": "s.sock"},
                 ui={"splash": False, "language": "en", "systems": SYSTEMS[:2]})
    app = ScadaApp(cfg)
    hub = Hub(FakeServer(tmp_path))
    hub.server._broadcast = lambda msg: app._handle(msg)
    hub.server.clients = {object()}                 # the hub only sends "points" when someone listens
    vent = Mem("vent", {"tags": {"U1_RT401": {"type": "float", "unit": "°C"}, "U1_JV401": {"unit": "%"},
                                 "U1_AL": {"type": "bool"}}}, hub, server=False)
    meter = Mem("meter", {"tags": {"Total active power": {"type": "float", "unit": "kW"}}}, hub, server=False)

    async def fake_request(cmd, **kw):
        if cmd in ("protocols", "points"):
            return await hub.command({"cmd": cmd, **kw}, "test")
        return [] if cmd != "hello" else None

    async def fake_connect():
        app._handle({"type": "hello", "state": {}, "status": {"connected": False, "connection": "off"},
                     "alarms": [], "stats": {}})

    monkeypatch.setattr(app.client, "request", fake_request)
    monkeypatch.setattr(app.client, "connect", fake_connect)
    monkeypatch.setattr(type(app.client), "connected", property(lambda self: True))
    monkeypatch.setattr(app.client, "close_nowait", lambda: None)

    async def run():
        hub.data, hub.load = {"connections": {}, "gateways": []}, (lambda: None)
        for d in (vent, meter):
            hub._add(d)
        await hub.start()
        try:
            hub.update("vent", "U1_RT401", 19.5)
            hub.update("vent", "U1_JV401", 60)
            hub.update("meter", "Total active power", 2.5)
            async with app.run_test(size=(180, 50)) as pilot:
                await pilot.pause(0.3)
                assert [str(n.label) for n in app.menu_nodes["systems"].children] == ["AHU 1"]
                await pilot.press("ctrl+e")
                view = app.query_one("#systems", SystemsView)
                card = view.query_one("#sys-card", LiveStatic)
                await until(lambda: "19.5 °C" in str(card.render()), 5)
                assert "Power 2.50 kW" in str(card.render()) and card.border_title == "AHU 1"
                tags = view.query_one("#sys-tags")
                assert tags.row_count == 0                              # filled only when its tab is shown
                hub.update("vent", "U1_AL", True)                       # live: an alarm turns the card red
                await until(lambda: "ALARM" in str(card.render()) and card.has_class("bad"), 5)
                view.query_one("#sys-tabs").active = "sys-tab-settings"
                await until(lambda: "U1_RT401" in tags.rows, 5)
                assert [k.value for k in tags.rows][1:] == ["U1_RT401"]  # a section row, then settings: ["U1_RT*"]
                hub.update("vent", "U1_RT401", 20.5)                    # the settings table follows
                await until(lambda: str(tags.get_cell("U1_RT401", "value")) == "20.50 °C", 5)
                await pilot.press("ctrl+g")
                energy = app.query_one("#energy", EnergyView)
                table = energy.query_one("#en-meters")
                await until(lambda: [str(c) for c in table.get_row_at(0)[:2]] == ["Main", "2.50 kW"], 5)
                assert "Main" in str(energy.query_one("#en-detail", LiveStatic).render())
        finally:
            await hub.stop()

    asyncio.run(run())


def test_week_program_line():
    vent = Vent("AHU", "v", {r: r for r in ("week_start_hour", "week_start_minute", "week_stop_hour",
                                             "week_stop_minute", "week_enabled", "overtime")})
    values = {"v/week_start_hour": ([5, 5, 5, 5, 5, 10, 10], "", "good"),
              "v/week_start_minute": ([0, 0, 0, 0, 0, 0, 0], "", "good"),
              "v/week_stop_hour": ([21, 18, 18, 18, 16, 12, 12], "", "good"),
              "v/week_stop_minute": ([0, 30, 30, 30, 0, 0, 0], "", "good"),
              "v/week_enabled": ([True, True, True, True, True, True, False], "", "good"),
              "v/overtime": (True, "", "good")}
    text = render_vent(vent, values.get, Colors()).plain
    assert "Week" in text and "Mon 05:00–21:00" in text and "Tue 05:00–18:30" in text
    assert "Sat 10:00–12:00" in text and "Sun off" in text and "Overtime on" in text
    assert "Week" not in render_vent(Vent("x", "v"), {}.get, Colors()).plain


def test_arrays_are_converted_per_element():
    from knxscada.protocols.base import coerce, same_value

    assert coerce([1, 0, 2], "bool") == [True, False, True]
    assert coerce((5.0, "6"), "int") == [5, 6]
    assert same_value([1.0, 2.0], [1.00001, 2.0]) and not same_value([1, 2], [1, 3]) and not same_value([1], 1)


def test_consumption_from_counter():
    pts = [(0, 100.0), (1800, 101.0), (3600, 102.0), (5400, 104.0), (7200, 106.0)]
    assert value_at(pts, -1) is None and value_at(pts, 3600) == 102.0 and value_at(pts, 4500) == 103.0
    assert value_at(pts, 9000) == 106.0                                   # after the last point: the last reading
    assert consumption([(0, 0.0), (7200, 8.0)], 0, 7200, 2) == [4.0, 4.0]  # a quiet log is spread, not lumped
    assert consumption(pts, 0, 7200, 2) == [2.0, 4.0]
    assert consumption(pts, -3600, 7200, 3) == [None, 2.0, 4.0]          # no reading before the first bucket
    assert consumption([(0, 10.0), (3600, 5.0)], 0, 3600, 1) == [None]   # counter replaced: no negative bar
    assert consumption([], 0, 3600, 2) == [None, None]


def test_settings_sections():
    vents, _meters, problems = parse_systems([
        {"name": "AHU", "connection": "v", "prefix": "U1_", "hide": ["Act_Time"],
         "groups": [{"title": "Operation", "tags": ["SYS_*", "SD_CMD"]}, {"tags": ["x"]}]}])
    s = vents[0]
    assert s.groups == [("Operation", ["SYS_*", "SD_CMD"])] and len(problems) == 1   # group without title
    tags = [{"name": "U1_" + n, "description": d} for n, d in (
        ("Act_Time", "PLC time"), ("SYS_AL", "System – Alarm"), ("SD_CMD", "System switch"),
        ("JV401_OP", "Supply fan output"), ("JV401_AL", "Supply fan – Alarm"), ("JV401_DT", "Supply fan run time"),
        ("Seq_Kp", "Sequence Kp"), ("Seq_Tn", "Sequence Tn"), ("RT401_PV", "Supply temperature"), ("Lonely", ""))]
    groups = group_tags(s, tags)
    assert [(g.title, g.code) for g in groups] == [("Operation", ""), ("Supply fan", "JV401"), ("Sequence", ""),
                                                   ("Other", "")]
    assert [label for _p, label in groups[1].rows] == ["Output", "Alarm", "Run time"]
    assert [label for _p, label in groups[3].rows] == ["Supply temperature", "Lonely"]   # no description: the tag
    assert all(p["name"] != "U1_Act_Time" for g in groups for p, _l in g.rows)          # hidden
    assert common_title(["Heating coil – Alarm", "Electric heating coil output", "Electric heating coil run time"]) \
        == "Electric heating coil"
    assert common_title(["Only one"]) == ""
    assert strip_title("Holidays active", "Holidays and vacation") == "Holidays active"
    assert strip_title("Supply fan output", "Supply fan JV401") == "Output"
    assert strip_title("Supply fanatic", "Supply fan") == "Supply fanatic"               # whole words only


def test_hub_sees_small_steps_of_a_large_counter(tmp_path):
    """1e-4 of 147 912 kWh is 15 kWh: the hub must not hold back a counter until it moved that far."""
    from knxscada.protocols.base import same_value

    assert same_value(147912.0, 147913.0) and not same_value(147912.0, 147913.0, rel=1e-6)
    hub = Hub(FakeServer(tmp_path))
    hub.update("m", "E", 147912.0)
    hub.update("m", "E", 147912.5)
    assert hub.values["m/E"]["v"] == 147912.5
    hub.update("m", "E", 147912.5000001)                                 # float32 rounding noise: no change
    assert hub.values["m/E"]["v"] == 147912.5


def test_tui_systems_write(tmp_path, monkeypatch):
    """Settings tab: Enter on a writable tag → value → confirmation → written; read-only tags refuse.
    Overview: m → the operating modes of mode_texts → confirmation → written."""
    from textual.widgets import Button, Input

    from knxscada.tui.app import ScadaApp
    from knxscada.tui.dialogs import ConfirmDialog
    from knxscada.tui.gateways import PointDialog
    from knxscada.tui.systems import ModeDialog, SystemsView

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    systems = [{"name": "AHU 1", "connection": "vent", "tags": {"setpoint": "SP", "mode": "SD", "supply": "PV"},
                "mode_texts": {0: "Auto", 1: "Off", 2: "On"}}]
    cfg = Config(path=tmp_path / "config.yaml", server={"socket": "s.sock"},
                 ui={"splash": False, "language": "en", "systems": systems})
    app = ScadaApp(cfg)
    hub = Hub(FakeServer(tmp_path))
    hub.server._broadcast = lambda msg: app._handle(msg)
    hub.server.clients = {object()}
    class Connected(Mem):                     # Mem's session ends at once; writes need a connection
        async def session(self):
            self.set_state(True)
            await self.wait_stopped(60)

    vent = Connected("vent", {"tags": {"PV": {"type": "float", "unit": "°C", "writable": False},
                                 "SP": {"type": "float", "unit": "°C", "writable": True, "description": "Setpoint"},
                                 "SD": {"type": "int", "writable": True}}}, hub, server=False)

    async def fake_request(cmd, **kw):
        if cmd in ("protocols", "points", "point_write"):
            return await hub.command({"cmd": cmd, **kw}, "test")
        return [] if cmd != "hello" else None

    async def fake_connect():
        app._handle({"type": "hello", "state": {}, "status": {"connected": False, "connection": "off"},
                     "alarms": [], "stats": {}})

    monkeypatch.setattr(app.client, "request", fake_request)
    monkeypatch.setattr(app.client, "connect", fake_connect)
    monkeypatch.setattr(type(app.client), "connected", property(lambda self: True))
    monkeypatch.setattr(app.client, "close_nowait", lambda: None)

    async def run():
        hub.data, hub.load = {"connections": {}, "gateways": []}, (lambda: None)
        try:
            async with app.run_test(size=(180, 50)) as pilot:
                await pilot.pause(0.3)                   # the driver reports its state to the app: start it now
                hub._add(vent)
                await hub.start()
                await until(lambda: vent.connected, 5)
                hub.update("vent", "SP", 21.0)
                hub.update("vent", "SD", 0)
                hub.update("vent", "PV", 20.0)
                await pilot.press("ctrl+e")
                view = app.query_one("#systems", SystemsView)
                await until(lambda: "vent" in view.tags, 5)
                await pilot.press("m")                                   # operating mode from the overview
                await until(lambda: isinstance(app.screen, ModeDialog) and app.screen.query("#mode-1"), 5)
                app.screen.query_one("#mode-1", Button).press()
                await until(lambda: isinstance(app.screen, ConfirmDialog) and app.screen.query("#yes"), 5)
                app.screen.query_one("#yes", Button).press()
                await until(lambda: ("SD", 1) in vent.writes, 5)
                view.query_one("#sys-tabs").active = "sys-tab-settings"
                tags = view.query_one("#sys-tags")
                await until(lambda: "SP" in tags.rows, 5)
                tags.move_cursor(row=tags.get_row_index("PV"))           # read-only: refused, no dialog
                view.action_write()
                await pilot.pause(0.2)
                assert not isinstance(app.screen, PointDialog)
                tags.move_cursor(row=tags.get_row_index("SP"))
                view.action_write()
                await until(lambda: isinstance(app.screen, PointDialog) and app.screen.query("#p-value"), 5)
                app.screen.query_one("#p-value", Input).value = "22.5"
                app.screen.query_one("#p-ok", Button).press()
                await until(lambda: isinstance(app.screen, ConfirmDialog) and app.screen.query("#yes"), 5)
                assert "22.5 °C" in app.screen.text and "21.00 °C" in app.screen.text
                app.screen.query_one("#yes", Button).press()
                await until(lambda: ("SP", 22.5) in vent.writes, 5)
        finally:
            await hub.stop()

    asyncio.run(run())


def test_panel_type():
    vents, _m, problems = parse_systems([{"name": "Annex", "type": "panel", "connection": "gw",
                                          "groups": [{"title": "Lights", "tags": ["Light *"]}]}])
    assert not problems and vents[0].kind == "panel" and vents[0].label() == "Annex"
    rows = group_tags(vents[0], [{"name": "Light hall On/Off"}, {"name": "Wind"}])
    assert [g.title for g in rows] == ["Lights", "Other"]


def test_tui_point_rooms(tmp_path, monkeypatch):
    """Rooms from protocol tags: Floors and rooms → building → floor → room; values from the hub; the switch
    writes with point_write."""
    from textual.widgets import Switch

    from knxscada.tui.app import ScadaApp
    from knxscada.tui.widgets import DatapointRow, SwitchRow

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    (tmp_path / "protocols.yaml").write_text(
        "connections:\n  gw:\n    protocol: mem\n    tags:\n"
        "      Light On/Off: {object: 'bv:4', writable: true}\n      Light Status: {object: 'bv:5'}\n"
        "      RT205_PV: {object: 'av:53', unit: °C}\n", encoding="utf-8")
    rooms = [{"building": "Annex", "floor": "2nd floor", "name": "Office 205", "cards": [
        {"name": "Light", "usage": "light", "rows": [{"point": "gw/Light On/Off", "status": "gw/Light Status",
                                                      "label": "Light"}]},
        {"name": "Heating", "usage": "heating", "rows": [{"point": "gw/RT205_PV", "role": "temp"}]}]}]
    cfg = Config(path=tmp_path / "config.yaml", server={"socket": "s.sock", "protocols": "protocols.yaml"},
                 knx={"enabled": False}, project={"point_rooms": rooms},
                 ui={"splash": False, "language": "en", "building": "Main"})
    app = ScadaApp(cfg)
    hub = Hub(FakeServer(tmp_path))
    hub.server._broadcast = lambda msg: app._handle(msg)
    hub.server.clients = {object()}

    class Connected(Mem):
        async def session(self):
            self.set_state(True)
            await self.wait_stopped(60)

    gw = Connected("gw", {"tags": {"Light On/Off": {"type": "bool", "writable": True},
                                   "Light Status": {"type": "bool", "writable": False},
                                   "RT205_PV": {"type": "float", "unit": "°C", "writable": False}}}, hub, server=False)

    async def fake_request(cmd, **kw):
        if cmd in ("protocols", "points", "point_write"):
            return await hub.command({"cmd": cmd, **kw}, "test")
        return [] if cmd != "hello" else None

    async def fake_connect():
        app._handle({"type": "hello", "state": {}, "status": {"connected": False, "connection": "off"},
                     "alarms": [], "stats": {}})

    monkeypatch.setattr(app.client, "request", fake_request)
    monkeypatch.setattr(app.client, "connect", fake_connect)
    monkeypatch.setattr(type(app.client), "connected", property(lambda self: True))
    monkeypatch.setattr(app.client, "close_nowait", lambda: None)

    async def run():
        hub.data, hub.load = {"connections": {}, "gateways": []}, (lambda: None)
        try:
            async with app.run_test(size=(180, 50)) as pilot:
                hub._add(gw)
                await hub.start()
                await until(lambda: gw.connected, 5)
                hub.update("gw", "RT205_PV", 21.5)
                hub.update("gw", "Light Status", False)
                rooms_node = app.menu_nodes["rooms"]
                # Only one building (no ETS floors): its floors directly under "Floors and rooms".
                assert [str(n.label) for n in rooms_node.children] == ["2nd floor"]
                floor = rooms_node.children[0]
                app._select(floor.children[0].data)
                await until(lambda: len(app.query(DatapointRow)) == 2, 5)
                await until(lambda: any(r.text == "21.5 °C" for r in app.query(DatapointRow)), 5)
                sw = app.query_one(SwitchRow).query_one(Switch)
                sw.value = True                                       # the user switches the light on
                await until(lambda: ("Light On/Off", True) in gw.writes, 5)
                hub.update("gw", "Light Status", True)                # the feedback arrives
                await until(lambda: app.query_one(SwitchRow).text == "on", 5)
                assert app.home.rooms[0].temp_ga == "pt:gw/RT205_PV"
        finally:
            await hub.stop()

    asyncio.run(run())
