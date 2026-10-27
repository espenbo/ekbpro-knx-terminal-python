# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""Interpretation of the ETS project: group addresses, rooms, cards and their rows.

The tests build a small project in the same structure as ``xknxproject``'s
parse result, so no ``.knxproj`` file is needed.
"""

import json
from pathlib import Path

import pytest

from knxscada import project
from knxscada.config import Config

CONFIG = Path(__file__).resolve().parent.parent / "config.yaml"


# ------------------------------------------------------------------ helpers
def co(write=False, read=False, dpts=None):
    return {"flags": {"write": write, "read": read}, "dpts": dpts or []}


def raw_project(functions=True):
    """A tiny project: a dimmable kitchen light, a heating function and an unused address."""
    cos = {
        "C1": co(write=True),                                # switch input of the actuator
        "C2": co(read=True),                                 # switch status output
        "C3": co(write=True, dpts=[{"main": 5, "sub": 1}]),  # brightness (DPT only on the object)
        "C4": co(read=True),                                 # room temperature
        "C5": co(write=True, read=True),                     # setpoint
        "C6": co(write=True, read=True),                     # HVAC mode
        "C7": co(read=True),                                 # heating output
    }
    def ga(name, dpt, ids, desc=""):
        return {"name": name, "dpt": dpt, "communication_object_ids": ids, "description": desc}

    gas = {
        "1/1/1": ga("Kjøkken tak switching", {"main": 1, "sub": 1}, ["C1"]),
        "1/1/2": ga("Kjøkken tak status", {"main": 1, "sub": 1}, ["C2"]),
        "1/1/3": ga("Kjøkken tak value", None, ["C3"]),
        "3/0/1": ga("Kjøkken temperatur", {"main": 9, "sub": 1}, ["C4"]),
        "3/0/2": ga("Kjøkken setpunkt", {"main": 9, "sub": 1}, ["C5"]),
        "3/0/3": ga("Kjøkken driftsmodus", {"main": 20, "sub": 102}, ["C6"]),
        "3/0/4": ga("Kjøkken varmeutgang", {"main": 1, "sub": 1}, ["C7"]),
        "1/1/9": ga("------", {"main": 1, "sub": 1}, []),
    }
    ranges = {
        "1": {"name": "Lys", "group_addresses": [], "group_ranges": {
            "1/1": {"name": "Kjøkken", "group_addresses": ["1/1/1", "1/1/2", "1/1/3", "1/1/9"], "group_ranges": {}}}},
        "3": {"name": "Varme", "group_addresses": [], "group_ranges": {
            "3/0": {"name": "Kjøkken", "group_addresses": ["3/0/1", "3/0/2", "3/0/3", "3/0/4"], "group_ranges": {}}}},
    }
    funcs = {
        "F1": {"name": "Tak", "space_id": "R1", "usage_text": "dimmable light", "function_type": "",
               "group_addresses": {"1/1/1": {"role": "SwitchOnOff"}, "1/1/2": {"role": "InfoOnOff"},
                                   "1/1/3": {"role": "ActualDimmingValue"}}},
        "F2": {"name": "Varme", "space_id": "R1", "usage_text": "heating radiator", "function_type": "",
               "group_addresses": {"3/0/1": {"role": "TempRoom"}, "3/0/2": {"role": "TempRoomSetpoint"},
                                   "3/0/3": {"role": "HVACMode"}, "3/0/4": {"role": ""}}},
    }
    locations = {
        "B": {"identifier": "B", "name": "Hus", "type": "Building", "functions": [], "spaces": {
            "F": {"identifier": "F", "name": "1 etg", "type": "Floor", "functions": [], "spaces": {
                "R1": {"identifier": "R1", "name": "Kjøkken", "type": "Room", "functions": ["F1", "F2"], "spaces": {}},
                "R2": {"identifier": "R2", "name": "Tomt rom", "type": "Room", "functions": [], "spaces": {}},
            }},
        }},
    }
    return {"info": {"name": "Testhus"}, "communication_objects": cos, "group_addresses": gas,
            "group_ranges": ranges, "locations": locations, "functions": funcs if functions else {}}


def card(model, name):
    return next(f for f in model["functions"].values() if f["name"] == name)


def row(func, kind):
    return next(d for d in func["datapoints"] if d["kind"] == kind)


# ------------------------------------------------------------------ group addresses
def test_group_address_flags_and_dpt_inheritance():
    gas = project.build_model(raw_project())["gas"]
    assert gas["1/1/1"]["writable"] and not gas["1/1/1"]["readable"]
    assert gas["1/1/2"]["readable"]
    assert gas["1/1/3"]["dpt"] == "5.001"   # inherited from the communication object
    assert not gas["1/1/9"]["used"] and gas["1/1/9"]["placeholder"]
    assert gas["1/1/1"]["range"] == "Lys / Kjøkken"


def test_addresses_are_sorted_numerically():
    raw = raw_project()
    raw["group_addresses"]["1/1/10"] = dict(raw["group_addresses"]["1/1/9"], name="ten")
    order = list(project.build_model(raw)["gas"])
    assert order.index("1/1/9") < order.index("1/1/10")


# ------------------------------------------------------------------ ETS functions
def test_floors_and_rooms_from_building_structure():
    model = project.build_model(raw_project())
    assert model["source"] == "ets-functions"
    assert [f["name"] for f in model["floors"]] == ["1 etg"]
    assert [r["name"] for r in model["floors"][0]["rooms"]] == ["Kjøkken"]  # empty room dropped


def test_light_card_pairs_switch_with_status():
    light = card(project.build_model(raw_project()), "Tak")
    sw = row(light, "switch")
    assert (sw["ga"], sw["status"]) == ("1/1/1", "1/1/2")
    assert sw["label"] == "On/Off"
    pc = row(light, "percent")
    assert pc["ga"] == "1/1/3" and pc["label"] == "Brightness"
    assert [d["kind"] for d in light["datapoints"]] == ["switch", "percent"]  # row order


def test_heating_card():
    heat = card(project.build_model(raw_project()), "Varme")
    kinds = [d["kind"] for d in heat["datapoints"]]
    assert kinds == ["value", "setpoint", "mode", "bool"]  # room temp, setpoint, mode, output
    temp = heat["datapoints"][0]
    assert temp["role"] == "TempRoom" and temp["ga"] is None and temp["status"] == "3/0/1"
    assert row(heat, "setpoint")["ga"] == "3/0/2"
    assert row(heat, "mode")["ga"] == "3/0/3"
    assert row(heat, "bool")["label"] == "Varmeutgang"  # room and function name stripped


def test_functions_without_room_go_to_other():
    raw = raw_project()
    raw["functions"]["F3"] = {"name": "Løs", "space_id": None, "usage_text": "", "function_type": "",
                              "group_addresses": {"3/0/4": {"role": ""}}}
    floors = project.build_model(raw)["floors"]
    assert floors[-1]["name"] == "Other" and floors[-1]["rooms"][0]["functions"] == ["F3"]


# ------------------------------------------------------------------ fallback: group address structure
def test_fallback_builds_cards_from_group_ranges():
    model = project.build_model(raw_project(functions=False))
    assert model["source"] == "group-addresses"
    assert [f["name"] for f in model["floors"]] == ["Lys", "Varme"]
    light = card(model, "Kjøkken tak")
    sw = row(light, "switch")
    assert (sw["ga"], sw["status"]) == ("1/1/1", "1/1/2")  # "switching"/"status" merged into one card
    assert light["usage"] == "dimmable light"
    # Placeholders and unused addresses never become rows.
    used = {d["ga"] or d["status"] for f in model["functions"].values() for d in f["datapoints"]}
    assert "1/1/9" not in used


# ------------------------------------------------------------------ name helpers
@pytest.mark.parametrize(
    ("name", "token", "expected"),
    [
        ("Sov2 Anna CO2", "Sov 2 Anna", "CO2"),     # spacing differs
        ("kjokken tak", "Kjøkken", "tak"),               # Nordic letters folded
        ("Stue lys", "Kjøkken", "Stue lys"),             # no prefix: unchanged
    ],
)
def test_strip_prefix(name, token, expected):
    assert project._strip_prefix(name, token) == expected


def test_label_maps_plain_words():
    assert project._label("Kjøkken spot switching", "Kjøkken", "Spot") == "On/Off"
    assert project._label("Stue lys verdi", "Stue", "Lys") == "Verdi"


@pytest.mark.parametrize("name", ["------", "", "x", "- - -"])
def test_placeholder_names(name):
    assert project.PLACEHOLDER_RE.match(name)


# ------------------------------------------------------------------ cache
def test_cache_is_used_and_rebuilt_on_version_change(tmp_path):
    cfg = Config(path=tmp_path / "config.yaml",
                 project={"file": "missing.knxproj", "cache": "cache.json"})
    model = project.build_model(raw_project())
    cfg.cache_path.write_text(json.dumps(model), encoding="utf-8")
    # Without the .knxproj the cache is still used.
    assert project.load_project(cfg)["info"]["name"] == "Testhus"
    # An old cache format is not trusted; without the project file that is an error.
    cfg.cache_path.write_text(json.dumps(dict(model, cache_version=-1)), encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        project.load_project(cfg)


@pytest.mark.skipif(not CONFIG.exists(), reason="no local config.yaml")
def test_local_project_cache_is_consistent():
    """Sanity check of the real project (if installed): every card row points at known addresses."""
    from knxscada.config import load_config

    cfg = load_config(CONFIG)
    if not cfg.cache_path.exists():
        pytest.skip("project cache not built")
    model = json.loads(cfg.cache_path.read_text(encoding="utf-8"))
    for f in model["functions"].values():
        for d in f["datapoints"]:
            for addr in (d["ga"], d["status"]):
                assert addr is None or addr in model["gas"]


# --------------------------------------------------------------------------- rooms from group address names
def _ga(addr, name, dpt, writable=True):
    return {"address": addr, "name": name, "description": "", "dpt": dpt, "writable": writable, "readable": True,
            "used": True, "placeholder": False, "range": ""}


def test_rooms_from_names():
    gas = {a: _ga(a, n, d, w) for a, n, d, w in [
        ("0/0/0", "av/pa Kontor 1 tak", "1.001", True),
        ("0/1/0", "av/pa Status kontor 1 tak", "1.001", False),
        ("0/3/0", "Bright Value Kontor 1 tak", "5.001", True),
        ("0/0/1", "av/pa Kontor 1 Nedhengt", "1.001", True),
        ("3/0/0", "Er Verdi Kontor 1", "9.001", False),
        ("3/2/0", "Set temp Kontor 1", "9.001", True),
        ("3/6/0", "Kontor 1 Request", "1.001", False),
        ("3/0/9", "Er Verdi Spiserom", "9.001", False),
        ("5/0/0", "Time", "10.001", False),                      # no room in the name
    ]}
    ranges = [{"address": "0", "name": "Lys 2 etg", "gas": [], "children": [
                  {"address": "0/0", "name": "av/pa", "gas": ["0/0/0", "0/0/1"], "children": []}]},
              {"address": "3", "name": "Varme 2 etg", "gas": [], "children": []},
              {"address": "5", "name": "Styringer", "gas": [], "children": [
                  {"address": "5/0", "name": "Z 41", "gas": ["5/0/0"], "children": []}]}]
    rule = {"pattern": r"(?i)\b(kontor\s*\d+|spiserom)\b", "floors": {0: "2. etg", 3: "2. etg"},
            "names": {"2. etg/Kontor 1": "Kontor 1 · RT6201"}}
    floors, functions = project.build_from_names(ranges, gas, rule)
    assert [f["name"] for f in floors] == ["2. etg", "Styringer"]
    rooms = {r["name"]: r for r in floors[0]["rooms"]}
    assert list(rooms) == ["Kontor 1 · RT6201", "Spiserom"]
    cards = {functions[f]["name"]: functions[f] for f in rooms["Kontor 1 · RT6201"]["functions"]}
    assert set(cards) == {"Lys tak", "Lys Nedhengt", "Varme"}          # floor words removed from the title
    tak = cards["Lys tak"]["datapoints"]
    assert tak[0]["kind"] == "switch" and tak[0]["ga"] == "0/0/0" and tak[0]["status"] == "0/1/0"
    assert {d["label"] for d in tak} == {"Av/pa", "Bright Value"}
    varme = {d["label"] for d in cards["Varme"]["datapoints"]}
    assert {"Er Verdi", "Request"} <= varme                             # "Kontor 1 Request": label after the room
    # Addresses without a room keep the group address structure.
    assert floors[1]["rooms"][0]["name"] == "Z 41"


def test_rooms_setpoint_and_read_only():
    gas = {a: _ga(a, n, d) for a, n, d in [
        ("2/0/0", "Er-Verdi - rom H1001 - VF", "9.001"),
        ("2/1/0", "Set Temp - rom H1001 - VF", "9.001"),
        ("2/2/0", "Onsket Temp - rom H1001 - VF", "9.001"),
        ("2/3/0", "Varmepaadrag - rom H1001 - VF", "5.001"),
    ]}
    ranges = [{"address": "2", "name": "1. Etg Varme", "gas": [], "children": []}]
    rule = {"pattern": r"(?:\brom\s+)?\b(H1\d{3})\b.*", "floors": {2: "1. etg"}}
    kinds = lambda r: {d["label"]: (d["kind"], d["ga"]) for f in project.build_from_names(ranges, gas, r)[1].values()
                       for d in f["datapoints"]}
    # Without the settings "Onsket" is a built-in setpoint word and the W flag makes the valve a control.
    assert kinds(rule)["Set Temp"] == ("value", None)
    assert kinds(rule)["Onsket Temp"] == ("setpoint", "2/2/0")
    assert kinds(rule)["Varmepaadrag"] == ("percent", "2/3/0")
    k = kinds(dict(rule, setpoint="(?i)set temp", read_only="(?i)onsket temp|varmepaadrag"))
    assert k["Set Temp"] == ("setpoint", "2/1/0")
    assert k["Onsket Temp"] == ("value", None) and k["Varmepaadrag"] == ("value", None)
    assert k["Er-Verdi"] == ("value", None)


def test_rooms_rule_is_used_only_without_ets_functions():
    raw = raw_project()
    with_functions = project.build_model(raw, rooms={"pattern": "(?i)kitchen"})
    assert with_functions["source"] == "ets-functions"


def test_extra_rooms():
    model = project.build_model(raw_project())
    gas = list(model["gas"])
    switch = next(a for a, g in model["gas"].items() if g["dpt"] == "1.001" and g["writable"])
    temp = next(a for a, g in model["gas"].items() if g["dpt"] == "9.001")
    project.add_extra_rooms(model, [{"floor": "Attic", "name": "Loft", "cards": [
        {"name": "Temperature", "addresses": [temp]},
        {"name": "Light", "addresses": [switch], "status": {switch: temp}, "usage": "light"},
    ]}])
    floor = next(f for f in model["floors"] if f["name"] == "Attic")
    (room,) = floor["rooms"]
    assert room["name"] == "Loft"
    t, light = (model["functions"][f] for f in room["functions"])
    assert t["usage"] == "heating radiator" and t["datapoints"][0]["status"] == temp
    assert light["usage"] == "switchable light"
    assert light["datapoints"][0]["ga"] == switch and light["datapoints"][0]["status"] == temp
    assert len(gas) == len(model["gas"])                  # nothing added to the addresses


def test_extra_rooms_reject_unknown_addresses():
    model = project.build_model(raw_project())
    with pytest.raises(ValueError, match="31/7/255"):
        project.add_extra_rooms(model, [{"name": "X", "cards": [{"addresses": ["31/7/255"]}]}])
