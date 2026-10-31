# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""What every protocol module has in common: tags, values and the driver base class.

A **connection** (one entry under ``connections:`` in ``protocols.yaml``) is
served by one :class:`Driver`. A driver knows a set of **tags** – named values
such as a Modbus register, an MQTT topic, an OPC UA variable or a KNX group
address – and

* reports values it reads or receives with :meth:`Driver.publish`,
* writes values to its device (client) or serves them to others (server) in :meth:`Driver.write`.

Values are plain Python values: ``bool``, ``int``, ``float`` or ``str`` (KNX may
also give other JSON values, e.g. a colour as a dict). Every tag has a
*type* (one of :data:`TYPES`) that values are converted to before they are written.

Drivers never talk to each other; the gateway engine (:mod:`.gateway`) moves
values between them.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..i18n import _

if TYPE_CHECKING:
    from .hub import Hub

log = logging.getLogger("knxscada.protocols")

#: Value types of tags. "" = not known (values are passed on as they are).
TYPES = ("bool", "int", "float", "str")

#: Words read as true / false (English and Norwegian, as in the rest of the program).
TRUE_WORDS = {"1", "true", "on", "yes", "ja", "på", "pa", "sann", "high"}
FALSE_WORDS = {"0", "false", "off", "no", "nei", "av", "usann", "low", ""}


def coerce(value: Any, typ: str) -> Any:
    """Convert *value* to the tag type *typ* (ValueError when that is not possible).

    ``None`` stays ``None``; an empty *typ* returns the value unchanged.
    """
    if value is None or not typ:
        return value
    if typ == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        text = str(value).strip().lower()
        if text in TRUE_WORDS:
            return True
        if text in FALSE_WORDS:
            return False
        raise ValueError(_("{value} is not on/off").format(value=repr(value)))
    if typ == "int":
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, float):
            return round(value)
        return int(float(str(value).strip().replace(",", ".")))
    if typ == "float":
        if isinstance(value, (bool, int, float)):
            return float(value)
        return float(str(value).strip().replace(",", "."))
    if typ == "str":
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)
    raise ValueError(_("Unknown value type {type}").format(type=repr(typ)))


def type_of(value: Any) -> str:
    """The tag type that fits *value* best (``""`` for other values)."""
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    return ""


def same_value(a: Any, b: Any) -> bool:
    """Equal for change detection and echo suppression (floats compared loosely: 32-bit values round)."""
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b or (a == b and type(a) is type(b))
    if isinstance(a, float) or isinstance(b, float):
        try:
            return abs(float(a) - float(b)) <= 1e-4 * max(1.0, abs(float(a)), abs(float(b)))
        except (TypeError, ValueError):
            return False
    return a == b


def scale_value(value: Any, scale: float | None, offset: float | None) -> Any:
    """``value × scale + offset`` for numbers; other values unchanged."""
    if (scale is None and offset is None) or isinstance(value, bool) or not isinstance(value, (int, float)):
        return value
    return value * (1.0 if scale is None else scale) + (offset or 0.0)


def unscale_value(value: Any, scale: float | None, offset: float | None) -> Any:
    """The inverse of :func:`scale_value`."""
    if (scale is None and offset is None) or isinstance(value, bool) or not isinstance(value, (int, float)):
        return value
    s = 1.0 if scale is None else scale
    if s == 0:
        raise ValueError(_("scale 0 cannot be reversed"))
    return (value - (offset or 0.0)) / s


def as_float(value: Any) -> float | None:
    """A setting as a float, or None when it is empty."""
    if value is None or value == "":
        return None
    return float(value)


@dataclass
class Tag:
    """A named value of a connection.

    *address* is the protocol's own address (``hr:100``, a topic, a node id,
    a group address). *scale*/*offset* convert the device's raw value into
    the value the gateway sees: ``value = raw × scale + offset``.
    *extra* keeps the protocol-specific settings from ``protocols.yaml``.
    """

    name: str
    address: str = ""
    type: str = ""
    writable: bool = True
    unit: str = ""
    description: str = ""
    scale: float | None = None
    offset: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def from_raw(self, raw: Any) -> Any:
        """Raw device value → gateway value (scaled; the caller converts it to the tag type)."""
        return scale_value(raw, self.scale, self.offset)

    def to_raw(self, value: Any) -> Any:
        """Gateway value → raw device value (unscaled)."""
        return unscale_value(value, self.scale, self.offset)

    def info(self) -> dict[str, Any]:
        """What clients (the TUI) are told about the tag."""
        return {"name": self.name, "address": self.address, "type": self.type, "writable": self.writable,
                "unit": self.unit, "description": self.description}


def tag_from_spec(name: str, spec: dict[str, Any], address_key: str = "address") -> Tag:
    """A :class:`Tag` from its entry under ``tags:`` (common keys only; *extra* gets everything)."""
    typ = str(spec.get("type") or "")
    return Tag(
        name=name,
        address=str(spec.get(address_key) or ""),
        type=typ if typ in TYPES else "",
        writable=bool(spec.get("writable", True)),
        unit=str(spec.get("unit") or ""),
        description=str(spec.get("description") or ""),
        scale=as_float(spec.get("scale")),
        offset=as_float(spec.get("offset")),
        extra=dict(spec),
    )


