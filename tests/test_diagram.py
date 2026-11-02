# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""Piping diagrams (``type: diagram`` in ui.systems): the template, the fields, the colours and the Systems view."""

import asyncio

from knxscada.config import Config
from knxscada.protocols.hub import Hub
from knxscada.tui.diagram import (
    DiagramColors,
    field_keys,
    parse_fields,
    placeholders,
    render_diagram,
)
from knxscada.tui.systems import parse_systems
from tests.test_protocols import FakeServer, Mem, until

TEMPLATE = (
    "━━━{p1}━━━▶ {t_sup  } supply\n"
    "═══════◀═══ {t_ret  } return  {state }  {valve}\n"
    "{nothing}"
)
FIELDS = {
    "p1": {"kind": "pump", "run": "JP401_V", "alarm": "JP401_AL", "safety": "JP401_SB"},
    "t_sup": {"tag": "RT401_PV", "alarm": ["RT401_L_AL"]},
    "t_ret": {"tag": "RT501_PV", "decimals": 0},
    "state": {"kind": "state", "tag": "IE001_V", "on": "drift", "off": "stopp"},
    "valve": {"tag": "RelPos", "connection": "valve1", "decimals": 0},
}


def colors() -> DiagramColors:
    """Distinct colour names, so the style of each character tells which rule coloured it."""
    return DiagramColors(hot="HOT", cold="COLD", ok="OK", warn="WARN", bad="BAD", dim="DIM", value="VAL")


def style_at(text, index: int) -> str:
    """The style of the character at *index* of a rich Text (the last span covering it)."""
    styles = [str(s.style) for s in text.spans if s.start <= index < s.end]
    return styles[-1] if styles else ""


def test_parse_fields_and_keys():
    """Fields are parsed with their kinds; bad kinds, a missing tag and a pump without feedback are reported, and
    every key a field reads (tag, run, safety, alarms) gets the prefix or its own connection."""
    fields, problems = parse_fields(FIELDS)
    assert not problems and fields["p1"].kind == "pump" and fields["t_ret"].decimals == 0
    assert placeholders(TEMPLATE) == ["p1", "t_sup", "t_ret", "state", "valve", "nothing"]
    assert field_keys(fields["p1"], "plc", "320_") == ["plc/320_JP401_V", "plc/320_JP401_SB", "plc/320_JP401_AL"]
    assert field_keys(fields["valve"], "plc", "320_") == ["valve1/RelPos"]          # no prefix on another conn
    import yaml

    loaded, _p = parse_fields(yaml.safe_load("s: {kind: state, tag: X, on: drift, off: stopp}"))
    assert (loaded["s"].on, loaded["s"].off) == ("drift", "stopp")     # YAML turned the keys into True/False
    _f, problems = parse_fields({"a": {"kind": "gauge", "tag": "x"}, "b": {"kind": "value"}, "c": {"kind": "pump"},
                                 "d": "RT401", "e": {"kind": "text", "tag": "S", "texts": {"x": "?"}}})
    assert len(problems) == 5
    aligned, problems = parse_fields({"l": {"tag": "X", "align": "left"}, "r": {"tag": "X", "align": "right"},
                                      "bad": {"tag": "X", "align": "middle"}})
    assert len(problems) == 1 and aligned["bad"].align == "center"
    get = {"plc/X": (7, "", "good")}.get
    text = render_diagram("[{l    }][{r    }]", aligned, get, "plc", "", colors()).plain
    assert text == "[7      ][      7]"


def test_render_values_pumps_and_pipes():
    """Each placeholder becomes a field exactly as wide as itself; supply pipes are hot, return pipes cold and the
    arrows follow their pipe; a running pump is green, an alarm red, the safety switch yellow; a value with an
    alarm is drawn red and reversed; a placeholder without a field stays visible."""
    fields, _p = parse_fields(FIELDS)
    values = {"plc/320_JP401_V": (True, "", "good"), "plc/320_RT401_PV": (45.25, "°C", "good"),
              "plc/320_RT501_PV": (30.4, "°C", "good"), "plc/320_IE001_V": (False, "", "good"),
              "valve1/RelPos": (37.6, "%", "good")}
    text = render_diagram(TEMPLATE, fields, values.get, "plc", "320_", colors())
    lines = text.plain.split("\n")
    assert len(lines[0]) == len(TEMPLATE.split("\n")[0]) and len(lines[1]) == len(TEMPLATE.split("\n")[1])
    assert "(▶)" in lines[0] and "45.2 °C" in lines[0] and "30 °C" in lines[1] and "stopp" in lines[1]
    assert "38 %" in lines[1] and lines[2] == "{nothing}"
    assert style_at(text, 0) == "HOT" and style_at(text, lines[0].index("▶ ")) == "bold HOT"
    second = len(lines[0]) + 1
    assert style_at(text, second) == "COLD" and style_at(text, second + lines[1].index("◀")) == "bold COLD"
    assert style_at(text, lines[0].index("(▶)")) == "bold OK"
    assert style_at(text, second + lines[1].index("stopp")) == "DIM"
    values.update({"plc/320_JP401_AL": (True, "", "good"), "plc/320_RT401_L_AL": (True, "", "good")})
    text = render_diagram(TEMPLATE, fields, values.get, "plc", "320_", colors())
    assert style_at(text, lines[0].index("(▶)")) == "bold BAD"
    assert style_at(text, lines[0].index("45.2")) == "bold reverse BAD"
    values.update({"plc/320_JP401_AL": (False, "", "good"), "plc/320_JP401_SB": (True, "", "good")})
    text = render_diagram(TEMPLATE, fields, values.get, "plc", "320_", colors())
    assert style_at(text, lines[0].index("(▶)")) == "bold WARN"
    empty = render_diagram(TEMPLATE, fields, {}.get, "plc", "320_", colors()).plain
    assert "–" in empty and "(▶)" in empty                  # no values yet: dashes, a dim pump


