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
