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
from knxscada.protocols.base import Driver, Tag, coerce, same_value, tag_from_spec
from knxscada.protocols.config import save
from knxscada.protocols.gateway import convert, convert_back
from knxscada.protocols.hub import Hub
from knxscada.protocols.modbus import from_registers, parse_address, to_registers
from knxscada.protocols.mqtt import make_payload, parse_payload, topic_matches


# -- helpers -----------------------------------------------------------------------------------------
class FakeServer:
    """What the hub needs of the SCADA server."""

    def __init__(self, base: Path) -> None:
        """No KNX, no clients; files are resolved relative to *base* (the test's tmp_path)."""
        self.cfg = SimpleNamespace(resolve=lambda p: base / p, server={}, base=base)
        self.knx_enabled = False
        self.events: list[tuple[str, str]] = []
        self.clients: set = set()
        self.state: dict = {}
        self.gas: dict = {}

    def event(self, level, category, text, user=None):
        """Keep (level, text) so tests can check what the hub reported."""
        self.events.append((level, text))

    def _broadcast(self, msg):
        """No TUI clients unless a test replaces this."""


class Mem(Driver):
    """A connection that only lives in memory: remembers writes, creates tags on demand."""

    protocol = "mem"

    def __init__(self, name, settings, hub, initial_allowed=True, server=True, reads_first=False):
        """*initial_allowed* and *reads_first* play the safety switches of real drivers (KNX, devices that are read
        first).
        """
        super().__init__(name, {"role": "server" if server else "client", **settings}, hub)
        self.initial_allowed = initial_allowed
        self.reads_first = reads_first
        self.writes: list[tuple[str, Any]] = []

    def create_tag(self, name, spec):
        """As a server, accept any tag a gateway asks for, as our own Modbus/OPC UA servers do."""
        if self.role != "server":
            return super().create_tag(name, spec)
        tag = tag_from_spec(name, spec)
        self.tags[name] = tag
        return tag

    async def session(self):
        """Report the connection as up, then end after a moment; tests that need a lasting connection override this."""
        self.set_state(True)
        await self.wait_stopped(0.05)

    async def write(self, tag, value, user=""):
        """Record the write instead of sending it anywhere."""
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
    """Wait until cond() is true, polling every 20 ms; fail after *timeout* seconds."""
    end = asyncio.get_running_loop().time() + timeout
    while not cond():
        if asyncio.get_running_loop().time() > end:
            raise AssertionError("timed out")
        await asyncio.sleep(0.02)


wait_for = until

#: Tests that need the Modbus module (pymodbus).
pytestmark_modbus = pytest.mark.skipif(not installed("modbus"), reason="pymodbus is not installed")


def free_port() -> int:
    """A TCP port on localhost that is free right now."""
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
    """A map line can be a tag name, 'a -> b' or a dict; empty options are dropped."""
    assert pconfig.parse_link(item) == expected


def test_link_text_is_the_inverse():
    """Map lines are written back in the shortest form that keeps all options."""
    assert pconfig.link_text({"from": "a", "to": ""}) == "a"
    assert pconfig.link_text({"from": "a", "to": "a"}) == "a"
    assert pconfig.link_text({"from": "a", "to": "b"}) == "a -> b"
    assert pconfig.link_text({"from": "a", "to": "b", "scale": 0.1}) == {"from": "a", "to": "b", "scale": 0.1}


