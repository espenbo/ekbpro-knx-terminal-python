# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""Power limiter card (``type: power`` in ui.systems): parsing, tags of other connections and the drawing."""

from knxscada.tui.power import bar, parse_power, render_power, tag_key
from knxscada.tui.systems import Colors, parse_systems

ENTRY = {
    "name": "Power limiter",
    "type": "power",
    "connection": "plc",
    "tags": {
        "consumption": {"connection": "knx", "tag": "0/1/30"},
        "setpoint": "forbrukSetpunkt",
        "overuse": "overforbruk",
        "peak": {"connection": "bac", "tag": "rkWMaxMoth"},
    },
    "loads": 3,
    "load_tags": {"name": "sLast_{n}_Navn", "shed": "Last_{n}_kobletUt", "status": "Last_{n}_status",
                  "count": "Last_{n}_gangerUtkoblet", "min_off": "Last_{n}_minUtkoblingstid"},
}


def colors() -> Colors:
    """Distinct colour names, so a span's style tells which rule coloured it."""
    return Colors(ok="OK", warn="WARN", bad="BAD", dim="DIM", accent="ACC")


def values(consumption: float, shed: tuple[int, ...] = ()) -> dict:
    """Hub values for :data:`ENTRY`: three loads (the numbers in *shed* switched off) and a 9 kW limit."""
    v = {"knx/0/1/30": (consumption, "kW", ""), "plc/forbrukSetpunkt": (9.0, "", ""),
         "plc/overforbruk": (consumption - 9.0, "", ""), "bac/rkWMaxMoth": (12.3, "kW", "")}
    for n, name in enumerate(("VVB", "Bath", "EV charger"), 1):
        v[f"plc/sLast_{n}_Navn"] = (name, "", "")
        v[f"plc/Last_{n}_kobletUt"] = (n in shed, "", "")
        v[f"plc/Last_{n}_status"] = (True, "", "")
        v[f"plc/Last_{n}_gangerUtkoblet"] = (float(n), "", "")
        v[f"plc/Last_{n}_minUtkoblingstid"] = (5.0, "", "")
    return v


def test_tag_key_plain_prefixed_and_other_connection():
    """A plain name gets the entry's connection and prefix; ``{connection:, tag:}`` reads another connection."""
    assert tag_key("x", "plc", "P_") == "plc/P_x"
    assert tag_key({"connection": "knx", "tag": "0/1/30"}, "plc", "P_") == "knx/0/1/30"
    assert tag_key({"tag": "y"}, "plc", "") == "plc/y"
    assert tag_key("", "plc", "") is None
    assert tag_key({"connection": "knx"}, "plc", "") is None


def test_parse_power_loads_from_number_and_list():
    """``loads: 3`` makes three numbered loads with ``{n}`` filled in; a list gives fixed names."""
    p, problems = parse_power(ENTRY, "plc", "")
    assert problems == []
    assert [load.number for load in p.loads] == [1, 2, 3]
    assert p.loads[1].keys["shed"] == "plc/Last_2_kobletUt"
    assert p.time_unit == "min"
    p, _ = parse_power({**ENTRY, "loads": ["A", "B"]}, "plc", "")
    assert [(load.number, load.name) for load in p.loads] == [(1, "A"), (2, "B")]


def test_parse_power_reports_unknown_roles():
    """Typos in roles and a missing ``loads`` are reported instead of silently ignored."""
    _p, problems = parse_power({"tags": {"consumtion": "x"}, "load_tags": {"shedd": "y"}}, "plc", "")
    assert len(problems) == 3


def test_parse_systems_power_entry():
    """The Systems view accepts ``type: power``: its extra connections and chart curves come from the roles."""
    vents, _meters, problems = parse_systems([ENTRY])
    assert problems == []
    s = vents[0]
    assert s.kind == "power"
    assert s.extra_connections() == ["knx", "bac"]
    temps, outputs = s.chart_curves()
    assert [k for k, _n in temps] == ["knx/0/1/30", "plc/forbrukSetpunkt"]
    assert outputs == []                       # no shed_count role → no second chart


