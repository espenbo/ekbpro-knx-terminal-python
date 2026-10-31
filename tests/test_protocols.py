# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""Protocol modules and gateways: protocols.yaml, value conversion, the gateway rules and real Modbus/OPC UA/MQTT."""

import asyncio
import socket
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from knxscada.config import Config
from knxscada.protocols import PROTOCOLS, installed
from knxscada.protocols import config as pconfig
from knxscada.protocols.config import save
from knxscada.protocols.base import Driver, Tag, coerce, same_value, tag_from_spec
from knxscada.protocols.gateway import convert, convert_back
from knxscada.protocols.hub import Hub
from knxscada.protocols.modbus import from_registers, parse_address, to_registers
from knxscada.protocols.mqtt import make_payload, parse_payload, topic_matches


# -- helpers -----------------------------------------------------------------------------------------
class FakeServer:
    """What the hub needs of the SCADA server."""

    def __init__(self, base: Path) -> None:
        self.cfg = SimpleNamespace(resolve=lambda p: base / p, server={}, base=base)
        self.knx_enabled = False
        self.events: list[tuple[str, str]] = []
        self.clients: set = set()
        self.state: dict = {}
        self.gas: dict = {}

    def event(self, level, category, text, user=None):
        self.events.append((level, text))

    def _broadcast(self, msg):
        pass


class Mem(Driver):
    """A connection that only lives in memory: remembers writes, creates tags on demand."""

    protocol = "mem"

    def __init__(self, name, settings, hub, initial_allowed=True, server=True, reads_first=False):
        super().__init__(name, {"role": "server" if server else "client", **settings}, hub)
        self.initial_allowed = initial_allowed
        self.reads_first = reads_first
        self.writes: list[tuple[str, Any]] = []

    def create_tag(self, name, spec):
        if self.role != "server":
            return super().create_tag(name, spec)
        tag = tag_from_spec(name, spec)
        self.tags[name] = tag
        return tag

    async def session(self):
        self.set_state(True)
        await self.wait_stopped(0.05)

    async def write(self, tag, value, user=""):
        self.writes.append((tag.name, value))


async def make_hub(tmp_path: Path, data: dict, mems: dict[str, Mem] | None = None) -> Hub:
    """A started hub with the connections of *data* and the in-memory connections *mems*."""
    hub = Hub(FakeServer(tmp_path))
    hub.data = data
    hub.load = lambda: None
    for drv in (mems or {}).values():
        drv.hub = hub
        hub._add(drv)
    await asyncio.sleep(0.05)   # let the memory connections come up (initial values count from here)
    await hub.start()
    return hub


async def until(cond, timeout=5.0):
    end = asyncio.get_running_loop().time() + timeout
    while not cond():
        if asyncio.get_running_loop().time() > end:
            raise AssertionError("timed out")
        await asyncio.sleep(0.02)


wait_for = until

#: Tests that need the Modbus module (pymodbus).
pytestmark_modbus = pytest.mark.skipif(not installed("modbus"), reason="pymodbus is not installed")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# -- protocols.yaml ----------------------------------------------------------------------------------
@pytest.mark.parametrize(("item", "expected"), [
    ("Temp", {"from": "Temp", "to": ""}),
    ("hr:100 -> Ute.Temperatur", {"from": "hr:100", "to": "Ute.Temperatur"}),
    ({"from": "a", "to": "b", "direction": "both", "scale": None}, {"from": "a", "to": "b", "direction": "both"}),
])
def test_parse_link(item, expected):
    assert pconfig.parse_link(item) == expected


def test_link_text_is_the_inverse():
    assert pconfig.link_text({"from": "a", "to": ""}) == "a"
    assert pconfig.link_text({"from": "a", "to": "a"}) == "a"
    assert pconfig.link_text({"from": "a", "to": "b"}) == "a -> b"
    assert pconfig.link_text({"from": "a", "to": "b", "scale": 0.1}) == {"from": "a", "to": "b", "scale": 0.1}


def test_validate_finds_problems():
    data = {
        "connections": {
            "1bad": {"protocol": "modbus"},
            "plc": {"protocol": "modbus", "role": "master", "port": "x", "tags": {"t": {"address": "zz:1"}}},
            "mq": {"protocol": "mqtt", "host": ""},
            "b": {"protocol": "sparkplug"},
            "knx2": {"protocol": "knx"},
        },
        "gateways": [
            {"name": "g", "from": "plc", "to": "nowhere", "direction": "sideways", "map": ["", {"from": "a", "scale": "x"}]},
            {"name": "g", "from": "plc", "to": "plc"},
        ],
    }
    text = "\n".join(pconfig.validate(data))
    for part in ("connection 1bad: the name", "role 'master'", "port: 'x' is not a number", "unknown table",
                 "mq: host is missing", "not available yet", "KNX is not set up", "must be called knx",
                 "no connection called 'nowhere'", "direction must be forward or both", "line 1: the source tag",
                 "line 2: scale must be a number", "gateway g is listed twice", "from and to are the same"):
        assert part in text, part


