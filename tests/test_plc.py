# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""PLC mapping: names, types, directions, the mapping file, the GVL and value conversion."""

import pytest

from knxscada import plc, project
from tests.test_project import raw_project


@pytest.fixture
def model():
    """The project model built from the test project."""
    return project.build_model(raw_project())


@pytest.mark.parametrize(("dpt", "typ"), [("1.001", "BOOL"), ("5.001", "REAL"), ("5.010", "USINT"), ("7.001", "UINT"),
                                          ("9.001", "REAL"), ("13.010", "DINT"), ("14.056", "REAL"), ("16.000", "STRING(14)"),
                                          ("17.001", "USINT"), ("20.102", "USINT"), ("3.007", None), ("10.001", None), (None, None)])
def test_plc_type(dpt, typ):
    """The PLC data type per DPT; DPTs without a sensible IEC type are not supported (None)."""
    assert plc.plc_type(dpt) == typ


@pytest.mark.parametrize(("addr", "name", "var"), [
    ("1/1/5", "Hall ceiling light switching", "GA_1_1_5_Hall_ceiling_light_switching"),
    ("3/0/1", "Kjøkken temp. (°C)", "GA_3_0_1_Kjokken_temp_C"),
    ("0/0/1", "Ærfugl Åsen ØST", "GA_0_0_1_Aerfugl_Asen_OST"),
    ("1/2/3", "---", "GA_1_2_3"),
])
def test_var_name(addr, name, var):
    """GVL variable names are valid IEC identifiers: Norwegian letters are spelled out, symbols dropped, no '__'."""
    assert plc.var_name(addr, name) == var
    assert plc.IDENT_RE.match(var) and "__" not in var


def test_var_name_is_limited():
    """Long ETS names are cut so the variable name stays within the PLC's limit."""
    assert len(plc.var_name("31/7/255", "x" * 200)) <= plc.VAR_MAX


def test_default_direction(model):
    """Controls on a card are exchanged both ways by default, feedback and sensors only to the PLC."""
    assert plc.default_direction("1/1/1", model) == "both"     # switching (control on a card)
    assert plc.default_direction("3/0/2", model) == "both"     # setpoint
    assert plc.default_direction("1/1/2", model) == "to_plc"   # feedback
    assert plc.default_direction("3/0/1", model) == "to_plc"   # sensor


def test_build_write_load_round_trip(tmp_path, model):
    """A generated plc_map.yaml has nothing active until lines are marked, skips placeholders and reads back unchanged.
    """
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
    """Rebuilding the map after a project change keeps the user's choices (use, direction, own name) and reports removed
    addresses.
    """
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
    """select: all makes every line active, marked or not."""
    pmap, _u, _r = plc.build_map(model)
    pmap.select = "all"
    assert len(pmap.active()) == len(pmap.entries)


def test_load_map_reports_errors(tmp_path, model):
    """Errors in a hand-edited map (select, direction, invalid or duplicate names, unknown address) are all reported in
    one message.
    """
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
    """The GVL declares only the marked lines with their types, and has linkalways so CODESYS keeps it in the symbol
    configuration.
    """
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
    """Values convert between KNX and the PLC type and back, enums as their number."""
    e = plc.PlcEntry(ga="1/1/1", on=True, dir="both", type=typ, var="x", dpt=dpt)
    assert plc.to_plc_value(e, knx) == plc_value
    assert plc.to_knx_value(e, plc_value) == knx


def test_value_conversion_bad_input():
    """Missing or non-numeric values are not sent to the PLC."""
    e = plc.PlcEntry(ga="1/1/1", on=True, dir="both", type="REAL", var="x", dpt="9.001")
    assert plc.to_plc_value(e, None) is None
    assert plc.to_plc_value(e, "text") is None


def test_same_value_is_loose_for_float32():
    """A float that comes back from a REAL (float32) counts as the same, so it is not echoed as a change."""
    import struct
    as_float32 = struct.unpack("f", struct.pack("f", 21.4))[0]   # what the PLC's REAL gives back
    assert plc.same_value(21.4, as_float32)
    assert not plc.same_value(21.4, 21.5)
    assert plc.same_value(True, True) and not plc.same_value(True, False)