def test_validate_finds_problems():
    """Validation reports every problem at once, each with the connection or gateway line it belongs to."""
    data = {
        "connections": {
            "1bad": {"protocol": "modbus"},
            "plc": {"protocol": "modbus", "role": "master", "port": "x", "tags": {"t": {"address": "zz:1"}}},
            "mq": {"protocol": "mqtt", "host": ""},
            "b": {"protocol": "matter"},
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
    """A gateway from 'knx' is only valid when KNX is set up in config.yaml."""
    data = {"connections": {"m": {"protocol": "mqtt"}}, "gateways": [{"name": "g", "from": "knx", "to": "m", "map": ["1/1/1"]}]}
    assert pconfig.validate(data, knx=True) == []
    assert "no connection called 'knx'" in "\n".join(pconfig.validate(data, knx=False))


def test_save_and_load_round_trip(tmp_path):
    """Saving leaves out defaults, writes tags and map lines compactly, is private (0600), keeps a backup and is stable
    on a second save.
    """
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
    """Invalid data is never written, so a running server is not left with a broken protocols.yaml."""
    with pytest.raises(ValueError, match="unknown protocol"):
        pconfig.save(tmp_path / "p.yaml", {"connections": {"x": {"protocol": "nope"}}})
    assert not (tmp_path / "p.yaml").exists()


def test_registry_is_consistent():
    """No duplicate setting keys in PROTOCOLS, and every default is one of its choices (the F11 form is built from
    them).
    """
    for p in PROTOCOLS.values():
        keys = [f.key for f in p.settings]
        assert len(keys) == len(set(keys)), p.name
        for f in (*p.settings, *p.tag_fields):
            assert not f.choices or f.default in f.choices or f.default in (None, ""), (p.name, f.key)
    assert installed("knx") and not installed("matter")


# -- values ------------------------------------------------------------------------------------------
@pytest.mark.parametrize(("value", "typ", "expected"), [
    ("på", "bool", True), ("0", "bool", False), (2, "bool", True), ("21,5", "float", 21.5), (21.6, "int", 22),
    (True, "int", 1), (True, "str", "true"), (None, "int", None), ({"r": 1}, "", {"r": 1}),
])
def test_coerce(value, typ, expected):
    """Values are converted to the tag type, also Norwegian words and decimal comma; values without a type are kept as
    they are.
    """
    assert coerce(value, typ) == expected


def test_coerce_refuses_nonsense():
    """A word that is neither on nor off is refused, never guessed."""
    with pytest.raises(ValueError):
        coerce("kanskje", "bool")


def test_same_value():
    """Floats are equal within a small tolerance (float32 noise); a bool is never equal to a number."""
    assert same_value(21.5, 21.500001) and not same_value(21.5, 21.6)
    assert not same_value(True, 1) and same_value("a", "a")


def test_convert_and_back():
    """Gateway conversion scales, inverts and maps enum names to numbers, and convert_back undoes the scale."""
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
    """Modbus addresses: table:number, bits, hex and the classic 4xxxx/3xxxx/1xxxx/0xxxx numbers (1-based)."""
    assert parse_address(text) == expected


@pytest.mark.parametrize("text", ["xx:1", "hr:70000", "co:1.2", "40000", "1234", "hr:1.16", ""])
def test_parse_address_refuses(text):
    """Unknown tables, numbers out of range, bits on coils and ambiguous numbers are refused."""
    with pytest.raises(ValueError):
        parse_address(text)


@pytest.mark.parametrize(("value", "dtype"), [(-5, "int16"), (65535, "uint16"), (-70000, "int32"), (21.5, "float32"),
                                             (2**40, "int64"), (1e300, "float64")])
@pytest.mark.parametrize("order", ["big", "little"])
def test_registers_round_trip(value, dtype, order):
    """Every register type survives to_registers → from_registers in both word orders."""
    assert from_registers(to_registers(value, dtype, order), dtype, order) == pytest.approx(value)


@pytest.mark.parametrize(("value", "dtype"), [(9007199254740993, "int64"), (2**64 - 1, "uint64"), (-(2**63), "int64")])
def test_large_integers_keep_every_digit(value, dtype):
    """64-bit integers above 2**53 are written exactly (not via float: 9007199254740993 must not become …992), and a
    scaled float or numeric text is still rounded to an integer."""
    from knxscada.protocols.base import as_int

    assert from_registers(to_registers(value, dtype), dtype) == value
    assert as_int(215.00000000000003) == 215 and as_int("42") == 42 and as_int("21,6") == 22 and as_int(True) == 1
    assert as_int("9007199254740993.0") == 9007199254740993                  # decimal text: not via float either
    with pytest.raises(ValueError):
        as_int("abc")
    with pytest.raises(ValueError):
        as_int("inf")


@pytest.mark.parametrize("value", [9007199254740993, -9007199254740993, 2**63 - 1, -(2**63),
                                   [9007199254740993, -9007199254740995]])
def test_large_integers_through_the_write_path(tmp_path, value):
    """The ordinary write path keeps every digit: Hub.write (coerce to the tag type) and a gateway line from an int
    tag to an int tag – the second review's reproduction (9007199254740993 arrived as …992). Lists too (arrays)."""
    async def run():
        """A user write, then the same value from a source through a gateway."""
        src = Mem("src", {"tags": {"n": {"type": "int"}}}, None, server=False)
        dev = Mem("dev", {"tags": {"n": {"type": "int", "writable": True}, "m": {"type": "int", "writable": True}}},
                  None, server=False)
        for drv in (src, dev):
            drv.session = _lasting_session(drv)
        hub = await make_hub(tmp_path, {"gateways": [{"name": "g", "from": "src", "to": "dev", "map": ["n -> m"]}]},
                             {"src": src, "dev": dev})
        try:
            await until(lambda: src.connected and dev.connected, 2)
            await hub.write("dev", "n", value)
            assert dev.writes[-1] == ("n", value)
            hub.update("src", "n", 0)
            await asyncio.sleep(0.05)
            hub.update("src", "n", value)
            await until(lambda: ("m", value) in dev.writes, 2)
        finally:
            await hub.stop()

    asyncio.run(run())


def _lasting_session(drv):
    """A session that keeps *drv* connected until the hub stops it (the default Mem session ends at once)."""
    async def session():
        """Connected until stopped."""
        drv.set_state(True)
        await drv.wait_stopped(60)

    return session


def test_coerce_int_never_goes_through_float():
    """coerce(…, "int") – used for every write through the hub – keeps 64-bit integers and rounds the rest."""
    assert coerce(9007199254740993, "int") == 9007199254740993
    assert coerce("-9007199254740993", "int") == -9007199254740993
    assert coerce(2.5, "int") == 2 and coerce("2,6", "int") == 3 and coerce(True, "int") == 1
    with pytest.raises(ValueError):
        coerce("x", "int")


def test_registers_word_order():
    """Word order 'little' puts the low word first; a value too large for the type is refused."""
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
    """MQTT payloads: numbers and true/false are parsed, JSON objects by key or 'value', other text kept."""
    assert parse_payload(payload, key) == expected


def test_make_payload():
    """Outgoing MQTT payloads are plain text or JSON with a value field; None is an empty payload."""
    assert make_payload(True, "plain") == "true"
    assert make_payload(None, "plain") == ""
    assert '"value": 21.5' in make_payload(21.5, "json")


@pytest.mark.parametrize(("pattern", "topic", "ok"), [
    ("a/#", "a/b/c", True), ("a/+/c", "a/b/c", True), ("a/+", "a/b/c", False), ("a/b", "a/b", True), ("#", "x", True),
])
def test_topic_matches(pattern, topic, ok):
    """MQTT wildcards: '#' matches the rest, '+' one level."""
    assert topic_matches(pattern, topic) is ok


# -- the gateway engine --------------------------------------------------------------------------------
def test_gateway_forward_rename_scale_and_no_echo(tmp_path):
    """A gateway line renames and scales, creates a writable target tag, and a value sent one way is not echoed back."""
    async def run():
        """Forward a raw value, then write the target and check it goes back unscaled, once."""
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
        """Send statuses, a change and values after a reconnect to a target with initial_allowed = False."""
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


def test_initial_values_wait_for_a_target_that_is_not_connected(tmp_path):
    """Start-up order: KNX statuses arrive before the PLC is connected. With initial: true they are not reported as
    errors ("not connected") but delivered when the PLC is ready; a real change while it is down is still reported."""
    async def run():
        """A source with statuses, then the device connects; then a change while the device is down."""
        src = Mem("src", {"tags": {"a": {}, "b": {}}}, None, server=False, reads_first=True)
        dev = Mem("dev", {"tags": {"x": {}, "y": {}}}, None, server=False)
        up = asyncio.Event()

        async def later():
            """Connect only when the test says so, and stay connected."""
            await up.wait()
            dev.set_state(True)
            dev.set_ready()
            await dev.wait_stopped(60)

        dev.session = later
        hub = await make_hub(tmp_path, {"gateways": [{"name": "g", "from": "src", "to": "dev", "initial": True,
                                                      "map": ["a -> x", "b -> y"]}]}, {"src": src, "dev": dev})
        try:
            hub.update("src", "a", 1)            # statuses while dev is not connected
            hub.update("src", "b", 2)
            await asyncio.sleep(0.1)
            assert not [t for level, t in hub.server.events if "not connected" in t]
            up.set()
            await until(lambda: sorted(dev.writes) == [("x", 1), ("y", 2)])
            src.set_ready()
            dev.set_state(False)                 # the device drops out; a real change is reported
            hub.update("src", "a", 3)
            await until(lambda: any("not connected" in t for level, t in hub.server.events))
        finally:
            await hub.stop()

    asyncio.run(run())


def test_start_up_sends_nothing_to_devices(tmp_path):
    """Read first, send afterwards: initial values reach our own servers, never a device (client) by default."""
    async def run():
        """Bring up a source with values and check what reaches our server and the device."""
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
        """Update the source before and after it is ready."""
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
        """Send changes while the target is reading, with one value it already has."""
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
    """When our own server reconnects (e.g. a restarted MQTT broker) it gets the current values again."""
    async def run():
        """Write a value, then drop and restore the server's connection."""
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
    """initial: false holds back the first values, and the deadband skips changes smaller than it."""
    async def run():
        """Send a status and then small steps; only the one past the deadband goes out."""
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
    """min_interval_ms sends the first value at once and then the latest one, not every step."""
    async def run():
        """Send five values within the interval."""
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
    """A wildcard map line also covers tags that appear later (new MQTT topics), with initial: false respected."""
    async def run():
        """Add a tag after start and update it twice."""
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
    """Missing tags and unavailable modules are reported as events and in the status, and writes to a failed connection
    are refused.
    """
    async def run():
        """Start with a bad connection and a gateway with unknown tags."""
        src = Mem("src", {"tags": {"x": {}}}, None, server=False)
        cli = Mem("cli", {}, None, server=False)
        hub = await make_hub(tmp_path, {"connections": {"bad": {"protocol": "matter"}},
                                        "gateways": [{"name": "g", "from": "src", "to": "cli", "map": ["nope", "x"]}]},
                             {"src": src, "cli": cli})
        try:
            text = "\n".join(t for _l, t in hub.server.events)
            assert "src has no tag 'nope'" in text and "cli has no tag 'x'" in text and "bad: The Matter module is not available yet" in text
            assert hub.failed == {"bad": "The Matter module is not available yet"}
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
        """Pass values from memory through a real Modbus slave and master, then write back and try refused writes."""
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
        """Start our OPC UA server first, then the client, and check values and a write back."""
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


def test_opcua_server_shows_the_source_quality(tmp_path):
    """Our OPC UA server never shows a value as Good that is not: a variable without a value yet is
    BadWaitingForInitialData; a bad source gives BadNoCommunication (a client gets no value with a Bad status), a
    lost source connection UncertainLastUsableValue with the last value, and the next good value – even the same –
    makes it Good again (the review's reproduction: 21.0, then bad, read 21.0 with Good)."""
    pytest.importorskip("asyncua")
    from asyncua import Client

    port = free_port()

    async def run():
        """A source linked to our server; an independent asyncua client reads the status codes."""
        src = Mem("src", {"tags": {"t": {"type": "float"}, "u": {"type": "float"}}}, None, server=False)

        async def lasting():
            """Stay connected until the test sets the connection down."""
            src.set_state(True)
            await src.wait_stopped(60)

        src.session = lasting
        data = {"connections": {"ua": {"protocol": "opcua", "role": "server",
                                       "endpoint": f"opc.tcp://127.0.0.1:{port}"}},
                "gateways": [{"name": "to-ua", "from": "src", "to": "ua", "map": ["t -> Rom.Temp", "u -> Rom.Fukt"]}]}
        hub = await make_hub(tmp_path, data, {"src": src})
        try:
            await until(lambda: hub.drivers["ua"].connected and src.connected, 10)
            src.set_ready()
            hub.update("src", "t", 21.0)
            await asyncio.sleep(0.3)
            async with Client(f"opc.tcp://127.0.0.1:{port}") as client:
                temp, fukt = client.get_node("ns=2;s=Rom.Temp"), client.get_node("ns=2;s=Rom.Fukt")

                async def status(node):
                    """(value, status code name) of *node*."""
                    dv = await node.read_data_value(raise_on_bad_status=False)
                    return dv.Value.Value if dv.Value else None, dv.StatusCode.name

                assert await status(temp) == (21.0, "Good")
                assert (await status(fukt))[1] == "BadWaitingForInitialData"       # no value yet: not 0.0 as Good
                hub.update("src", "t", 21.0, "bad")
                await asyncio.sleep(0.2)
                assert (await status(temp))[1] == "BadNoCommunication"
                hub.update("src", "t", 21.0)                                       # good again, same value
                await asyncio.sleep(0.2)
                assert await status(temp) == (21.0, "Good")
                src.set_state(False)                                               # the source connection is lost
                await asyncio.sleep(0.2)
                assert await status(temp) == (21.0, "UncertainLastUsableValue")
                # Good → Bad → lost must end as Uncertain too (the second review: reading the Bad value raised, and
                # the change to Uncertain was lost).
                src.set_state(True)
                hub.update("src", "t", 22.0)
                await until(lambda: hub.values.get("ua/Rom.Temp", {}).get("v") == 22.0, 5)
                hub.update("src", "t", 22.0, "bad")
                await asyncio.sleep(0.2)
                assert (await status(temp))[1] == "BadNoCommunication"
                src.set_state(False)
                await asyncio.sleep(0.2)
                assert await status(temp) == (22.0, "UncertainLastUsableValue")
                assert hub.values["ua/Rom.Temp"]["q"] == "old"                     # the hub knows it too
        finally:
            await hub.stop()

    asyncio.run(run())


def test_opcua_requests_split_at_the_server_limits():
    """WAGO PFC servers take at most 100 nodes per Read / CreateMonitoredItems (BadTooManyOperations)."""
    pytest.importorskip("asyncua")
    from asyncua import ua

    from knxscada.opcua_bridge import operation_limits, read_values, subscribe

    class Node:
        """A node whose read returns its value or raises it (a missing limit)."""
        def __init__(self, value):
            """The value to return, or an exception to raise."""
            self.value = value

        async def read_value(self):
            """Return the value, or raise it if it is an exception."""
            if isinstance(self.value, Exception):
                raise self.value
            return self.value

    class Client:
        """A client whose server publishes the given OperationLimits; it counts the nodes per Read."""
        def __init__(self, limits):
            """The published limits by node id; no reads yet."""
            self.limits, self.reads = limits, []

        def get_node(self, oid):
            """A node for a limit; an unpublished limit raises when read."""
            return Node(self.limits.get(oid, RuntimeError("no such node")))

        async def read_attributes(self, nodes):
            """Count the nodes in this Read and return one value per node."""
            self.reads.append(len(nodes))
            return [f"dv{n}" for n in nodes]

    class Sub:
        """A subscription that counts the nodes per CreateMonitoredItems call."""
        def __init__(self):
            """No calls yet."""
            self.calls = []

        async def subscribe_data_change(self, nodes):
            """Count the nodes in this call."""
            self.calls.append(len(nodes))

    async def run():
        """Read the limits (published, missing, 0) and read/subscribe 250 nodes in chunks."""
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
    """A demo ScadaServer with a Modbus slave linked to KNX both ways, and an old value of 1/1/1 from the database.
    Yields the server and the slave's port.
    """
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
    """KNX ↔ Modbus: nothing is sent at start-up (the database value stays local), bus values reach the slave, master
    writes become KNX writes logged with the gateway as user, and the actuator's echo does not loop.
    """
    from pymodbus.client import AsyncModbusTcpClient

    from knxscada import dpt as dptlib

    s, port = knx_server

    async def main():
        """Start the hub and drive the slave with a Modbus client."""
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
    """Without knx: in config.yaml the server runs only the protocols; KNX writes are refused and 'knx' is not a
    connection.
    """
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
            """Start the hub, ask for the protocols snapshot and stop."""
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
    """protocols.example.yaml is valid and its meter gets its tags from the device template."""
    data = pconfig.load_data(ROOT / "protocols.example.yaml")
    assert pconfig.validate(data, knx=True) == []
    meter = pconfig.settings_of(data["connections"]["meter1"])   # tags from the device template
    assert parse_address(meter["tags"]["Total active energy"]["address"]) == ("hr", 0x6000, None)


def test_cli_modules_and_gateway(tmp_path, capsys):
    """./scada modules lists the modules, ./scada gateway shows the gateways or the errors, and installing nothing or an
    unknown module fails.
    """
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
    assert main(["-c", cfg, "modules", "install", "matter"]) == 1


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
        """Answer protocol commands, including saving and reloading, from the real hub."""
        if cmd in ("protocols", "points", "point_write", "protocols_save", "protocols_reload"):
            return await hub.command({"cmd": cmd, **kw}, "test")
        return [] if cmd != "hello" else None

    async def fake_connect():
        """Act as a server without a KNX connection that has just said hello."""
        app._handle({"type": "hello", "state": {}, "status": {"connected": False, "connection": "off"},
                     "alarms": [], "stats": {}})

    monkeypatch.setattr(app.client, "request", fake_request)
    monkeypatch.setattr(app.client, "connect", fake_connect)
    monkeypatch.setattr(type(app.client), "connected", property(lambda self: True))
    monkeypatch.setattr(app.client, "close_nowait", lambda: None)

    async def run():
        """Open F11, add a Modbus master and a gateway through the forms, then try an invalid gateway."""
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


def test_logging_of_tags(tmp_path):
    """log: patterns on the connection, the tag's own log wins; deadband, interval and held-back changes."""
    from knxscada.store import Store

    async def run():
        """Log values directly with explicit time stamps and through the hub, then read the history back."""
        src = Mem("src", {"tags": {"T": {"type": "float"}, "On": {"type": "bool"}, "Quiet": {"log": False},
                                   "Own": {"log": True}, "Other": {}},
                          "log": ["T", "On", "Q*"], "log_deadband": 0.2, "log_min_s": 10}, None, server=False)
        hub = await make_hub(tmp_path, {"connections": {}, "gateways": []}, {"src": src})
        hub.server.store = store = Store(tmp_path / "db.sqlite")
        try:
            assert [t for t in ("T", "On", "Quiet", "Own", "Other") if hub.logs(src, t)] == ["T", "On", "Own"]
            hub._log(src, "T", 20.0, 1000.0)
            hub._log(src, "T", 21.0, 1005.0)     # 5 s after the last stored value: held back …
            assert hub._log_wait
            hub.flush_log(1008.0)
            hub.flush_log(1010.0)                # … and stored when the 10 s are over
            hub._log(src, "T", 21.1, 1030.0)     # inside the deadband
            hub._log(src, "On", True, 1031.0)
            hub._log(src, "On", False, 1032.0)   # on/off: always
            hub._log(src, "Other", 5, 1033.0)
            hub.update("src", "Own", 7)          # through the hub as from a driver
            store.commit()
            assert store.history("src/T", 0, point=True) == [(1000.0, 20.0), (1010.0, 21.0)]
            assert [v for _ts, v in store.history("src/On", 0, point=True)] == [1.0, 0.0]
            assert store.logged_points() == ["src/On", "src/Own", "src/T"]
        finally:
            await hub.stop()
            store.close()

    asyncio.run(run())


def test_alarm_on_protocol_tag_with_delay(knx_server):
    """match_point rules raise alarms from tags of protocols.yaml; delay_s waits; a short blip raises nothing."""
    from knxscada.alarms import Rule

    s, _port = knx_server
    s.point_rules = [Rule.from_dict({"match_point": "vent/*_AL", "condition": "true", "severity": "critical",
                                     "delay_s": 0.2, "text": "Vent: {name} = {value}"})]

    async def run():
        """Raise the alarm tag briefly, then for longer than the delay, then clear it."""
        vent = Mem("vent", {"tags": {"Fan_AL": {"type": "bool", "description": "Tilluftsvifte alarm"},
                                     "Fan_PV": {"type": "float", "unit": "%"}}}, s.hub, server=False)
        s.hub.drivers["vent"] = vent
        s.point_value("vent/Fan_PV", 50.0, "good")             # no rule for it
        s.point_value("vent/Fan_AL", True, "good")
        s.point_value("vent/Fan_AL", False, "good")            # cleared before the delay: nothing
        await asyncio.sleep(0.3)
        assert not [a for a in s.alarms.values() if a["ga"].startswith("vent/")]
        s.point_value("vent/Fan_AL", True, "good")
        s.point_value("vent/Fan_AL", None, "bad")              # bad quality changes nothing
        await asyncio.sleep(0.3)
        (alarm,) = [a for a in s.alarms.values() if a["ga"] == "vent/Fan_AL"]
        assert alarm["text"] == "Vent: Tilluftsvifte alarm = True" and alarm["severity"] == "critical"
        s.point_value("vent/Fan_AL", False, "good")
        assert alarm["cleared_ts"] is not None

    asyncio.run(run())


def test_normalise_raw_follows_the_device_resolution():
    """Driver.normalise_raw gives a value as the device returns it: float32 rounded as the drivers read it, a scaled
    integer register as a whole raw number, an int64 untouched, and anything else (bool, text, too large) unchanged."""
    from knxscada.protocols.base import as_int, float32
    from knxscada.protocols.modbus import _float32_registers

    drv = Mem("m", {}, None)
    plain = tag_from_spec("e", {"type": "float"})
    scaled = tag_from_spec("t", {"type": "float", "scale": 0.1})
    big = tag_from_spec("n", {"type": "int"})
    assert float32(21.123456789) == 21.12346 and float32(1e39) == 1e39
    assert drv.normalise_raw(plain, 21.123456789) == 21.12346
    assert drv.normalise_raw(plain, 1_000_001.0) == 1_000_001.0                  # a counter step survives
    assert drv.normalise_raw(scaled, 21.57, rounding=as_int) == pytest.approx(21.6)
    assert drv.normalise_raw(big, 9007199254740993, rounding=as_int) == 9007199254740993
    assert drv.normalise_raw(plain, True) is True and drv.normalise_raw(plain, "x") == "x"
    assert drv.normalise_raw(plain, 1e300, rounding=_float32_registers) == 1e300
