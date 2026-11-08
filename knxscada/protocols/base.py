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
import math
import struct
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from ..i18n import _

if TYPE_CHECKING:
    from .hub import Hub

log = logging.getLogger("knxscada.protocols")

#: Value types of tags. "" = not known (values are passed on as they are).
TYPES = ("bool", "int", "float", "str")

#: Words read as true / false (English and Norwegian, as in the rest of the program).
#: "" counts as false so an empty MQTT payload or text field switches off rather than failing.
TRUE_WORDS = {"1", "true", "on", "yes", "ja", "på", "pa", "sann", "high"}
FALSE_WORDS = {"0", "false", "off", "no", "nei", "av", "usann", "low", ""}


def coerce(value: Any, typ: str) -> Any:
    """Convert *value* to the tag type *typ* (ValueError when that is not possible).

    ``None`` stays ``None``; an empty *typ* returns the value unchanged. A list (an OPC UA array,
    e.g. a week program with one value per day) is converted element by element.
    """
    if value is None or not typ:
        return value
    if isinstance(value, (list, tuple)):
        return [coerce(v, typ) for v in value]
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
        # Never through float: a 64-bit counter or id above 2**53 would lose its last digits on the way to the
        # driver (9007199254740993 → …992). Floats and decimal text are rounded, as everywhere else.
        return as_int(value)
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


def same_value(a: Any, b: Any, rel: float = 1e-4) -> bool:
    """Equal for change detection and echo suppression (floats compared loosely: 32-bit values round).

    *rel* is the relative tolerance. The default suits echo suppression (a KNX 2-byte float comes back
    coarser than it was sent); for a large counter it hides real changes – 1e-4 of 147 912 kWh is 15 kWh.
    """
    if isinstance(a, (list, tuple)) or isinstance(b, (list, tuple)):
        return isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)) and len(a) == len(b) and \
            all(same_value(x, y, rel) for x, y in zip(a, b))
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b or (a == b and type(a) is type(b))
    if isinstance(a, float) or isinstance(b, float):
        try:
            fa, fb = float(a), float(b)
        except (TypeError, ValueError):
            return False
        if math.isnan(fa) and math.isnan(fb):   # NaN twice (a sensor fault value) is no change, or every poll would be one
            return True
        if fa == fb:                # also equal infinities, which the subtraction below cannot compare
            return True
        return abs(fa - fb) <= rel * max(1.0, abs(fa), abs(fb))
    return a == b


def as_int(value: Any) -> int:
    """*value* as an integer without going through float when it already is one: ``round(float(v))`` loses the last
    digits of 64-bit values above 2**53 (9007199254740993 → …992). Floats (a scaled value like 215.00000000000003) and
    numeric text are rounded (half to even, like ``round``). Raises ValueError/TypeError for anything else.

    Used by :func:`coerce` (every value written through the hub) and by the drivers that pack integers themselves.
    """
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        try:
            return int(text)
        except ValueError:
            pass
        # Decimal, not float: "9007199254740993.0" or "1,0e16" keep every digit. Decimal's own errors are not
        # ValueErrors, so they are turned into one (callers catch ValueError for "not a number").
        try:
            number = Decimal(text.replace(",", "."))
        except InvalidOperation:
            raise ValueError(_("{value} is not a number").format(value=repr(value))) from None
        if not number.is_finite():
            raise ValueError(_("{value} is not a number").format(value=repr(value)))
        return int(number.to_integral_value(rounding=ROUND_HALF_EVEN))
    return round(float(value))


