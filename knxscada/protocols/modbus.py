# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""Modbus TCP and RTU (RS485): master (``role: client``) and slave (``role: server``).

Addresses
---------
A tag's ``address`` names the table and the number as it goes over the wire
(starting at 0)::

    hr:100      holding register 100        (read/write, function 3/6/16)
    ir:5        input register 5            (read only,  function 4)
    co:3        coil 3                      (read/write, function 1/5/15)
    di:7        discrete input 7            (read only,  function 2)
    hr:100.3    bit 3 of holding register 100
    hr:0x5002   the number in hex, as in many data sheets (= hr:20482)

The classic 5/6-digit form is understood too and converted (it starts at 1):
``40001`` = ``400001`` = ``hr:0``, ``30001`` = ``ir:0``, ``10001`` = ``di:0``, ``00001`` = ``co:0``.

Data types: ``bool``, ``int16``, ``uint16`` (default), ``int32``, ``uint32``,
``float32``, ``int64``, ``uint64``, ``float64``. 32/64-bit values use several
registers; ``word_order: big`` (default) puts the high word first.
``scale``/``offset`` convert the raw number: ``value = raw × scale + offset``.

Master
------
Tags are read every ``poll_ms``. Neighbouring addresses are read in one
request (up to 100 registers or 1000 bits). When a device refuses such a
block, its tags are read one by one from then on.

