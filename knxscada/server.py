# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""The SCADA server: KNX connection, logging, alarms and the client API.

One server process owns the KNX connection. Every TUI session (local or over
SSH) is just a client of this server, connected through a Unix socket. That
design gives three important properties:

* only **one KNX tunnel per IP interface** is used no matter how many people are logged in
  (IP interfaces typically offer only 1–5 tunnels);
* **logging and alarms run continuously**, also when nobody is logged in;
* clients can come and go (or crash) without affecting the bus.

Data flow::

    KNX bus ──xknx──▶ _on_telegram ──▶ ingest ──▶ SQLite (store)
                                          ├──▶ state (last value per GA)
                                          ├──▶ alarm evaluation
                                          └──▶ broadcast to all clients
    client "write" ──▶ _command ──▶ _send ──▶ xknx ──▶ bus (and back through ingest)

With several KNX IP interfaces (``knx.interfaces``) there is one xknx instance
per interface; :mod:`knxscada.knx_interfaces` chooses the interface for each
outgoing telegram and drops copies of a telegram seen through more than one.

In ``--demo`` mode a :class:`Simulator` produces telegrams instead of xknx,
so the whole system can be tried without a KNX installation.

Protocol: newline-delimited JSON, documented in ``docs/08-architecture.md``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import pwd
import random
import re
import signal
import socket
import struct
import time
from collections import deque
from functools import partial
from typing import Any

from xknx.dpt import DPTArray, DPTBinary
from xknx.telegram import GroupAddress, Telegram, TelegramDirection
from xknx.telegram.apci import GroupValueRead, GroupValueResponse, GroupValueWrite

from . import dpt as dptlib
from .alarms import build_rules, normalize_severity
from .config import Config
from .i18n import N_, _
from .knx_interfaces import (
    DEDUP_MS,
    Deduplicator,
    Router,
    connection_type,
    describe,
    interface_configs,
)
from .project import SETPOINT_RE, ga_key, load_project, split_key
from .store import Store

log = logging.getLogger("knxscada.server")

#: A client whose unsent output exceeds this many bytes is considered stuck and dropped,
#: so one frozen SSH session can never make the server run out of memory.
MAX_CLIENT_BUFFER = 4 * 1024 * 1024

#: xknx connection state name → status code sent to clients (translated by the TUI).
CONNECTION_STATES = {"CONNECTED": N_("connected"), "CONNECTING": N_("connecting"), "DISCONNECTED": N_("disconnected")}

#: Upper bounds for client queries, so one request cannot block the server or exhaust its memory.
MAX_LIMIT = 5000
MAX_MINUTES = 24 * 60
MAX_HOURS = 24 * 400

#: What may appear in a user name in the audit log (no markup, no control characters).
USER_RE = re.compile(r"[^\w@.:\-]+")

#: Telegrams older than the retention time are deleted in batches of this size, with a
#: pause for the event loop in between, so a large purge never blocks the server.
PURGE_BATCH = 5000


def read_mode(value: Any) -> str:
    """``server.read_on_start`` → ``"off"`` (default), ``"status"`` or ``"all"``.

    ``true`` means ``"status"``: reading control addresses is never done unless
    ``"all"`` is given explicitly (see :func:`read_targets` for why).
    """
    if value is None or value is False or str(value).strip().lower() in ("false", "off", "no", "0", ""):
        return "off"
    if str(value).strip().lower() == "all":
        return "all"
    return "status"


def read_targets(model: dict, gas: dict[str, dict], alarm_addresses, mode: str) -> list[str]:
    """Addresses to read at start-up, in display order (UI first, then alarms).

    Only addresses with the read flag (R) on some device are asked; others
    would never answer.

    In ``"status"`` mode no address that is used to *control* something is
    read. A GroupValueRead on a control address is answered by every device
    with the R flag on it – also by a push button or logic module holding a
    stale value – and actuators whose object has the U (update) flag treat
    that response like a write. Reading "Hall light switching" can therefore
    switch the light. Feedback, sensor and alarm addresses are safe.
    """
    if mode == "off":
        return []
    controls = {dp["ga"] for f in model["functions"].values() for dp in f["datapoints"] if dp["ga"]}
    wanted: dict[str, None] = {}  # ordered set
    for f in model["functions"].values():
        for dp in f["datapoints"]:
            for ga in (dp["status"], dp["ga"]) if mode == "all" else (dp["status"],):
                if ga and gas.get(ga, {}).get("readable"):
                    wanted[ga] = None
    for ga in alarm_addresses:
        if gas.get(ga, {}).get("readable"):
            wanted[ga] = None
    if mode != "all":
        for ga in controls:
            wanted.pop(ga, None)
    return list(wanted)


