# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""The hub: runs the protocol drivers inside the SCADA server.

The server owns one hub. It

* starts a driver per connection in ``protocols.yaml`` (and one for KNX when
  KNX is set up), importing a protocol module only when it is used;
* keeps the latest value of every tag (``values["conn/tag"]``) and hands new
  values to the gateway engine;
* tells the TUI clients about values (``points`` messages, at most twice a
  second) and connection states (``protocols`` messages);
* answers the client commands ``protocols``, ``points``, ``point_write``,
  ``protocols_save`` and ``protocols_reload`` (see :meth:`command`).

A connection that cannot start (module not installed, bad setting) is
reported in the event log and shown in the TUI; the others run anyway.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import TYPE_CHECKING, Any

from ..i18n import _
from . import PROTOCOLS, driver_class, installed
from .base import Driver, coerce, same_value
from .config import load_data, protocols_path, save, settings_of
from .gateway import GatewayEngine

if TYPE_CHECKING:
    from ..server import ScadaServer

log = logging.getLogger("knxscada.protocols")

#: Values are sent to the clients in batches, at most this often (seconds).
FLUSH_S = 0.5


class Hub:
    """Drivers, tag values and gateways of one server."""

    def __init__(self, server: ScadaServer) -> None:
        self.server = server
        self.path = protocols_path(server.cfg)
        self.data: dict[str, Any] = {"connections": {}, "gateways": []}
        self.drivers: dict[str, Driver] = {}
        self.failed: dict[str, str] = {}             # connection → why it could not start
        self.tasks: dict[str, asyncio.Task] = {}
        self.values: dict[str, dict[str, Any]] = {}  # "conn/tag" → {"v", "ts", "q"}
        self._seen: set[str] = set()                 # tags with a value since their connection came up
                                                     # (Driver.first_value_is_status)
        self._dirty: dict[str, dict[str, Any]] = {}  # values not yet sent to the clients
        self._flusher: asyncio.Task | None = None
        self.engine = GatewayEngine(self, [])
        self.started = False

    @property
    def knx(self) -> bool:
        """KNX is set up (the connection ``knx`` exists)."""
        return self.server.knx_enabled

    # -- life cycle -------------------------------------------------------------------------------
    def load(self) -> None:
        """Read ``protocols.yaml``. A broken file is reported and treated as empty (the KNX part still runs)."""
        try:
            self.data = load_data(self.path)
        except Exception as exc:  # noqa: BLE001 - YAML error
            self.event("error", _("{path} cannot be read: {error}").format(path=self.path.name, error=exc))
            self.data = {"connections": {}, "gateways": []}

    async def start(self) -> None:
        self.load()
        if self.knx:
            from .knx import Driver as KnxDriver

            self._add(KnxDriver("knx", {"protocol": "knx"}, self))
        for name, conn in (self.data.get("connections") or {}).items():
            if name != "knx" and isinstance(conn, dict):
                self._make(str(name), conn)
        self._build_engine()
        self.started = True
        self._flusher = asyncio.create_task(self._flush_loop())
        if self.drivers or self.failed:
            self.event("info", _("Protocols: {n} connections, {links} gateway links").format(
                n=len(self.drivers), links=len(self.engine.links)))

    def _make(self, name: str, conn: dict[str, Any]) -> None:
        """Create and start the driver of one connection (failures are reported, not raised)."""
        protocol = str(conn.get("protocol") or "")
        try:
            cls = driver_class(protocol)
            drv = cls(name, settings_of(conn), self)
        except Exception as exc:  # noqa: BLE001 - module missing, bad setting …
            self.failed[name] = str(exc)
            self.event("error", _("{connection}: {error}").format(connection=name, error=exc))
            return
        self.failed.pop(name, None)
        self._add(drv)

    def _add(self, drv: Driver) -> None:
        self.drivers[drv.name] = drv
        self.tasks[drv.name] = asyncio.create_task(drv.run(), name=f"protocol:{drv.name}")

    def _build_engine(self) -> None:
        self.engine.stop()
        self.engine = GatewayEngine(self, self.data.get("gateways") or [])
        self.engine.build()

    async def _stop_driver(self, name: str) -> None:
        drv = self.drivers.pop(name, None)
        task = self.tasks.pop(name, None)
        if drv is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(drv.stop(), 5)
        if task is not None:
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
        for key in [k for k in self.values if k.startswith(name + "/")]:
            self.values.pop(key, None)
            self._seen.discard(key)

    async def stop(self) -> None:
        self.engine.stop()
        if self._flusher is not None:
            self._flusher.cancel()
        for name in list(self.drivers):
            await self._stop_driver(name)

    async def reload(self, data: dict[str, Any] | None = None) -> None:
        """Apply new settings: restart only the connections that changed, rebuild every gateway."""
        old = self.data.get("connections") or {}
        if data is None:
            self.load()
        else:
            self.data = data
        new = self.data.get("connections") or {}
        for name in list(self.drivers):
            if name == "knx":
                continue
            if name not in new or settings_of(new[name]) != settings_of(old.get(name, {})):
                await self._stop_driver(name)
        for name in list(self.failed):
            if name not in new:
                del self.failed[name]
        # Servers whose tags came from gateways start afresh, so tags of removed lines disappear.
        for name, drv in list(self.drivers.items()):
            if drv.role == "server":
                await self._stop_driver(name)
        for name, conn in new.items():
            if name != "knx" and name not in self.drivers and isinstance(conn, dict):
                self._make(str(name), conn)
        self._build_engine()
        self._broadcast_status()

    # -- values -----------------------------------------------------------------------------------
    def update(self, conn: str, tag: str, value: Any, quality: str = "good") -> None:
        """A driver got a value for one of its tags."""
        key = f"{conn}/{tag}"
        # Until its connection has read everything once, a value is a status, not a change.
        drv = self.drivers.get(conn)
        initial = drv is None or not drv.ready or (drv.first_value_is_status and key not in self._seen)
        self._seen.add(key)
        old = self.values.get(key)
        if old is not None and old["q"] == quality and same_value(old["v"], value):
            return
        rec = {"v": value, "ts": time.time(), "q": quality}
        self.values[key] = rec
        self._dirty[key] = rec
        if quality == "good":
            self.engine.on_value(conn, tag, value, initial)

    def connection_state(self, conn: str, connected: bool) -> None:
        """A driver connected or lost its connection."""
        if not connected:
            self.engine.disconnected(conn)
            prefix = conn + "/"
            self._seen = {k for k in self._seen if not k.startswith(prefix)}
            for key, rec in self.values.items():
                if key.startswith(prefix) and rec["q"] == "good":
                    rec["q"] = "old"
                    self._dirty[key] = rec
        self._broadcast_status()

    def connection_ready(self, conn: str) -> None:
        """A driver has read all its tags after connecting: gateways may write to it now."""
        self.engine.on_ready(conn)
        self._broadcast_status()

    async def write(self, conn: str, tag: str, value: Any, user: str = "") -> None:
        """Write a value to a tag (from a gateway or a user). Raises when it cannot be done."""
        drv = self.drivers.get(conn)
        if drv is None:
            raise RuntimeError(self.failed.get(conn) or _("There is no connection called {name}").format(name=repr(conn)))
        t = drv.resolve(tag)
        if t is None:
            raise ValueError(_("{connection} has no tag {tag}").format(connection=conn, tag=repr(tag)))
        if not t.writable and drv.role == "client":
            raise ValueError(_("{tag} is read-only").format(tag=f"{conn}/{t.name}"))
        if drv.role == "client" and not drv.connected:
            raise RuntimeError(_("{connection} is not connected").format(connection=conn))
        value = coerce(value, t.type)
        await drv.write(t, value, user)
        if not drv.echoes:
            self.update(conn, t.name, value)

    def event(self, level: str, text: str, user: str | None = None) -> None:
        self.server.event(level, "gateway", text, user)

    # -- clients ----------------------------------------------------------------------------------
    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(FLUSH_S)
            if not self._dirty:
                continue
            dirty, self._dirty = self._dirty, {}
            if self.server.clients:
                self.server._broadcast({"type": "points", "values": {
                    k: [r["v"], r["ts"], r["q"]] for k, r in dirty.items() if not k.startswith("knx/")}})

    def _broadcast_status(self) -> None:
        self.server._broadcast({"type": "protocols", "status": self.status()})

    def status(self) -> list[dict[str, Any]]:
        """One line per connection for the TUI."""
        out = []
        conns = self.data.get("connections") or {}
        names = (["knx"] if self.knx else []) + [n for n in conns if n != "knx"]
        for name in names:
            drv = self.drivers.get(name)
            conn = conns.get(name) or {"protocol": "knx"}
            protocol = str(conn.get("protocol") or "")
            out.append({
                "name": name,
                "protocol": protocol,
                "title": PROTOCOLS[protocol].title if protocol in PROTOCOLS else protocol,
                "role": drv.role if drv else str(conn.get("role") or "client"),
                "connected": bool(drv and drv.connected),
                "ready": bool(drv and drv.ready),
                "error": (drv.error if drv else self.failed.get(name, "")) or "",
                "where": drv.describe() if drv else "",
                "tags": len(drv.tags) if drv else 0,
                "running": drv is not None,
            })
        return out

    def snapshot(self) -> dict[str, Any]:
        """Everything the TUI's gateway view needs."""
        return {
            "path": str(self.path),
            "knx": self.knx,
            "config": self.data,
            "status": self.status(),
            "links": [link.info() for link in self.engine.links],
            "problems": self.engine.problems,
            "modules": [{"name": p.name, "title": p.title, "installed": p.module is not None and installed(p.name),
                         "planned": p.planned, "roles": list(p.roles)} for p in PROTOCOLS.values()],
        }

    def points(self, conn: str, limit: int = 5000) -> list[dict[str, Any]]:
        """The tags of one connection with their values."""
        drv = self.drivers.get(conn)
        if drv is None:
            return []
        out = []
        for t in list(drv.tags.values())[:limit]:
            rec = self.values.get(f"{conn}/{t.name}") or {}
            if conn == "knx":
                st = self.server.state.get(t.name) or {}
                rec = {"v": st.get("value"), "ts": st.get("ts"), "q": ("old" if st.get("old") else "good") if st else ""}
            out.append({**t.info(), "value": rec.get("v"), "ts": rec.get("ts"), "q": rec.get("q", "")})
        return out

    async def command(self, req: dict[str, Any], user: str) -> Any:
        """Client commands for protocols and gateways."""
        cmd = req.get("cmd")
        if cmd == "protocols":
            return self.snapshot()
        if cmd == "points":
            return self.points(str(req.get("conn") or ""), int(req.get("limit") or 5000))
        if cmd == "point_write":
            conn, tag = str(req["conn"]), str(req["tag"])
            await self.write(conn, tag, req.get("value"), user)
            if conn != "knx":  # the KNX driver logs its own writes
                self.event("info", f"{conn}/{tag} ← {req.get('value')}", user)
            return None
        if cmd == "protocols_save":
            data = req.get("data") or {}
            await asyncio.to_thread(save, self.path, data, self.knx)
            self.event("info", _("Connections and gateways changed ({path})").format(path=self.path.name), user)
            await self.reload(data)
            return self.snapshot()
        if cmd == "protocols_reload":
            await self.reload()
            self.event("info", _("Connections and gateways re-read from {path}").format(path=self.path.name), user)
            return self.snapshot()
        raise ValueError(_("Unknown command {cmd}").format(cmd=repr(cmd)))