Slave
-----
Other systems (a SCADA, a PLC) read the tags of a gateway from us, and may
write the ones mapped with ``direction: both``. Addresses without a tag read
as 0; writing them is refused (exception 2, illegal address). The tag name of
a gateway target is its address (``to: hr:100``), or a name defined under
``tags:`` with an ``address``.
"""

from __future__ import annotations

import asyncio
import re
import struct
from dataclasses import dataclass
from typing import Any, ClassVar

from ..i18n import _
from . import MODBUS_TYPES
from .base import Driver as BaseDriver
from .base import Tag, as_float

#: Table aliases → short name.
TABLES = {"co": "co", "coil": "co", "coils": "co", "di": "di", "discrete": "di", "hr": "hr", "holding": "hr",
          "ir": "ir", "input": "ir"}

#: Classic numbering: first digit → table.
CLASSIC = {"0": "co", "1": "di", "3": "ir", "4": "hr"}

ADDRESS_RE = re.compile(r"^\s*([a-z]+)\s*[:\s]?\s*(0x[0-9a-f]+|\d+)(?:\.(\d{1,2}))?\s*$", re.IGNORECASE)

#: struct format and number of registers per data type.
FORMATS = {"int16": ("h", 1), "uint16": ("H", 1), "int32": ("i", 2), "uint32": ("I", 2), "float32": ("f", 2),
           "int64": ("q", 4), "uint64": ("Q", 4), "float64": ("d", 4)}

#: Largest block read in one request, and the gap that is still read across to join two blocks.
MAX_REGS, MAX_BITS = 100, 1000
GAP_REGS, GAP_BITS = 8, 64


def parse_address(text: str) -> tuple[str, int, int | None]:
    """``"hr:100.3"`` → ``("hr", 100, 3)``; ``"40101"`` → ``("hr", 100, None)``. ValueError if unclear."""
    text = str(text).strip()
    m = ADDRESS_RE.match(text)
    if m:
        table = TABLES.get(m.group(1).lower())
        if table is None:
            raise ValueError(_("Modbus address {address}: unknown table – use hr, ir, co or di").format(address=repr(text)))
        number = m.group(2).lower()
        index, bit = int(number, 16) if number.startswith("0x") else int(number), m.group(3)
    else:
        number, _dot, bit = text.partition(".")
        bit = bit or None
        if not number.isdigit() or len(number) not in (5, 6) or number[0] not in CLASSIC:
            raise ValueError(_("Modbus address {address}: write e.g. hr:100, ir:5, co:3, di:7 or 40101").format(
                address=repr(text)))
        table, index = CLASSIC[number[0]], int(number[1:]) - 1
        if index < 0:
            raise ValueError(_("Modbus address {address}: the classic numbering starts at 1").format(address=repr(text)))
    if not 0 <= index <= 65535:
        raise ValueError(_("Modbus address {address}: the number must be 0–65535").format(address=repr(text)))
    if bit is not None:
        if table not in ("hr", "ir") or not 0 <= int(bit) <= 15:
            raise ValueError(_("Modbus address {address}: a bit (0–15) can only be taken from a register").format(
                address=repr(text)))
        return table, index, int(bit)
    return table, index, None


def to_registers(value: Any, dtype: str, word_order: str = "big") -> list[int]:
    """A number → registers (ValueError when it does not fit the data type)."""
    if dtype == "bool":
        return [1 if value else 0]
    fmt, _n = FORMATS[dtype]
    if fmt not in "fd":
        value = round(float(value))
    try:
        data = struct.pack(">" + fmt, value)
    except struct.error as exc:
        raise ValueError(_("{value} does not fit in {type}").format(value=value, type=dtype)) from exc
    words = [int.from_bytes(data[i:i + 2], "big") for i in range(0, len(data), 2)]
    return words[::-1] if word_order == "little" else words


def from_registers(regs: list[int], dtype: str, word_order: str = "big") -> int | float | bool:
    """Registers → a number."""
    if dtype == "bool":
        return bool(regs[0])
    fmt, n = FORMATS[dtype]
    words = list(regs[:n])
    if word_order == "little":
        words.reverse()
    value = struct.unpack(">" + fmt, b"".join(w.to_bytes(2, "big") for w in words))[0]
    if fmt in "fd":
        return round(value, 6) if fmt == "f" else value
    return value


@dataclass
class Point:
    """Where a tag is in the device: table, first address, number of registers/bits, bit, data type, unit id."""

    table: str
    index: int
    count: int
    bit: int | None
    dtype: str
    unit: int

    @property
    def end(self) -> int:
        return self.index + self.count


@dataclass
class Block:
    """Neighbouring tags read in one request."""

    table: str
    unit: int
    start: int
    end: int
    tags: list[Tag]
    single: bool = False   # the device refused the block: read the tags one by one


def default_dtype(table: str, bit: int | None, value_type: str = "") -> str:
    if table in ("co", "di") or bit is not None:
        return "bool"
    return {"float": "float32", "int": "int32", "bool": "bool"}.get(value_type, "uint16")


class Driver(BaseDriver):
    protocol = "modbus"

    def __init__(self, name: str, settings: dict[str, Any], hub) -> None:
        self.word_order = str(settings.get("word_order") or "big")
        self.unit = int(settings.get("unit_id") if settings.get("unit_id") not in (None, "") else 1)
        self.points: dict[str, Point] = {}
        super().__init__(name, settings, hub)
        self.echoes = self.role == "client"   # the next poll reads a written value back
        self.reads_first = self.role == "client"   # ready after the first poll
        self.client = None
        self.server = None
        self.blocks: list[Block] = []
        self._lock = asyncio.Lock()
        self.store = Store(self)

    # -- tags -------------------------------------------------------------------------------------
    def make_tag(self, name: str, spec: dict[str, Any]) -> Tag:
        address = str(spec.get("address") or name)
        table, index, bit = parse_address(address)
        typ = str(spec.get("type") or "")
        dtype = typ if typ in MODBUS_TYPES else default_dtype(table, bit, typ)
        if table in ("co", "di") or bit is not None:
            dtype = "bool"
        count = 1 if dtype == "bool" else FORMATS[dtype][1]
        unit = spec.get("unit_id")
        scale, offset = as_float(spec.get("scale")), as_float(spec.get("offset"))
        if dtype == "bool":
            vtype = "bool"
        elif dtype.startswith("float") or scale is not None or offset is not None:
            vtype = "float"
        else:
            vtype = "int"
        writable = bool(spec.get("writable", True))
        if self.role == "client" and table in ("ir", "di"):
            writable = False
        tag = Tag(name=name, address=f"{table}:{index}" + (f".{bit}" if bit is not None else ""), type=vtype,
                  writable=writable, unit=str(spec.get("unit") or ""), description=str(spec.get("description") or ""),
                  scale=scale, offset=offset, extra={**spec, "dtype": dtype})
        self.points[name] = Point(table, index, count, bit, dtype,
                                  int(unit) if unit not in (None, "") else self.unit)
        return tag

    def resolve(self, ref: str) -> Tag | None:
        tag = super().resolve(ref)
        if tag is None:
            try:
                table, index, bit = parse_address(ref)
            except ValueError:
                return None
            canon = f"{table}:{index}" + (f".{bit}" if bit is not None else "")
            tag = next((t for t in self.tags.values() if t.address == canon), None)
        return tag

    def create_tag(self, name: str, spec: dict[str, Any]) -> Tag:
        if self.role != "server":
            return super().create_tag(name, spec)
        try:
            tag = self.make_tag(name, spec)
        except ValueError as exc:
            raise ValueError(_("{connection}: the target {name} must be a Modbus address such as hr:100 – {error}")
                             .format(connection=self.name, name=repr(name), error=exc)) from None
        self.tags[name] = tag
        self.store.index()
        return tag

    # -- connection -------------------------------------------------------------------------------
    def describe(self) -> str:
        where = super().describe()
        return f"{where}  unit {self.unit}" if self.role == "client" else where

    async def session(self) -> None:
        if self.role == "server":
            await self._serve()
        else:
            await self._poll()

    async def close(self) -> None:
        if self.client is not None:
            client, self.client = self.client, None
            client.close()
        if self.server is not None:
            server, self.server = self.server, None
            await server.shutdown()

    def _make_client(self):
        from pymodbus import FramerType
        from pymodbus.client import AsyncModbusSerialClient, AsyncModbusTcpClient

        s = self.settings
        timeout = float(s.get("timeout_s") or 3)
        transport = str(s.get("transport") or "tcp")
        if transport == "rtu":
            return AsyncModbusSerialClient(
                str(s.get("device") or "/dev/ttyUSB0"), framer=FramerType.RTU, baudrate=int(s.get("baudrate") or 9600),
                parity=str(s.get("parity") or "N"), stopbits=int(s.get("stopbits") or 1), bytesize=8,
                timeout=timeout, retries=1, reconnect_delay=0)
        return AsyncModbusTcpClient(
            str(s.get("host") or "127.0.0.1"), port=int(s.get("port") or 502), timeout=timeout, retries=1,
            reconnect_delay=0, framer=FramerType.RTU if transport == "rtu_over_tcp" else FramerType.SOCKET)

    # -- master -----------------------------------------------------------------------------------
    def _build_blocks(self) -> None:
        groups: dict[tuple[int, str], list[tuple[Point, Tag]]] = {}
        for tag in self.tags.values():
            p = self.points[tag.name]
            groups.setdefault((p.unit, p.table), []).append((p, tag))
        self.blocks = []
        for (unit, table), items in groups.items():
            bits = table in ("co", "di")
            limit, gap = (MAX_BITS, GAP_BITS) if bits else (MAX_REGS, GAP_REGS)
            items.sort(key=lambda it: it[0].index)
            cur: Block | None = None
            for p, tag in items:
                if cur is not None and p.index <= cur.end + gap and max(cur.end, p.end) - cur.start <= limit:
                    cur.end = max(cur.end, p.end)
                    cur.tags.append(tag)
                else:
                    cur = Block(table, unit, p.index, p.end, [tag])
                    self.blocks.append(cur)

    async def _poll(self) -> None:
        self.client = self._make_client()
        if not await self.client.connect():
            raise ConnectionError(_("cannot connect to {where}").format(where=super().describe()))
        self._build_blocks()
        self.set_state(True)
        interval = max(int(self.settings.get("poll_ms") or 1000), 50) / 1000
        while not self._stop:
            started = asyncio.get_running_loop().time()
            for block in self.blocks:
                if block.single:
                    for tag in block.tags:
                        p = self.points[tag.name]
                        await self._read_block(Block(p.table, p.unit, p.index, p.end, [tag]), alone=True)
                else:
                    await self._read_block(block)
            self.set_ready()
            await asyncio.sleep(max(0.0, interval - (asyncio.get_running_loop().time() - started)))

    async def _read(self, table: str, unit: int, start: int, count: int):
        """One read request (raises on connection problems; returns None for an exception response)."""
        from pymodbus.exceptions import ModbusIOException

        c = self.client
        if c is None:
            raise ConnectionError(_("not connected"))
        fn = {"hr": c.read_holding_registers, "ir": c.read_input_registers, "co": c.read_coils,
              "di": c.read_discrete_inputs}[table]
        async with self._lock:
            try:
                rr = await fn(start, count=count, device_id=unit)
            except ModbusIOException as exc:
                if not c.connected:
                    raise ConnectionError(str(exc)) from exc
                return None   # no answer from this unit id (RS485): the others may still answer
        if rr.isError():
            return None
        return rr.bits if table in ("co", "di") else rr.registers

    async def _read_block(self, block: Block, alone: bool = False) -> None:
        data = await self._read(block.table, block.unit, block.start, block.end - block.start)
        if data is None:
            if len(block.tags) > 1 and not alone:
                block.single = True   # try the tags one by one from the next poll on
            for tag in block.tags:
                rec = self.hub.values.get(f"{self.name}/{tag.name}")
                self.hub.update(self.name, tag.name, rec["v"] if rec else None, "bad")
            return
        for tag in block.tags:
            p = self.points[tag.name]
            off = p.index - block.start
            if block.table in ("co", "di"):
                raw: Any = bool(data[off])
            elif p.bit is not None:
                raw = bool((data[off] >> p.bit) & 1)
            else:
                raw = from_registers(data[off:off + p.count], p.dtype, self._order(tag))
            self.publish(tag, raw)

    def _order(self, tag: Tag) -> str:
        return str(tag.extra.get("word_order") or self.word_order)

    async def write(self, tag: Tag, value: Any, user: str = "") -> None:
        p = self.points[tag.name]
        raw = tag.to_raw(value)
        if self.role == "server":
            self.store.put(p, raw, self._order(tag))
            return
        c = self.client
        if c is None or not self.connected:
            raise ConnectionError(_("{connection} is not connected").format(connection=self.name))
        async with self._lock:
            if p.table == "co":
                rr = await c.write_coil(p.index, bool(raw), device_id=p.unit)
            elif p.table == "hr" and p.bit is not None:
                cur = await c.read_holding_registers(p.index, count=1, device_id=p.unit)
                if cur.isError():
                    raise RuntimeError(_("{tag}: the device refused the request ({error})").format(tag=tag.name, error=cur))
                reg = cur.registers[0]
                reg = reg | (1 << p.bit) if raw else reg & ~(1 << p.bit)
                rr = await c.write_register(p.index, reg, device_id=p.unit)
            elif p.table == "hr":
                regs = to_registers(raw, p.dtype, self._order(tag))
                if len(regs) == 1:
                    rr = await c.write_register(p.index, regs[0], device_id=p.unit)
                else:
                    rr = await c.write_registers(p.index, regs, device_id=p.unit)
            else:
                raise ValueError(_("{tag} is read-only").format(tag=f"{self.name}/{tag.name}"))
        if rr.isError():
            raise RuntimeError(_("{tag}: the device refused the request ({error})").format(tag=tag.name, error=rr))

    # -- slave ------------------------------------------------------------------------------------
    async def _serve(self) -> None:
        from pymodbus import FramerType
        from pymodbus.server import ModbusSerialServer, ModbusTcpServer
        from pymodbus.simulator import DataType, SimData, SimDevice

        s = self.settings
        # pymodbus needs a device to start with; every request is then answered by our Store.
        dummy = SimDevice(id=0, simdata=[SimData(0, values=0, datatype=DataType.REGISTERS)])
        if str(s.get("transport") or "tcp") == "rtu":
            self.server = ModbusSerialServer(
                dummy, framer=FramerType.RTU, port=str(s.get("device") or "/dev/ttyUSB0"),
                baudrate=int(s.get("baudrate") or 9600), parity=str(s.get("parity") or "N"),
                stopbits=int(s.get("stopbits") or 1), bytesize=8)
        else:
            framer = FramerType.RTU if s.get("transport") == "rtu_over_tcp" else FramerType.SOCKET
            self.server = ModbusTcpServer(dummy, framer=framer,
                                          address=(str(s.get("host") or "0.0.0.0"), int(s.get("port") or 502)))
        self.server.context = self.store
        self.store.index()
        await self.server.serve_forever(background=True)
        self.set_state(True)
        await self.wait_stopped()


class Store:
    """The slave's data: the tags' values in the four tables, answering pymodbus' requests.

    pymodbus calls :meth:`async_getValues`/:meth:`async_setValues` with the
    function code; writes from the master are passed to the tags they touch.
    """

    #: Function code → table.
    FC: ClassVar[dict[int, str]] = {1: "co", 5: "co", 15: "co", 2: "di", 3: "hr", 6: "hr", 16: "hr", 22: "hr", 23: "hr", 4: "ir"}

    def __init__(self, driver: Driver) -> None:
        self.d = driver
        self.data: dict[str, dict[int, int]] = {"co": {}, "di": {}, "hr": {}, "ir": {}}
        self.writable: dict[str, set[int]] = {"co": set(), "hr": set()}
        self.by_addr: dict[tuple[str, int], list[Tag]] = {}

    def index(self) -> None:
        """Rebuild the address → tag index (after tags were added)."""
        self.writable = {"co": set(), "hr": set()}
        self.by_addr = {}
        for tag in self.d.tags.values():
            p = self.d.points[tag.name]
            for a in range(p.index, p.end):
                self.by_addr.setdefault((p.table, a), []).append(tag)
                if tag.writable and p.table in self.writable:
                    self.writable[p.table].add(a)

    def device_ids(self) -> list[int]:
        return [self.d.unit] if self.d.unit else [0]

    def _check(self, device_id: int) -> None:
        if self.d.unit and device_id not in (self.d.unit, 0):
            from pymodbus.exceptions import NoSuchIdException

            raise NoSuchIdException(f"device_id {device_id}")

    def put(self, p: Point, raw: Any, word_order: str) -> None:
        """Our own write (from a gateway): store the value where the master reads it."""
        table = self.data[p.table]
        if p.table in ("co", "di"):
            table[p.index] = 1 if raw else 0
        elif p.bit is not None:
            reg = table.get(p.index, 0)
            table[p.index] = reg | (1 << p.bit) if raw else reg & ~(1 << p.bit)
        else:
            for i, w in enumerate(to_registers(raw, p.dtype, word_order)):
                table[p.index + i] = w

    async def async_getValues(self, device_id: int, func_code: int, address: int, count: int = 1):
        self._check(device_id)
        table = self.FC.get(func_code)
        if table is None:
            from pymodbus.constants import ExcCodes

            return ExcCodes.ILLEGAL_FUNCTION
        data = self.data[table]
        if table in ("co", "di"):
            return [bool(data.get(a, 0)) for a in range(address, address + count)]
        return [data.get(a, 0) for a in range(address, address + count)]

    async def async_setValues(self, device_id: int, func_code: int, address: int, values):
        from pymodbus.constants import ExcCodes

        self._check(device_id)
        table = self.FC.get(func_code)
        if table not in ("co", "hr"):
            return ExcCodes.ILLEGAL_FUNCTION
        values = values if isinstance(values, list) else [values]
        if any(a not in self.writable[table] for a in range(address, address + len(values))):
            return ExcCodes.ILLEGAL_ADDRESS
        data = self.data[table]
        for i, v in enumerate(values):
            data[address + i] = (1 if v else 0) if table == "co" else int(v)
        touched: dict[str, Tag] = {}
        for a in range(address, address + len(values)):
            for tag in self.by_addr.get((table, a), ()):
                touched[tag.name] = tag
        for tag in touched.values():
            p = self.d.points[tag.name]
            if table == "co":
                raw: Any = bool(data.get(p.index, 0))
            elif p.bit is not None:
                raw = bool((data.get(p.index, 0) >> p.bit) & 1)
            else:
                raw = from_registers([data.get(p.index + i, 0) for i in range(p.count)], p.dtype, self.d._order(tag))
            self.d.publish(tag, raw)
        return None
