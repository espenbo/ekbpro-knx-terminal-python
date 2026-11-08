# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""MQTT Sparkplug B: values with a known type, birth and death, over an MQTT broker.

Sparkplug B (Eclipse Tahu, v3.0) puts a protobuf ``Payload`` on fixed topics
``spBv1.0/<group>/<type>/<edge node>[/<device>]``. An *edge node* announces all its metrics with a type in
``NBIRTH``/``DBIRTH``, then sends only changes (``NDATA``/``DDATA``, by alias), and the broker publishes its
``NDEATH`` (the MQTT will) when it disappears. Commands go the other way (``NCMD``/``DCMD``).

Roles
-----
* ``server`` – we are an edge node (``edge_node``, default ``knxscada``) in ``group_id``: every gateway target becomes
  a metric. Connecting publishes NBIRTH with all of them (and ``bdSeq`` and ``Node Control/Rebirth``), each change
  NDATA. A new target after the birth gives a new NBIRTH. An NCMD with ``Node Control/Rebirth`` = true asks for the
  birth again; an NCMD for a writable metric (gateway ``direction: both``) is passed back to the source, as MQTT's
  ``/set``. Initial values (gateway ``initial: true``) are allowed: the metrics are ours.
* ``client`` – a host application that follows the edge node ``edge_node`` (``+`` = every node) in ``group_id``.
  Every metric of a birth becomes a tag by itself (``<device>/<metric>`` for device metrics), and the tags under
  ``tags:`` pick metrics by ``metric``/``device`` (and ``node`` when following every node) to give them a name, scale,
  unit or ``writable``. A metric is identified by (node, device, metric) throughout: with ``edge_node: "+"`` the
  automatic tags are named ``<node>/<device>/<metric>``, so two nodes with the same metric names never share a tag,
  and a death marks only that node's (or device's) tags "bad", so gateways forget their values. After connecting it asks the node for a rebirth
  (an NCMD ``Node Control/Rebirth`` – a standard control command, not a process value; ``rebirth_request: false``
  turns it off). Writing a tag sends NCMD/DCMD with the metric's type from the birth.