def test_bar_marks_the_limit_and_colours_by_share():
    """The limit mark sits at 80 % of the bar; green below 80 % of the limit, warning above, alarm over it."""
    c = colors()
    low = bar(3.0, 9.0, 20, c)
    assert low.plain[16] == "│"
    assert any(str(s.style) == "OK" for s in low.spans)
    assert any(str(s.style) == "WARN" for s in bar(8.0, 9.0, 20, c).spans)
    over = bar(10.0, 9.0, 20, c)
    assert any(str(s.style) == "BAD" for s in over.spans)
    assert bar(None, 9.0, 10, c).plain == "░" * 10


def test_render_power_card():
    """The card shows the consumption against the limit, the room left, the loads switched off and their names."""
    p, _ = parse_power(ENTRY, "plc", "")
    vals = values(4.5, shed=(3,))
    text = render_power(p, lambda k: vals.get(k) if k else None, colors(), 110).plain
    lines = text.splitlines()
    assert "4.50 kW / 9.00 kW" in lines[0] and "(50%)" in lines[0]
    assert "Room left 4.50 kW" in lines[1]
    assert "1 of 3 loads switched off" in lines[1]       # counted from the loads without a shed_count tag
    assert "Highest this month 12.30 kW" in lines[1]
    assert len(lines) == 3 + 1 + 3
    assert "EV charger" in lines[-1] and "SWITCHED OFF" in lines[-1] and "3×" in lines[-1] and "5 min" in lines[-1]
    assert "VVB" in lines[4] and "SWITCHED OFF" not in lines[4]


def test_render_power_over_the_limit_and_unknown_values():
    """Over the limit is said in words; with no values at all the card still draws (dashes, no crash)."""
    p, _ = parse_power(ENTRY, "plc", "")
    vals = values(10.0)
    assert "Over the limit by 1.00 kW" in render_power(p, lambda k: vals.get(k) if k else None, colors()).plain
    vals = {**values(2.66), "plc/forbrukSetpunkt": (0.028, "kW", "")}
    assert "(>999%)" in render_power(p, lambda k: vals.get(k) if k else None, colors(), 110).plain
    empty = render_power(p, lambda _k: None, colors(), 100).plain
    assert "–" in empty and "Load 1" in empty


def test_room_left_from_consumption_and_limit_when_the_plc_keeps_overuse_at_zero():
    """A PLC that reports overuse 0 below the limit still gives the real room left (limit − consumption)."""
    p, _ = parse_power(ENTRY, "plc", "")
    vals = {**values(2.76), "plc/overforbruk": (0.0, "", "")}
    text = render_power(p, lambda k: vals.get(k) if k else None, colors(), 110).plain
    assert "Room left 6.24 kW" in text and "-0.00" not in text


def test_loads_with_room_temperature_and_heat_demand():
    """A load list with own tags adds the room columns; a shed heater whose room asks for heat is marked "wanted"."""
    entry = {**ENTRY, "loads": [
        {},
        {"name": "Bath", "tags": {"temp": {"connection": "knx", "tag": "3/0/1"},
                                  "heat": {"connection": "knx", "tag": "3/2/15"}}},
        {"tags": {"heat": {"connection": "knx", "tag": "3/2/0"}}},
    ]}
    p, problems = parse_power(entry, "plc", "")
    assert problems == []
    assert p.loads[1].keys["temp"] == "knx/3/0/1" and p.loads[1].keys["shed"] == "plc/Last_2_kobletUt"
    assert "knx" in p.connections()
    vals = {**values(5.0, shed=(2,)), "knx/3/0/1": (20.4, "°C", ""), "knx/3/2/15": (True, "", ""),
            "knx/3/2/0": (False, "", "")}
    lines = render_power(p, lambda k: vals.get(k) if k else None, colors(), 140).plain.splitlines()
    assert "Heat demand" in lines[3]
    assert "20.4°" in lines[5] and "wanted" in lines[5]
    assert "off" in lines[6] and "wanted" not in lines[6]
    _p, problems = parse_power({**entry, "loads": [{"tags": {"tmp": "x"}}]}, "plc", "")
    assert problems