def test_validate_accepts_knx_when_set_up():
    data = {"connections": {"m": {"protocol": "mqtt"}}, "gateways": [{"name": "g", "from": "knx", "to": "m", "map": ["1/1/1"]}]}
    assert pconfig.validate(data, knx=True) == []
    assert "no connection called 'knx'" in "\n".join(pconfig.validate(data, knx=False))


def test_save_and_load_round_trip(tmp_path):
    path = tmp_path / "protocols.yaml"
    data = {
        "connections": {
            "plc1": {"protocol": "modbus", "role": "client", "host": "10.0.0.5", "port": 502, "poll_ms": 1000,
                     "tags": {"Temp": {"address": "ir:0", "type": "int16", "scale": 0.1, "unit": "°C", "description": ""}}},
            "scada": {"protocol": "opcua", "role": "server", "endpoint": "opc.tcp://0.0.0.0:4840", "password": "hemmelig"},
        },
        "gateways": [{"name": "Anlegg", "from": "plc1", "to": "scada", "direction": "forward",
                      "map": ["Temp -> Ute.Temperatur", {"from": "Temp", "to": "Temp", "deadband": 0.5}]}],
    }
    pconfig.save(path, data)
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# Connections and gateways")
    assert "port: 502" not in text and "poll_ms" not in text and "role: client" not in text   # defaults left out
    assert "role: server" in text and "direction" not in text.split("gateways:")[1].split("map:")[0]
    assert "Temp: {address: 'ir:0', type: int16, scale: 0.1, unit: °C}" in text
    assert "- Temp -> Ute.Temperatur" in text and "{from: Temp, to: Temp, deadband: 0.5}" in text
    assert path.stat().st_mode & 0o777 == 0o600
    loaded = pconfig.load_data(path)
    assert loaded["connections"]["plc1"]["host"] == "10.0.0.5"
    assert pconfig.settings_of(loaded["connections"]["plc1"])["port"] == 502
    assert loaded["gateways"][0]["map"][0] == "Temp -> Ute.Temperatur"
    pconfig.save(path, loaded)
    assert (tmp_path / "protocols.yaml.bak").read_text(encoding="utf-8") == text
    assert path.read_text(encoding="utf-8") == text                     # stable


def test_save_refuses_invalid_data(tmp_path):
    with pytest.raises(ValueError, match="unknown protocol"):
        pconfig.save(tmp_path / "p.yaml", {"connections": {"x": {"protocol": "nope"}}})
    assert not (tmp_path / "p.yaml").exists()


def test_registry_is_consistent():
    for p in PROTOCOLS.values():
        keys = [f.key for f in p.settings]
        assert len(keys) == len(set(keys)), p.name
        for f in (*p.settings, *p.tag_fields):
            assert not f.choices or f.default in f.choices or f.default in (None, ""), (p.name, f.key)
    assert installed("knx") and not installed("sparkplug")


# -- values ------------------------------------------------------------------------------------------
@pytest.mark.parametrize(("value", "typ", "expected"), [
    ("på", "bool", True), ("0", "bool", False), (2, "bool", True), ("21,5", "float", 21.5), (21.6, "int", 22),
    (True, "int", 1), (True, "str", "true"), (None, "int", None), ({"r": 1}, "", {"r": 1}),
])
def test_coerce(value, typ, expected):
    assert coerce(value, typ) == expected


def test_coerce_refuses_nonsense():
    with pytest.raises(ValueError):
        coerce("kanskje", "bool")


def test_same_value():
    assert same_value(21.5, 21.500001) and not same_value(21.5, 21.6)
    assert not same_value(True, 1) and same_value("a", "a")


def test_convert_and_back():
    assert convert(215, "float", 0.1, None, False) == pytest.approx(21.5)
    assert convert_back(21.5, "int", 0.1, None, False) == 215
    assert convert(True, "bool", None, None, True) is False
    assert convert("COMFORT", "int", None, None, False) == 1      # KNX HVAC mode → number


# -- Modbus addresses and registers --------------------------------------------------------------------
@pytest.mark.parametrize(("text", "expected"), [
    ("hr:100", ("hr", 100, None)), ("HR 100", ("hr", 100, None)), ("holding:7.3", ("hr", 7, 3)), ("co:0", ("co", 0, None)),
    ("40001", ("hr", 0, None)), ("400101", ("hr", 100, None)), ("30005", ("ir", 4, None)), ("10001", ("di", 0, None)),
    ("00010", ("co", 9, None)), ("hr:0x5002", ("hr", 0x5002, None)), ("HR:0X600A.1", ("hr", 0x600A, 1)),
])
def test_parse_address(text, expected):
    assert parse_address(text) == expected


@pytest.mark.parametrize("text", ["xx:1", "hr:70000", "co:1.2", "40000", "1234", "hr:1.16", ""])
def test_parse_address_refuses(text):
    with pytest.raises(ValueError):
        parse_address(text)


@pytest.mark.parametrize(("value", "dtype"), [(-5, "int16"), (65535, "uint16"), (-70000, "int32"), (21.5, "float32"),
                                             (2**40, "int64"), (1e300, "float64")])
@pytest.mark.parametrize("order", ["big", "little"])
def test_registers_round_trip(value, dtype, order):
    assert from_registers(to_registers(value, dtype, order), dtype, order) == pytest.approx(value)


