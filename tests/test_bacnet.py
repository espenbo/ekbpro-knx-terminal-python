# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""BACnet/IP: object names, validation, and our BACnet device read and written by a BACnet client."""

from __future__ import annotations

import asyncio
import json

import pytest

from knxscada.protocols import installed
from knxscada.protocols.bacnet import object_text, parse_object, to_bacnet
from knxscada.protocols.config import validate
from knxscada.protocols.hub import Hub

from .test_protocols import FakeServer, Mem, free_port, until

pytestmark_bacnet = pytest.mark.skipif(not installed("bacnet"), reason="bacpypes3 is not installed")


@pytest.mark.parametrize(("text", "expected"), [
    ("av:3", ("analog-value", 3)),
    ("AV3", ("analog-value", 3)),
    ("bi 1", ("binary-input", 1)),
    ("msv:2", ("multi-state-value", 2)),
    ("analog-input,12", ("analog-input", 12)),
    ("analogInput:12", ("analog-input", 12)),
    ("accumulator,1", ("accumulator", 1)),
])
def test_parse_object(text, expected):
    """Object identifiers are accepted in the common spellings (av:3, AV3, 'bi 1', analog-input,12, camelCase)."""
    assert parse_object(text) == expected


@pytest.mark.parametrize("text", ["", "av", "3", "av:4194303", "Ute.Temp"])
def test_parse_object_refuses(text):
    """Texts without type or instance, instances above the BACnet maximum and plain names are refused."""
    with pytest.raises(ValueError):
        parse_object(text)


def test_object_text_and_values():
    """Objects are written back in the short form where one exists, and values are converted to what each object type
    expects.
    """
    assert object_text("analog-value", 3) == "av:3"
    assert object_text("accumulator", 1) == "acc:1"
    assert object_text("trend-log", 1) == "trend-log,1"
    assert to_bacnet("binary-output", "present-value", True) == 1
    assert to_bacnet("binary-value", "present-value", "off") == 0
    assert to_bacnet("multi-state-value", "present-value", 2.4) == 2
    assert to_bacnet("analog-output", "present-value", 21) == 21.0
    assert to_bacnet("analog-output", "out-of-service", True) is True


def test_validate_bacnet():
    """Validation accepts a client with tags and a server with device_instance, and explains a missing instance, a bad
    object and a non-numeric setting.
    """
    def errors(conn):
        """Validation errors for one BACnet connection."""
        return validate({"connections": {"b": {"protocol": "bacnet", **conn}}, "gateways": []})

    assert errors({"address": "192.0.2.20", "tags": {"t": {"object": "av:1"}}}) == []
    assert any("device_instance is missing" in e for e in errors({"role": "server"}))
    assert errors({"role": "server", "device_instance": 1001}) == []
    assert any("write e.g. av:3" in e for e in errors({"address": "192.0.2.20", "tags": {"t": {"object": "xx"}}}))
    assert any("not a number" in e for e in errors({"address": "192.0.2.20", "poll_ms": "x"}))


def _hub(tmp_path, data, mems):
    """A hub with the given protocols.yaml data and memory drivers added (not started)."""
    hub = Hub(FakeServer(tmp_path))
    hub.data, hub.load = data, (lambda: None)
    for drv in mems:
        drv.hub = hub
        hub._add(drv)
    return hub


def _device(port, **extra):
    """Settings for our own BACnet device (server role) on localhost."""
    return {"protocol": "bacnet", "role": "server", "device_instance": 1001, "device_name": "Test gateway",
            "local_address": "127.0.0.1/8", "local_port": port, **extra}


def _client(port, tags, **extra):
    """Settings for a BACnet client that reads our device on localhost, with short poll and timeout."""
    return {"protocol": "bacnet", "address": f"127.0.0.1:{port}", "local_address": "127.0.0.1/8",
            "poll_ms": 200, "timeout_s": 1, "tags": tags, **extra}


