# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""Device templates (``model:``): the WAGO 879-30xx meters, merging, validation and identification."""

import asyncio

import pytest

from knxscada.protocols import PROTOCOLS, devices, installed
from knxscada.protocols.config import dump, settings_of, validate

from .test_protocols import make_hub, until, free_port


def test_wago_meters_are_known_by_name_and_number():
    assert {"WAGO 879-3000", "WAGO 879-3020", "WAGO 879-3040"} <= set(devices.names("modbus"))
    assert devices.find("WAGO 879-3020")["model"] == "WAGO 879-3020"
    assert devices.find(" wago  879-3040 ")["number"] == "879-3040"
    assert devices.find("879-3000")["model"] == "WAGO 879-3000"
    assert devices.find("") is None
    with pytest.raises(ValueError, match="unknown device"):
        devices.find("WAGO 879-9999")
    model = next(f for f in PROTOCOLS["modbus"].settings if f.key == "model")
    assert "WAGO 879-3000" in model.choices


def test_wago_template_has_the_35_main_values_named_as_in_the_manual():
    tags = devices.find("WAGO 879-3000")["tags"]
    assert len(tags) == 35
    assert tags["L1 voltage"] == {"address": "hr:0x5002", "type": "float32", "unit": "V", "writable": False}
    assert tags["Total active power"]["address"] == "hr:0x5012"
    assert tags["Total active energy"]["unit"] == "kWh"
    assert all(t["type"] == "float32" for t in tags.values())


def test_template_settings_are_defaults_and_own_tags_are_added():
    conn = {"protocol": "modbus", "model": "WAGO 879-3000", "baudrate": "19200",
            "tags": {"Day register": {"address": "hr:0x6049", "type": "float32"},
                     "L1 voltage": {"description": "incoming"}}}
    s = settings_of(conn)
    assert (s["transport"], s["parity"], s["baudrate"], s["poll_ms"]) == ("rtu", "E", "19200", 5000)
    assert len(s["tags"]) == 36
    assert s["tags"]["L1 voltage"] == {"address": "hr:0x5002", "type": "float32", "unit": "V", "writable": False,
                                       "description": "incoming"}
    # Only what differs from the template is written back to protocols.yaml.
    text = dump({"connections": {"meter1": {**conn, "parity": "E", "transport": "rtu"}}, "gateways": []})
    assert "parity" not in text and "transport" not in text and "baudrate: '19200'" in text
    assert "hr:0x5002" not in text


def test_validate_reports_an_unknown_model():
    data = {"connections": {"m": {"protocol": "modbus", "model": "ABB 123"},
                            "ok": {"protocol": "modbus", "model": "879-3000"}}, "gateways": []}
    errors = validate(data)
    assert len(errors) == 1 and "ABB 123" in errors[0]


def test_describe_and_mismatches():
    tpl = devices.find("WAGO 879-3000")
    found = {"meter_code": "879-3040", "unit_id": 1, "baudrate": "9600", "parity": "N", "serial": "00123456"}
    assert devices.describe(tpl, found) == "WAGO 879-3040 (2PU CT – current transformer)  unit 1  9600 N  serial 00123456"
    problems = devices.mismatches(tpl, found, {"baudrate": "9600", "parity": "E", "unit_id": 1})
    assert len(problems) == 2
    assert "879-3040" in problems[0] and "parity" in problems[1]


@pytest.mark.skipif(not installed("modbus"), reason="pymodbus is not installed")
def test_master_identifies_the_meter(tmp_path):
    """A Modbus slave plays the meter; the master with model: reads its identification and its values."""
    port = free_port()

    async def run():
        meter = {"protocol": "modbus", "role": "server", "host": "127.0.0.1", "port": port, "tags": {
            "code": {"address": "hr:0x4002"}, "id": {"address": "hr:0x4003"}, "baud": {"address": "hr:0x4004"},
            "parity": {"address": "hr:0x4011"}, "serial": {"address": "hr:0x4000", "type": "uint32"},
            "power": {"address": "hr:0x5012", "type": "float32"}}}
        hub = await make_hub(tmp_path, {"connections": {"meter": meter}, "gateways": []})
        try:
            await until(lambda: hub.drivers["meter"].connected)
            for tag, value in (("code", 0x1112), ("id", 1), ("baud", 6), ("parity", 2), ("serial", 0x123456),
                               ("power", 1.5)):
                await hub.write("meter", tag, value)
            # Over TCP for the test; the template would choose RS485.
            hub._make("master", {"protocol": "modbus", "host": "127.0.0.1", "port": port, "transport": "tcp",
                                 "model": "WAGO 879-3000", "poll_ms": 100})
            master = hub.drivers["master"]
            assert len(master.tags) == 35
            await until(lambda: master.ready)
            assert master.identity == "WAGO 879-3020 (4PS – direct connection, 40 A)  unit 1  9600 N  serial 00123456"
            assert "WAGO 879-3020" in master.describe()
            texts = [t for _level, t in hub.server.events]
            assert any("found WAGO 879-3020" in t for t in texts)
            assert any("not a 879-3000" in t for t in texts)
            assert any("parity is N in the device, E in the settings" in t for t in texts)
            assert hub.values["master/Total active power"]["v"] == pytest.approx(1.5)
        finally:
            await hub.stop()

    asyncio.run(run())