The protobuf messages are encoded and decoded here (:func:`encode_payload`, :func:`decode_payload`) – only the fields
Sparkplug uses for single values – so the module needs no protobuf package, only ``aiomqtt`` like ``mqtt``.
Datasets, templates, properties and metadata are skipped when reading.
"""

from __future__ import annotations

import asyncio
import contextlib
import struct
import time
from typing import Any

from ..i18n import _
from .base import TYPES, Tag, as_float
from .base import Driver as BaseDriver

#: Topic namespace of Sparkplug B.
NAMESPACE = "spBv1.0"
#: The metric that asks an edge node to publish its birth again.
REBIRTH = "Node Control/Rebirth"
#: Seconds after subscribing until the connection counts as ready (births asked for by the rebirth have arrived).
BIRTH_WAIT_S = 2.0

#: Sparkplug data types used here (value → name).
INT8, INT16, INT32, INT64, UINT8, UINT16, UINT32, UINT64 = 1, 2, 3, 4, 5, 6, 7, 8
FLOAT, DOUBLE, BOOLEAN, STRING, DATETIME, TEXT = 9, 10, 11, 12, 13, 14
#: Signed integer types → their bit width (stored as two's complement in the unsigned protobuf field).
SIGNED_BITS = {INT8: 8, INT16: 16, INT32: 32, INT64: 64}
#: Integer types carried in ``int_value`` (uint32); the 64-bit ones and DateTime use ``long_value`` (uint64).
INT_FIELD_TYPES = {INT8, INT16, INT32, UINT8, UINT16, UINT32}


# -- protobuf ---------------------------------------------------------------------------------------------------------
def _varint(n: int) -> bytes:
    """An unsigned protobuf varint."""
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _field(number: int, wire: int, data: bytes) -> bytes:
    """Field key and data (*data* already encoded for the wire type; length-delimited gets its length here)."""
    key = _varint((number << 3) | wire)
    return key + (_varint(len(data)) + data if wire == 2 else data)


def metric_type(value: Any) -> int:
    """The Sparkplug data type for a Python value: Boolean, Int64, Double or String."""
    if isinstance(value, bool):
        return BOOLEAN
    if isinstance(value, int):
        return INT64
    if isinstance(value, float):
        return DOUBLE
    return STRING


def encode_metric(name: str | None, value: Any, datatype: int, alias: int | None = None,
                  ts: int | None = None) -> bytes:
    """One ``Metric``: name (left out in DATA when the alias is enough), alias, timestamp (ms), datatype, value.

    None is sent as ``is_null``. Signed integers go as two's complement in the unsigned field (as Eclipse Tahu).
    """
    out = b""
    if name is not None:
        out += _field(1, 2, name.encode("utf-8"))
    if alias is not None:
        out += _field(2, 0, _varint(alias))
    if ts is not None:
        out += _field(3, 0, _varint(ts))
    out += _field(4, 0, _varint(datatype))
    if value is None:
        return out + _field(7, 0, _varint(1))
    if datatype == BOOLEAN:
        return out + _field(14, 0, _varint(1 if value else 0))
    if datatype == FLOAT:
        return out + _field(12, 5, struct.pack("<f", float(value)))
    if datatype == DOUBLE:
        return out + _field(13, 1, struct.pack("<d", float(value)))
    if datatype in (STRING, TEXT):
        return out + _field(15, 2, str(value).encode("utf-8"))
    n = round(float(value)) if not isinstance(value, int) else value
    bits = SIGNED_BITS.get(datatype)
    if bits and n < 0:
        n += 1 << bits
    return out + _field(10 if datatype in INT_FIELD_TYPES else 11, 0, _varint(n))


def encode_payload(metrics: list[bytes], seq: int | None, ts: int | None = None) -> bytes:
    """A ``Payload``: timestamp (ms, default now), the encoded metrics and the sequence number (None for NDEATH)."""
    out = _field(1, 0, _varint(int(time.time() * 1000) if ts is None else ts))
    for m in metrics:
        out += _field(2, 2, m)
    if seq is not None:
        out += _field(3, 0, _varint(seq))
    return out


def _fields(data: bytes):
    """Yield ``(number, wire type, value)`` of a protobuf message; value is an int (varint), bytes or raw bytes."""
    i, n = 0, len(data)
    while i < n:
        key, i = _read_varint(data, i)
        number, wire = key >> 3, key & 7
        if wire == 0:
            value, i = _read_varint(data, i)
        elif wire == 1:
            value, i = data[i:i + 8], i + 8
        elif wire == 2:
            length, i = _read_varint(data, i)
            value, i = data[i:i + length], i + length
        elif wire == 5:
            value, i = data[i:i + 4], i + 4
        else:
            raise ValueError(f"protobuf wire type {wire}")
        yield number, wire, value


def _read_varint(data: bytes, i: int) -> tuple[int, int]:
    """A varint at *i*: ``(value, next index)``."""
    shift = result = 0
    while True:
        if i >= len(data):
            raise ValueError("truncated varint")
        b = data[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, i
        shift += 7


def decode_metric(data: bytes) -> dict[str, Any]:
    """A ``Metric`` → ``{name, alias, timestamp, datatype, value}`` (value None for is_null or unknown kinds)."""
    m: dict[str, Any] = {"name": None, "alias": None, "timestamp": None, "datatype": 0, "value": None}
    raw: Any = None
    for number, _wire, value in _fields(data):
        if number == 1:
            m["name"] = value.decode("utf-8", "replace")
        elif number == 2:
            m["alias"] = value
        elif number == 3:
            m["timestamp"] = value
        elif number == 4:
            m["datatype"] = value
        elif number in (10, 11):
            raw = value
        elif number == 12:
            raw = struct.unpack("<f", value)[0]
            raw = float(f"{raw:.7g}")                 # float32: 21.5, not 21.499999046325684
        elif number == 13:
            raw = struct.unpack("<d", value)[0]
        elif number == 14:
            raw = bool(value)
        elif number == 15:
            raw = value.decode("utf-8", "replace")
    bits = SIGNED_BITS.get(m["datatype"])
    if bits and isinstance(raw, int) and not isinstance(raw, bool) and raw >= 1 << (bits - 1):
        raw -= 1 << bits
    if m["datatype"] == BOOLEAN and isinstance(raw, int):
        raw = bool(raw)
    m["value"] = raw
    return m


def decode_payload(data: bytes) -> dict[str, Any]:
    """A ``Payload`` → ``{timestamp, seq, metrics: [decode_metric …]}``."""
    out: dict[str, Any] = {"timestamp": None, "seq": None, "metrics": []}
    for number, _wire, value in _fields(data):
        if number == 1:
            out["timestamp"] = value
        elif number == 2:
            out["metrics"].append(decode_metric(value))
        elif number == 3:
            out["seq"] = value
    return out


def topic(group: str, kind: str, node: str, device: str = "") -> str:
    """``spBv1.0/<group>/<kind>/<node>[/<device>]``."""
    return "/".join(p for p in (NAMESPACE, group, kind, node, device) if p)


# -- the driver -------------------------------------------------------------------------------------------------------
class Driver(BaseDriver):
    """One Sparkplug B connection: an edge node of our own (server) or a host application (client)."""

    protocol = "sparkplug"
    # Client: births asked for at connect are statuses, not changes – ready BIRTH_WAIT_S after subscribing.
    reads_first = True

    def __init__(self, name: str, settings: dict[str, Any], hub) -> None:
        """Set up the connection; the MQTT client is made in :meth:`session`."""
        self.group = str(settings.get("group_id") or "knxscada")
        self.node = str(settings.get("edge_node") or ("knxscada" if settings.get("role") == "server" else "+"))
        self.qos = int(settings.get("qos") or 0)
        self.client = None
        self.seq = 0                    # server: 0 in NBIRTH, +1 per message, wraps at 256
        self.bd_seq = 0                 # server: birth/death sequence, the same in NBIRTH and its NDEATH (will)
        self.aliases: dict[str, int] = {}                 # server: metric name → alias
        # client: (node, device, alias) → metric name and (node, device, name) → datatype, from the births
        self.alias_names: dict[tuple[str, str, int], str] = {}
        self.types: dict[tuple[str, str, str], int] = {}
        self.by_metric: dict[tuple[str, str, str], Tag] = {}   # client: (node, device, metric) → tag
        self._birth_pending = False
        super().__init__(name, settings, hub)

    # tags --------------------------------------------------------------------------------------------------------
    def make_tag(self, name: str, spec: dict[str, Any]) -> Tag:
        """A configured tag (client): ``metric`` (default the name) of ``device`` (empty = a node metric) of ``node``
        (default the followed ``edge_node``; required when it is ``+``, else the tag would match no node)."""
        typ = str(spec.get("type") or "")
        metric = str(spec.get("metric") or name)
        device = str(spec.get("device") or "")
        follow = str(self.settings.get("edge_node") or "+")
        node = str(spec.get("node") or (follow if follow != "+" else ""))
        tag = Tag(name=name, address=f"{device}/{metric}" if device else metric, type=typ if typ in TYPES else "",
                  writable=bool(spec.get("writable", False)), unit=str(spec.get("unit") or ""),
                  description=str(spec.get("description") or ""), scale=as_float(spec.get("scale")),
                  offset=as_float(spec.get("offset")), extra={**spec, "metric": metric, "device": device, "node": node})
        self.by_metric[(node, device, metric)] = tag
        return tag

    def create_tag(self, name: str, spec: dict[str, Any]) -> Tag:
        """A gateway target (server): a metric of our edge node; a new one after the birth asks for a new NBIRTH."""
        typ = str(spec.get("type") or "")
        tag = Tag(name=name, address=name, type=typ if typ in TYPES else "", writable=bool(spec.get("writable")),
                  unit=str(spec.get("unit") or ""), description=str(spec.get("description") or ""),
                  extra={"target": True, "metric": name, "device": ""})
        self.tags[name] = tag
        self.aliases.setdefault(name, len(self.aliases) + 1)
        if self.client is not None and self.connected and not self._birth_pending:
            self._birth_pending = True
            asyncio.get_running_loop().call_later(0.2, lambda: asyncio.ensure_future(self._birth()))
        return tag

    def initial_default(self, tag: Tag) -> bool:
        """Initial values only to our own metrics (server), never to a remote edge node."""
        return self.role == "server"

    def describe(self) -> str:
        """Broker and Sparkplug address for the TUI."""
        return f"{self.settings.get('host') or '127.0.0.1'} {NAMESPACE}/{self.group}/{self.node}"

    # session -----------------------------------------------------------------------------------------------------
    async def session(self) -> None:
        """Connect (server: with NDEATH as the will), announce or subscribe, then handle messages until lost."""
        import aiomqtt

        s = self.settings
        kw: dict[str, Any] = {}
        if s.get("tls"):
            kw["tls_params"] = aiomqtt.TLSParameters()
        death = b""
        if self.role == "server":
            self.bd_seq = (self.bd_seq + 1) % 256
            death = encode_payload([encode_metric("bdSeq", self.bd_seq, INT64)], None)
            # The broker sends the will only when we vanish; a clean disconnect discards it, so the finally below
            # publishes the same NDEATH itself (Sparkplug: an edge node announces its death when it goes away).
            kw["will"] = aiomqtt.Will(topic(self.group, "NDEATH", self.node), death, qos=1, retain=False)
        async with aiomqtt.Client(
            str(s.get("host") or "127.0.0.1"), port=int(s.get("port") or 1883),
            username=str(s["username"]) if s.get("username") else None,
            password=str(s["password"]) if s.get("password") else None,
            identifier=str(s["client_id"]) if s.get("client_id") else None,
            keepalive=30, **kw,
        ) as client:
            self.client = client
            if self.role == "server":
                await client.subscribe(topic(self.group, "NCMD", self.node), qos=self.qos)
                await client.subscribe(topic(self.group, "DCMD", self.node, "+"), qos=self.qos)
                self.set_state(True)
                await self._birth()
                self.set_ready()
            else:
                await client.subscribe(f"{NAMESPACE}/{self.group}/+/{self.node}", qos=self.qos)
                await client.subscribe(f"{NAMESPACE}/{self.group}/+/{self.node}/+", qos=self.qos)
                self.set_state(True)
                if s.get("rebirth_request", True) and self.node != "+":
                    await self._command(self.node, "", REBIRTH, True, BOOLEAN)
            ready = asyncio.get_running_loop().call_later(BIRTH_WAIT_S, self.set_ready) \
                if self.role != "server" else None
            try:
                async for msg in client.messages:
                    self._message(str(msg.topic), bytes(msg.payload))
                    if self._stop:
                        break
            finally:
                if ready is not None:
                    ready.cancel()
                if death:
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(client.publish(topic(self.group, "NDEATH", self.node), death, qos=1),
                                               2)

    async def close(self) -> None:
        """Forget the client (the ``async with`` has disconnected; the broker then sends our NDEATH)."""
        self.client = None

    # server --------------------------------------------------------------------------------------------------------
    def _next_seq(self) -> int:
        """The next message sequence number (0–255)."""
        seq, self.seq = self.seq, (self.seq + 1) % 256
        return seq

    async def _birth(self) -> None:
        """NBIRTH: every metric with name, alias, type and current value, plus bdSeq and the rebirth control."""
        self._birth_pending = False
        if self.client is None:
            return
        self.seq = 0
        metrics = [encode_metric("bdSeq", self.bd_seq, INT64), encode_metric(REBIRTH, False, BOOLEAN)]
        for name, tag in self.tags.items():
            rec = self.hub.values.get(f"{self.name}/{name}")
            value = rec["v"] if rec else None
            metrics.append(encode_metric(name, value, self._type(tag, value), alias=self.aliases.setdefault(
                name, len(self.aliases) + 1)))
        await self.client.publish(topic(self.group, "NBIRTH", self.node), encode_payload(metrics, self._next_seq()),
                                  qos=0, retain=False)

    def normalise(self, tag: Tag, value: Any) -> Any:
        """Client: a metric the edge node declared as Float (float32 in its birth) comes back rounded as
        :func:`decode_metric` rounds it. Our own edge node sends Double (:meth:`_type`), so its values are exact."""
        if self.role != "client":
            return value
        node = str(tag.extra.get("node") or (self.node if self.node != "+" else ""))
        metric, device = str(tag.extra.get("metric") or tag.name), str(tag.extra.get("device") or "")
        if self.types.get((node, device, metric)) == FLOAT:
            return self.normalise_raw(tag, value)
        return value

    def _type(self, tag: Tag, value: Any) -> int:
        """The Sparkplug type of a metric: from the tag's type, else from the value (Double when unknown)."""
        return {"bool": BOOLEAN, "int": INT64, "float": DOUBLE, "str": STRING}.get(
            tag.type, metric_type(value) if value is not None else DOUBLE)

    # client --------------------------------------------------------------------------------------------------------
    def _message(self, topic_: str, payload: bytes) -> None:
        """One Sparkplug message (anything else on the subscribed topics is ignored)."""
        parts = topic_.split("/")
        if len(parts) < 4 or parts[0] != NAMESPACE:
            return
        kind, node, device = parts[2], parts[3], parts[4] if len(parts) > 4 else ""
        try:
            data = decode_payload(payload)
        except ValueError:
            return
        if self.role == "server":
            if kind in ("NCMD", "DCMD"):
                self._command_in(data)
            return
        if kind in ("NBIRTH", "DBIRTH"):
            self._births(node, device, data["metrics"])
        elif kind in ("NDATA", "DDATA"):
            self._values(node, device, data["metrics"])
        elif kind in ("NDEATH", "DDEATH"):
            for (tnode, dev, _metric), tag in list(self.by_metric.items()):
                # Only the node (NDEATH: all its devices) or the one device that died.
                if tnode == node and (kind == "NDEATH" or dev == device):
                    rec = self.hub.values.get(f"{self.name}/{tag.name}")
                    self.hub.update(self.name, tag.name, rec["v"] if rec else None, "bad")

    def _command_in(self, data: dict[str, Any]) -> None:
        """NCMD/DCMD to our edge node: rebirth, or a new value for a writable metric (passed back to its source)."""
        names = {alias: name for name, alias in self.aliases.items()}
        for m in data["metrics"]:
            name = m["name"] or names.get(m["alias"])
            if name == REBIRTH and m["value"]:
                asyncio.ensure_future(self._birth())
                continue
            tag = self.tags.get(name or "")
            if tag is not None and tag.writable:
                self.publish(tag, m["value"])
                asyncio.ensure_future(self._data(tag, m["value"]))

    def _births(self, node: str, device: str, metrics: list[dict[str, Any]]) -> None:
        """A birth: remember aliases and types, make a tag for every new metric, and publish the values."""
        for m in metrics:
            if not m["name"] or m["name"] in ("bdSeq",) or m["name"].startswith(("Node Control/", "Device Control/")):
                continue
            if m["alias"] is not None:
                self.alias_names[(node, device, m["alias"])] = m["name"]
            self.types[(node, device, m["name"])] = m["datatype"]
            tag = self.by_metric.get((node, device, m["name"]))
            if tag is None:
                # Following every node: the node is part of the name, so equal metric names of two nodes stay apart.
                parts = ([node] if self.node == "+" else []) + ([device] if device else []) + [m["name"]]
                tname = "/".join(parts)
                tag = Tag(name=tname, address=tname, writable=False,
                          extra={"auto": True, "metric": m["name"], "device": device, "node": node})
                self.tags[tname] = tag
                self.by_metric[(node, device, m["name"])] = tag
            self.publish(tag, m["value"])

    def _values(self, node: str, device: str, metrics: list[dict[str, Any]]) -> None:
        """A DATA message: metrics by name or by the alias from the birth."""
        for m in metrics:
            name = m["name"] or self.alias_names.get((node, device, m["alias"]))
            tag = self.by_metric.get((node, device, name or ""))
            if tag is not None:
                self.publish(tag, m["value"])

    async def _command(self, node: str, device: str, metric: str, value: Any, datatype: int) -> None:
        """Publish NCMD (node metric) or DCMD (device metric) with one metric."""
        if self.client is None:
            raise ConnectionError(_("{connection} is not connected").format(connection=self.name))
        kind = "DCMD" if device else "NCMD"
        await self.client.publish(topic(self.group, kind, node, device),
                                  encode_payload([encode_metric(metric, value, datatype)], None), qos=self.qos)

    async def _data(self, tag: Tag, value: Any) -> None:
        """Server: NDATA with one metric, by alias."""
        if self.client is None:
            raise ConnectionError(_("{connection} is not connected").format(connection=self.name))
        alias = self.aliases.setdefault(tag.name, len(self.aliases) + 1)
        await self.client.publish(topic(self.group, "NDATA", self.node),
                                  encode_payload([encode_metric(None, value, self._type(tag, value), alias=alias)],
                                                 self._next_seq()), qos=0, retain=False)

    async def write(self, tag: Tag, value: Any, user: str = "") -> None:
        """Server: the metric's new value as NDATA. Client: NCMD/DCMD to the edge node with the type from its birth."""
        raw = tag.to_raw(value)
        if self.role == "server":
            await self._data(tag, raw)
            return
        node = str(tag.extra.get("node") or (self.node if self.node != "+" else ""))
        if not node:
            raise ValueError(_("{tag}: no birth from its edge node yet").format(tag=tag.name))
        metric, device = str(tag.extra.get("metric") or tag.name), str(tag.extra.get("device") or "")
        datatype = self.types.get((node, device, metric)) or metric_type(raw)
        await self._command(node, device, metric, raw, datatype)