@pytestmark_bacnet
def test_device_and_client_through_gateways(tmp_path):
    """mem → our BACnet device → BACnet client → mem; a client write goes back to the source; read-only refuses."""
    port = free_port()

    async def run():
        """Publish memory tags through our device, read them with a client, then write back from the client."""
        src = Mem("src", {"tags": {"t": {"type": "float", "unit": "°C"}, "on": {"type": "bool"},
                                   "mode": {"type": "int"}, "txt": {"type": "str"}}}, None, server=False)
        sink = Mem("sink", {}, None)
        data = {
            "connections": {"bac": _device(port)},
            "gateways": [
                {"name": "to-bacnet", "from": "src", "to": "bac", "map": [
                    {"from": "t", "to": "Rom101.Temp", "object": "ai:7"},
                    {"from": "on", "to": "Rom101.Lys", "direction": "both"},
                    {"from": "mode", "to": "Rom101.Modus", "object": "msv:1", "states": 4, "direction": "both"},
                    "txt -> Rom101.Tekst"]},
                {"name": "from-bacnet", "from": "cli", "to": "sink", "map": ["T", "Lys", "Modus", "Tekst", "Navn"]},
            ],
        }
        hub = _hub(tmp_path, data, (src, sink))
        await asyncio.sleep(0.05)
        await hub.start()
        try:
            bac = hub.drivers["bac"]
            await until(lambda: bac.connected, 10)
            assert bac.tags["Rom101.Temp"].address == "ai:7"
            assert bac.tags["Rom101.Lys"].address == "bv:1"           # the first free binary-value
            assert bac.tags["Rom101.Tekst"].address == "csv:1"
            hub.update("src", "t", 21.5)
            hub.update("src", "on", False)
            hub.update("src", "mode", 3)
            hub.update("src", "txt", "Hei")
            hub._make("cli", _client(port, {"T": {"object": "ai:7"}, "Lys": {"object": "bv:1"},
                                            "Modus": {"object": "msv:1"}, "Tekst": {"object": "csv:1"},
                                            "Navn": {"object": "ai:7", "property": "object-name"}}))
            hub._build_engine()
            cli = hub.drivers["cli"]
            await until(lambda: cli.ready, 10)
            assert cli.identity.startswith("Test gateway")
            v = hub.values
            await until(lambda: v.get("cli/T", {}).get("v") == pytest.approx(21.5), 5)
            assert v["cli/Lys"]["v"] is False and v["cli/Modus"]["v"] == 3 and v["cli/Tekst"]["v"] == "Hei"
            assert v["cli/Navn"]["v"] == "Rom101.Temp"
            assert str(bac.local["Rom101.Temp"].units) == "degrees-celsius"
            # Changes on the source reach the client.
            hub.update("src", "t", 22.0)
            await until(lambda: v["cli/T"]["v"] == pytest.approx(22.0), 5)
            # The BMS writes the writable objects: the values go back to the source.
            await hub.write("cli", "Lys", True)
            await until(lambda: ("on", True) in src.writes, 5)
            await hub.write("cli", "Modus", 2)
            await until(lambda: ("mode", 2) in src.writes, 5)
            # Read-only: an input, a target without direction both, and other properties.
            for tag in ("T", "Tekst"):
                cli.tags[tag].writable = True
                with pytest.raises(RuntimeError, match="refused"):
                    await hub.write("cli", tag, 1)
            assert not [t for level, t in hub.server.events if level == "error"]
        finally:
            await hub.stop()

    asyncio.run(run())


@pytestmark_bacnet
def test_client_follows_changes_with_cov(tmp_path):
    """With cov: true the client gets changes by subscription, long before the next poll."""
    port = free_port()

    async def run():
        """Poll only once a minute, so the second value can only arrive by COV."""
        src = Mem("src", {"tags": {"t": {"type": "float"}}}, None, server=False)
        data = {"connections": {"bac": _device(port)},
                "gateways": [{"name": "g", "from": "src", "to": "bac", "map": [{"from": "t", "to": "T", "object": "av:1"}]}]}
        hub = _hub(tmp_path, data, (src,))
        await asyncio.sleep(0.05)
        await hub.start()
        try:
            await until(lambda: hub.drivers["bac"].connected, 10)
            hub.update("src", "t", 1.0)
            hub._make("cli", _client(port, {"T": {"object": "av:1"}}, poll_ms=60000, cov=True))
            await until(lambda: hub.drivers["cli"].ready, 10)
            await until(lambda: hub.values.get("cli/T", {}).get("v") == 1.0, 5)
            hub.update("src", "t", 5.0)   # only COV can bring this before the next poll in 60 s
            await until(lambda: hub.values["cli/T"]["v"] == 5.0, 5)
        finally:
            await hub.stop()

    asyncio.run(run())