def test_registers_word_order():
    assert to_registers(0x00010002, "uint32", "big") == [1, 2]
    assert to_registers(0x00010002, "uint32", "little") == [2, 1]
    with pytest.raises(ValueError):
        to_registers(70000, "uint16")


# -- MQTT payloads ---------------------------------------------------------------------------------------
@pytest.mark.parametrize(("payload", "key", "expected"), [
    (b"21.5", "", 21.5), (b"7", "", 7), (b"true", "", True), (b"on", "", "on"), (b'{"value": 3}', "", 3),
    (b'{"temp": 20.1, "hum": 40}', "temp", 20.1), (b'{"x": 1}', "", '{"x": 1}'), (b"[1,2]", "", [1, 2]),
])
def test_parse_payload(payload, key, expected):
    assert parse_payload(payload, key) == expected


def test_make_payload():
    assert make_payload(True, "plain") == "true"
    assert make_payload(None, "plain") == ""
    assert '"value": 21.5' in make_payload(21.5, "json")


@pytest.mark.parametrize(("pattern", "topic", "ok"), [
    ("a/#", "a/b/c", True), ("a/+/c", "a/b/c", True), ("a/+", "a/b/c", False), ("a/b", "a/b", True), ("#", "x", True),
])
def test_topic_matches(pattern, topic, ok):
    assert topic_matches(pattern, topic) is ok


# -- the gateway engine --------------------------------------------------------------------------------
def test_gateway_forward_rename_scale_and_no_echo(tmp_path):
    async def run():
        src = Mem("src", {"tags": {"raw": {"type": "int"}}}, None, server=False)
        dst = Mem("dst", {}, None)
        hub = await make_hub(tmp_path, {"gateways": [{"name": "g", "from": "src", "to": "dst", "map": [
            {"from": "raw", "to": "Ute.Temp", "scale": 0.1, "type": "float", "direction": "both"}]}]},
            {"src": src, "dst": dst})
        try:
            assert dst.tags["Ute.Temp"].type == "float" and dst.tags["Ute.Temp"].writable
            hub.update("src", "raw", 215)
            await until(lambda: dst.writes)
            assert dst.writes == [("Ute.Temp", pytest.approx(21.5))]
            assert hub.values["dst/Ute.Temp"]["v"] == pytest.approx(21.5)   # no echo: recorded by the hub
            await asyncio.sleep(0.1)
            assert src.writes == []                                          # ... and not sent back
            # A client writes the target: the value goes back, unscaled.
            hub.update("dst", "Ute.Temp", 22.0)
            await until(lambda: src.writes)
            assert src.writes == [("raw", 220)]
            await asyncio.sleep(0.1)
            assert len(dst.writes) == 1
        finally:
            await hub.stop()

    asyncio.run(run())


def test_initial_values_never_reach_a_quiet_target(tmp_path):
    """Like KNX: a source that (re)connects must not cause writes to a target with initial_allowed = False."""
    async def run():
        src = Mem("src", {"tags": {"a": {}, "b": {}}}, None, server=False, reads_first=True)
        quiet = Mem("quiet", {"tags": {"x": {}, "y": {}}}, None, initial_allowed=False, server=False)
        hub = await make_hub(tmp_path, {"gateways": [{"name": "g", "from": "src", "to": "quiet", "map": ["a -> x", "b -> y"]}]},
                             {"src": src, "quiet": quiet})
        try:
            hub.update("src", "a", 1)            # read after src came up: a status
            src.set_ready()
            await asyncio.sleep(0.1)
            assert quiet.writes == []
            hub.update("src", "a", 1)            # unchanged: nothing
            hub.update("src", "a", 2)            # a real change goes out
            await until(lambda: quiet.writes)
            assert quiet.writes == [("x", 2)]
            src.set_state(False)                 # reconnect: the first values are initial again
            src.set_state(True)
            hub.update("src", "a", 3)
            await asyncio.sleep(0.1)
            assert quiet.writes == [("x", 2)]
        finally:
            await hub.stop()

    asyncio.run(run())


def test_start_up_sends_nothing_to_devices(tmp_path):
    """Read first, send afterwards: initial values reach our own servers, never a device (client) by default."""
    async def run():
        src = Mem("src", {"tags": {"a": {}, "b": {}}}, None, server=False, reads_first=True)
        dev = Mem("dev", {"tags": {"x": {}}}, None, server=False)
        srv = Mem("srv", {}, None)
        hub = await make_hub(tmp_path, {"gateways": [
            {"name": "g1", "from": "src", "to": "dev", "map": ["a -> x"]},
            {"name": "g2", "from": "src", "to": "srv", "map": ["b"]},
            {"name": "g3", "from": "src", "to": "dev", "initial": True, "map": ["b -> x"]}]},
            {"src": src, "dev": dev, "srv": srv})
        try:
            hub.update("src", "a", 1)
            hub.update("src", "b", 5)
            src.set_ready()
            await until(lambda: srv.writes)
            await asyncio.sleep(0.1)
            assert srv.writes == [("b", 5)]          # our own server is filled at once
            assert dev.writes == [("x", 5)]          # the device only with initial: true
            hub.update("src", "a", 2)                # the first change after the start goes out
            await until(lambda: len(dev.writes) == 2)
            assert dev.writes[-1] == ("x", 2)
        finally:
            await hub.stop()

    asyncio.run(run())