class Driver:
    """Base class of every protocol driver (one instance per connection).

    Subclasses implement :meth:`session` (connect and serve until the
    connection is lost – raise to reconnect) and :meth:`write`. Servers also
    implement :meth:`create_tag`, so gateways can add tags to them.
    """

    #: Protocol name (key in the registry).
    protocol = ""
    #: True: the driver reads every tag after connecting and calls :meth:`set_ready` when done.
    #: Until then its values are *initial* (statuses, not changes) and gateways wait with writes to it.
    reads_first = False
    #: False: gateways never send initial values here, not even with ``initial: true``.
    #: KNX uses this – a start-up or a reconnect must never put telegrams on the bus.
    initial_allowed = True
    #: True when the device reports a written value back by itself (polling, subscription, bus echo).
    #: Otherwise the hub records a written value as the tag's new value.
    echoes = False

    def __init__(self, name: str, settings: dict[str, Any], hub: Hub) -> None:
        self.name = name
        self.settings = settings
        self.hub = hub
        self.role = str(settings.get("role") or "client")
        self.connected = False
        self.ready = False      # connected, and the first read is done
        self.error = ""
        self.tags: dict[str, Tag] = {}
        self._stop = False
        for tname, spec in (settings.get("tags") or {}).items():
            self.tags[str(tname)] = self.make_tag(str(tname), spec or {})

    # -- tags -----------------------------------------------------------------------------------
    def make_tag(self, name: str, spec: dict[str, Any]) -> Tag:
        """A tag from its ``tags:`` entry. Subclasses check addresses here (raise ValueError)."""
        return tag_from_spec(name, spec)

    def resolve(self, ref: str) -> Tag | None:
        """The tag called *ref*, or the first one with that address."""
        tag = self.tags.get(ref)
        if tag is not None:
            return tag
        return next((t for t in self.tags.values() if t.address and t.address == ref), None)

    def initial_default(self, tag: Tag) -> bool:
        """Do gateways send initial values to *tag* when the gateway does not say (``initial``)?

        Only to our own servers: filling our address space sends nothing to a device. A client
        tag is a device, which keeps its own value until something really changes.
        """
        return self.role == "server"

    def create_tag(self, name: str, spec: dict[str, Any]) -> Tag:
        """Add a tag that a gateway writes to. Clients can only use tags they have (ValueError)."""
        raise ValueError(_("{connection} has no tag {tag}").format(connection=self.name, tag=repr(name)))

    # -- life cycle -----------------------------------------------------------------------------
    async def run(self) -> None:
        """Run :meth:`session` until stopped, reconnecting with back-off after errors."""
        delay = 2.0
        while not self._stop:
            try:
                await self.session()
                delay = 2.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - report every problem and retry
                text = str(exc) or type(exc).__name__
                if self.connected or text != self.error:
                    self.hub.event("error", _("{connection}: {error}. Retrying in {delay} s").format(
                        connection=self.name, error=text, delay=int(delay)))
                self.set_state(False, text)
            finally:
                with contextlib.suppress(Exception):
                    await self.close()
            if self._stop:
                break
            if self.connected:
                self.set_state(False)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60.0)

    async def session(self) -> None:
        """Connect and serve until the connection is lost (raise) or the driver is stopped."""
        raise NotImplementedError

    async def close(self) -> None:
        """Release the connection (called after every session)."""

    async def stop(self) -> None:
        self._stop = True
        with contextlib.suppress(Exception):
            await self.close()

    async def wait_stopped(self, check: float = 1.0) -> None:
        """Sleep until :meth:`stop` is called (for drivers that only react to callbacks)."""
        while not self._stop:
            await asyncio.sleep(check)

    # -- values ---------------------------------------------------------------------------------
    async def write(self, tag: Tag, value: Any, user: str = "") -> None:
        """Write *value* (already converted to ``tag.type``) to the device, or serve it.

        *user* is who asked for it (``gw:<gateway>`` or the operator), for drivers that log writes.
        """
        raise NotImplementedError

    def publish(self, tag: Tag, raw: Any, quality: str = "good") -> None:
        """Report a raw value read from or sent by the other side."""
        try:
            value = coerce(tag.from_raw(raw), tag.type)
        except (TypeError, ValueError) as exc:
            log.debug("%s/%s: %r: %s", self.name, tag.name, raw, exc)
            value, quality = raw, "bad"
        self.hub.update(self.name, tag.name, value, quality)

    def set_state(self, connected: bool, error: str = "") -> None:
        """Report the connection state (the hub tells the clients and resets *initial* values)."""
        changed = connected != self.connected or error != self.error
        self.connected = connected
        self.error = "" if connected else error
        if not connected:
            self.ready = False
        if changed:
            self.hub.connection_state(self.name, connected)
        if connected and not self.reads_first:
            self.set_ready()

    def set_ready(self) -> None:
        """The first read after connecting is done: from now on values are changes."""
        if self.connected and not self.ready:
            self.ready = True
            self.hub.connection_ready(self.name)

    def describe(self) -> str:
        """Short text for the TUI: where the connection goes."""
        s = self.settings
        if s.get("endpoint"):
            return str(s["endpoint"])
        if s.get("device") and s.get("transport") == "rtu":
            return f"{s['device']} {s.get('baudrate', 9600)} {s.get('parity', 'N')}"
        if s.get("host"):
            return f"{s['host']}:{s.get('port', '')}".rstrip(":")
        return ""