def test_on_is_accepted_as_alias_of_use(tmp_path, model):
    """'on:' and 'use:' both mark a line in plc_map.yaml."""
    # YAML 1.1 reads the key "on" as True – a hand-written "on: true" must still work.
    path = tmp_path / "plc_map.yaml"
    path.write_text('addresses:\n  - {ga: "3/0/1", on: true, dir: to_plc, type: REAL, var: X}\n'
                    '  - {ga: "3/0/2", use: true, dir: both, type: REAL, var: Y}\n'
                    '  - {ga: "1/1/1", use: false, dir: both, type: BOOL, var: Z}\n', encoding="utf-8")
    assert [e.ga for e in plc.load_map(path, model["gas"]).active()] == ["3/0/1", "3/0/2"]


# --------------------------------------------------------------------------- names from tagged PLC objects
def test_tag_name_from_object_texts():
    """The tag name is built from a KNX object text in the PLC (line, system, component, signal) with the site prefix;
    plain texts give none.
    """
    from knxscada.plc import tag_name

    assert tag_name(["KNX_Linje_M1.M1_000_564_002_RT6101._01_Rom_SP"], "BY1234") == "BY1234_564_002_RT6101_Rom_SP"
    assert tag_name(["KNX_Linje_M1.M1_031_564_001_XG101_Un"], "BY1234") == "BY1234_564_001_XG101_Un"
    assert tag_name(["Switch", "Status"], "BY1234") is None


def test_tagged_address_gets_the_tag_and_is_only_read():
    """With a tag prefix an address written by the PLC's own KNX object gets the tag name and goes to_plc only;
    duplicates and others keep GA_ names, and --rename renames existing lines.
    """
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
    """The single-PLC form gives one controller with the default name, map file and GVL."""
    from knxscada.plc import plc_configs

    (c,) = plc_configs({"enabled": True, "endpoint": "opc.tcp://a:4840"})
    assert (c["name"], c["map"], c["gvl"]) == ("plc", "plc_map.yaml", "GVL_KNX")


def test_plc_configs_several_with_defaults():
    """With controllers, shared settings come from the plc: level, but endpoint, map and GVL are per PLC."""
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
    """Duplicate names (case-insensitive), a missing name and names with spaces are refused."""
    from knxscada.plc import plc_configs

    with pytest.raises(ValueError):
        plc_configs({"controllers": controllers})


def _entries():
    """Three mapping lines, one per direction, as plc_map.yaml would have them (use: true)."""
    return [plc.PlcEntry("1/1/1", True, "to_plc", "REAL", "GA_1_1_1_Temp", "Temperatur", "9.001"),
            plc.PlcEntry("1/1/2", True, "both", "BOOL", "GA_1_1_2_Lys", "Lys", "1.001"),
            plc.PlcEntry("1/1/3", True, "to_knx", "USINT", "GA_1_1_3_Modus", "Modus", "20.102")]