# -- Carlo Gavazzi ---------------------------------------------------------------------------------------
@pytest.mark.parametrize(("model", "n_tags", "max_regs", "max_gap"), [
    ("Carlo Gavazzi EM111", 17, 50, 8), ("Carlo Gavazzi EM112", 17, 50, 8), ("Carlo Gavazzi ET112", 18, 50, 8),
    ("Carlo Gavazzi EM270", 36, 18, 8), ("Carlo Gavazzi EM280", 36, 18, 8), ("Carlo Gavazzi EM271", 44, 18, 8),
    ("Carlo Gavazzi EM580", 34, 125, 0), ("Carlo Gavazzi WM15", 40, 125, 8),
])
def test_carlo_gavazzi_templates(model, n_tags, max_regs, max_gap):
    s = settings_of({"protocol": "modbus", "model": model})
    assert len(s["tags"]) == n_tags
    assert (s["parity"], s["word_order"], s["max_registers"]) == ("N", "little", max_regs)
    assert s.get("max_gap", 8) == max_gap
    assert ("Hour counter" in s["tags"]) == model.endswith("ET112")   # only: [ET112]
    assert all("only" not in t for t in s["tags"].values())
    assert validate({"connections": {"m": {"protocol": "modbus", "model": model}}, "gateways": []}) == []


@pytest.mark.skipif(not installed("modbus"), reason="pymodbus is not installed")
def test_blocks_respect_max_registers_and_gaps():
    from types import SimpleNamespace

    from knxscada.protocols.modbus import Driver

    hub = SimpleNamespace(values={})
    for model, limit in (("Carlo Gavazzi EM270", 18), ("Carlo Gavazzi EM111", 50), ("Carlo Gavazzi EM580", 125)):
        drv = Driver("m", settings_of({"protocol": "modbus", "model": model}), hub)
        drv._build_blocks()
        assert all(b.end - b.start <= limit for b in drv.blocks), model
    # EM580: unknown addresses answer with an error – only neighbouring registers are read together.
    em580 = Driver("m", settings_of({"protocol": "modbus", "model": "Carlo Gavazzi EM580"}), hub)
    em580._build_blocks()
    assert [(b.start, b.end) for b in em580.blocks] == [(0x00, 0x38), (0x4E, 0x52), (0x56, 0x58)]


@pytest.mark.skipif(not installed("modbus"), reason="pymodbus is not installed")
def test_master_reads_a_carlo_gavazzi_meter(tmp_path):
    """Low word first, value weights, identification code 114 = EM111, and the overflow code shown as bad."""
    port = free_port()

    async def run():
        meter = {"protocol": "modbus", "role": "server", "host": "127.0.0.1", "port": port, "word_order": "little",
                 "tags": {"code": {"address": "hr:0x000B"}, "id": {"address": "hr:0x2000"},
                          "baud": {"address": "hr:0x2001"}, "parity": {"address": "hr:0x2002"},
                          "v": {"address": "hr:0x0000", "type": "int32"}, "a": {"address": "hr:0x0002", "type": "int32"},
                          "w": {"address": "hr:0x0004", "type": "int32"}, "pf": {"address": "hr:0x000E", "type": "int16"},
                          "kwh": {"address": "hr:0x0010", "type": "int32"}}}
        hub = await make_hub(tmp_path, {"connections": {"meter": meter}, "gateways": []})
        try:
            await until(lambda: hub.drivers["meter"].connected)
            for tag, value in (("code", 114), ("id", 1), ("baud", 1), ("parity", 1), ("v", 2301), ("a", 5250),
                               ("w", 2147483647), ("pf", -950), ("kwh", 1234567)):
                await hub.write("meter", tag, value)
            hub._make("master", {"protocol": "modbus", "host": "127.0.0.1", "port": port, "transport": "tcp",
                                 "model": "Carlo Gavazzi EM111", "poll_ms": 100})
            master = hub.drivers["master"]
            await until(lambda: master.ready)
            assert master.identity.startswith("Carlo Gavazzi EM111 (1-phase energy meter")
            assert master.identity.endswith("unit 1  9600 N")
            assert not [t for level, t in hub.server.events if level == "error"]
            v = hub.values
            assert v["master/V L-N"]["v"] == pytest.approx(230.1)
            assert v["master/A"]["v"] == pytest.approx(5.25)
            assert v["master/PF"]["v"] == pytest.approx(-0.95)
            assert v["master/kWh (+) TOT"]["v"] == pytest.approx(123456.7)
            assert v["master/W"]["q"] == "bad"            # 7FFFFFFFh = "EEE"
        finally:
            await hub.stop()

    asyncio.run(run())