@pytestmark_bacnet
def test_instance_numbers_are_kept(tmp_path):
    """The numbers given to gateway targets are saved, kept after a restart and never given to another target."""
    port = free_port()

    async def start(targets):
        """Start the device with these gateway targets and return the object each got."""
        src = Mem("src", {"tags": {n: {"type": "float"} for n in targets}}, None, server=False)
        hub = _hub(tmp_path, {"connections": {"bac": _device(port)},
                              "gateways": [{"name": "g", "from": "src", "to": "bac", "map": list(targets)}]}, (src,))
        await asyncio.sleep(0.05)
        await hub.start()
        await until(lambda: hub.drivers["bac"].connected, 10)
        numbers = {n: hub.drivers["bac"].tags[n].address for n in targets}
        await hub.stop()
        return numbers

    async def run():
        """Start twice with different targets and check the numbers and the saved file."""
        assert await start(["a", "b", "c"]) == {"a": "av:1", "b": "av:2", "c": "av:3"}
        assert await start(["c", "d"]) == {"c": "av:3", "d": "av:4"}       # b's number 2 is not reused
        saved = json.loads((tmp_path / "var" / "bacnet" / "bac.json").read_text(encoding="utf-8"))
        assert saved == {"a": "av:1", "b": "av:2", "c": "av:3", "d": "av:4"}

    asyncio.run(run())


@pytestmark_bacnet
def test_client_without_answer_is_not_connected(tmp_path):
    """A client whose device does not answer is shown as not connected with 'no answer', not as ready."""
    port = free_port()

    async def run():
        """Point a client at a port where nothing listens and wait for its error."""
        hub = _hub(tmp_path, {"connections": {"cli": _client(port, {"T": {"object": "av:1"}}, timeout_s=0.2)},
                              "gateways": []}, ())
        await hub.start()
        try:
            await until(lambda: hub.drivers["cli"].error, 10)
            assert not hub.drivers["cli"].connected
            assert "no answer" in hub.drivers["cli"].error
        finally:
            await hub.stop()

    asyncio.run(run())


@pytestmark_bacnet
def test_scan_objects_and_yaml(tmp_path, capsys):
    """``./scada bacnet-scan ADDRESS``: the objects of our device, and ``--yaml`` as tags for protocols.yaml."""
    import yaml

    from knxscada.__main__ import main
    from knxscada.protocols import bacnet

    port = free_port()

    async def run():
        """Scan our device directly, then run the bacnet-scan command against it."""
        src = Mem("src", {"tags": {"t": {"type": "float", "unit": "°C"}, "on": {"type": "bool"}}}, None, server=False)
        data = {"connections": {"bac": _device(port)},
                "gateways": [{"name": "g", "from": "src", "to": "bac", "map": [
                    {"from": "t", "to": "Rom101.Temp", "object": "ai:7", "description": "Romtemperatur"},
                    "on -> Rom101.Lys"]}]}
        hub = _hub(tmp_path, data, (src,))
        await asyncio.sleep(0.05)
        await hub.start()
        try:
            await until(lambda: hub.drivers["bac"].connected, 10)
            hub.update("src", "t", 21.5)
            hub.update("src", "on", True)
            app = bacnet.make_app("127.0.0.1/8:0", bacnet.MAX_INSTANCE, "scan", 1)
            try:
                await asyncio.sleep(0)
                target, device_id = await bacnet.find_device(app, f"127.0.0.1:{port}")
                assert device_id == 1001
                rows = {r["object"]: r for r in await bacnet.scan_objects(app, target, device_id)}
            finally:
                app.close()
            assert rows["ai:7"]["name"] == "Rom101.Temp" and rows["ai:7"]["unit"] == "°C"
            assert rows["ai:7"]["value"] == pytest.approx(21.5)
            assert rows["bv:1"]["name"] == "Rom101.Lys" and rows["bv:1"]["value"] is True
            # The command line, in a thread: it runs its own event loop.
            code = await asyncio.to_thread(main, ["bacnet-scan", f"127.0.0.1:{port}", "--local", "127.0.0.1/8",
                                                  "--timeout", "1", "--yaml"])
            assert code == 0
        finally:
            await hub.stop()

    asyncio.run(run())
    out = yaml.safe_load(capsys.readouterr().out)
    conn = out["connections"]["device1001"]
    assert conn["protocol"] == "bacnet" and conn["device_id"] == 1001
    assert conn["tags"]["Rom101.Temp"] == {"object": "ai:7", "unit": "°C", "description": "Romtemperatur"}
    assert conn["tags"]["Rom101.Lys"]["object"] == "bv:1"
    assert validate({"connections": {"d": conn}, "gateways": []}) == []