def test_gateway_config_from_the_bridge_mapping():
    """``plc-gateway``: the bridge's mapping becomes an OPC UA client with one tag per variable (node id from the GVL
    prefix, writable only towards the PLC) and two gateways – KNX → PLC with initial values (as the bridge synced the
    PLC at start-up) and PLC → KNX – with the bridge's rate limit and guard settings."""
    from knxscada.protocols import config as pconf

    cfg = {"name": "plc", "endpoint": "opc.tcp://192.168.1.10:4840", "username": "admin", "password": "x",
           "security": "None", "min_interval_ms": 300, "burst_limit": 6}
    name, conn, gws = plc.gateway_config(cfg, _entries(), "|var|PLC.Application.GVL_KNX.", 4)
    assert name == "plc" and conn["protocol"] == "opcua" and conn["role"] == "client"
    assert conn["password"] == "x" and conn["endpoint"].endswith(":4840")
    t = conn["tags"]
    assert t["GA_1_1_1_Temp"] == {"node": "ns=4;s=|var|PLC.Application.GVL_KNX.GA_1_1_1_Temp", "type": "float",
                                  "writable": True, "description": "Temperatur"}
    assert t["GA_1_1_2_Lys"]["type"] == "bool" and t["GA_1_1_3_Modus"]["type"] == "int"
    assert t["GA_1_1_3_Modus"]["writable"] is False          # PLC → KNX only: the PLC variable is never written
    to_plc, to_knx = gws
    assert to_plc["name"] == "knx-plc" and to_plc["from"] == "knx" and to_plc["initial"] is True
    assert to_plc["map"] == [{"from": "1/1/1", "to": "GA_1_1_1_Temp"},
                             {"from": "1/1/2", "to": "GA_1_1_2_Lys", "direction": "both", "min_interval_ms": 300}]
    assert to_knx["name"] == "plc-knx" and to_knx["map"] == [{"from": "GA_1_1_3_Modus", "to": "1/1/3"}]
    assert to_knx["min_interval_ms"] == 300 and to_knx["burst_limit"] == 6 and to_plc["burst_limit"] == 6
    data = {"connections": {name: conn}, "gateways": gws}
    assert pconf.validate(data, knx=True) == []


#: A bridge set up with encryption, its own client certificate, a pinned PLC certificate and a longer timeout.
_SECURE_PLC = {"name": "plc", "endpoint": "opc.tcp://192.168.1.10:4840", "username": "admin", "password": "x",
               "security": "Basic256Sha256,SignAndEncrypt", "timeout_s": 25,
               "certificate": "var/opcua/site_cert.der", "private_key": "var/opcua/site_key.pem",
               "server_certificate": "var/opcua/plc_cert.der"}


def test_gateway_config_keeps_the_security_settings():
    """``plc-gateway`` copies the certificate pinning, the client certificate and key and the timeout unchanged
    (paths stay relative to config.yaml, as for the bridge), and the result passes validation."""
    from knxscada.protocols import config as pconf

    name, conn, _gws = plc.gateway_config(_SECURE_PLC, _entries(), "P.", 4)
    for key in ("security", "timeout_s", "certificate", "private_key", "server_certificate", "username", "password"):
        assert conn[key] == _SECURE_PLC[key], key
    assert pconf.validate({"connections": {name: conn}, "gateways": _gws}, knx=True) == []


def test_gateway_config_adds_no_empty_security_settings():
    """A bridge without certificates or timeout gives a connection without those keys, so the driver's defaults
    (the same as the bridge's) apply – no empty path that would resolve to the config folder."""
    _name, conn, _gws = plc.gateway_config({"name": "plc", "endpoint": "opc.tcp://192.168.1.10:4840",
                                             "server_certificate": "", "certificate": None}, _entries(), "P.", 4)
    assert not {"timeout_s", "certificate", "private_key", "server_certificate"} & conn.keys()


def test_opcua_security_settings_are_validated():
    """The OPC UA connection knows the settings now: a timeout that is not a number is refused."""
    from knxscada.protocols import config as pconf

    conn = {"protocol": "opcua", "endpoint": "opc.tcp://192.168.1.10:4840", "timeout_s": "ten"}
    errors = pconf.validate({"connections": {"plc": conn}})
    assert len(errors) == 1 and "timeout_s" in errors[0]


def test_migrated_security_settings_survive_saving(tmp_path):
    """Written with --write (``config.save``) and read back, the settings are unchanged; the file stays 0600 because
    it holds the password, and the previous file is kept as ``.bak``."""
    from knxscada.protocols import config as pconf

    name, conn, gws = plc.gateway_config(_SECURE_PLC, _entries(), "P.", 4)
    path = tmp_path / "protocols.yaml"
    pconf.save(path, {"connections": {name: conn}, "gateways": gws}, knx=True)
    pconf.save(path, pconf.load_data(path), knx=True)          # a second save, as F11 does after an edit
    back = pconf.load_data(path)["connections"][name]
    for key in ("security", "timeout_s", "certificate", "private_key", "server_certificate", "password"):
        assert back[key] == _SECURE_PLC[key], key
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.with_suffix(".yaml.bak").exists()