def test_values_before_ready_are_statuses(tmp_path):
    """Until a connection has read everything once, its values are not passed on as changes."""
    async def run():
        src = Mem("src", {"tags": {"a": {}}}, None, server=False, reads_first=True)
        dev = Mem("dev", {"tags": {"x": {}}}, None, server=False)
        hub = await make_hub(tmp_path, {"gateways": [{"name": "g", "from": "src", "to": "dev", "map": ["a -> x"]}]},
                             {"src": src, "dev": dev})
        try:
            hub.update("src", "a", 1)
            hub.update("src", "a", 2)                # still reading: a status, not a change
            await asyncio.sleep(0.1)
            assert dev.writes == []
            assert src.connected and not src.ready
            src.set_ready()
            hub.update("src", "a", 3)
            await until(lambda: dev.writes)
            assert dev.writes == [("x", 3)]
        finally:
            await hub.stop()

    asyncio.run(run())


def test_target_is_read_before_it_is_written(tmp_path):
    """A change waits while the target is reading, and is dropped when the target has that value already."""
    async def run():
        src = Mem("src", {"tags": {"a": {}, "b": {}}}, None, server=False)
        dev = Mem("dev", {"tags": {"x": {}, "y": {}}}, None, server=False, reads_first=True)
        hub = await make_hub(tmp_path, {"gateways": [{"name": "g", "from": "src", "to": "dev",
                                                     "map": ["a -> x", "b -> y"]}]}, {"src": src, "dev": dev})
        try:
            hub.update("src", "a", 1)                # statuses
            hub.update("src", "b", 1)
            hub.update("src", "a", 7)                # changes while dev is still reading
            hub.update("src", "b", 8)
            hub.update("dev", "x", 7)                # dev has x = 7 already, y = 0
            hub.update("dev", "y", 0)
            await asyncio.sleep(0.1)
            assert dev.writes == []
            dev.set_ready()
            await until(lambda: dev.writes)
            await asyncio.sleep(0.05)
            assert dev.writes == [("y", 8)]
            # A change equal to what the device reports is not sent either.
            hub.update("dev", "x", 9)
            hub.update("src", "a", 9)
            await asyncio.sleep(0.1)
            assert dev.writes == [("y", 8)]
        finally:
            await hub.stop()

    asyncio.run(run())


def test_own_server_is_filled_again_when_it_comes_back(tmp_path):
    async def run():
        src = Mem("src", {"tags": {"a": {}}}, None, server=False)
        srv = Mem("srv", {}, None)
        hub = await make_hub(tmp_path, {"gateways": [{"name": "g", "from": "src", "to": "srv", "map": ["a"]}]},
                             {"src": src, "srv": srv})
        try:
            hub.update("src", "a", 4)
            await until(lambda: srv.writes)
            srv.set_state(False)
            srv.set_state(True)                      # e.g. a restarted MQTT broker
            await until(lambda: len(srv.writes) == 2)
            assert srv.writes == [("a", 4), ("a", 4)]
        finally:
            await hub.stop()

    asyncio.run(run())


def test_initial_false_and_deadband(tmp_path):
    async def run():
        src = Mem("src", {"tags": {"t": {"type": "float"}}}, None, server=False, reads_first=True)
        dst = Mem("dst", {}, None)
        hub = await make_hub(tmp_path, {"gateways": [{"name": "g", "from": "src", "to": "dst", "initial": False,
                                                     "deadband": 0.5, "map": ["t"]}]}, {"src": src, "dst": dst})
        try:
            hub.update("src", "t", 20.0)
            src.set_ready()
            await asyncio.sleep(0.1)
            assert dst.writes == []
            for v in (20.2, 20.4, 20.6):
                hub.update("src", "t", v)
            await until(lambda: dst.writes)
            await asyncio.sleep(0.05)
            assert dst.writes == [("t", 20.6)]
        finally:
            await hub.stop()

    asyncio.run(run())


def test_rate_limit_sends_the_latest_value(tmp_path):
    async def run():
        src = Mem("src", {"tags": {"t": {"type": "int"}}}, None, server=False)
        dst = Mem("dst", {}, None)
        hub = await make_hub(tmp_path, {"gateways": [{"name": "g", "from": "src", "to": "dst", "min_interval_ms": 300,
                                                     "map": ["t"]}]}, {"src": src, "dst": dst})
        try:
            for v in range(1, 6):
                hub.update("src", "t", v)
                await asyncio.sleep(0.02)
            await asyncio.sleep(0.5)
            assert dst.writes == [("t", 1), ("t", 5)]
        finally:
            await hub.stop()

    asyncio.run(run())