def float32(value: float) -> float:
    """*value* as a 32-bit float stores it, rounded to 7 significant digits – exactly what the drivers report for a
    float32 value they read (OPC UA Float, BACnet REAL, Sparkplug Float), so the read-back of our own write compares
    equal to what was written. Values outside the float32 range are returned unchanged."""
    try:
        stored = struct.unpack("<f", struct.pack("<f", value))[0]
    except (OverflowError, struct.error):
        return value
    return float(f"{stored:.7g}")


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
    #: True: the first good value of a tag is a status even after the connection is ready
    #: (a polled tag that could not be read the first time).
    first_value_is_status = False
    #: False: gateways never send initial values here, not even with ``initial: true``.
    #: KNX uses this – a start-up or a reconnect must never put telegrams on the bus.
    initial_allowed = True
    #: True when the device reports a written value back by itself (polling, subscription, bus echo).
    #: Otherwise the hub records a written value as the tag's new value.
    echoes = False
    #: True when a write can carry a priority (BACnet's priority array, 1 = highest … 16 = lowest) and a written
    #: value can be released again. :meth:`write` then takes ``priority`` and ``release`` as keyword arguments;
    #: the hub refuses both for every other driver, so a user never believes a priority was applied when not.
    priorities = False

    def __init__(self, name: str, settings: dict[str, Any], hub: Hub) -> None:
        """Set up the driver of connection *name* (nothing is opened yet; :meth:`run` does that).

        *settings* is the connection's entry from ``protocols.yaml`` after :func:`.config.settings_of`
        (defaults and device template applied); *hub* is the :class:`~.hub.Hub` that receives values and
        events. The tags under ``tags:`` are built here with :meth:`make_tag`, so a bad address raises
        ValueError and the hub reports the connection as failed instead of starting it.
        """
        self.name = name
        self.settings = settings
        self.hub = hub
        #: "client" (we talk to a device) or "server" (others talk to us).
        self.role = str(settings.get("role") or "client")
        #: The session is up; set only through :meth:`set_state` so the hub is told.
        self.connected = False
        self.ready = False      # connected, and the first read is done
        #: Text of the last connection error, shown in the TUI ("" while connected).
        self.error = ""
        #: Tag name → tag. Servers may get more at run time through :meth:`create_tag`.
        self.tags: dict[str, Tag] = {}
        #: Set by :meth:`stop`; sessions and loops check it to end without reconnecting.
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
        """Stop for good: no further reconnects, and the connection is closed now.

        Called by the hub when the connection is removed or changed and when the server shuts down.
        Errors from :meth:`close` are swallowed – a dead connection must not block the shutdown.
        """
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

    def normalise(self, tag: Tag, value: Any) -> Any:
        """*value* as the device stores and reports it back, for change and echo detection in the gateways.

        The default is the value itself. A driver whose values are coarser overrides it (KNX: encoded and decoded
        with the tag's DPT), so the read-back of our own write is recognised as an echo without a loose tolerance.
        Must not raise: a value that cannot be converted is returned unchanged.
        """
        return value

    def normalise_raw(self, tag: Tag, value: Any, rounding=float32) -> Any:
        """:meth:`normalise` for a tag whose device stores less than a Python number: the value as it comes back after
        a write – to the raw value, through *rounding* (what the device keeps: :func:`float32` by default, a whole
        number for an integer register), back to the tag's value and type. Other values (bool, text, lists, None) are
        returned unchanged. Called by the drivers' :meth:`normalise`.

        Without this the gateways would need a loose tolerance to recognise a float32 echo, and a loose tolerance
        hides real steps of a large counter (1 000 000 → 1 000 001 Wh).
        """
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return value
        try:
            raw = tag.to_raw(value)
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                return value
            return coerce(tag.from_raw(rounding(raw)), tag.type)   # raw as it is: an int64 must not pass float
        except (TypeError, ValueError, ZeroDivisionError, OverflowError):
            return value

    async def set_quality(self, tag: Tag, quality: str) -> None:
        """Show that the value of *tag* (a gateway target of our own server) is not trustworthy now: *quality* is
        ``"bad"`` (its source reports a bad status) or ``"old"`` (its source connection is lost).

        Called by the gateway engine. The next value written (:meth:`write`) makes it good again. Only drivers that
        can show a quality to their clients override this (OPC UA server); the others keep the last value.
        """

    async def priority_array(self, tag: Tag) -> dict[str, Any]:
        """Who commands *tag* now: ``{"slots": [16 values, None = empty], "default": relinquish default}``.

        Only drivers with :attr:`priorities` (BACnet client) can answer; called by the hub for the write dialog.
        """
        raise ValueError(_("{connection} has no write priorities (only BACnet has)").format(connection=self.name))

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
