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


@pytest.mark.parametrize(("value", "datatype"), [(-1, 8), (2**64, 8), (256, 5), (-129, INT8), (2**63, INT64),
                                                 (-1, UINT16), (2**32, 7)])
def test_out_of_range_integers_are_refused(value, datatype):
    """A value outside its integer type is refused with ValueError before encoding – -1 as UInt64 used to hang the
    server in an endless _varint loop (the third review)."""
    with pytest.raises(ValueError):
        encode_metric("x", value, datatype)


def test_varint_refuses_negative_numbers():
    """_varint itself refuses a negative number instead of looping for ever."""
    from knxscada.protocols.sparkplug import _varint

    with pytest.raises(ValueError):
        _varint(-1)


def test_range_limits_round_trip():
    """The edges of every integer type are accepted and come back unchanged."""
    from knxscada.protocols.sparkplug import INT_RANGES

    for datatype, (low, high) in INT_RANGES.items():
        for v in (low, high):
            assert decode_metric(encode_metric("x", v, datatype))["value"] == v


def test_hub_write_of_an_out_of_range_value_returns(tmp_path):
    """Hub.write of -1 to a UInt64 metric raises at once (it never returned before)."""
    from knxscada.protocols.hub import Hub
    from knxscada.protocols.sparkplug import UINT64, Driver

    from .test_protocols import FakeServer

    async def run():
        """A client with a known UInt64 metric; the command would be published if it got that far."""
        hub = Hub(FakeServer(tmp_path))
        host = Driver("host", {"protocol": "sparkplug", "role": "client", "group_id": "Hus", "edge_node": "A",
                               "tags": {"N": {"metric": "N", "type": "int", "writable": True}}}, hub)
        hub.drivers["host"] = host
        host.connected = host.ready = True
        host.types[("A", "", "N")] = UINT64
        sent = []

        class Client:
            """Records publishes."""
            async def publish(self, *a, **kw):
                """Record."""
                sent.append(a)

        host.client = Client()
        with pytest.raises(ValueError):
            await asyncio.wait_for(hub.write("host", "N", -1), 2)
        assert sent == []

    asyncio.run(run())


def test_late_birth_is_a_status(tmp_path):
    """A birth that arrives after the connection is ready – a node starting after us, or a rebirth while connected –
    is a status: its values are not passed to a target that never takes initial values (KNX). A DATA message
    afterwards is a change and is. (The driver is not started: no broker is needed, messages are fed directly.)"""
    from knxscada.protocols.sparkplug import Driver

    from .test_protocols import Mem

    async def run():
        """host (Sparkplug client, ready) → knx-like target."""
        knx = Mem("knx", {"tags": {"1/1/1": {"type": "float"}}}, None, initial_allowed=False, server=False)

        async def lasting():
            """Stay connected."""
            knx.set_state(True)
            await knx.wait_stopped(60)

        knx.session = lasting
        data = {"connections": {},
                "gateways": [{"name": "g", "from": "host", "to": "knx", "settle_ms": 0, "map": ["T -> 1/1/1"]}]}
        hub = await make_hub(tmp_path, data, {"knx": knx})
        try:
            host = Driver("host", {"protocol": "sparkplug", "role": "client", "group_id": "Hus", "edge_node": "A",
                                   "tags": {"T": {"metric": "T", "type": "float"}}}, hub)
            hub.drivers["host"] = host
            host.connected = host.ready = True             # as BIRTH_WAIT_S after subscribing
            hub._build_engine()
            await until(lambda: knx.connected, 2)
            hub.update("knx", "1/1/1", 20.0)
            host._message("spBv1.0/Hus/NBIRTH/A", encode_payload([encode_metric("T", 25.0, DOUBLE, alias=1)], 0))
            await asyncio.sleep(0.1)
            assert hub.values["host/T"]["v"] == 25.0 and knx.writes == []    # the late birth is only remembered
            host._message("spBv1.0/Hus/NDATA/A", encode_payload([encode_metric(None, 26.0, DOUBLE, alias=1)], 1))
            await until(lambda: ("1/1/1", 26.0) in knx.writes, 2)
            host._message("spBv1.0/Hus/NBIRTH/A", encode_payload([encode_metric("T", 27.0, DOUBLE, alias=1)], 0))
            await asyncio.sleep(0.1)
            assert ("1/1/1", 27.0) not in knx.writes        # a rebirth while connected: a status too
        finally:
            await hub.stop()

    asyncio.run(run())


