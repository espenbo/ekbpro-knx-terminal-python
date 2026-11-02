# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""The gateway engine: moves values between tags of different connections.

Every mapping line of a gateway becomes a :class:`Link` between a source tag
(connection ``from``) and a target tag (connection ``to``), with an optional
new name, scaling and direction::

    source value ──scale/offset/invert/type──▶ target tag            (forward)
    source tag   ◀──the inverse──────────────── target value          (both)

The target tag is created when the target is a server (OPC UA server, Modbus
slave, MQTT): that is how a Modbus register becomes an OPC UA variable with
a name of your choice. A client target (e.g. a Modbus master) must have the tag.

Rules that keep the plant safe and quiet (the same as for the PLC bridge):

* **No echoes.** The last value exchanged is remembered on both sides of every
  link; a value equal to it is not passed on, so a value written to the target
  and read back does not travel back to the source.
* **Read first, send afterwards.** After a start-up or a reconnect a
  connection reads the statuses of its tags first (:attr:`Driver.ready`).
  These *initial* values are remembered on both sides of a link, but not sent:
  devices keep their own values until something really changes. Only our own
  address space is filled at once – OPC UA server, Modbus slave, MQTT state
  topics made by a gateway (:meth:`Driver.initial_default`). ``initial: true``
  sends them to devices too, ``initial: false`` to nothing; KNX never gets
  them (:attr:`Driver.initial_allowed`).
* **The target is read before it is written.** A change for a target that is
  still reading waits until it is done, and is dropped if the target already
  has that value. The value a target reports is remembered, so a change is
  not sent when the target has it already.
* **Deadband** (``deadband``): numbers that changed less than this are not sent.
* **Rate limit** (``min_interval_ms``): at most one write per link and direction in
  that time; the latest value is sent when the interval is over.
"""

from __future__ import annotations

import asyncio
import fnmatch
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..i18n import _
from .base import coerce, same_value, scale_value, unscale_value
from .config import parse_link

if TYPE_CHECKING:
    from .hub import Hub

#: "No value yet" – distinct from None, which is a real (empty) value.
MISSING: Any = object()

#: Keys of a mapping line that belong to the engine; the others are passed on to a new target tag.
ENGINE_KEYS = ("from", "to", "direction", "scale", "offset", "invert", "deadband", "min_interval_ms", "initial",
               "type", "unit", "description")

#: DPT 20.102 (HVAC mode) names and their numbers, for gateways between KNX and number-only protocols.
HVAC_CODES = {"AUTO": 0, "COMFORT": 1, "STANDBY": 2, "ECONOMY": 3, "BUILDING_PROTECTION": 4}


def _flag(value: Any) -> bool | None:
    """``initial``: None when not given, otherwise yes or no."""
    return None if value is None or value == "" else bool(value)


def _num(value: Any, default: float | None = None) -> float | None:
    """A numeric line setting (``scale``, ``deadband`` …) as float; *default* when empty. ValueError if not a number."""
    if value is None or value == "":
        return default
    return float(value)


@dataclass
class Link:
    """One mapping line between a source tag (a) and a target tag (b)."""

    gateway: str                   # name of the gateway the line belongs to (shown in errors and the TUI)
    a_conn: str
    a_tag: str
    b_conn: str
    b_tag: str
    both: bool = False
    scale: float | None = None
    offset: float | None = None
    invert: bool = False
    deadband: float = 0.0
    min_interval: float = 0.0      # seconds
    initial: bool | None = None    # None: as the target driver says (Driver.initial_default)
    #: Last value known on each side (received or sent) – the echo and "already has it" checks compare with it.
    last_a: Any = MISSING
    last_b: Any = MISSING
    count: int = 0                 # writes done, for the TUI
    last_ts: float = 0.0           # time of the last successful write (epoch seconds)
    error: str = ""                # last error, "" while the link works
    #: Rate-limited writes waiting to go out, per direction (True = forward).
    pending: dict[bool, asyncio.TimerHandle] = field(default_factory=dict)
    held: dict[bool, Any] = field(default_factory=dict)    # changes waiting for the target to finish reading
    sent_at: dict[bool, float] = field(default_factory=dict)   # monotonic time of the last write per direction

    @property
    def key(self) -> str:
        """Unique id of the link (gateway, source and target); a line that matches twice is linked once."""
        return f"{self.gateway}|{self.a_conn}/{self.a_tag}|{self.b_conn}/{self.b_tag}"

    def info(self) -> dict[str, Any]:
        """What the TUI's gateway view shows of the link: ends, direction, write count, last write and error."""
        return {"key": self.key, "gateway": self.gateway, "from": f"{self.a_conn}/{self.a_tag}",
                "to": f"{self.b_conn}/{self.b_tag}", "both": self.both, "count": self.count, "ts": self.last_ts,
                "error": self.error}


