# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""The hub: runs the protocol drivers inside the SCADA server.

The server owns one hub. It

* starts a driver per connection in ``protocols.yaml`` (and one for KNX when
  KNX is set up), importing a protocol module only when it is used;
* keeps the latest value of every tag (``values["conn/tag"]``) and hands new
  values to the gateway engine;
* logs the values of the tags chosen with ``log`` to the database (trends) and
  hands them to the server's alarm rules for tags (``match_point``);
* tells the TUI clients about values (``points`` messages, at most twice a
  second) and connection states (``protocols`` messages);
* answers the client commands ``protocols``, ``points``, ``logged_points``,
  ``point_write``, ``protocols_save`` and ``protocols_reload`` (see :meth:`command`).

A connection that cannot start (module not installed, bad setting) is
reported in the event log and shown in the TUI; the others run anyway.
"""

from __future__ import annotations

import asyncio
import contextlib
import fnmatch
import logging
import time
from typing import TYPE_CHECKING, Any

from ..i18n import _
from . import PROTOCOLS, driver_class, installed
from .base import Driver, coerce, same_value
from .config import load_data, protocols_path, save, settings_of, validate
from .gateway import GatewayEngine

if TYPE_CHECKING:
    from ..server import ScadaServer

log = logging.getLogger("knxscada.protocols")

#: Values are sent to the clients in batches, at most this often (seconds).
FLUSH_S = 0.5
#: Seconds between two connection status messages to the clients while values arrive (read/error counters).
STATS_S = 10.0



#: BACnet priorities a user may not write with (1–2 manual/automatic life safety, 5 critical equipment control).
RESERVED_PRIORITIES = (1, 2, 5)


def check_priority(value: Any) -> int:
    """A BACnet write priority as int, 1 (highest, life safety) … 16 (lowest, automatic control).

    Raises ValueError for anything else. Priority 6 (minimum on/off) is allowed here; the device itself refuses
    it if it reserves the slot.
    """
    try:
        p = int(value)
    except (TypeError, ValueError):
        raise ValueError(_("Priority must be a number from 1 to 16, not {value}").format(value=repr(value))) from None
    if not 1 <= p <= 16:
        raise ValueError(_("Priority must be a number from 1 to 16, not {value}").format(value=repr(value)))
    return p

class Hub:
    """Drivers, tag values and gateways of one server."""

    def __init__(self, server: ScadaServer) -> None:
        """An empty hub for *server*; nothing runs until :meth:`start` (called from the server's start-up)."""
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
        self._logged: dict[str, tuple[float, Any]] = {}   # "conn/tag" → (ts, value) last stored in the database
        self._log_wait: dict[str, tuple[str, str, Any]] = {}   # changes held back by log_min_s: key → (conn, tag, value)
        self.engine = GatewayEngine(self, [])
        self.started = False
        #: Counters per connection since the server started, for the Monitor: values read (every value a driver
        #: reports, changed or not), changes, values with bad quality, and lost connections.
        self.stats: dict[str, dict[str, int]] = {}
        self._stats_at = 0.0                          # time.time() of the last status sent because of the counters

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
        """Read ``protocols.yaml``, start every driver and build the gateway links.

        Called once by the server after its KNX setup. The KNX driver only wraps the server's own KNX
        connection (it never opens one), so it is added whenever KNX is configured. Connections that cannot
        be created end up in :attr:`failed` and the rest start anyway.
        """
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
        """Register *drv* and run it (:meth:`Driver.run` reconnects on its own) as a named task."""
        self.drivers[drv.name] = drv
        self.tasks[drv.name] = asyncio.create_task(drv.run(), name=f"protocol:{drv.name}")

    def _build_engine(self) -> None:
        """Replace the gateway engine with one built from the current ``gateways:`` list.

        The old engine is stopped first so its pending rate-limited sends are dropped rather than
        delivered through links that may no longer exist.
        """
        self.engine.stop()
        self.engine = GatewayEngine(self, self.data.get("gateways") or [])
        self.engine.build()

    async def _stop_driver(self, name: str) -> None:
        """Stop the driver of connection *name* and forget its values.

        The stop gets 5 s; then the task is cancelled anyway, so a device that hangs in a close cannot
        block a reload or the shutdown. Its values are dropped so a restarted driver begins with no
        values (nothing old is passed on as if it were new).
        """
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
        """Stop the gateways, the client flush loop and every driver (server shutdown)."""
        self.engine.stop()
        if self._flusher is not None:
            self._flusher.cancel()
        for name in list(self.drivers):
            await self._stop_driver(name)

    async def reload(self, data: dict[str, Any] | None = None) -> bool:
        """Apply new settings: restart only the connections that changed, rebuild every gateway.

        *data* None reads ``protocols.yaml`` again (F11 "reload"). The new settings are read and validated first and
        only then replace the running ones: a YAML error or an invalid setting is reported and **everything keeps
        running as it is** – before, a broken file was read as empty, and the reload stopped every connection.
        Returns False when the settings were refused.
        """
        old = self.data.get("connections") or {}
        if data is None:
            try:
                data = load_data(self.path)
                errors = validate(data, self.knx)
            except Exception as exc:  # noqa: BLE001 - YAML error, unreadable file
                errors = [str(exc)]
            if errors:
                self.event("error", _("{path} has errors – the running settings are kept: {error}").format(
                    path=self.path.name, error="; ".join(errors)))
                return False
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
        return True

    # -- values -----------------------------------------------------------------------------------
    def update(self, conn: str, tag: str, value: Any, quality: str = "good", status: bool = False) -> None:
        """A driver got a value for one of its tags. *status*: the value is a status by its kind (a Sparkplug birth),
        not a change, also when the connection is ready (see ``Driver.publish``)."""
        key = f"{conn}/{tag}"
        st = self._stats(conn)
        st["reads"] += 1
        if quality != "good":
            st["errors"] += 1
        # Until its connection has read everything once, a value is a status, not a change.
        drv = self.drivers.get(conn)
        initial = status or drv is None or not drv.ready or (drv.first_value_is_status and key not in self._seen)
        self._seen.add(key)
        old = self.values.get(key)
        # Exact: a value read from a device is a change whenever it differs at all (a float32 value arrives already
        # rounded by its driver, so it does not flicker). Any tolerance here is size-dependent and swallows real
        # steps of a large counter (1e-6 hid 1 000 000 → 1 000 001 Wh). Echoes of our own writes are judged by the
        # gateways (with Driver.normalise), and how often a value is stored by log_deadband / log_min_s.
        if old is not None and old["q"] == quality and same_value(old["v"], value, rel=0.0):
            return
        st["changes"] += 1
        rec = {"v": value, "ts": time.time(), "q": quality}
        self.values[key] = rec
        self._dirty[key] = rec
        if quality == "good":
            self.engine.on_value(conn, tag, value, initial)
            if drv is not None:
                self._log(drv, tag, value, rec["ts"])
        else:
            # A bad status (e.g. a PLC loading a new application): the gateways forget the value, so the next good
            # one is only remembered, and changes from this connection towards KNX pause for a while.
            self.engine.on_bad(conn, tag)
        if hasattr(self.server, "point_value"):
            self.server.point_value(key, value, quality)

    def mark_quality(self, conn: str, tag: str, quality: str) -> None:
        """Give the value of *conn/tag* (a gateway target on our own server) the *quality* ``"bad"`` or ``"old"``
        because its source turned bad or was lost; the value itself is kept. Called by the gateway engine.

        The record changes like any other value (clients, alarms, the database see the quality), and the gateways
        reading from this tag are told (``on_bad``), so the quality travels on through a chain of gateways. The next
        good value written to the tag (:meth:`update`) makes it good again. Nothing is done when the tag has no
        value yet or already has this quality – which also ends the walk round a loop of ``direction: both`` lines.
        """
        key = f"{conn}/{tag}"
        old = self.values.get(key)
        if old is None or old["q"] == quality:
            return
        rec = {**old, "q": quality}   # the time stays: when the value was last good
        self.values[key] = rec
        self._dirty[key] = rec
        self.engine.on_bad(conn, tag, quality)
        if hasattr(self.server, "point_value"):
            self.server.point_value(key, rec["v"], quality)

    # -- logging ----------------------------------------------------------------------------------
    def logs(self, drv: Driver, tag: str) -> bool:
        """Whether the values of *tag* are stored: the tag's own ``log``, else the connection's patterns."""
        t = drv.tags.get(tag)
        own = t.extra.get("log") if t is not None else None
        if own is not None and own != "":
            return bool(own)
        patterns = drv.settings.get("log")
        if patterns is True:
            return True
        if not patterns:
            return False
        if isinstance(patterns, str):
            patterns = [p.strip() for p in patterns.split(",")]
        return any(fnmatch.fnmatchcase(tag, str(p)) for p in patterns)

    def _setting(self, drv: Driver, tag: str, key: str, default: float) -> float:
        """A numeric logging setting (*key*, e.g. ``log_min_s``) of *tag*: the tag's own, else the connection's.

        A decimal comma is accepted. An unreadable value gives *default* rather than falling back to the
        connection's value, so a typo is not silently replaced by a different number.
        """
        t = drv.tags.get(tag)
        for value in ((t.extra.get(key) if t is not None else None), drv.settings.get(key)):
            if value not in (None, ""):
                try:
                    return float(str(value).replace(",", "."))
                except ValueError:
                    break
        return default

    def _log(self, drv: Driver, tag: str, value: Any, ts: float) -> None:
        """Store a new value of a logged tag (``log_deadband``, ``log_min_s``; on/off changes always)."""
        store = getattr(self.server, "store", None)
        if store is None or value is None or not self.logs(drv, tag):
            return
        key = f"{drv.name}/{tag}"
        last = self._logged.get(key)
        if last is not None and not isinstance(value, bool):
            if ts - last[0] < self._setting(drv, tag, "log_min_s", 10):
                self._log_wait[key] = (drv.name, tag, value)   # stored when the interval is over (flush_log)
                return
            if isinstance(value, (int, float)) and isinstance(last[1], (int, float)) and \
                    abs(value - last[1]) < self._setting(drv, tag, "log_deadband", 0):
                return
        self._log_wait.pop(key, None)
        store.add_point_value(key, value, ts)
        self._logged[key] = (ts, value)

    def flush_log(self, now: float | None = None) -> None:
        """Store the held-back changes whose ``log_min_s`` is over (the last value of a burst is not lost)."""
        now = time.time() if now is None else now
        for key, (conn, tag, value) in list(self._log_wait.items()):
            drv = self.drivers.get(conn)
            if drv is None:
                self._log_wait.pop(key, None)
            elif now - self._logged.get(key, (0.0, None))[0] >= self._setting(drv, tag, "log_min_s", 10):
                self._log(drv, tag, value, now)

    def _stats(self, conn: str) -> dict[str, int]:
        """The counters of *conn* (made on first use)."""
        st = self.stats.get(conn)
        if st is None:
            st = self.stats[conn] = {"reads": 0, "changes": 0, "errors": 0, "drops": 0}
        return st

    def connection_state(self, conn: str, connected: bool) -> None:
        """A driver connected or lost its connection."""
        if not connected:
            self._stats(conn)["drops"] += 1
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

    async def write(self, conn: str, tag: str, value: Any, user: str = "", priority: int | None = None,
                    release: bool = False) -> None:
        """Write a value to a tag (from a gateway or a user). Raises when it cannot be done.

        *priority* (1–16) and *release* are for drivers with write priorities (BACnet, ``Driver.priorities``):
        the value is written into that slot of the priority array instead of the tag's or connection's default
        ``priority``; *release* writes "nothing" (Null) into the slot, so the next lower priority – often the
        automatic control at 16 – takes over again. Both are refused for other drivers.
        """
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
        if priority is not None or release:
            if not drv.priorities:
                raise ValueError(_("{connection} has no write priorities (only BACnet has)").format(connection=conn))
            if priority is not None:
                priority = check_priority(priority)
            # A release has no value to convert; the driver writes Null, and the tag keeps showing what the
            # device reports afterwards (the next poll or COV) – it is not set to None here.
            await drv.write(t, None if release else coerce(value, t.type), user, priority=priority, release=release)
            if not drv.echoes and not release:
                self.update(conn, t.name, coerce(value, t.type))
            return
        value = coerce(value, t.type)
        await drv.write(t, value, user)
        if not drv.echoes:
            self.update(conn, t.name, value)

    def event(self, level: str, text: str, user: str | None = None) -> None:
        """Write a line to the server's event log under the source "gateway" (used by drivers and the engine).

        *level* is "info", "warning" or "error"; *user* is who caused it, if anyone.
        """
        self.server.event(level, "gateway", text, user)

    # -- clients ----------------------------------------------------------------------------------
    async def _flush_loop(self) -> None:
        """Every :data:`FLUSH_S`: store held-back logged values, then send the changed values to the clients.

        Batching keeps a busy Modbus poll from flooding the TUIs. KNX values are left out because the
        server sends KNX telegrams to the clients by itself. Without clients the batch is just dropped.
        """
        while True:
            await asyncio.sleep(FLUSH_S)
            if self._log_wait:
                self.flush_log()
            # The counters change with every read, also when no value changes; the clients get them with the status
            # every STATS_S seconds (a status message is a few hundred bytes per connection).
            if self.server.clients and time.time() - self._stats_at >= STATS_S:
                self._stats_at = time.time()
                self._broadcast_status()
            if not self._dirty:
                continue
            dirty, self._dirty = self._dirty, {}
            if self.server.clients:
                self.server._broadcast({"type": "points", "values": {
                    k: [r["v"], r["ts"], r["q"]] for k, r in dirty.items() if not k.startswith("knx/")}})

    def _broadcast_status(self) -> None:
        """Send the connection list (:meth:`status`) to every client after a connect, disconnect or reload."""
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
                **self._stats(name),
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

    def logged_points(self) -> list[dict[str, Any]]:
        """Every logged tag (``log``) of every connection, for the trend view: key ``conn/tag`` and value."""
        out = []
        for conn, drv in self.drivers.items():
            if conn == "knx":
                continue
            for t in drv.tags.values():
                if self.logs(drv, t.name):
                    rec = self.values.get(f"{conn}/{t.name}") or {}
                    out.append({**t.info(), "key": f"{conn}/{t.name}", "conn": conn, "value": rec.get("v"),
                                "q": rec.get("q", "")})
        return out

    def points(self, conn: str, limit: int = 5000) -> list[dict[str, Any]]:
        """The tags of one connection with their values.

        For drivers with write priorities each tag also gets ``priority``: the slot a plain write uses (the
        tag's own ``priority``, else the connection's; 16 when neither is set). The TUI's write dialog uses the
        key both as the default and as the sign that a priority can be chosen.
        """
        drv = self.drivers.get(conn)
        if drv is None:
            return []
        out = []
        for t in list(drv.tags.values())[:limit]:
            rec = self.values.get(f"{conn}/{t.name}") or {}
            if conn == "knx":
                st = self.server.state.get(t.name) or {}
                rec = {"v": st.get("value"), "ts": st.get("ts"), "q": ("old" if st.get("old") else "good") if st else ""}
            info = {**t.info(), "value": rec.get("v"), "ts": rec.get("ts"), "q": rec.get("q", "")}
            if drv.priorities:
                info["priority"] = self._default_priority(conn, t.name)
            out.append(info)
        return out

    def _default_priority(self, conn: str, tag: str) -> int:
        """The priority a write to *conn/tag* uses when none is chosen: the tag's, the connection's, else 16."""
        drv = self.drivers[conn]
        t = drv.resolve(tag)
        value = (t.extra.get("priority") if t is not None else None) or drv.settings.get("priority")
        try:
            return check_priority(value)
        except (TypeError, ValueError):
            return 16

    async def command(self, req: dict[str, Any], user: str) -> Any:
        """Client commands for protocols and gateways."""
        cmd = req.get("cmd")
        if cmd == "protocols":
            return self.snapshot()
        if cmd == "logged_points":
            return self.logged_points()
        if cmd == "points":
            return self.points(str(req.get("conn") or ""), int(req.get("limit") or 5000))
        if cmd == "point_write":
            conn, tag = str(req["conn"]), str(req["tag"])
            priority = req.get("priority")
            release = bool(req.get("release"))
            # 1, 2 and 5 are the life-safety and critical-equipment slots (fire, smoke control): an operator in
            # this program must not be able to take them, even by sending the command by hand.
            if priority is not None and check_priority(priority) in RESERVED_PRIORITIES:
                raise ValueError(_("Priority {priority} is reserved for life safety and critical equipment")
                                 .format(priority=priority))
            await self.write(conn, tag, req.get("value"), user, priority=priority, release=release)
            if conn != "knx":  # the KNX driver logs its own writes
                # The log line keeps the form "conn/tag ← value" (the TUI's monitor reads it back); a priority
                # chosen by the user and a release are added after the value.
                if release:
                    text = _("released (priority {priority})").format(priority=priority if priority is not None
                                                                       else self._default_priority(conn, tag))
                else:
                    text = str(req.get("value"))
                    if priority is not None:
                        text += " " + _("(priority {priority})").format(priority=priority)
                self.event("info", f"{conn}/{tag} ← {text}", user)
            return None
        if cmd == "point_priorities":
            # Read-only: the BACnet priority array of one tag, for the write dialog.
            conn, tag = str(req["conn"]), str(req["tag"])
            drv = self.drivers.get(conn)
            if drv is None or tag not in drv.tags:
                raise ValueError(_("Unknown tag {tag}").format(tag=f"{conn}/{tag}"))
            return await drv.priority_array(drv.tags[tag])
        if cmd == "protocols_save":
            data = req.get("data") or {}
            await asyncio.to_thread(save, self.path, data, self.knx)
            self.event("info", _("Connections and gateways changed ({path})").format(path=self.path.name), user)
            await self.reload(data)
            return self.snapshot()
        if cmd == "protocols_reload":
            if not await self.reload():
                # The reason is in the event log; the TUI shows this as the command's error.
                raise ValueError(_("{path} has errors – the running settings are kept: {error}").format(
                    path=self.path.name, error=_("see the event log")))
            self.event("info", _("Connections and gateways re-read from {path}").format(path=self.path.name), user)
            return self.snapshot()
        raise ValueError(_("Unknown command {cmd}").format(cmd=repr(cmd)))