def test_quality_property_round_trip():
    """The Quality property is encoded as an Int32 in the metric's property set and read back: 0 bad, 500 stale (old),
    192 good; a metric without it is good, and the OPC uncertain range counts as old."""
    from knxscada.protocols.sparkplug import QUALITY_CODES, code_quality

    for quality, code in (("bad", 0), ("old", 500), ("good", 192)):
        m = decode_metric(encode_metric("T", 21.5, DOUBLE, alias=3, quality=code))
        assert (m["name"], m["alias"], m["value"], m["quality"]) == ("T", 3, 21.5, quality)
    assert decode_metric(encode_metric("T", 1, INT64))["quality"] == "good"
    assert code_quality(100) == "old" and code_quality(None) == "good" and QUALITY_CODES["none"] == 0


@pytest.mark.filterwarnings("ignore::DeprecationWarning")   # amqtt's own configuration notes
def test_quality_travels_over_mqtt_and_sparkplug(tmp_path):
    """A bad source is shown by our own MQTT and Sparkplug targets and understood by their readers: plain MQTT gets
    ``<topic>/quality``, JSON a ``"quality"`` key, Sparkplug the Quality property. The readers' values turn bad (so
    their gateways would pass nothing on), and the next good source value makes everything good again."""
    amqtt = pytest.importorskip("amqtt.broker")
    port = free_port()

    async def run():
        """Broker; src → edge node, plain and JSON MQTT targets; a host application and an MQTT reader."""
        broker = amqtt.Broker({"listeners": {"default": {"type": "tcp", "bind": f"127.0.0.1:{port}"}},
                               "sys_interval": 0, "auth": {"allow-anonymous": True},
                               "topic-check": {"enabled": False}})
        await broker.start()
        src = Mem("src", {"tags": {"T": {"type": "float"}}}, None, server=False)

        async def lasting():
            """Stay connected (the default Mem session ends at once)."""
            src.set_state(True)
            await src.wait_stopped(60)

        src.session = lasting
        mq = {"host": "127.0.0.1", "port": port}
        data = {"connections": {
            "edge": {"protocol": "sparkplug", "role": "server", "edge_node": "knx", "group_id": "Hus", **mq},
            "host": {"protocol": "sparkplug", "role": "client", "edge_node": "knx", "group_id": "Hus", **mq},
            "plain": {"protocol": "mqtt", "topic_prefix": "p/", **mq},
            "js": {"protocol": "mqtt", "topic_prefix": "j/", "payload": "json", **mq},
            "watch": {"protocol": "mqtt", **mq, "tags": {"pq": {"topic": "p/T/quality", "type": "str"},
                                                          "pv": {"topic": "p/T"}, "jv": {"topic": "j/T"}}}},
            "gateways": [{"name": f"to-{c}", "from": "src", "to": c, "map": ["T"]} for c in ("edge", "plain", "js")]}
        hub = await make_hub(tmp_path, data, {"src": src})
        v = hub.values
        try:
            await until(lambda: all(hub.drivers[c].ready for c in data["connections"]), 15)
            hub.update("src", "T", 20.5)
            await until(lambda: v.get("host/T", {}).get("v") == 20.5 and v.get("watch/jv", {}).get("v") == 20.5
                        and v.get("watch/pq", {}).get("v") == "good", 10)
            assert v["host/T"]["q"] == v["watch/jv"]["q"] == "good"
            hub.update("src", "T", 20.5, "bad")                 # the source reports a bad status
            await until(lambda: v["host/T"]["q"] == "bad" and v["watch/jv"]["q"] == "bad"
                        and v["watch/pq"]["v"] == "bad", 10)
            assert v["host/T"]["v"] == 20.5 and v["watch/jv"]["v"] == 20.5 and v["watch/pv"]["q"] == "good"
            hub.update("src", "T", 21.0)
            await until(lambda: v["host/T"]["q"] == "good" and v["host/T"]["v"] == 21.0
                        and v["watch/jv"]["q"] == "good" and v["watch/pq"]["v"] == "good", 10)
        finally:
            await hub.stop()
            await broker.shutdown()

    asyncio.run(run())