def test_wildcard_lines_follow_new_tags(tmp_path):
    async def run():
        src = Mem("src", {"tags": {"Lys1": {}, "Varme": {}}}, None, server=False)
        dst = Mem("dst", {}, None)
        hub = await make_hub(tmp_path, {"gateways": [{"name": "g", "from": "src", "to": "dst", "map": [
            {"from": "Lys*", "to": "Lights/{name}", "initial": False}]}]}, {"src": src, "dst": dst})
        try:
            assert set(dst.tags) == {"Lights/Lys1"}
            src.tags["Lys2"] = Tag("Lys2")       # e.g. a new MQTT topic
            hub.update("src", "Lys2", True)
            hub.update("src", "Lys2", False)
            await until(lambda: dst.writes)
            assert dst.writes == [("Lights/Lys2", False)]
        finally:
            await hub.stop()

    asyncio.run(run())


def test_problems_are_reported(tmp_path):
    async def run():
        src = Mem("src", {"tags": {"x": {}}}, None, server=False)
        cli = Mem("cli", {}, None, server=False)
        hub = await make_hub(tmp_path, {"connections": {"bad": {"protocol": "sparkplug"}},
                                        "gateways": [{"name": "g", "from": "src", "to": "cli", "map": ["nope", "x"]}]},
                             {"src": src, "cli": cli})
        try:
            text = "\n".join(t for _l, t in hub.server.events)
            assert "src has no tag 'nope'" in text and "cli has no tag 'x'" in text and "bad: The MQTT Sparkplug B module is not available yet" in text
            assert hub.failed == {"bad": "The MQTT Sparkplug B module is not available yet"}
            row = next(s for s in hub.status() if s["name"] == "bad")
            assert not row["running"] and row["error"]
            with pytest.raises(RuntimeError):
                await hub.write("bad", "x", 1)
        finally:
            await hub.stop()

    asyncio.run(run())


# -- real protocols ------------------------------------------------------------------------------------
def test_modbus_slave_and_master_through_gateways(tmp_path):
    """mem → Modbus slave ← Modbus master (polling) → mem, and a write from the master back to the source."""
    pytest.importorskip("pymodbus")
    port = free_port()

    async def run():
        src = Mem("src", {"tags": {"temp": {"type": "float"}, "pump": {"type": "bool"}, "big": {"type": "int"}}}, None,
                  server=False)
        sink = Mem("sink", {}, None)
        data = {
            "connections": {
                "slave": {"protocol": "modbus", "role": "server", "host": "127.0.0.1", "port": port},
                "master": {"protocol": "modbus", "host": "127.0.0.1", "port": port, "poll_ms": 100, "tags": {
                    "T": {"address": "hr:10", "type": "int16", "scale": 0.1},
                    "P": {"address": "co:3"},
                    "B": {"address": "40021", "type": "uint32", "word_order": "little"},
                    "Bit": {"address": "hr:30.2"},
                }},
            },
            "gateways": [
                {"name": "in", "from": "src", "to": "slave", "map": [
                    {"from": "temp", "to": "hr:10", "type": "int16", "scale": 10},   # 0.1 °C in the register
                    {"from": "pump", "to": "co:3", "direction": "both"},
                    {"from": "big", "to": "hr:20", "type": "uint32", "word_order": "little"},
                ]},
                {"name": "out", "from": "master", "to": "sink", "map": ["T", "P", "B"]},
            ],
        }
        hub = await make_hub(tmp_path, data, {"src": src, "sink": sink})
        try:
            assert not hub.failed, hub.failed
            await until(lambda: hub.drivers["slave"].connected and hub.drivers["master"].connected)
            hub.update("src", "temp", -12.3)
            hub.update("src", "pump", True)
            hub.update("src", "big", 100000)
            await until(lambda: hub.values.get("master/P", {}).get("v") is True)
            await until(lambda: hub.values.get("master/B", {}).get("v") == 100000)
            await until(lambda: hub.values.get("master/T", {}).get("v") == pytest.approx(-12.3))
            await until(lambda: ("T", pytest.approx(-12.3)) in sink.writes)
            # The master switches the pump off: the slave passes it back to the source.
            await hub.write("master", "P", False)
            await until(lambda: ("pump", False) in src.writes)
            # Writing a register no gateway maps is refused by the slave.
            with pytest.raises(RuntimeError, match="refused"):
                await hub.write("master", "Bit", True)
            # Read-only tables stay read-only on the master.
            hub.drivers["master"].tags["RO"] = hub.drivers["master"].make_tag("RO", {"address": "ir:1"})
            with pytest.raises(ValueError, match="read-only"):
                await hub.write("master", "RO", 1)
        finally:
            await hub.stop()

    asyncio.run(run())


