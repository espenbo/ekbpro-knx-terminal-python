# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""PLC mapping: names, types, directions, the mapping file, the GVL and value conversion."""

import pytest

from knxscada import plc, project
from tests.test_project import raw_project


@pytest.fixture
def model():
    return project.build_model(raw_project())


@pytest.mark.parametrize(("dpt", "typ"), [("1.001", "BOOL"), ("5.001", "REAL"), ("5.010", "USINT"), ("7.001", "UINT"),
                                          ("9.001", "REAL"), ("13.010", "DINT"), ("14.056", "REAL"), ("16.000", "STRING(14)"),
                                          ("17.001", "USINT"), ("20.102", "USINT"), ("3.007", None), ("10.001", None), (None, None)])
def test_plc_type(dpt, typ):
    assert plc.plc_type(dpt) == typ


@pytest.mark.parametrize(("addr", "name", "var"), [
    ("1/1/5", "Hall ceiling light switching", "GA_1_1_5_Hall_ceiling_light_switching"),
    ("3/0/1", "Kjøkken temp. (°C)", "GA_3_0_1_Kjokken_temp_C"),
    ("0/0/1", "Ærfugl Åsen ØST", "GA_0_0_1_Aerfugl_Asen_OST"),
    ("1/2/3", "---", "GA_1_2_3"),
])
def test_var_name(addr, name, var):
    assert plc.var_name(addr, name) == var
    assert plc.IDENT_RE.match(var) and "__" not in var


def test_var_name_is_limited():
    assert len(plc.var_name("31/7/255", "x" * 200)) <= plc.VAR_MAX


def test_default_direction(model):
    assert plc.default_direction("1/1/1", model) == "both"     # switching (control on a card)
    assert plc.default_direction("3/0/2", model) == "both"     # setpoint
    assert plc.default_direction("1/1/2", model) == "to_plc"   # feedback
    assert plc.default_direction("3/0/1", model) == "to_plc"   # sensor


def test_build_write_load_round_trip(tmp_path, model):
    pmap, unsupported, removed = plc.build_map(model)
    assert pmap.select == "marked" and not pmap.active()        # nothing exchanged until marked
    assert "1/1/9" not in {e.ga for e in pmap.entries}           # placeholder / unused
    assert unsupported == [] and removed == []
    path = tmp_path / "plc_map.yaml"
    plc.write_map(pmap, path)
    loaded = plc.load_map(path, model["gas"])
    assert [(e.ga, e.dir, e.type, e.var) for e in loaded.entries] == [(e.ga, e.dir, e.type, e.var) for e in pmap.entries]
    assert loaded.entries[0].name                                 # names come from the project


def test_regeneration_keeps_choices(tmp_path, model):
    path = tmp_path / "plc_map.yaml"
    pmap, _u, _r = plc.build_map(model)
    plc.write_map(pmap, path)
    text = path.read_text(encoding="utf-8")
    text = text.replace('{ga: "3/0/1",   use: false, dir: to_plc', '{ga: "3/0/1",   use: true,  dir: both  ')
    text = text.replace("var: GA_3_0_1_Kjokken_temperatur", "var: rKitchenTemp")
    path.write_text(text, encoding="utf-8")
    raw = raw_project()
    del raw["group_addresses"]["3/0/4"]                           # removed from the project
    raw["functions"]["F2"]["group_addresses"].pop("3/0/4")
    new, _u, removed = plc.build_map(project.build_model(raw), plc.load_map(path))
    e = next(e for e in new.entries if e.ga == "3/0/1")
    assert (e.on, e.dir, e.var) == (True, "both", "rKitchenTemp")
    assert removed == ["3/0/4"]
    assert [x.ga for x in new.active()] == ["3/0/1"]


def test_select_all(model):
    pmap, _u, _r = plc.build_map(model)
    pmap.select = "all"
    assert len(pmap.active()) == len(pmap.entries)


def test_load_map_reports_errors(tmp_path, model):
    path = tmp_path / "plc_map.yaml"
    path.write_text('select: some\naddresses:\n'
                    '  - {ga: "1/1/1", on: true, dir: sideways, type: BOOL, var: 1bad}\n'
                    '  - {ga: "1/1/2", on: true, dir: to_plc, type: BOOL, var: a__b}\n'
                    '  - {ga: "3/0/1", on: true, dir: to_plc, type: REAL, var: X}\n'
                    '  - {ga: "3/0/2", on: true, dir: both, type: REAL, var: x}\n'
                    '  - {ga: "9/9/9", on: true, dir: both, type: REAL, var: Y}\n', encoding="utf-8")
    with pytest.raises(ValueError) as err:
        plc.load_map(path, model["gas"])
    msg = str(err.value)
    for part in ("select", "sideways", "1bad", "a__b", "used twice", "9/9/9"):
        assert part in msg


def test_gvl(model):
    pmap, _u, _r = plc.build_map(model)
    for e in pmap.entries:
        e.on = e.ga in ("1/1/1", "3/0/1")
    text = plc.gvl_text(pmap)
    assert "VAR_GLOBAL" in text and "END_VAR" in text
    assert "{attribute 'linkalways'}" in text      # else CODESYS drops an unused GVL from the symbols
    assert "GA_1_1_1_Kjokken_tak_switching" in text and ": BOOL;" in text
    assert ": REAL;" in text
    assert "GA_1_1_2" not in text                                 # not marked