def test_parse_systems_diagram():
    """A diagram entry: unknown placeholders and charts are reported; the charts use the fields' main tags and the
    extra connections come from the fields."""
    entry = {"name": "Floor heating", "type": "diagram", "connection": "plc", "prefix": "320_",
             "diagram": TEMPLATE.replace("{nothing}", ""), "fields": FIELDS,
             "charts": {"temperatures": {"Supply": "t_sup", "Return": "t_ret"}, "outputs": {"Valve": "valve"}}}
    vents, _m, problems = parse_systems([entry, {**entry, "diagram": "{x}", "charts": {"bad": {}}},
                                         {**entry, "diagram": ""}])
    assert len(problems) == 3                               # field x missing, bad chart, no drawing
    s = vents[0]
    assert s.kind == "diagram" and s.extra_connections() == ["valve1"]
    assert s.chart_curves() == ([("plc/320_RT401_PV", "Supply"), ("plc/320_RT501_PV", "Return")],
                                [("valve1/RelPos", "Valve")])


def test_tui_diagram(tmp_path, monkeypatch):
    """Ctrl+E shows a diagram entry with values from its own and another connection, live."""
    from knxscada.tui.app import ScadaApp
    from knxscada.tui.systems import SystemsView
    from knxscada.tui.widgets import LiveStatic

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    entry = {"name": "Floor heating", "type": "diagram", "connection": "plc", "prefix": "320_",
             "diagram": "━━{p1}━━ {t_sup  }  {valve}", "fields": FIELDS}
    cfg = Config(path=tmp_path / "config.yaml", server={"socket": "s.sock"}, knx={"enabled": False},
                 ui={"splash": False, "language": "en", "systems": [entry]})
    app = ScadaApp(cfg)
    hub = Hub(FakeServer(tmp_path))
    hub.server._broadcast = lambda msg: app._handle(msg)
    hub.server.clients = {object()}
    plc = Mem("plc", {"tags": {"320_RT401_PV": {"type": "float", "unit": "°C"}, "320_JP401_V": {"type": "bool"}}},
              hub, server=False)
    valve = Mem("valve1", {"tags": {"RelPos": {"type": "float", "unit": "%"}}}, hub, server=False)

    async def fake_request(cmd, **kw):
        """Tag lists and values come from the hub; history is empty."""
        if cmd in ("protocols", "points"):
            return await hub.command({"cmd": cmd, **kw}, "test")
        return []

    async def fake_connect():
        """The server greets without KNX."""
        app._handle({"type": "hello", "state": {}, "status": {"connected": True, "connection": "x"},
                     "alarms": [], "stats": {}, "knx": False})

    monkeypatch.setattr(app.client, "request", fake_request)
    monkeypatch.setattr(app.client, "connect", fake_connect)
    monkeypatch.setattr(type(app.client), "connected", property(lambda self: True))
    monkeypatch.setattr(app.client, "close_nowait", lambda: None)

    async def run():
        """Values set before and after opening the view both reach the drawing."""
        hub.data, hub.load = {"connections": {}, "gateways": []}, (lambda: None)
        try:
            async with app.run_test(size=(160, 45)) as pilot:
                for d in (plc, valve):
                    hub._add(d)
                await hub.start()
                hub.update("plc", "320_RT401_PV", 41.5)
                hub.update("valve1", "RelPos", 12.0)
                await pilot.press("ctrl+e")
                view = app.query_one("#systems", SystemsView)
                card = view.query_one("#sys-card", LiveStatic)
                await until(lambda: "41.5 °C" in str(card.render()) and "12 %" in str(card.render()), 5)
                hub.update("valve1", "RelPos", 55.0)                 # live from the other connection
                await until(lambda: "55 %" in str(card.render()), 5)
        finally:
            await hub.stop()

    asyncio.run(run())