def test_opcua_server_and_client_through_gateways(tmp_path):
    """mem → our OPC UA server → OPC UA client connection → mem; and a client write back to the source."""
    pytest.importorskip("asyncua")
    port = free_port()

    async def run():
        src = Mem("src", {"tags": {"t": {"type": "float"}, "on": {"type": "bool"}}}, None, server=False)
        sink = Mem("sink", {}, None)
        data = {
            "connections": {
                "ua": {"protocol": "opcua", "role": "server", "endpoint": f"opc.tcp://127.0.0.1:{port}"},
                "cli": {"protocol": "opcua", "endpoint": f"opc.tcp://127.0.0.1:{port}", "publish_interval_ms": 100,
                        "tags": {"T": {"node": "ns=2;s=Bygg.Rom1.Temp"}, "On": {"node": "ns=2;s=Bygg.Rom1.On"}}},
            },
            "gateways": [
                {"name": "to-ua", "from": "src", "to": "ua", "map": [
                    "t -> Bygg.Rom1.Temp", {"from": "on", "to": "Bygg.Rom1.On", "direction": "both"}]},
                {"name": "from-ua", "from": "cli", "to": "sink", "map": ["T", "On"]},
            ],
        }
        hub = Hub(FakeServer(tmp_path))
        hub.data, hub.load = data, (lambda: None)
        for drv in (src, sink):
            drv.hub = hub
            hub._add(drv)
        await asyncio.sleep(0.05)
        # The OPC UA server must be up before the client connects, so start the client after it.
        cli = data["connections"].pop("cli")
        await hub.start()
        try:
            await until(lambda: hub.drivers["ua"].connected, 10)
            hub.update("src", "t", 21.5)
            hub.update("src", "on", False)
            await asyncio.sleep(0.2)
            hub._make("cli", cli)
            hub._build_engine()
            await until(lambda: hub.drivers["cli"].connected, 10)
            await until(lambda: hub.values.get("cli/T", {}).get("v") == pytest.approx(21.5), 10)
            assert hub.drivers["cli"].tags["T"].type == "float"            # type read from the server
            assert hub.drivers["ua"].tags["Bygg.Rom1.On"].writable
            await hub.write("cli", "On", True)
            await until(lambda: ("on", True) in src.writes, 10)
        finally:
            await hub.stop()

    asyncio.run(run())


def test_opcua_requests_split_at_the_server_limits():
    """WAGO PFC servers take at most 100 nodes per Read / CreateMonitoredItems (BadTooManyOperations)."""
    pytest.importorskip("asyncua")
    from asyncua import ua

    from knxscada.opcua_bridge import operation_limits, read_values, subscribe

    class Node:
        def __init__(self, value):
            self.value = value

        async def read_value(self):
            if isinstance(self.value, Exception):
                raise self.value
            return self.value

    class Client:
        def __init__(self, limits):
            self.limits, self.reads = limits, []

        def get_node(self, oid):
            return Node(self.limits.get(oid, RuntimeError("no such node")))

        async def read_attributes(self, nodes):
            self.reads.append(len(nodes))
            return [f"dv{n}" for n in nodes]

    class Sub:
        def __init__(self):
            self.calls = []

        async def subscribe_data_change(self, nodes):
            self.calls.append(len(nodes))

    async def run():
        ids = ua.ObjectIds
        wago = Client({ids.Server_ServerCapabilities_OperationLimits_MaxNodesPerRead: 100,
                       ids.Server_ServerCapabilities_OperationLimits_MaxMonitoredItemsPerCall: 100})
        assert await operation_limits(wago) == (100, 100)
        assert await operation_limits(Client({})) == (100, 100)                       # not published → default
        assert await operation_limits(Client({ids.Server_ServerCapabilities_OperationLimits_MaxNodesPerRead: 0}),
                                      ) == (1000, 100)                                 # 0 = no limit
        nodes = list(range(250))
        assert await read_values(wago, nodes, 100) == [f"dv{n}" for n in nodes]
        assert wago.reads == [100, 100, 50]
        sub = Sub()
        await subscribe(sub, nodes, 100)
        assert sub.calls == [100, 100, 50]

    asyncio.run(run())


def test_opcua_client_rounds_float32(tmp_path):
    """A REAL in the PLC is shown as 16.6, not 16.600000381469727 (as the BACnet client does)."""
    pytest.importorskip("asyncua")
    from asyncua import ua

    from knxscada.protocols.opcua import Driver as UaDriver

    hub = Hub(FakeServer(tmp_path))
    drv = UaDriver("ua", {"protocol": "opcua", "role": "client", "tags": {"T": {"node": "ns=4;s=T"},
                                                                          "D": {"node": "ns=4;s=D"}}}, hub)
    drv.vtypes = {"T": ua.VariantType.Float, "D": ua.VariantType.Double}
    drv.publish(drv.tags["T"], 16.600000381469727)
    drv.publish(drv.tags["D"], 16.600000381469727)
    assert hub.values["ua/T"]["v"] == 16.6
    assert hub.values["ua/D"]["v"] == 16.600000381469727


# -- KNX as a protocol: the real server (demo bus), a Modbus slave and a master on the other side ------------
@pytest.fixture
def knx_server(tmp_path):
    import json

    from knxscada import project
    from knxscada.server import ScadaServer, Simulator
    from tests.test_project import raw_project

    (tmp_path / "cache.json").write_text(json.dumps(project.build_model(raw_project())), encoding="utf-8")
    cfg = Config(path=tmp_path / "config.yaml", project={"file": "none.knxproj", "cache": "cache.json"},
                 server={"database": "db.sqlite", "socket": "s.sock"})
    port = free_port()
    save(tmp_path / "protocols.yaml", {
        "connections": {"plc": {"protocol": "modbus", "role": "server", "host": "127.0.0.1", "port": port,
                                "unit_id": 0, "tags": {"Light": {"address": "co:0"}}}},
        "gateways": [{"name": "KNX-PLC", "from": "knx", "to": "plc",
                      "map": [{"from": "1/1/1", "to": "Light", "direction": "both"},
                              {"from": "3/0/1", "to": "hr:0", "type": "float32"}]}],
    }, knx=True)
    s = ScadaServer(cfg, demo=True)
    s.sim = Simulator(s)
    s.state["1/1/1"] = {"value": True, "text": "On", "raw": "01", "ts": 1.0, "src": "1.1.1"}   # from the database
    yield s, port
    s.store.close()


