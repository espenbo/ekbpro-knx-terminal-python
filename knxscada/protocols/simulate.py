# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""``./scada simulate MODEL``: a Modbus slave that plays a device from a template.

For testing without the equipment: the simulator answers on the template's
registers – its tags with plausible, slowly changing values (voltage around
230 V, energy counters that count up …), keeps what a master writes to the
tags with ``write_address`` (power limits), and answers its ``identify`` registers with
the model, unit id, baud rate and parity – so a master with ``model: …``
identifies it and reads real-looking values. It listens on Modbus TCP (default
``127.0.0.1:5020``) or, with ``--device``, on a serial port with the template's
serial settings.

It reuses the Modbus slave of :mod:`.modbus`; only a small hub stands in for the
server.
"""

from __future__ import annotations

import asyncio
import math
import random
import re
import time
from typing import Any

from ..i18n import _
from . import devices
from .config import settings_of


class _Hub:
    """What a driver needs of the hub, without a server: values are kept, events printed."""

    def __init__(self) -> None:
        """No values yet."""
        self.values: dict[str, dict[str, Any]] = {}

    def update(self, conn: str, tag: str, value: Any, quality: str = "good") -> None:
        """Keep a value the slave received from a master (there are no gateways to pass it to)."""
        self.values[f"{conn}/{tag}"] = {"v": value, "q": quality}

    def connection_state(self, conn: str, connected: bool) -> None:
        """Ignored: there are no clients to tell."""

    def connection_ready(self, conn: str) -> None:
        """Ignored: there is no gateway engine."""

    def event(self, level: str, text: str, user: str | None = None) -> None:
        """Print a driver message (errors, retries) on the simulator's terminal."""
        print(text)


def base_value(name: str, unit: str) -> float:
    """A plausible value for a tag, from its unit and name (0 when nothing fits)."""
    u, n = unit.lower(), name.lower()
    line_to_line = re.search(r"l\d-l\d|l-l", n) is not None
    table = {"v": 400.0 if line_to_line else 230.0, "hz": 50.0, "a": 5.0, "w": 1150.0, "kw": 1.15, "var": 120.0,
             "kvar": 0.12, "va": 1160.0, "kva": 1.16, "wh": 12345600.0, "kwh": 12345.6, "kvarh": 1234.5,
             "kvah": 12400.0, "h": 4321.0}
    if u in table:
        return table[u]
    if not u and ("pf" in n or "power factor" in n):
        return 0.95
    return 0.0


def value_at(name: str, unit: str, start: float, now: float) -> float:
    """The value of a tag *now*: counters count up, other values wander a little around the base value."""
    base = base_value(name, unit)
    u = unit.lower()
    if u in ("wh", "kwh", "kvarh", "kvah", "h"):
        rate = {"h": 1 / 3600}.get(u, base_value("", u.removesuffix("h")) / 3600)   # per second
        if "(-)" in name or "export" in name.lower():
            rate = 0.0
        return base + rate * (now - start)
    if not base:
        return 0.0
    # A slow sine (period ~6 min) with a per-tag phase plus a little noise, so trends look alive.
    wave = math.sin((now - start) / 60 + (hash(name) % 100))
    spread = 0.02 if u in ("v", "hz") or base < 1 else 0.3
    return base * (1 + spread * wave * 0.5 + random.uniform(-spread, spread) * 0.05)


def identify_values(tpl: dict[str, Any], unit: int, baudrate: str, parity: str) -> dict[str, int]:
    """Raw register values that make the template's ``identify`` describe this device."""
    want = {"meter_code": tpl["number"], "baudrate": str(baudrate), "parity": str(parity)}
    out: dict[str, int] = {}
    for key, spec in (tpl.get("identify") or {}).items():
        if key == "unit_id":
            out[key] = unit
        elif key == "serial":
            out[key] = 0x00123456
        elif key in want:
            raw = next((k for k, v in (spec.get("values") or {}).items() if str(v) == want[key]), None)
            if raw is not None:
                out[key] = int(raw)
    return out