def _invert(value: Any) -> Any:
    """``invert``: on ↔ off. Numbers become 0/1 (a non-zero number counts as on); text is left as it is."""
    if isinstance(value, bool):
        return not value
    if isinstance(value, (int, float)):
        return 0 if value else 1
    return value


def convert(value: Any, typ: str, scale: float | None, offset: float | None, invert: bool) -> Any:
    """Source value → target value: invert, scale, then the target's type."""
    if invert:
        value = _invert(value)
    value = scale_value(value, scale, offset)
    try:
        return coerce(value, typ)
    except (TypeError, ValueError):
        if isinstance(value, str) and value.upper() in HVAC_CODES and typ in ("int", "float"):
            return coerce(HVAC_CODES[value.upper()], typ)
        raise


def convert_back(value: Any, typ: str, scale: float | None, offset: float | None, invert: bool) -> Any:
    """Target value → source value (the inverse of :func:`convert`)."""
    value = unscale_value(value, scale, offset)
    if invert:
        value = _invert(value)
    try:
        return coerce(value, typ)
    except (TypeError, ValueError):
        if isinstance(value, str) and value.upper() in HVAC_CODES and typ in ("int", "float"):
            return coerce(HVAC_CODES[value.upper()], typ)
        raise


def target_name(template: str, tag) -> str:
    """The target name of a wildcard line: ``{name}``, ``{address}`` and ``{description}`` are filled in."""
    if not template:
        return tag.name
    if "{" not in template:
        return template
    return template.format_map({"name": tag.name, "address": tag.address or tag.name,
                                "description": tag.description or tag.name})