def test_scan_without_module(monkeypatch, capsys):
    """bacnet-scan without bacpypes3 tells how to install the module instead of failing."""
    from knxscada import __main__, protocols

    monkeypatch.setattr(protocols, "installed", lambda name: False)
    assert __main__.main(["bacnet-scan"]) == 1
    assert "modules install bacnet" in capsys.readouterr().out


@pytestmark_bacnet
def test_from_bacnet():
    """Values from BACnet become plain Python values: float32 rounded to 7 digits, Double kept, BinaryPV as bool."""
    from bacpypes3.basetypes import BinaryPV
    from bacpypes3.primitivedata import Double, Real

    from knxscada.protocols.bacnet import from_bacnet

    assert from_bacnet(Real(1.688)) == 1.688                  # float32 is rounded to 7 digits
    assert from_bacnet(Double(1.687999963760376)) == 1.687999963760376
    assert from_bacnet(BinaryPV("active")) is True


def test_route_source():
    """The client binds to the address the system routes from (over Tailscale: the VPN address, not the LAN)."""
    from knxscada.protocols.bacnet import route_source

    assert route_source("127.0.0.1") == "127.0.0.1/32"
    assert route_source("127.0.0.1:47809") == "127.0.0.1/32"
    assert route_source("2:5") is None and route_source("not-an-address") is None


@pytestmark_bacnet
def test_client_write_priority_and_release(tmp_path):
    """WriteProperty gets the chosen priority, else the connection's; a release sends Null at that priority.
    Our own BACnet device (server role) has no priority array: a release there is refused."""
    from bacpypes3.primitivedata import Null

    port = free_port()

    async def run():
        """Replace the client's WriteProperty with a recorder and write, write with priority and release."""
        src = Mem("src", {"tags": {"on": {"type": "bool"}}}, None, server=False)
        data = {"connections": {"bac": _device(port)}, "gateways": [
            {"name": "to-bacnet", "from": "src", "to": "bac", "map": [{"from": "on", "to": "Lys", "direction": "both"}]}]}
        hub = _hub(tmp_path, data, (src,))
        await asyncio.sleep(0.05)
        await hub.start()
        try:
            bac = hub.drivers["bac"]
            await until(lambda: bac.connected, 10)
            hub.update("src", "on", False)
            hub._make("cli", _client(port, {"Lys": {"object": "bv:1"}}, priority=16))
            hub._build_engine()
            cli = hub.drivers["cli"]
            await until(lambda: cli.ready, 10)
            sent = []

            async def record(address, objid, prop, value, priority=None):
                """Keep what would have been written, instead of sending it."""
                sent.append((objid, prop, value, priority))

            cli.app.write_property = record
            assert hub.points("cli")[0]["priority"] == 16            # the default the TUI shows
            await hub.write("cli", "Lys", True)
            await hub.write("cli", "Lys", False, priority=8)
            await hub.write("cli", "Lys", None, priority=8, release=True)
            assert [(s[2], s[3]) for s in sent[:2]] == [(1, 16), (0, 8)]
            assert isinstance(sent[2][2], Null) and sent[2][3] == 8
            with pytest.raises(ValueError, match="no priority array"):
                await hub.write("bac", "Lys", None, priority=8, release=True)
        finally:
            await hub.stop()

    asyncio.run(run())