class ScadaServer:
    """Owns the KNX connection, the database, the alarm state and all client connections."""

    def __init__(self, cfg: Config, demo: bool = False) -> None:
        self.cfg = cfg
        self.demo = demo
        self.model = load_project(cfg)
        self.gas: dict[str, dict] = self.model["gas"]
        db = cfg.db_path
        # The demo uses its own database so simulated data never mixes with real history.
        self.store = Store(db.with_name(db.stem + "-demo" + db.suffix) if demo else db)
        self.rules = build_rules(cfg.alarms, self.gas)
        self.state: dict[str, dict] = {}  # GA → {"value", "text", "raw", "ts", "src"}
        self.clients: set[asyncio.StreamWriter] = set()
        self.alarms: dict[int, dict] = {}  # open alarms (active or unacknowledged) by id
        for a in self.store.open_alarms():
            a["severity"] = normalize_severity(a["severity"])  # old databases used Norwegian names
            self.alarms[a["id"]] = a
        self.started = time.time()
        self.tel_count = 0
        self.tel_times: deque[float] = deque(maxlen=100_000)  # timestamps of the last minute's telegrams (rate)
        # One entry per KNX IP interface; validated here so a typo stops the server at once.
        self.ifaces: list[dict[str, Any]] = [] if demo else interface_configs(
            cfg.knx, {p["name"]: p["prefix"] for p in cfg.projects})
        self.router = Router(self.ifaces) if self.ifaces else None
        self._prefix = {c["name"]: c["prefix"] for c in self.ifaces}
        self.dedup = Deduplicator(int(cfg.knx.get("dedup_ms", DEDUP_MS))) if len(self.ifaces) > 1 else None
        self.xknx: dict[str, Any] = {}  # interface name → XKNX
        self.status: dict[str, Any] = {
            "connected": False,
            "connection": N_("demo") if demo else N_("disconnected"),
            "gateway": "simulator" if demo else ", ".join(describe(c) for c in self.ifaces),
            "interfaces": [{"name": c["name"], "gateway": describe(c), "project": c["project"], "connected": False,
                            "connection": N_("disconnected")} for c in self.ifaces],
            "started": self.started,
        }
        self.sim: Simulator | None = None
        self._stop = asyncio.Event()
        self.plc = self._make_plc_bridge()

        # Start with the last known values from the database so clients see
        # something immediately, before the bus has answered our read requests.
        for ga, last in self.store.last_values(list(self.gas)).items():
            if ga in self.gas:
                last["text"] = dptlib.fmt(self.gas[ga]["dpt"], last["value"])
                self.state[ga] = last

    # -- life cycle -----------------------------------------------------------------------
    async def run(self) -> None:
        """Run until SIGINT/SIGTERM, then shut down cleanly."""
        sock = self.cfg.socket_path
        sock.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(FileNotFoundError):
            sock.unlink()  # stale socket from a previous run
        server = await asyncio.start_unix_server(self._client, path=str(sock), limit=2**20)
        # rw for owner and group only: whoever can open the socket can operate the plant.
        os.chmod(sock, 0o660)
        log.info(_("Listening on %s"), sock)
        self.event("info", "system", _("Server started ({mode})").format(mode="demo" if self.demo else "KNX"))

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):  # not available on Windows
                loop.add_signal_handler(sig, self._on_signal)

        tasks = [asyncio.create_task(self._housekeeping())]
        if self.plc is not None:
            tasks.append(asyncio.create_task(self.plc.run()))
        if self.demo:
            self.sim = Simulator(self)
            tasks.append(asyncio.create_task(self.sim.run()))
            self._set_status(True, N_("demo"))
        else:
            tasks += [asyncio.create_task(self._knx_connect(c)) for c in self.ifaces]
            tasks.append(asyncio.create_task(self._read_on_start()))

        await self._stop.wait()
        log.info(_("Stopping …"))
        server.close()
        # Since Python 3.12, Server.wait_closed() waits until every client has
        # disconnected. Close the client connections ourselves first, otherwise
        # an open TUI session would keep the server from stopping.
        for w in list(self.clients):
            w.close()
        self.clients.clear()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(server.wait_closed(), 2)
        if self.plc is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.plc.stop(), 5)
        for t in tasks:
            t.cancel()
        for xknx in self.xknx.values():
            with contextlib.suppress(Exception):
                await asyncio.wait_for(xknx.stop(), 5)
        self.event("info", "system", _("Server stopped"))
        self.store.close()
        with contextlib.suppress(FileNotFoundError):
            sock.unlink()

    def _on_signal(self) -> None:
        """First Ctrl+C/SIGTERM: graceful stop. Second: exit immediately."""
        if self._stop.is_set():
            log.warning(_("Forcing exit"))
            os._exit(1)
        self._stop.set()

    async def _housekeeping(self) -> None:
        """Once per second: commit the database and send statistics; hourly: purge old data."""
        last_purge = 0.0
        while True:
            await asyncio.sleep(1)
            self.store.commit()
            self._broadcast({"type": "stats", "stats": self.stats()})
            if time.time() - last_purge > 3600:
                last_purge = time.time()
                await self._purge()

    async def _purge(self) -> None:
        """Delete data older than ``server.retention_days`` in small batches."""
        cutoff = time.time() - float(self.cfg.server.get("retention_days", 90)) * 86400
        while self.store.purge_batch(cutoff, PURGE_BATCH) >= PURGE_BATCH:
            await asyncio.sleep(0.05)  # let telegrams and clients through between batches

    def stats(self) -> dict[str, Any]:
        """Telegram count, telegrams in the last minute and number of connected clients."""
        now = time.time()
        while self.tel_times and self.tel_times[0] < now - 60:
            self.tel_times.popleft()
        return {"count": self.tel_count, "per_min": len(self.tel_times), "clients": len(self.clients)}

    # -- KNX ------------------------------------------------------------------------------
    async def _knx_connect(self, c: dict[str, Any]) -> None:
        """Connect to one KNX IP interface, retrying with back-off until it succeeds.

        After the first successful connect xknx reconnects by itself.
        """
        from xknx import XKNX
        from xknx.io import ConnectionConfig

        name = c["name"]
        secure = None
        if c.get("keyring_file"):
            # KNX Secure: keys come from the keyring exported from ETS.
            from xknx.io import SecureConfig

            secure = SecureConfig(
                knxkeys_file_path=str(self.cfg.resolve(c["keyring_file"])),
                knxkeys_password=c.get("keyring_password"),
                user_id=c.get("user_id"),
            )
        conn = ConnectionConfig(
            connection_type=connection_type(c["connection"]),
            gateway_ip=c.get("gateway_ip"),
            gateway_port=int(c.get("gateway_port", 3671)),
            local_ip=c.get("local_ip"),
            route_back=bool(c.get("route_back", False)),  # behind NAT / VPN (Tailscale, WireGuard)
            individual_address=c.get("individual_address"),
            auto_reconnect=True,
            secure_config=secure,
        )
        delay = 5
        while True:
            xknx = XKNX(connection_config=conn, connection_state_changed_cb=partial(self._on_conn_state, name))
            self.xknx[name] = xknx
            # match_for_outgoing: our own telegrams also pass through _on_telegram,
            # so they are logged and update the state exactly like incoming ones.
            xknx.telegram_queue.register_telegram_received_cb(partial(self._on_telegram, name), match_for_outgoing=True)
            try:
                await xknx.start()
                return
            except Exception as exc:  # noqa: BLE001 - any failure: report and retry
                self.event("error", "knx", _("Could not connect to KNX ({error}). Retrying in {delay} s").format(
                    error=exc, delay=delay) + self._iface_suffix(name))
                with contextlib.suppress(Exception):
                    await xknx.stop()
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60)

    def _iface_suffix(self, name: str) -> str:
        """`` [name]`` after KNX messages when there is more than one interface."""
        return f" [{name}]" if len(self.ifaces) > 1 else ""

    def _on_conn_state(self, name: str, state) -> None:
        """xknx callback when the connection state of interface *name* changes."""
        connected = state.name == "CONNECTED"
        code = CONNECTION_STATES.get(state.name, state.name.lower())
        for i in self.status["interfaces"]:
            if i["name"] == name:
                i.update(connected=connected, connection=code)
        self.event("info" if connected else "error", "knx",
                   _("KNX connection: {state}").format(state=state.name) + self._iface_suffix(name))
        # Overall: connected while at least one interface is; the TUI shows the others per interface.
        ifs = self.status["interfaces"]
        n = sum(i["connected"] for i in ifs)
        if n == len(ifs):
            self._set_status(True, N_("connected"))
        elif n:
            self._set_status(True, N_("partly connected"))
        else:
            self._set_status(False, code if len(ifs) == 1 else N_("disconnected"))

    def _set_status(self, connected: bool, code: str) -> None:
        self.status.update(connected=connected, connection=code)
        self._broadcast({"type": "status", "status": self.status})

    def _on_telegram(self, name: str, telegram) -> None:
        """xknx callback for every telegram (incoming and outgoing) of interface *name*."""
        if not isinstance(telegram.destination_address, GroupAddress):
            return  # device management traffic – not interesting for a SCADA
        payload = telegram.payload
        if isinstance(payload, GroupValueWrite):
            kind = "write"
        elif isinstance(payload, GroupValueResponse):
            kind = "response"
        elif isinstance(payload, GroupValueRead):
            kind = "read"
        else:
            return
        # With several projects the address gets the prefix of this interface's project.
        dst = ga_key(self._prefix[name], str(telegram.destination_address))
        value = getattr(payload, "value", None)
        incoming = telegram.direction != TelegramDirection.OUTGOING
        if self.dedup is not None:
            raw = dptlib.raw_hex(value) if value is not None else None
            if self.dedup.duplicate(name, dst, kind, raw):
                return  # the same telegram, seen through another (coupled) interface
            if incoming:
                assert self.router is not None
                self.router.learn(dst, name)
        self.ingest(
            src=str(telegram.source_address),
            dst=dst,
            kind=kind,
            value=value,
            direction="in" if incoming else "out",
            via=name if len(self.ifaces) > 1 else "",
        )

    async def _send(self, ga: str, kind: str, payload: DPTArray | DPTBinary | None) -> None:
        """Send a GroupValueWrite (*kind* "write") or GroupValueRead (*kind* "read")."""
        if self.demo:
            assert self.sim is not None
            self.ingest(src="0.0.0", dst=ga, kind=kind, value=payload, direction="out")
            self.sim.on_outgoing(ga, kind, payload)
            return
        assert self.router is not None
        name = self.router.route(ga)
        if name is None:
            raise RuntimeError(_("No KNX interface for the project of {address}").format(address=ga))
        xknx = self.xknx.get(name)
        if xknx is None or not self._iface_connected(name):
            raise RuntimeError(_("Not connected to KNX") + self._iface_suffix(name))
        apci = GroupValueRead() if kind == "read" else GroupValueWrite(payload)
        await xknx.telegrams.put(Telegram(destination_address=GroupAddress(split_key(ga)[1]), payload=apci))

    def _iface_connected(self, name: str) -> bool:
        return any(i["connected"] for i in self.status["interfaces"] if i["name"] == name)

    async def _read_on_start(self) -> None:
        """``server.read_on_start``: once every interface is connected (at most 30 s), read the plant."""
        if read_mode(self.cfg.server.get("read_on_start")) == "off":
            return
        for _i in range(300):
            if all(i["connected"] for i in self.status["interfaces"]):
                break
            await asyncio.sleep(0.1)
        while not self.status["connected"]:
            await asyncio.sleep(1)
        await self._read_all()

    async def _read_all(self) -> None:
        """Ask the bus for the current value of the addresses chosen by :func:`read_targets`.

        A small pause between reads keeps the bus load low.
        """
        mode = read_mode(self.cfg.server.get("read_on_start"))
        wanted = read_targets(self.model, self.gas, self.rules, mode)
        interval = int(self.cfg.server.get("read_interval_ms", 80)) / 1000
        self.event("info", "knx", _("Reading the status of {n} group addresses").format(n=len(wanted)))
        for ga in wanted:
            with contextlib.suppress(Exception):
                await self._send(ga, "read", None)
            await asyncio.sleep(interval)

    # -- telegram processing ------------------------------------------------------------------
    def ingest(self, src: str, dst: str, kind: str, value: Any, direction: str, via: str = "") -> None:
        """Handle one group telegram: store, update state, evaluate alarms, broadcast.

        *via* is the name of the KNX interface (only set when there are several).
        """
        ga = self.gas.get(dst)
        dpt = ga["dpt"] if ga else None
        decoded = None
        raw = None
        if value is not None:  # read requests carry no value
            raw = dptlib.raw_hex(value)
            decoded = dptlib.decode(dpt, value)
        now = time.time()
        t = {
            "ts": now,
            "dir": direction,
            "src": src,
            "dst": dst,
            "via": via,
            "type": kind,
            "raw": raw,
            "value": decoded,
            "text": dptlib.fmt(dpt, decoded) if value is not None else "",
        }
        t["id"] = self.store.add_telegram(t)
        self.tel_count += 1
        self.tel_times.append(now)
        msg: dict[str, Any] = {"type": "telegram", "t": t}
        if kind != "read":
            # Writes and responses carry the current value of the address.
            st = {"value": decoded, "text": t["text"], "raw": raw, "ts": now, "src": src}
            self.state[dst] = st
            msg["state"] = st
        self._broadcast(msg)
        if kind != "read" and dst in self.rules:
            self._check_alarm(dst, decoded)
        if kind != "read" and self.plc is not None:
            self.plc.on_knx(dst, decoded)

    def _make_plc_bridge(self):
        """The OPC UA bridge to a PLC when ``plc.enabled`` is set (see :mod:`knxscada.opcua_bridge`)."""
        if not self.cfg.plc.get("enabled"):
            return None
        from .opcua_bridge import PlcBridge, parse_security
        from .plc import load_map

        path = self.cfg.resolve(self.cfg.plc.get("map", "plc_map.yaml"))
        if not path.exists():
            raise FileNotFoundError(_("plc.enabled is set, but {path} does not exist – create it with ./scada plc-map").format(
                path=path))
        parse_security(self.cfg.plc.get("security"))  # fail early on a typo
        pmap = load_map(path, self.gas)
        self.status["plc"] = N_("disconnected")
        log.info(_("PLC bridge: {n} group addresses ({path})").format(n=len(pmap.active()), path=path.name))
        return PlcBridge(self, self.cfg, pmap)

    def _check_alarm(self, addr: str, value: Any) -> None:
        """Raise or clear the alarm bound to *addr* according to its rule.

        An alarm has two independent properties: active/cleared (the
        condition) and acknowledged/unacknowledged (the operator). It stays
        in the open list until it is both cleared and acknowledged.
        """
        rule = self.rules[addr]
        active = rule.active(value)
        if active is None:
            return  # value cannot be judged – keep the current state
        ga = self.gas[addr]
        current = next((a for a in self.alarms.values() if a["ga"] == addr and a["cleared_ts"] is None), None)
        changed = False
        if active and current is None:
            text = rule.format_text(name=ga["name"], address=addr, value=dptlib.fmt(ga["dpt"], value))
            aid = self.store.raise_alarm(addr, text, rule.severity, value)
            self.alarms[aid] = {
                "id": aid, "ga": addr, "text": text, "severity": rule.severity, "raised_ts": time.time(),
                "cleared_ts": None, "acked_ts": None, "acked_by": None, "value": json.dumps(value),
            }
            self.event("alarm", "alarm", _("ALARM {severity}: {text} ({address})").format(
                severity=_(rule.severity).upper(), text=text, address=addr))
            changed = True
        elif not active and current is not None:
            self.store.clear_alarm(current["id"])
            current["cleared_ts"] = time.time()
            self.event("info", "alarm", _("Alarm cleared: {text} ({address})").format(text=current["text"], address=addr))
            if current["acked_ts"]:
                del self.alarms[current["id"]]  # cleared and acknowledged: no longer open
            changed = True
        if changed:
            self._broadcast_alarms()

    def _broadcast_alarms(self) -> None:
        self._broadcast({"type": "alarms", "alarms": list(self.alarms.values())})

    def event(self, level: str, kind: str, text: str, user: str | None = None) -> None:
        """Add an entry to the event log, write it to the server log and broadcast it.

        *level*: ``info`` | ``error`` | ``alarm``.
        *kind*: ``system`` | ``knx`` | ``user`` | ``operation`` | ``alarm``.
        """
        ev = self.store.add_event(level, kind, text, user)
        log.info("[%s] %s%s", kind, text, f" ({user})" if user else "")
        self._broadcast({"type": "event", "event": ev})

    # -- clients ---------------------------------------------------------------------------
    def _broadcast(self, msg: dict) -> None:
        """Send a message to every connected client (without waiting for slow ones)."""
        if not self.clients:
            return
        line = (json.dumps(msg, ensure_ascii=False, default=str) + "\n").encode()
        for w in list(self.clients):
            if w.transport.get_write_buffer_size() > MAX_CLIENT_BUFFER:
                log.warning(_("Client is not keeping up – disconnecting it"))
                self.clients.discard(w)
                w.close()
                continue
            w.write(line)

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Serve one client connection: send the snapshot, then answer requests until it leaves."""
        hello = {
            "type": "hello",
            "status": self.status,
            "stats": self.stats(),
            "state": self.state,
            "alarms": list(self.alarms.values()),
            "demo": self.demo,
        }
        writer.write((json.dumps(hello, ensure_ascii=False, default=str) + "\n").encode())
        self.clients.add(writer)
        peer = _peer_user(writer)
        user = peer or "?"
        try:
            while line := await reader.readline():
                try:
                    req = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(req, dict):
                    continue
                user = _audit_user(peer, req.get("user"))
                rid = req.get("id")
                try:
                    data = await self._command(req, user)
                    reply = {"type": "reply", "id": rid, "ok": True, "data": data}
                except Exception as exc:  # noqa: BLE001 - report every error to the client
                    reply = {"type": "reply", "id": rid, "ok": False, "error": str(exc)}
                writer.write((json.dumps(reply, ensure_ascii=False, default=str) + "\n").encode())
                await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            self.clients.discard(writer)
            with contextlib.suppress(Exception):
                writer.close()

    async def _command(self, req: dict, user: str) -> Any:
        """Execute one client request and return its reply data."""
        cmd = req.get("cmd")
        if cmd == "hello":
            self.event("info", "user", _("Client connected"), user)
            return None
        if cmd == "bye":
            self.event("info", "user", _("Client disconnected"), user)
            return None
        if cmd == "write":
            ga = req["ga"]
            if ga not in self.gas:
                raise ValueError(_("{address} is not in the ETS project").format(address=ga))
            # The project's DPT wins; the client's is only used when ETS has none.
            dpt = self.gas[ga].get("dpt") or req.get("dpt")
            payload = dptlib.encode(dpt, req["value"])
            await self._send(ga, "write", payload)
            # Audit log: who wrote what (decoded back so the log shows the real value).
            name = self.gas.get(ga, {}).get("name", "")
            shown = dptlib.fmt(dpt, dptlib.decode(dpt, payload))
            self.event("info", "operation", f"{ga} {name} ← {shown}", user)
            return None
        if cmd == "read":
            if req["ga"] not in self.gas:
                raise ValueError(_("{address} is not in the ETS project").format(address=req["ga"]))
            await self._send(req["ga"], "read", None)
            return None
        if cmd == "ack":
            ids = [a["id"] for a in self.alarms.values() if a["acked_ts"] is None] if req.get("all") else [req["alarm_id"]]
            acked = 0
            for aid in ids:
                a = self.alarms.get(aid)
                if not a or a["acked_ts"]:
                    continue
                acked += 1
                self.store.ack_alarm(aid, user)
                a["acked_ts"], a["acked_by"] = time.time(), user
                self.event("info", "alarm", _("Alarm acknowledged: {text}").format(text=a["text"]), user)
                if a["cleared_ts"]:
                    del self.alarms[aid]
            self._broadcast_alarms()
            return acked
        if cmd == "telegrams":
            return self.store.telegrams(_bounded(req.get("limit", 500), MAX_LIMIT), req.get("ga"), req.get("before_id"))
        if cmd == "events":
            return self.store.events(_bounded(req.get("limit", 500), MAX_LIMIT))
        if cmd == "alarm_history":
            return self.store.alarm_history(_bounded(req.get("limit", 200), MAX_LIMIT))
        if cmd == "traffic":
            return self.store.traffic(_bounded(req.get("minutes", 60), MAX_MINUTES))
        if cmd == "history":
            # In a worker thread: a 30-day trend of a busy address must not stop the telegram handling.
            return await asyncio.to_thread(
                self.store.read_history, req["ga"], time.time() - _bounded(req.get("hours", 24), MAX_HOURS) * 3600,
                _bounded(req.get("max_points", 4000), 20000))
        raise ValueError(_("Unknown command {cmd}").format(cmd=repr(cmd)))


def _bounded(value: Any, upper: int) -> int:
    """A client-supplied count, limited to 1 … *upper*."""
    return max(1, min(int(float(value)), upper))


def _peer_user(writer: asyncio.StreamWriter) -> str | None:
    """The account of the process at the other end of the Unix socket (from the kernel, not the client)."""
    sock = writer.get_extra_info("socket")
    try:
        creds = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        _pid, uid, _gid = struct.unpack("3i", creds)
    except (AttributeError, OSError):  # not Linux, or not a Unix socket
        return None
    if uid == os.getuid():
        return None  # the server's own account (e.g. inside Docker): the client's name is all we have
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return f"uid{uid}"


def _audit_user(peer: str | None, claimed: Any) -> str:
    """The name for the audit log: the kernel's account name, plus the SSH address the client reports.

    The client's own name is only used when the kernel cannot tell us more (same account as the
    server). Anything that is not a plain name/address is removed, so it cannot inject markup.
    """
    text = USER_RE.sub("", claimed if isinstance(claimed, str) else "")[:64]
    if peer is None:
        return text or "?"
    host = text.partition("@")[2]
    return f"{peer}@{host}" if host else peer


class Simulator:
    """A simple simulation of a KNX installation for testing without a bus (``--demo``).

    Built from the project model, it

    * gives every used address a plausible initial value,
    * lets sensors (temperatures, CO₂, power …) drift slowly,
    * toggles a random switch now and then (someone pressing a wall switch),
    * echoes writes to the paired status address, like a real actuator –
      including switch ↔ brightness for dimmers,
    * answers read requests, and
    * occasionally raises or clears a fault so the alarm handling can be seen.
    """

    def __init__(self, server: ScadaServer) -> None:
        self.s = server
        self.echo: dict[str, list[tuple[str, str]]] = {}  # control GA → [(status GA, mode)]
        self.sensors: list[str] = []
        self.switches: list[str] = []
        for f in server.model["functions"].values():
            sw = next((d for d in f["datapoints"] if d["kind"] == "switch"), None)
            pc = next((d for d in f["datapoints"] if d["kind"] == "percent"), None)
            for d in f["datapoints"]:
                if d["ga"] and d["status"]:
                    self.echo.setdefault(d["ga"], []).append((d["status"], "same"))
                if d["kind"] == "switch":
                    self.switches.append(d["ga"])
            if sw and pc:
                # Dimmer: switching on sets the brightness, a brightness > 0 means "on".
                if pc["status"]:
                    self.echo.setdefault(sw["ga"], []).append((pc["status"], "sw2pct"))
                if sw["status"]:
                    self.echo.setdefault(pc["ga"], []).append((sw["status"], "pct2sw"))
        for addr, ga in server.gas.items():
            main, _sub = dptlib.split(ga["dpt"])
            if ga["used"] and main in (9, 13, 14) and not SETPOINT_RE.search(ga["name"]):
                self.sensors.append(addr)

    def _initial(self, addr: str, ga: dict) -> Any:
        """A plausible start value for an address, based on its DPT and name."""
        main, sub = dptlib.split(ga["dpt"])
        name = ga["name"].lower()
        if main == 1:
            return random.random() < (0.3 if "status" in name or "switching" in name else 0.02)
        if main == 5:
            return random.choice([0, 0, 40, 75, 100])
        if main == 9:
            if sub == 8:  # CO₂ ppm
                return random.uniform(450, 900)
            if sub == 7:  # humidity %
                return random.uniform(30, 55)
            if sub == 1:  # temperature
                if "ute" in name or "outdoor" in name or "outside" in name:
                    return random.uniform(-3, 8)
                if SETPOINT_RE.search(name):
                    return 21.0
                return random.uniform(19.5, 22.5)
            return random.uniform(0, 100)
        if main == 20 and sub == 102:
            return "COMFORT"
        if main in (13, 14):  # energy / power
            return random.uniform(0, 3000)
        return None

    def _emit(self, addr: str, value: Any, src: str = "1.1.24", kind: str = "write") -> None:
        """Inject a telegram as if it came from the bus."""
        dpt = self.s.gas[addr]["dpt"]
        try:
            payload = dptlib.encode(dpt, value)
        except Exception:  # noqa: BLE001 - value not valid for this DPT: skip it
            return
        self.s.ingest(src=src, dst=addr, kind=kind, value=payload, direction="in")

    async def run(self) -> None:
        # Initial values for everything that has no value in the database yet.
        for addr, ga in self.s.gas.items():
            if addr in self.s.state or not ga["used"]:
                continue
            v = self._initial(addr, ga)
            if v is not None:
                self._emit(addr, v, src="1.1.1", kind="response")
            await asyncio.sleep(0)  # stay responsive while seeding many addresses
        while True:
            await asyncio.sleep(random.uniform(0.3, 1.5))
            r = random.random()
            if r < 0.8 and self.sensors:
                # 80 %: a sensor value drifts a little.
                addr = random.choice(self.sensors)
                cur = self.s.state.get(addr, {}).get("value")
                if isinstance(cur, (int, float)) and not isinstance(cur, bool):
                    main, sub = dptlib.split(self.s.gas[addr]["dpt"])
                    step = {8: 25, 7: 1.5}.get(sub, 0.15) if main == 9 else max(abs(cur) * 0.03, 1)
                    self._emit(addr, round(cur + random.uniform(-step, step), 2), src="1.1.15")
            elif r < 0.95 and self.switches:
                # 15 %: somebody presses a wall switch.
                addr = random.choice(self.switches)
                st = self.echo.get(addr, [(addr, "same")])[0][0]
                cur = bool(self.s.state.get(st, {}).get("value"))
                self._emit(addr, not cur, src="1.1.25")
                self.on_outgoing(addr, "write", DPTBinary(0 if cur else 1))
            else:
                # 5 %: a fault message (non-smoke alarm rule) appears or disappears.
                cands = [a for a in self.s.rules if dptlib.split(self.s.gas[a]["dpt"])[0] == 1 and "smoke" not in self.s.gas[a]["name"].lower()]
                if cands:
                    addr = random.choice(cands)
                    cur = bool(self.s.state.get(addr, {}).get("value"))
                    self._emit(addr, not cur if cur else random.random() < 0.3, src="1.1.7")

    def on_outgoing(self, addr: str, kind: str, payload: Any) -> None:
        """React to a telegram we sent: answer reads, echo writes to the status address."""
        if kind == "read":
            st = self.s.state.get(addr)
            if st and st.get("value") is not None:
                asyncio.get_running_loop().call_later(0.05, self._emit, addr, st["value"], "1.1.24", "response")
            return
        value = dptlib.decode(self.s.gas.get(addr, {}).get("dpt"), payload)
        for status, mode in self.echo.get(addr, []):
            if mode == "same":
                v = value
            elif mode == "sw2pct":
                cur = self.s.state.get(status, {}).get("value")
                if value and isinstance(cur, (int, float)) and cur > 0:
                    continue  # already on at some brightness: keep it
                v = 100 if value else 0
            else:  # pct2sw
                v = bool(value)
            # A real actuator answers after a short moment.
            asyncio.get_running_loop().call_later(random.uniform(0.1, 0.4), self._emit, status, v)
