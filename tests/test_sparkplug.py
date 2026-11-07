# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""MQTT Sparkplug B: the protobuf messages, and our edge node read and commanded by a host application through a
local MQTT broker (amqtt)."""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from knxscada.protocols.config import validate
from knxscada.protocols.sparkplug import (
    BOOLEAN,
    DOUBLE,
    FLOAT,
    INT8,
    INT32,
    INT64,
    STRING,
    UINT16,
    decode_metric,
    decode_payload,
    encode_metric,
    encode_payload,
)

from .test_protocols import Mem, free_port, make_hub, until


def test_payload_bytes():
    """The bytes of a small payload, worked out by hand from the protobuf rules: timestamp (field 1), one metric
    (field 2: name "a", datatype 11 = Boolean, boolean_value true in field 14) and seq 0 (field 3)."""
    metric = encode_metric("a", True, BOOLEAN)
    assert metric == bytes.fromhex("0a0161200b7001")
    assert encode_payload([metric], 0, ts=1) == bytes.fromhex("0801" + "1207" + "0a0161200b7001" + "1800")


@pytest.mark.parametrize(("value", "datatype"), [
    (True, BOOLEAN), (False, BOOLEAN), (21.5, DOUBLE), (21.5, FLOAT), (-5, INT8), (-70000, INT32),
    (-(2 ** 40), INT64), (65535, UINT16), ("Stue", STRING), (None, DOUBLE),
])
def test_metric_round_trip(value, datatype):
    """Every value type comes back unchanged, negative integers included (two's complement as Eclipse Tahu)."""
    m = decode_metric(encode_metric("x", value, datatype, alias=7, ts=123))
    assert (m["name"], m["alias"], m["timestamp"], m["datatype"], m["value"]) == ("x", 7, 123, datatype, value)


def test_data_by_alias_and_payload_round_trip():
    """A DATA metric may carry only the alias; the payload keeps its timestamp, sequence and metrics."""
    data = encode_payload([encode_metric(None, 3.0, DOUBLE, alias=2)], 5, ts=99)
    p = decode_payload(data)
    assert (p["timestamp"], p["seq"]) == (99, 5)
    assert p["metrics"][0]["name"] is None and p["metrics"][0]["alias"] == 2 and p["metrics"][0]["value"] == 3.0


def test_validate_sparkplug():
    """A Sparkplug connection is a real module now (not "planned"), and its settings are checked."""
    ok = {"connections": {"sp": {"protocol": "sparkplug", "role": "server", "group_id": "Bygg"}}}
    assert validate(ok) == []
    bad = {"connections": {"sp": {"protocol": "sparkplug", "qos": "2"}}}
    assert any("qos" in e for e in validate(bad))


@pytest.mark.filterwarnings("ignore::DeprecationWarning")   # amqtt's own configuration notes
def test_edge_node_and_host_application(tmp_path):
    """Through a local broker: our edge node announces the gateway targets in NBIRTH and sends changes; a host
    application of the same group gets every metric as a tag; a write from the host (NCMD) reaches the source of a
    ``direction: both`` line; when the edge node stops, its NDEATH marks the host's tags bad."""
    amqtt = pytest.importorskip("amqtt.broker")
    port = free_port()

    async def run():
        """Broker, then a hub with a source, our edge node and a host application of it."""
        broker = amqtt.Broker({"listeners": {"default": {"type": "tcp", "bind": f"127.0.0.1:{port}"}},
                               "sys_interval": 0, "auth": {"allow-anonymous": True},
                               "topic-check": {"enabled": False}})
        await broker.start()
        src = Mem("src", {"tags": {"T": {"type": "float"}, "Lys": {"type": "bool", "writable": True}}}, None,
                  server=False)

        async def lasting():
            """Stay connected (the default Mem session ends at once)."""
            src.set_state(True)
            await src.wait_stopped(60)

        src.session = lasting
        mqtt = {"host": "127.0.0.1", "port": port, "group_id": "Hus"}
        data = {"connections": {
            "edge": {"protocol": "sparkplug", "role": "server", "edge_node": "knx", **mqtt},
            "host": {"protocol": "sparkplug", "role": "client", "edge_node": "knx", **mqtt,
                     "tags": {"Lys": {"metric": "Lys", "writable": True}}}},
            "gateways": [{"name": "to-edge", "from": "src", "to": "edge", "initial": True,
                          "map": [{"from": "T", "to": "Temp"}, {"from": "Lys", "to": "Lys", "direction": "both"}]}]}
        hub = await make_hub(tmp_path, data, {"src": src})
        try:
            edge, host = hub.drivers["edge"], hub.drivers["host"]
            await until(lambda: edge.connected and host.connected, 10)
            hub.update("src", "T", 20.5)
            hub.update("src", "Lys", False)
            await until(lambda: hub.values.get("host/Temp", {}).get("v") == 20.5, 10)
            assert host.tags["Temp"].extra["node"] == "knx" and "Lys" in edge.tags
            hub.update("src", "T", 21.0)                                  # a change: NDATA by alias
            await until(lambda: hub.values.get("host/Temp", {}).get("v") == 21.0, 10)
            await hub.write("host", "Lys", True)                           # NCMD → edge → back to the source
            await until(lambda: ("Lys", True) in src.writes, 10)
            await edge.stop()                                              # NDEATH (the will) → tags bad
            with contextlib.suppress(Exception):
                await edge.close()
            hub.tasks["edge"].cancel()
            await until(lambda: hub.values["host/Temp"]["q"] == "bad", 15)
        finally:
            await hub.stop()
            await broker.shutdown()

    asyncio.run(run())


def test_two_edge_nodes_with_the_same_metric_names(tmp_path):
    """Following every node (edge_node "+"): node A and node B both have "Temp". They become two tags, a write goes
    to the right node, and A's death marks only A's tag bad (the review's reproduction)."""
    from knxscada.protocols.hub import Hub
    from knxscada.protocols.sparkplug import DOUBLE, Driver

    from .test_protocols import FakeServer

    hub = Hub(FakeServer(tmp_path))
    host = Driver("host", {"protocol": "sparkplug", "role": "client", "group_id": "Hus", "edge_node": "+",
                           "tags": {"B temp": {"metric": "Temp", "node": "B", "writable": True}}}, hub)
    hub.drivers["host"] = host
    sent = []

    async def command(node, device, metric, value, datatype):
        """Record the NCMD/DCMD instead of publishing it."""
        sent.append((node, device, metric, value))

    host._command = command

    def message(kind, node, metrics, device=""):
        """Deliver one Sparkplug message as the broker would."""
        body = encode_payload([encode_metric(name, value, DOUBLE, alias=alias) for name, value, alias in metrics], 0)
        host._message("/".join(p for p in ("spBv1.0", "Hus", kind, node, device) if p), body)

    message("NBIRTH", "A", [("Temp", 20.0, 1)])
    message("NBIRTH", "B", [("Temp", 30.0, 1)])
    message("NDATA", "A", [(None, 21.0, 1)])                       # by alias – the same alias on both nodes
    assert hub.values["host/A/Temp"]["v"] == 21.0 and hub.values["host/B temp"]["v"] == 30.0
    asyncio.run(host.write(host.tags["B temp"], 31.0))
    assert sent == [("B", "", "Temp", 31.0)]
    message("NDEATH", "A", [])
    assert hub.values["host/A/Temp"]["q"] == "bad" and hub.values["host/B temp"]["q"] == "good"