@pytest.mark.parametrize(("dpt", "typ", "knx", "plc_value"), [
    ("1.001", "BOOL", True, True), ("9.001", "REAL", 21.5, 21.5), ("5.001", "REAL", 40, 40.0),
    ("7.001", "UINT", 1000, 1000), ("20.102", "USINT", "ECONOMY", 3), ("16.000", "STRING(14)", "Hello", "Hello"),
])
def test_value_conversion_round_trip(dpt, typ, knx, plc_value):
    e = plc.PlcEntry(ga="1/1/1", on=True, dir="both", type=typ, var="x", dpt=dpt)
    assert plc.to_plc_value(e, knx) == plc_value
    assert plc.to_knx_value(e, plc_value) == knx


def test_value_conversion_bad_input():
    e = plc.PlcEntry(ga="1/1/1", on=True, dir="both", type="REAL", var="x", dpt="9.001")
    assert plc.to_plc_value(e, None) is None
    assert plc.to_plc_value(e, "text") is None


def test_same_value_is_loose_for_float32():
    import struct
    as_float32 = struct.unpack("f", struct.pack("f", 21.4))[0]   # what the PLC's REAL gives back
    assert plc.same_value(21.4, as_float32)
    assert not plc.same_value(21.4, 21.5)
    assert plc.same_value(True, True) and not plc.same_value(True, False)


def test_on_is_accepted_as_alias_of_use(tmp_path, model):
    # YAML 1.1 reads the key "on" as True – a hand-written "on: true" must still work.
    path = tmp_path / "plc_map.yaml"
    path.write_text('addresses:\n  - {ga: "3/0/1", on: true, dir: to_plc, type: REAL, var: X}\n'
                    '  - {ga: "3/0/2", use: true, dir: both, type: REAL, var: Y}\n'
                    '  - {ga: "1/1/1", use: false, dir: both, type: BOOL, var: Z}\n', encoding="utf-8")
    assert [e.ga for e in plc.load_map(path, model["gas"]).active()] == ["3/0/1", "3/0/2"]


# --------------------------------------------------------------------------- names from tagged PLC objects
def test_tag_name_from_object_texts():
    from knxscada.plc import tag_name

    assert tag_name(["KNX_Linje_M1.M1_000_564_002_RT6101._01_Rom_SP"], "BY1234") == "BY1234_564_002_RT6101_Rom_SP"
    assert tag_name(["KNX_Linje_M1.M1_031_564_001_XG101_Un"], "BY1234") == "BY1234_564_001_XG101_Un"
    assert tag_name(["Switch", "Status"], "BY1234") is None


def test_tagged_address_gets_the_tag_and_is_only_read():
    from knxscada.plc import build_map

    model = {"gas": {
        "2/2/5": {"name": "Set Temp Kontor 1", "dpt": "9.001", "used": True, "placeholder": False,
                  "objects": ["KNX_Linje_M1.M1_000_564_002_RT6101._01_Rom_SP"]},
        "2/2/6": {"name": "Set Temp Kontor 2", "dpt": "9.001", "used": True, "placeholder": False,
                  "objects": ["KNX_Linje_M1.M1_000_564_002_RT6101._01_Rom_SP"]},   # same tag twice
        "0/0/1": {"name": "Light", "dpt": "1.001", "used": True, "placeholder": False, "objects": []},
    }, "functions": {"f": {"datapoints": [{"ga": "2/2/5"}, {"ga": "0/0/1"}]}}}
    pmap, _unsupported, _removed = build_map(model, tag_prefix="BY1234")
    by = {e.ga: e for e in pmap.entries}
    assert by["2/2/5"].var == "BY1234_564_002_RT6101_Rom_SP"
    assert by["2/2/5"].dir == "to_plc"            # the PLC's own KNX object writes it
    assert by["2/2/6"].var.startswith("GA_2_2_6")  # no duplicate names
    assert by["0/0/1"].var.startswith("GA_0_0_1") and by["0/0/1"].dir == "both"
    # Without a prefix nothing changes.
    plain, _u, _r = build_map(model)
    assert all(e.var.startswith("GA_") for e in plain.entries)
    # --rename gives existing lines the new names.
    renamed, _u, _r = build_map(model, plain, tag_prefix="BY1234", rename=True)
    assert {e.ga: e.var for e in renamed.entries}["2/2/5"] == "BY1234_564_002_RT6101_Rom_SP"


# --------------------------------------------------------------------------- several PLCs
def test_plc_configs_single_form():
    from knxscada.plc import plc_configs

    (c,) = plc_configs({"enabled": True, "endpoint": "opc.tcp://a:4840"})
    assert (c["name"], c["map"], c["gvl"]) == ("plc", "plc_map.yaml", "GVL_KNX")


def test_plc_configs_several_with_defaults():
    from knxscada.plc import plc_configs

    a, b = plc_configs({"enabled": True, "username": "knx", "endpoint": "ignored",
                        "controllers": [{"name": "pfc300", "endpoint": "opc.tcp://a:4840"},
                                        {"name": "board2", "endpoint": "opc.tcp://b:4840", "gvl": "GVL_B",
                                         "enabled": False}]})
    assert a["username"] == b["username"] == "knx"            # defaults from the plc: level
    assert a["endpoint"] == "opc.tcp://a:4840"                  # per-PLC keys are not inherited
    assert (a["map"], b["map"]) == ("plc_map_pfc300.yaml", "plc_map_board2.yaml")
    assert b["gvl"] == "GVL_B" and a["enabled"] and not b["enabled"]


@pytest.mark.parametrize("controllers", [[{"name": "a"}, {"name": "A"}], [{"endpoint": "x"}], [{"name": "with space"}]])
def test_plc_configs_invalid(controllers):
    from knxscada.plc import plc_configs

    with pytest.raises(ValueError):
        plc_configs({"controllers": controllers})