def test_migrated_connection_pins_the_plc_certificate(tmp_path, monkeypatch):
    """The migrated connection, as the OPC UA driver gets it (``settings_of``), makes the same client as the bridge:
    the pinned PLC certificate, the site's own certificate and key (resolved against config.yaml) and the timeout.
    Nothing connects – ``set_security`` is recorded and certificate creation is skipped."""
    import asyncio

    from asyncua import Client

    from knxscada import opcua_bridge
    from knxscada.config import Config
    from knxscada.protocols import config as pconf

    seen: dict = {}

    async def set_security(self, policy, cert, key, server_certificate=None, mode=None):
        """Record what the client would use instead of loading certificate files."""
        seen.update(policy=policy.__name__, cert=cert, key=key, pinned=server_certificate, mode=mode)

    async def no_certificate(cert, key):
        """Skip making a self-signed certificate: only the paths matter here."""

    monkeypatch.setattr(Client, "set_security", set_security)
    monkeypatch.setattr(opcua_bridge, "ensure_certificate", no_certificate)
    _name, conn, _gws = plc.gateway_config(_SECURE_PLC, _entries(), "P.", 4)
    cfg = Config(path=tmp_path / "config.yaml")
    client = asyncio.run(opcua_bridge.make_client(cfg, pconf.settings_of(conn)))
    assert seen["pinned"] == str(tmp_path / "var/opcua/plc_cert.der")
    assert seen["cert"] == str(tmp_path / "var/opcua/site_cert.der")
    assert seen["key"] == str(tmp_path / "var/opcua/site_key.pem")
    assert seen["policy"] == "SecurityPolicyBasic256Sha256" and seen["mode"].name == "SignAndEncrypt"
    assert client.uaclient._timeout == 25


def test_bridge_mapping_through_the_gateways(tmp_path):
    """The generated gateways move values as the bridge did: KNX statuses fill the PLC at start-up, the PLC's own
    start values never reach KNX, a two-way light and a PLC → KNX mode go to KNX after the settle time."""
    import asyncio

    from tests.test_protocols import Mem, make_hub, until

    _name, _conn, gws = plc.gateway_config({"name": "plc", "settle_ms": 50}, _entries(), "P.", 4)

    async def run():
        """KNX and the PLC as in-memory connections, with the generated gateways between them."""
        knx = Mem("knx", {"tags": {"1/1/1": {}, "1/1/2": {}, "1/1/3": {}}}, None, initial_allowed=False, server=False,
                  reads_first=True)
        dev = Mem("plc", {"tags": {"GA_1_1_1_Temp": {}, "GA_1_1_2_Lys": {}, "GA_1_1_3_Modus": {}}}, None,
                  server=False, reads_first=True)
        hub = await make_hub(tmp_path, {"gateways": gws}, {"knx": knx, "plc": dev})
        try:
            hub.update("plc", "GA_1_1_2_Lys", False)          # the PLC's start values: statuses
            hub.update("plc", "GA_1_1_3_Modus", 1)
            dev.set_ready()
            hub.update("knx", "1/1/1", 21.5)                   # KNX statuses read at start-up
            hub.update("knx", "1/1/2", True)
            knx.set_ready()
            await until(lambda: ("GA_1_1_1_Temp", 21.5) in dev.writes and ("GA_1_1_2_Lys", True) in dev.writes, 2)
            await asyncio.sleep(0.1)
            assert knx.writes == []                            # nothing to KNX at start-up
            hub.update("plc", "GA_1_1_2_Lys", False)           # the PLC switches the light off
            hub.update("plc", "GA_1_1_3_Modus", 3)             # and sets the mode
            await until(lambda: ("1/1/2", False) in knx.writes and ("1/1/3", 3) in knx.writes, 2)
        finally:
            await hub.stop()

    asyncio.run(run())