@pytestmark_modbus
def test_knx_gateway_both_ways_and_quiet_start(knx_server):
    from pymodbus.client import AsyncModbusTcpClient

    from knxscada import dpt as dptlib

    s, port = knx_server

    async def main():
        s._set_status(True, "demo")   # as ScadaServer.run() does before it starts the hub
        await s.hub.start()
        try:
            await wait_for(lambda: s.hub.drivers["plc"].connected and s.hub.drivers["knx"].connected)
            await asyncio.sleep(0.2)
            # Start-up: the old value from the database did not reach the slave, and nothing was sent to the bus.
            assert s.store.telegrams() == []
            c = AsyncModbusTcpClient("127.0.0.1", port=port)
            await c.connect()
            assert (await c.read_coils(0, count=1)).bits[0] is False
            assert not (await c.write_coil(0, True)).isError()   # the master's first write is a change
            await wait_for(lambda: any(t["dir"] == "out" and t["dst"] == "1/1/1" for t in s.store.telegrams()))
            await asyncio.sleep(0.6)
            # A telegram on the bus → the slave.
            s.ingest(src="1.1.5", dst="3/0/1", kind="write", value=dptlib.encode("9.001", 22.5), direction="in")
            await asyncio.sleep(0.1)
            rr = await c.read_holding_registers(0, count=2)
            assert from_registers(rr.registers, "float32") == 22.5
            # The master switches the light off → a KNX write, logged with the gateway as the user.
            assert not (await c.write_coil(0, False)).isError()
            await wait_for(lambda: any(t["dir"] == "out" and t["dst"] == "1/1/1" for t in s.store.telegrams()))
            await wait_for(lambda: len([t for t in s.store.telegrams() if t["dir"] == "out"]) == 2)
            out = [t for t in s.store.telegrams() if t["dir"] == "out"]
            assert [(t["dst"], t["value"]) for t in out] == [("1/1/1", True), ("1/1/1", False)]
            assert any(e["user"] == "gw:KNX-PLC" and "1/1/1" in e["text"] for e in s.store.events())
            # The status echo of the actuator (simulator) does not travel back to the slave as a new write.
            await asyncio.sleep(0.6)
            assert len([t for t in s.store.telegrams() if t["dir"] == "out"]) == 2
            c.close()
        finally:
            await s.hub.stop()

    asyncio.run(main())


def test_server_without_knx(tmp_path):
    from xknx.dpt import DPTBinary

    from knxscada.server import ScadaServer

    cfg = Config(path=tmp_path / "config.yaml", server={"database": "db.sqlite", "socket": "s.sock"})
    assert not cfg.knx_enabled
    s = ScadaServer(cfg)
    try:
        assert s.gas == {} and s.ifaces == [] and s.status["connection"] == "off"
        with pytest.raises(RuntimeError, match="KNX is not set up"):
            asyncio.run(s._send("1/1/1", "write", DPTBinary(1)))

        async def main():
            await s.hub.start()
            snap = await s.hub.command({"cmd": "protocols"}, "test")
            await s.hub.stop()
            return snap

        snap = asyncio.run(main())
        assert snap["knx"] is False and snap["status"] == [] and "knx" not in s.hub.drivers
        assert {m["name"] for m in snap["modules"]} >= {"modbus", "mqtt", "opcua"}
    finally:
        s.store.close()
    assert Config(path=tmp_path / "c.yaml", knx={"enabled": False}, project={"file": "x"}).knx_enabled is False


# -- the example file and the command line ----------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent


def test_example_file_is_valid():
    data = pconfig.load_data(ROOT / "protocols.example.yaml")
    assert pconfig.validate(data, knx=True) == []
    meter = pconfig.settings_of(data["connections"]["meter1"])   # tags from the device template
    assert parse_address(meter["tags"]["Total active energy"]["address"]) == ("hr", 0x6000, None)