# -- ./scada simulate -------------------------------------------------------------------------------------
@pytest.mark.skipif(not installed("modbus"), reason="pymodbus is not installed")
@pytest.mark.parametrize(("model", "tag", "lo", "hi"), [
    ("WAGO 879-3000", "L1 voltage", 220, 240),
    ("Carlo Gavazzi EM111", "V L-N", 220, 240),          # 000Bh is both the ID code and the high word of W dmd
    ("Carlo Gavazzi EM270", "kWh (+) TOT TCDA2", 12000, 13000),
    ("Carlo Gavazzi EM271", "W TOT TCDA1 DMD", 800, 1500),
])
def test_simulator_is_identified_and_read_by_the_master(tmp_path, model, tag, lo, hi):
    import time

    from knxscada.protocols import simulate

    port = free_port()

    async def run():
        tpl, sim = simulate.build(model, port=port)
        simulate.refresh(sim, time.time())
        task = asyncio.create_task(sim.run())
        hub = await make_hub(tmp_path, {"connections": {}, "gateways": []})
        try:
            await until(lambda: sim.connected)
            hub._make("master", {"protocol": "modbus", "transport": "tcp", "host": "127.0.0.1", "port": port,
                                 "model": model, "poll_ms": 100})
            master = hub.drivers["master"]
            await until(lambda: master.ready)
            assert master.identity.startswith(tpl["model"]), master.identity
            assert not [t for level, t in hub.server.events if level == "error"]
            assert lo < hub.values[f"master/{tag}"]["v"] < hi
            assert all(r["q"] == "good" for k, r in hub.values.items() if k.startswith("master/"))
        finally:
            await hub.stop()
            await sim.stop()
            task.cancel()

    asyncio.run(run())


# -- Solar-Log ---------------------------------------------------------------------------------------------
def test_solar_log_templates():
    free = settings_of({"protocol": "modbus", "model": "Solar-Log Modbus TCP", "host": "192.0.2.10"})
    pm = settings_of({"protocol": "modbus", "model": "Solar-Log Modbus TCP PM", "host": "192.0.2.10"})
    assert (free["transport"], free["port"], free["word_order"], free["max_gap"]) == ("tcp", 502, "little", 0)
    assert len(free["tags"]) == 17 and "PM Pac" not in free["tags"]
    assert len(pm["tags"]) == 17 + 28
    assert all(t["address"].startswith("ir:") for t in pm["tags"].values())   # only function code 4 reads
    for model in ("Solar-Log Modbus TCP", "Solar-Log Modbus TCP PM"):
        assert validate({"connections": {"s": {"protocol": "modbus", "model": model, "host": "192.0.2.10"}},
                         "gateways": []}) == []


@pytest.mark.skipif(not installed("modbus"), reason="pymodbus is not installed")
def test_tag_invalid_codes_and_solar_log_simulator(tmp_path):
    """A tag's own ``invalid`` (-273 = no sensor) gives bad quality; the rest of the Solar-Log reads fine."""
    import time

    from knxscada.protocols import simulate

    port = free_port()

    async def run():
        tpl, sim = simulate.build("Solar-Log Modbus TCP PM", port=port)
        simulate.refresh(sim, time.time())
        task = asyncio.create_task(sim.run())
        hub = await make_hub(tmp_path, {"connections": {}, "gateways": []})
        try:
            await until(lambda: sim.connected)
            sim.store.put(sim.points["PM Module temp."], -273, "little")
            hub._make("master", {"protocol": "modbus", "host": "127.0.0.1", "port": port,
                                 "model": "Solar-Log Modbus TCP PM", "poll_ms": 100})
            master = hub.drivers["master"]
            await until(lambda: master.ready)
            v = hub.values
            assert 800 < v["master/Pac"]["v"] < 1500
            assert v["master/Total yield"]["v"] > 12_000_000
            assert v["master/PM Module temp."]["q"] == "bad"
            assert v["master/PM Ambient temp."]["q"] == "good"
        finally:
            await hub.stop()
            await sim.stop()
            task.cancel()

    asyncio.run(run())


def test_simulate_lists_templates(capsys):
    from knxscada.__main__ import main

    assert main(["simulate"]) == 0
    out = capsys.readouterr().out
    assert "WAGO 879-3000" in out and "Carlo Gavazzi EM580" in out