def simulator_settings(model: str, host: str, port: int, unit: int, device: str | None) -> dict[str, Any]:
    """The slave connection: the template's serial settings, TCP unless a serial *device* is given."""
    conn: dict[str, Any] = {"protocol": "modbus", "role": "server", "model": model, "unit_id": unit}
    if device:
        conn.update(transport="rtu", device=device)
    else:
        conn.update(transport="tcp", host=host, port=port)
    return settings_of(conn)


def build(model: str, host: str = "127.0.0.1", port: int = 5020, unit: int = 1, device: str | None = None):
    """The slave driver with the template's tags and identify registers (ValueError: unknown model)."""
    from .modbus import Driver, Store, parse_address, to_registers

    tpl = devices.find(model)
    if tpl is None:
        raise ValueError(_("Name a device, e.g.: ./scada simulate \"WAGO 879-3000\""))
    if tpl.get("protocol") != "modbus":
        raise ValueError(_("{model} is a {protocol} device").format(model=tpl["model"], protocol=tpl.get("protocol")))
    settings = simulator_settings(tpl["model"], host, port, unit, device)
    drv = Driver("sim", settings, _Hub())
    raw = identify_values(tpl, unit, settings.get("baudrate", "9600"), settings.get("parity", "N"))
    # Identify registers answer a read of exactly their own registers. Some meters (Carlo Gavazzi 000Bh) use
    # the same address for a measured value read in a block, so they are kept apart from the tag registers.
    answers: dict[tuple[str, int, int], list[int]] = {}
    for key, spec in (tpl.get("identify") or {}).items():
        if key in raw:
            table, index, _bit = parse_address(str(spec["address"]))
            words = to_registers(raw[key], str(spec.get("type") or "uint16"), "big")
            answers[(table, index, len(words))] = words

    class SimStore(Store):
        """The slave's store, answering the template's identify reads before the normal tables."""

        async def async_getValues(self, device_id, func_code, address, count=1):
            """An exact read of an identify register gets the identify answer; anything else the tag tables."""
            table = self.FC.get(func_code)
            words = answers.get((table, address, count))
            if words is not None and table in ("hr", "ir"):
                self._check(device_id)
                return list(words)
            return await super().async_getValues(device_id, func_code, address, count)

    drv.store = SimStore(drv)
    drv.store.index()
    for name in drv.write_points:   # settings a master writes (power limits, watchdog): start at 0, then kept
        drv.store.put(drv.points[name], 0, drv._order(drv.tags[name]))
    return tpl, drv


def refresh(drv, start: float, now: float | None = None) -> None:
    """Put the current measured values into the slave's registers (not the ones a master writes)."""
    now = time.time() if now is None else now
    for name, tag in drv.tags.items():
        if name in drv.write_points:
            continue
        drv.store.put(drv.points[name], tag.to_raw(value_at(name, tag.unit, start, now)), drv._order(tag))


async def run(model: str, host: str, port: int, unit: int, device: str | None, interval: float = 1.0) -> None:
    """Serve until Ctrl+C."""
    tpl, drv = build(model, host, port, unit, device)
    start = time.time()
    refresh(drv, start)
    task = asyncio.create_task(drv.run())
    s = drv.settings
    where = (f"{s['device']} {s.get('baudrate')} 8{s.get('parity')}{s.get('stopbits', 1)}" if device
             else f"{host}:{port}")
    print(_("Simulating {model} on {where}, unit id {unit} – {n} tags. Ctrl+C stops.").format(
        model=tpl["model"], where=where, unit=unit, n=len(drv.tags)))
    print(_("Connect to it in protocols.yaml:"))
    print("  sim1:\n    protocol: modbus")
    if device:
        print(f"    device: {_('<the other serial port>')}")
    else:
        print(f"    transport: tcp\n    host: {host}\n    port: {port}")
    print(f"    model: {tpl['model']}", flush=True)
    try:
        while not task.done():
            await asyncio.sleep(interval)
            refresh(drv, start)
    finally:
        await drv.stop()
        task.cancel()