def test_relay_status_column_and_failed_shedding():
    """A relay tag adds the Relay column; a shed load whose relay still reports on is flagged as a fault."""
    entry = {**ENTRY, "loads": [{"tags": {"relay": {"connection": "knx", "tag": "0/3/1"}}},
                                {"tags": {"relay": {"connection": "knx", "tag": "0/3/4"}}}, {}]}
    p, _ = parse_power(entry, "plc", "")
    vals = {**values(5.0, shed=(1, 2)), "knx/0/3/1": (True, "", ""), "knx/0/3/4": (False, "", "")}
    text = render_power(p, lambda k: vals.get(k) if k else None, colors(), 140)
    lines = text.plain.splitlines()
    assert "Relay" in lines[3]
    assert "⚠ on" in lines[4] and "○ off" in lines[5]
    i = text.plain.index("⚠ on")
    assert any(s.start <= i < s.end and "BAD" in str(s.style) for s in text.spans)


def test_plain_loads_keep_the_narrow_table():
    """Without room tags the table has no room columns."""
    p, _ = parse_power(ENTRY, "plc", "")
    plain = render_power(p, lambda _k: None, colors(), 140).plain
    assert "Heat demand" not in plain and "Relay" not in plain


def test_running_column_only_with_a_status_tag():
    """The PLC's status column ("Running") is drawn only when a load has a status tag."""
    entry = {**ENTRY, "load_tags": {k: v for k, v in ENTRY["load_tags"].items() if k != "status"}}
    p, _ = parse_power(entry, "plc", "")
    assert "Running" not in render_power(p, lambda _k: None, colors(), 140).plain
    p, _ = parse_power(ENTRY, "plc", "")
    assert "Running" in render_power(p, lambda _k: None, colors(), 140).plain


def test_inverted_relay_shows_whether_the_load_gets_power():
    """relay_inverted: a contactor that cuts the load when on – feedback on reads "off", and a shed load is fine."""
    entry = {**ENTRY, "loads": [{"relay_inverted": True, "tags": {"relay": {"connection": "knx", "tag": "0/2/1"}}},
                                {}, {}]}
    p, _ = parse_power(entry, "plc", "")
    assert p.loads[0].relay_inverted and not p.loads[1].relay_inverted
    vals = {**values(5.0, shed=(1,)), "knx/0/2/1": (True, "", "")}
    line = render_power(p, lambda k: vals.get(k) if k else None, colors(), 140).plain.splitlines()[4]
    assert "○ off" in line and "⚠" not in line


def test_systems_view_takes_knx_values_from_telegrams():
    """KNX values are not in the hub's points messages: the Systems and Energy views read them from telegrams."""
    from types import SimpleNamespace

    from knxscada.tui.energy import EnergyView
    from knxscada.tui.systems import SystemsView

    for cls in (SystemsView, EnergyView):
        got = []
        fake = SimpleNamespace(connections=lambda: ["plc", "knx"], on_points=got.append)
        cls.on_telegram(fake, {"type": "write", "dst": "0/1/30", "value": 2.5, "ts": 1.0})
        cls.on_telegram(fake, {"type": "read", "dst": "0/1/30", "value": None, "ts": 2.0})
        assert got == [{"knx/0/1/30": [2.5, 1.0, "good"]}]
        fake.connections = lambda: ["plc"]
        cls.on_telegram(fake, {"type": "write", "dst": "0/1/30", "value": 3.0, "ts": 3.0})
        assert len(got) == 1