def test_cli_modules_and_gateway(tmp_path, capsys):
    from knxscada.__main__ import main

    (tmp_path / "config.yaml").write_text("server: {socket: s.sock}\n", encoding="utf-8")
    cfg = str(tmp_path / "config.yaml")
    assert main(["-c", cfg, "modules"]) == 0
    out = capsys.readouterr().out
    assert "modbus" in out and "planned" in out
    assert out.splitlines()[0].rstrip().endswith("installed")      # KNX is not set up: not used
    assert main(["-c", cfg, "gateway"]) == 0
    assert "does not exist yet" in capsys.readouterr().out
    save(tmp_path / "protocols.yaml", {"connections": {"m": {"protocol": "mqtt"}, "u": {"protocol": "opcua", "role": "server"}},
                                       "gateways": [{"name": "g", "from": "m", "to": "u", "map": ["a -> b"]}]})
    assert main(["-c", cfg, "gateway"]) == 0
    out = capsys.readouterr().out
    assert "g: m → u" in out and "a → b" in out and "not running" in out
    (tmp_path / "protocols.yaml").write_text("connections: {x: {protocol: nope}}\n", encoding="utf-8")
    assert main(["-c", cfg, "gateway"]) == 1
    assert "unknown protocol" in capsys.readouterr().out
    assert main(["-c", cfg, "modules", "install"]) == 1
    assert main(["-c", cfg, "modules", "install", "sparkplug"]) == 1


# -- the TUI (F11) against a real hub ----------------------------------------------------------------------
def test_tui_gateway_view_edits_protocols_yaml(tmp_path, monkeypatch):
    """Without KNX: F11 shows the connections; a connection and a gateway are added through the forms."""
    from textual.widgets import Input, Select, TextArea

    from knxscada.tui.app import ScadaApp
    from knxscada.tui.gateways import ConnectionDialog, GatewayDialog, GatewayView

    port = free_port()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    save(tmp_path / "protocols.yaml", {"connections": {
        "slave": {"protocol": "modbus", "role": "server", "host": "127.0.0.1", "port": port}}})
    cfg = Config(path=tmp_path / "config.yaml", server={"socket": "s.sock"}, ui={"splash": False, "language": "en"})
    assert not cfg.knx_enabled
    app = ScadaApp(cfg)
    hub = Hub(FakeServer(tmp_path))
    hub.server._broadcast = lambda msg: app._handle(msg)

    async def fake_request(cmd, **kw):
        if cmd in ("protocols", "points", "point_write", "protocols_save", "protocols_reload"):
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
        await hub.start()
        try:
            async with app.run_test(size=(180, 55)) as pilot:
                await pilot.pause(0.5)
                await pilot.press("f11")
                await pilot.pause(0.5)
                view = app.query_one("#gateways", GatewayView)
                assert view.display
                await until(lambda: hub.drivers["slave"].connected)
                await pilot.pause(0.3)
                conns = view.query_one("#gw-conns")
                assert conns.row_count == 1 and "slave" in str(conns.get_row_at(0))
                # a: new connection → Modbus → the form.
                await pilot.press("a")
                await pilot.pause(0.3)
                await pilot.press("enter")                         # the first protocol: Modbus
                await pilot.pause(0.3)
                dlg = app.screen
                assert isinstance(dlg, ConnectionDialog)
                assert not dlg.query_one("#row-device").display   # serial settings hidden for TCP
                dlg.query_one("#f-transport", Select).value = "rtu"
                await pilot.pause(0.1)
                assert dlg.query_one("#row-device").display and not dlg.query_one("#row-host").display
                dlg.query_one("#f-transport", Select).value = "tcp"
                dlg.query_one("#f-name", Input).value = "master"
                dlg.query_one("#f-host", Input).value = "127.0.0.1"
                dlg.query_one("#f-port", Input).value = str(port)
                dlg.query_one("#f-poll_ms", Input).value = "200"
                dlg.query_one("#f-tags", TextArea).text = "Temp: {address: 'hr:0x10', type: float32, unit: °C}"
                dlg.query_one("#f-ok").press()
                await until(lambda: "master" in hub.drivers)
                await pilot.pause(0.3)
                text = (tmp_path / "protocols.yaml").read_text(encoding="utf-8")
                assert "master:" in text and "poll_ms: 200" in text and "Temp: {address: hr:0x10" in text.replace("'", "")
                assert "device" not in text and "baudrate" not in text      # hidden settings are not saved
                # g: new gateway from the slave's register to the master (a loop through Modbus).
                await pilot.press("g")
                await pilot.pause(0.3)
                dlg = app.screen
                assert isinstance(dlg, GatewayDialog)
                dlg.query_one("#g-name", Input).value = "Test"
                dlg.query_one("#g-from", Select).value = "master"
                dlg.query_one("#g-to", Select).value = "slave"
                dlg.query_one("#g-map", TextArea).text = "Temp -> hr:0x20\n{from: Temp, to: 'hr:0x30', deadband: 0.5}"
                dlg.query_one("#g-ok").press()
                await until(lambda: len(hub.engine.links) == 2)
                text = (tmp_path / "protocols.yaml").read_text(encoding="utf-8")
                assert "- Temp -> hr:0x20" in text and "deadband: 0.5" in text
                await pilot.pause(0.5)
                assert view.query_one("#gw-links").row_count == 2
                # An invalid gateway is refused by the server, and the file stays as it was.
                await pilot.press("g")
                await pilot.pause(0.3)
                app.screen.query_one("#g-name", Input).value = "Bad"
                app.screen.query_one("#g-ok").press()
                await pilot.pause(0.5)
                assert "Bad" not in (tmp_path / "protocols.yaml").read_text(encoding="utf-8")
        finally:
            await hub.stop()

    asyncio.run(run())