class GatewayEngine:
    """All links of all gateways, indexed by the tags whose values they react to."""

    def __init__(self, hub: Hub, gateways: list[dict[str, Any]]) -> None:
        """An engine for the ``gateways:`` list of ``protocols.yaml`` (``enabled: false`` ones are left out).

        Nothing is linked until :meth:`build`; the hub calls it right after creating the engine.
        """
        self.hub = hub
        self.gateways = [g for g in gateways if isinstance(g, dict) and g.get("enabled", True) is not False]
        self.links: list[Link] = []
        self.by_tag: dict[tuple[str, str], list[tuple[Link, bool]]] = {}   # (conn, tag) → [(link, forward)]
        self.targets: dict[tuple[str, str], list[Link]] = {}               # targets of one-way links
        self._patterns: list[tuple[dict[str, Any], dict[str, Any]]] = []   # (gateway, line) with * or ?
        self._checked: set[tuple[str, str]] = set()                        # tags already matched with the patterns
        self._made: set[str] = set()                                       # link keys, so a tag is linked once
        self.problems: list[str] = []                                      # build errors, shown in the TUI

    # -- building ---------------------------------------------------------------------------------
    def build(self) -> None:
        """Create the links of every gateway from the tags the connections know now."""
        for gw in self.gateways:
            for item in gw.get("map") or []:
                try:
                    line = parse_link(item)
                except ValueError:
                    continue
                src = line["from"]
                if any(c in src for c in "*?["):
                    self._patterns.append((gw, line))
                    drv = self.hub.drivers.get(str(gw.get("from")))
                    if drv is not None:
                        for tag in list(drv.tags.values()):
                            if fnmatch.fnmatchcase(tag.name, src):
                                self._link(gw, line, tag.name, target_name(line["to"], tag))
                else:
                    self._link(gw, line, src, line["to"] or src)

    def _link(self, gw: dict[str, Any], line: dict[str, Any], src: str, dst: str) -> Link | None:
        """Resolve both tags (creating the target on servers) and index the link."""
        gname = str(gw.get("name") or "")
        a = self.hub.drivers.get(str(gw.get("from")))
        b = self.hub.drivers.get(str(gw.get("to")))
        if a is None or b is None:
            return None  # the connection could not be started; the hub has reported why
        a_tag = a.resolve(src)
        if a_tag is None:
            self._problem(_("{gateway}: {connection} has no tag {tag}").format(gateway=gname, connection=a.name, tag=repr(src)))
            return None
        direction = str(line.get("direction") or gw.get("direction") or "forward")
        both = direction == "both"
        b_tag = b.resolve(dst)
        if b_tag is None:
            # Keys the engine does not use are settings of the target tag (e.g. word_order for Modbus).
            spec = {
                **{k: v for k, v in line.items() if k not in ENGINE_KEYS},
                "type": line.get("type") or a_tag.type,
                "writable": both,
                "unit": line.get("unit") or a_tag.unit,
                "description": line.get("description") or a_tag.description or a_tag.name,
            }
            try:
                b_tag = b.create_tag(dst, spec)
            except ValueError as exc:
                self._problem(f"{gname}: {exc}")
                return None
        elif both and not b_tag.writable and b.role == "server":
            # Only our own server tags are opened for writing here; a device tag keeps its ``writable: false``.
            b_tag.writable = True
        link = Link(
            gateway=gname, a_conn=a.name, a_tag=a_tag.name, b_conn=b.name, b_tag=b_tag.name, both=both,
            scale=_num(line.get("scale")), offset=_num(line.get("offset")), invert=bool(line.get("invert")),
            deadband=_num(line.get("deadband", gw.get("deadband")), 0.0) or 0.0,
            min_interval=(_num(line.get("min_interval_ms", gw.get("min_interval_ms")), 0.0) or 0.0) / 1000,
            initial=_flag(line.get("initial", gw.get("initial"))),
        )
        if link.key in self._made:
            return None
        self._made.add(link.key)
        self.links.append(link)
        self.by_tag.setdefault((link.a_conn, link.a_tag), []).append((link, True))
        if both:
            self.by_tag.setdefault((link.b_conn, link.b_tag), []).append((link, False))
        else:
            self.targets.setdefault((link.b_conn, link.b_tag), []).append(link)
        # What the target has already, so a source value equal to it is not sent.
        known_b = self.hub.values.get(f"{link.b_conn}/{link.b_tag}")
        if known_b is not None and known_b.get("q") == "good":
            if both:
                self._deliver(link, False, known_b["v"], initial=True)
            else:
                link.last_b = known_b["v"]
        # A value that is already known goes out at once (e.g. KNX state when the gateway is added).
        known = self.hub.values.get(f"{link.a_conn}/{link.a_tag}")
        if known is not None and known.get("q") == "good":
            self._deliver(link, True, known["v"], initial=True)
        return link

    def _problem(self, text: str) -> None:
        """Record a build problem (missing tag, bad target) once and report it in the event log."""
        if text not in self.problems:
            self.problems.append(text)
            self.hub.event("error", text)

    def _expand(self, conn: str, tag: str) -> None:
        """A tag seen for the first time: link it if a wildcard line matches it (e.g. new MQTT topics)."""
        self._checked.add((conn, tag))
        for gw, line in self._patterns:
            if str(gw.get("from")) == conn and fnmatch.fnmatchcase(tag, line["from"]):
                drv = self.hub.drivers.get(conn)
                t = drv.tags.get(tag) if drv else None
                if t is not None:
                    self._link(gw, line, tag, target_name(line["to"], t))

    # -- values -----------------------------------------------------------------------------------
    def on_value(self, conn: str, tag: str, value: Any, initial: bool = False) -> None:
        """A tag got a new value: pass it on through every link that starts (or, with both, ends) there."""
        key = (conn, tag)
        if self._patterns and key not in self._checked:
            self._expand(conn, tag)
        for link, forward in self.by_tag.get(key, ()):
            self._deliver(link, forward, value, initial)
        for link in self.targets.get(key, ()):
            link.last_b = value   # what the target has now

    def _deliver(self, link: Link, forward: bool, value: Any, initial: bool) -> None:
        """Pass *value* of one end through *link* (*forward*: source → target, else target → source).

        *initial* marks a status read after a start-up or reconnect, which is only remembered unless the
        target takes initial values (:meth:`_sends_initial`) – this is what keeps a restart from writing
        anything to KNX or to devices. A target that is connected but still reading gets the value later
        (:meth:`on_ready`); for a client target that is not connected the hub refuses the write, which is
        reported on the link.
        """
        last_in = link.last_a if forward else link.last_b
        if last_in is not MISSING and same_value(value, last_in):
            return  # our own write coming back, or no change
        if forward:
            link.last_a = value
        else:
            link.last_b = value
        dst_conn, dst_tag = (link.b_conn, link.b_tag) if forward else (link.a_conn, link.a_tag)
        drv = self.hub.drivers.get(dst_conn)
        tag = drv.tags.get(dst_tag) if drv else None
        if tag is None:
            return
        try:
            if forward:
                out = convert(value, tag.type, link.scale, link.offset, link.invert)
            else:
                out = convert_back(value, tag.type, link.scale, link.offset, link.invert)
        except (TypeError, ValueError, ZeroDivisionError) as exc:
            self._link_error(link, _("{value} cannot be converted for {tag}: {error}").format(
                value=repr(value), tag=f"{dst_conn}/{dst_tag}", error=exc))
            return
        if initial and not self._sends_initial(link, drv, tag):
            # Remember it as the value the target has (unless it has told us), so it is not sent later
            # as a "change".
            if (link.last_b if forward else link.last_a) is MISSING:
                self._set_last(link, not forward, out)
            return
        if drv.connected and not drv.ready:
            link.held[forward] = out   # the target is still reading its values: wait for them
            return
        self._send(link, forward, dst_conn, dst_tag, out)

    @staticmethod
    def _sends_initial(link: Link, drv, tag) -> bool:
        """Does *link* send initial values to *tag* of *drv*? Never to KNX (``initial_allowed``); else the line's
        ``initial``, or the driver's default (our own servers yes, devices no)."""
        if not drv.initial_allowed:
            return False
        return drv.initial_default(tag) if link.initial is None else link.initial

    def _send(self, link: Link, forward: bool, dst_conn: str, dst_tag: str, out: Any) -> None:
        """Write the converted *out* unless the target has it already or it is inside the ``deadband``.

        The value is remembered as the target's at once (before the write is done), so the read-back of
        our own write is recognised as an echo and not sent back to the source.
        """
        last_out = link.last_b if forward else link.last_a
        if last_out is not MISSING and same_value(out, last_out):
            return  # the target has it already
        if (link.deadband and last_out is not MISSING and isinstance(out, (int, float))
                and isinstance(last_out, (int, float)) and not isinstance(out, bool)
                and abs(out - last_out) < link.deadband):
            return
        self._set_last(link, not forward, out)
        self._schedule(link, forward, dst_conn, dst_tag, out)

    def on_ready(self, conn: str) -> None:
        """*conn* has read its values: send the changes that waited for it (unless it has them already).

        Targets that take initial values (our own servers) also get the current source values again,
        e.g. an MQTT broker that was restarted.
        """
        for link in self.links:
            for forward in (True, False):
                dst_conn, dst_tag = (link.b_conn, link.b_tag) if forward else (link.a_conn, link.a_tag)
                if dst_conn != conn or (not forward and not link.both):
                    continue
                if forward in link.held:
                    self._send(link, forward, dst_conn, dst_tag, link.held.pop(forward))
                    continue
                drv = self.hub.drivers.get(dst_conn)
                tag = drv.tags.get(dst_tag) if drv else None
                src = self.hub.values.get(f"{link.a_conn}/{link.a_tag}" if forward else f"{link.b_conn}/{link.b_tag}")
                if tag is not None and src is not None and src.get("q") == "good" and self._sends_initial(link, drv, tag):
                    self._set_last(link, forward, MISSING)
                    self._deliver(link, forward, src["v"], initial=True)

    def disconnected(self, conn: str) -> None:
        """*conn* lost its connection: waiting changes for it are dropped (it is read again first),
        and what it had is forgotten until it tells us again."""
        for link in self.links:
            if link.b_conn == conn:
                link.last_b = MISSING
            if link.a_conn == conn:
                link.last_a = MISSING
            for forward in list(link.held):
                if (link.b_conn if forward else link.a_conn) == conn:
                    del link.held[forward]

    @staticmethod
    def _set_last(link: Link, a_side: bool, value: Any) -> None:
        """Remember *value* as what the source (*a_side*) or the target has; MISSING forgets it."""
        if a_side:
            link.last_a = value
        else:
            link.last_b = value

    def _schedule(self, link: Link, forward: bool, conn: str, tag: str, value: Any) -> None:
        """Write now, or – within the rate limit – when the interval is over (the latest value wins)."""
        loop = asyncio.get_running_loop()
        old = link.pending.pop(forward, None)
        if old is not None:
            old.cancel()
        wait = link.sent_at.get(forward, 0.0) + link.min_interval - time.monotonic()
        if wait <= 0:
            loop.create_task(self._write(link, forward, conn, tag, value))
        else:
            link.pending[forward] = loop.call_later(
                wait, lambda: loop.create_task(self._write(link, forward, conn, tag, value)))

    async def _write(self, link: Link, forward: bool, conn: str, tag: str, value: Any) -> None:
        """Write through the hub as user ``gw:<gateway>`` (so the hub's checks – ``writable``, connected – apply)."""
        link.pending.pop(forward, None)
        link.sent_at[forward] = time.monotonic()
        try:
            await self.hub.write(conn, tag, value, user=f"gw:{link.gateway}")
        except Exception as exc:  # noqa: BLE001 - not connected, value out of range, refused …
            # Forget the value, so the next one is tried again even when it is the same.
            self._set_last(link, not forward, MISSING)
            self._link_error(link, _("{tag}: {error}").format(tag=f"{conn}/{tag}", error=str(exc) or type(exc).__name__))
            return
        link.count += 1
        link.last_ts = time.time()
        link.error = ""

    def _link_error(self, link: Link, text: str) -> None:
        """Report a problem once (until the link works again), so a broken link does not flood the log."""
        if link.error != text:
            link.error = text
            self.hub.event("error", f"{link.gateway}: {text}")

    def stop(self) -> None:
        """Cancel every rate-limited write still waiting (the engine is being replaced or the server stops)."""
        for link in self.links:
            for h in link.pending.values():
                h.cancel()
            link.pending.clear()
