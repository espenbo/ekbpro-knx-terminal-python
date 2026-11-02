# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""BACnet/IP: client of a BACnet device (``role: client``), or our own BACnet device (``role: server``).

Objects
-------
A tag names a BACnet object and, if not the present value, a property::

    object: av:3                 analog-value 3 (short names: ai ao av bi bo bv msi mso msv csv iv piv lav acc)
    object: analog-input,12      the long names work too, also analogInput:12
    property: status-flags       default present-value

Client
------
The connection talks to one device: ``address`` (``192.168.1.20``,
``192.168.1.20:47809``, or ``2:5`` for MS/TP device 5 behind a router on
network 2), or ``device_id`` alone, found with Who-Is. Tags are read every
``poll_ms`` with ReadPropertyMultiple (one by one when the device does not
support it); ``cov: true`` also subscribes to changes of the present values
(COV), so changes arrive at once. Writes go with the tag's or the connection's
``priority`` (empty = the device's default, 16 for commandable objects).
The client uses its own UDP port (``local_port: 0`` = any free port), so it can
run on the same machine as our BACnet device on 47808. Without ``local_address``
it binds to the address the system routes to the device from (the VPN address for
a site reached over Tailscale): bound to the LAN address, the request would carry
that as its source and the answer would be routed somewhere else.

Server
------
We are a BACnet device (``device_instance``, ``device_name``) that BMS and
SCADA systems read. Every gateway target becomes an object named after the
target: numbers → analog-value, on/off → binary-value, text →
characterstring-value. Its instance number is kept in
``var/bacnet/<connection>.json``, so it stays the same when gateways change –
or given in the gateway line or under ``tags:`` (``object: av:10``, also
``ai``/``bi`` for read-only values and ``msv`` with ``states: 4``). Targets
mapped with ``direction: both`` are writable; what a BMS writes to the
present value goes back to the source. Other properties cannot be written.
Changes are sent to COV subscribers.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import ipaddress
import json
import re
import socket
from pathlib import Path
from typing import Any

from ..i18n import _
from .base import Driver as BaseDriver
from .base import Tag, tag_from_spec

#: Short object type names → BACnet names.
SHORT = {"ai": "analog-input", "ao": "analog-output", "av": "analog-value", "bi": "binary-input",
         "bo": "binary-output", "bv": "binary-value", "msi": "multi-state-input", "mi": "multi-state-input",
         "mso": "multi-state-output", "mo": "multi-state-output", "msv": "multi-state-value",
         "mv": "multi-state-value", "csv": "characterstring-value", "iv": "integer-value",
         "piv": "positive-integer-value", "lav": "large-analog-value", "acc": "accumulator", "dev": "device"}

#: BACnet name → the short name shown in tag addresses.
ABBREVIATION = {long: short for short, long in reversed(SHORT.items())}

#: An object reference: a type name (short, long or camelCase) and an instance, separated by ":", "," or a space.
OBJECT_RE = re.compile(r"^\s*([a-z][a-z-]*?)\s*[:,\s]?\s*(\d+)\s*$", re.IGNORECASE)

#: Object types our device can have, and the value type of their present value.
SERVER_TYPES = {"analog-value": "float", "analog-input": "float", "binary-value": "bool", "binary-input": "bool",
                "multi-state-value": "int", "multi-state-input": "int", "characterstring-value": "str"}

#: Unit text (as in the TUI) → BACnet engineering unit.
UNITS = {"°c": "degreesCelsius", "c": "degreesCelsius", "k": "degreesKelvin", "%": "percent",
         "%rh": "percentRelativeHumidity", "w": "watts", "kw": "kilowatts", "wh": "wattHours",
         "kwh": "kilowattHours", "kvarh": "kilowattHoursReactive", "var": "voltAmperesReactive", "v": "volts",
         "a": "amperes", "hz": "hertz", "m3/h": "cubicMetersPerHour", "m³/h": "cubicMetersPerHour",
         "l/s": "litersPerSecond", "pa": "pascals", "kpa": "kilopascals", "bar": "bars", "ppm": "partsPerMillion",
         "lux": "luxes", "lx": "luxes", "s": "seconds", "min": "minutes", "h": "hours", "m/s": "metersPerSecond",
         "w/m2": "wattsPerSquareMeter", "w/m²": "wattsPerSquareMeter"}

#: Highest device / object instance number (4194303 is the wildcard).
MAX_INSTANCE = 4194302
#: The wildcard device instance: a device asked for "device,4194303" answers as itself.
WILDCARD_INSTANCE = 4194303


def parse_object(text: str) -> tuple[str, int]:
    """``"av:3"`` → ``("analog-value", 3)``; ``"analogInput,1"`` → ``("analog-input", 1)``. ValueError if unclear."""
    m = OBJECT_RE.match(str(text))
    if not m:
        raise ValueError(_("BACnet object {object}: write e.g. av:3, bi:1 or analog-value,3").format(
            object=repr(str(text))))
    name = re.sub(r"(?<=[a-z])(?=[A-Z])", "-", m.group(1)).lower()
    name = SHORT.get(name, name)
    instance = int(m.group(2))
    if not 0 <= instance <= MAX_INSTANCE:
        raise ValueError(_("BACnet object {object}: the instance must be 0–{max}").format(
            object=repr(str(text)), max=MAX_INSTANCE))
    return name, instance


def object_text(name: str, instance: int) -> str:
    """The address shown for an object: ``av:3``, or ``accumulator,1`` for types without a short name."""
    short = ABBREVIATION.get(name)
    return f"{short}:{instance}" if short else f"{name},{instance}"


def from_bacnet(value: Any) -> Any:
    """A value read from BACnet → a plain Python value."""
    from bacpypes3.basetypes import BinaryPV
    from bacpypes3.primitivedata import Boolean, Double, Null, Real

    if value is None or isinstance(value, Null):
        return None
    if isinstance(value, BinaryPV):
        return int(value) == 1
    if isinstance(value, Boolean):
        return bool(value)
    if isinstance(value, Real) and not isinstance(value, Double):   # float32: 1.688, not 1.687999963760376
        return float(f"{float(value):.7g}")
    if isinstance(value, float):
        return float(value)
    if isinstance(value, int):
        return int(value)
    if isinstance(value, str):
        return str(value)
    return str(value)


def to_bacnet(object_type: str, prop: str, raw: Any) -> Any:
    """A raw value → what the present value of *object_type* takes (other properties: unchanged)."""
    if prop != "present-value":
        return raw
    if object_type.startswith("binary"):
        return 1 if raw and str(raw).strip().lower() not in ("0", "false", "off", "inactive", "av", "nei") else 0
    if object_type.startswith(("multi-state", "positive-integer", "integer")):
        return round(float(raw))
    if object_type.startswith(("analog", "large-analog")):
        return float(raw)
    if object_type == "characterstring-value":
        return "" if raw is None else str(raw)
    return raw


def route_source(address: str) -> str | None:
    """Our IP address towards the device at *address* (``192.168.1.20`` or ``192.168.1.20:47809``), as
    ``ip/32`` – None for a network address (``2:5``) or a name that does not resolve."""
    host = address.rsplit(":", 1)[0] if address.count(":") == 1 else address
    try:
        ipaddress.IPv4Address(host)          # strict: inet_aton would take "2" (network 2 of "2:5") for 0.0.0.2
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((host, 47808))         # no packet is sent: this only asks the routing table
            ip = s.getsockname()[0]
    except (OSError, ValueError):
        return None
    return f"{ip}/32" if ip and not ip.startswith("0.") else None


def make_app(local: str, instance: int, name: str, timeout_s: float = 3, bbmd: str = "", bbmd_ttl: int = 300):
    """A bacpypes3 application: a device *instance* called *name* on the UDP address *local* (``host:0``,
    ``192.168.1.10/24:47808``), registered as a foreign device at *bbmd* if given."""
    from bacpypes3.app import Application
    from bacpypes3.basetypes import HostNPort, IPMode
    from bacpypes3.local.device import DeviceObject
    from bacpypes3.local.networkport import NetworkPortObject

    device = DeviceObject(objectIdentifier=("device", instance), objectName=name, vendorIdentifier=999,
                          vendorName="EKBPRO", modelName="knxscada gateway",
                          apduTimeout=round(timeout_s * 1000), numberOfApduRetries=1)
    port = NetworkPortObject(local, objectIdentifier=("network-port", 1), objectName="NetworkPort-1")
    if bbmd:
        port.bacnetIPMode = IPMode.foreign
        port.fdBBMDAddress = HostNPort(bbmd)
        port.fdSubscriptionLifetime = bbmd_ttl
    return Application.from_object_list([device, port])


def _failures() -> tuple[type[BaseException], ...]:
    """What a request can raise. bacpypes3 raises the device's error, reject and abort answers as
    ``ErrorRejectAbortNack``, which is a BaseException – ``except Exception`` does not catch it."""
    from bacpypes3.apdu import ErrorRejectAbortNack

    return Exception, ErrorRejectAbortNack


def _no_answer(exc: BaseException) -> bool:
    """The device did not answer (as opposed to answering with an error such as unknown-object)."""
    return isinstance(exc, (TimeoutError, OSError)) or "no-response" in str(exc).lower()


class Driver(BaseDriver):
    """One BACnet/IP connection: a client of one remote device, or our own BACnet device (``role``).

    Client: polls its tags (and optionally follows them with COV), writes with a priority. Server: every
    tag is a local object of our device; tags come from ``tags:`` and from gateways (:meth:`create_tag`).
    """

    protocol = "bacnet"
    #: Writes go into a slot of the device's priority array; the hub passes a chosen priority or a release.
    priorities = True

    def __init__(self, name: str, settings: dict[str, Any], hub) -> None:
        """Set up the connection *name*; nothing is opened until :meth:`session`.

        ``objects`` must exist before the base class builds the tags, because :meth:`make_tag` fills it.
        """
        self.objects: dict[str, tuple[str, int]] = {}   # tag name → (object type, instance)
        super().__init__(name, settings, hub)
        self.echoes = self.role == "client"   # the next poll (or COV) reports a written value
        # Client: the first poll only reports statuses, so a reconnect never makes gateways send anything on.
        self.reads_first = self.role == "client"
        # A tag that failed the first poll (device busy, object missing) is still a status when it later appears.
        self.first_value_is_status = self.role == "client"
        self.app = None
        self.target = None          # Address of the device (client)
        self.identity = ""
        self.rpm = True             # the device answers ReadPropertyMultiple
        self.local: dict[str, Any] = {}   # tag name → our local object (server)
        self._tasks: list[asyncio.Task] = []
        self._numbers: dict[str, str] = {}   # server: target name → "av:3", kept in var/bacnet/<name>.json

    # -- tags -------------------------------------------------------------------------------------
    def make_tag(self, name: str, spec: dict[str, Any]) -> Tag:
        """A tag from its ``tags:`` entry (or a gateway target): parse ``object``/``property`` into ``objects``.

        The address shown is the short form (``av:3``, plus the property when it is not the present value).
        Raises ValueError for an unreadable object, or for an object type our own device cannot have.
        """
        tag = tag_from_spec(name, spec, "object")
        if self.role == "server" and not spec.get("object"):
            # A server tag without an object gets one when the device starts (see _object_of).
            tag.address = ""
            return tag
        otype, instance = parse_object(str(spec.get("object") or name))
        self.objects[name] = (otype, instance)
        tag.address = object_text(otype, instance)
        prop = str(spec.get("property") or "present-value")
        if prop != "present-value":
            tag.address += f" {prop}"
        if self.role == "server" and otype not in SERVER_TYPES:
            raise ValueError(_("{connection}: our BACnet device can have {types}, not {object}").format(
                connection=self.name, types=", ".join(ABBREVIATION[t] for t in SERVER_TYPES), object=otype))
        if self.role == "client" and otype.endswith("-input") and prop == "present-value":
            tag.writable = bool(spec.get("writable", False))   # inputs are only writable when out of service
        return tag

    def resolve(self, ref: str) -> Tag | None:
        """The tag called *ref*, or the tag of the object *ref* in any spelling (``analogValue:3`` finds ``av:3``)."""
        tag = super().resolve(ref)
        if tag is None:
            try:
                key = object_text(*parse_object(ref))
            except ValueError:
                return None
            tag = next((t for t in self.tags.values() if t.address == key), None)
        return tag

    def create_tag(self, name: str, spec: dict[str, Any]) -> Tag:
        """Add a gateway target as an object of our device (server); clients refuse (base class).

        ``writable`` is off unless the gateway says so (``direction: both``), so a BMS cannot write to a
        value that only flows out. While the device runs, the object is created at once.
        """
        if self.role != "server":
            return super().create_tag(name, spec)
        tag = self.make_tag(name, spec)
        tag.writable = bool(spec.get("writable"))
        self.tags[name] = tag
        if self.app is not None:
            self._add_object(tag)
        return tag

    def _prop(self, tag: Tag) -> str:
        """The BACnet property the tag reads and writes (``present-value`` unless ``property`` says otherwise)."""
        return str(tag.extra.get("property") or "present-value")

    # -- connection -------------------------------------------------------------------------------
    def describe(self) -> str:
        """Short text for the TUI: our device instance and UDP address (server), or the device and its identity."""
        s = self.settings
        if self.role == "server":
            return _("device {n}").format(n=s.get("device_instance")) + f"  {self._local_address()}"
        where = str(s.get("address") or "") or _("device {n}").format(n=s.get("device_id"))
        return where + (f"  · {self.identity}" if self.identity else "")

    def _local_address(self) -> str:
        """The UDP address our bacpypes3 application binds to: ``host:port`` or ``ip/prefix:port``.

        Without ``local_address`` a client binds to the address routed towards the device (see the module
        docstring: over a VPN the answer would otherwise go to the LAN address). ``host`` lets bacpypes3 pick
        the default interface. Servers default to the standard port 47808, clients to any free port, so both
        can run on one machine.
        """
        s = self.settings
        host = str(s.get("local_address") or "")
        if not host and self.role != "server" and s.get("address"):
            host = route_source(str(s["address"])) or ""
        host = host or "host"
        default = 47808 if self.role == "server" else 0
        port = s.get("local_port")
        port = default if port in (None, "") else int(port)
        # An address that already carries a port (``192.168.1.10/24:47809``) is used as it is.
        return host if ":" in host.rsplit("/", 1)[-1] else f"{host}:{port}"

    def _make_app(self, instance: int, name: str):
        """A bacpypes3 application as device *instance* named *name*, with this connection's address, APDU
        timeout and BBMD (foreign-device registration) settings."""
        s = self.settings
        return make_app(self._local_address(), instance, name, float(s.get("timeout_s") or 3),
                        str(s.get("bbmd") or ""), int(s.get("bbmd_ttl") or 300))

    async def session(self) -> None:
        """Run the client or the server until the connection is lost or the driver stops; COV tasks end with it."""
        try:
            if self.role == "server":
                await self._serve()
            else:
                await self._client_session()
        finally:
            for task in self._tasks:
                task.cancel()
            self._tasks = []

    async def close(self) -> None:
        """Close the UDP endpoint and drop our local objects (they are made again on the next session)."""
        if self.app is not None:
            app, self.app = self.app, None
            with contextlib.suppress(Exception):
                app.close()
        self.local.clear()

    # -- client -----------------------------------------------------------------------------------
    async def _client_session(self) -> None:
        """Find the device (``address`` or Who-Is for ``device_id``), identify it, then poll every ``poll_ms``.

        A device that does not answer the identification at all fails the session (retried later). ``rpm`` is
        reset on each connect so a device whose firmware was updated gets ReadPropertyMultiple again. The poll
        interval is measured from the start of each poll, so slow reads do not stretch the period.
        """
        from bacpypes3.pdu import Address

        s = self.settings
        self.app = self._make_app(int(s.get("client_instance") or MAX_INSTANCE), f"knxscada {self.name}")
        await asyncio.sleep(0)   # let the UDP endpoint open
        device_id = s.get("device_id")
        if s.get("address"):
            self.target = Address(str(s["address"]))
        elif device_id not in (None, ""):
            iams = await self.app.who_is(int(device_id), int(device_id), timeout=3)
            if not iams:
                raise ConnectionError(_("device {n} does not answer Who-Is").format(n=device_id))
            self.target = iams[0].pduSource
        else:
            raise ValueError(_("{connection}: set address or device_id").format(connection=self.name))
        self.identity = await self._identify(device_id)
        self.rpm = True
        self.set_state(True)
        if s.get("cov"):
            self._tasks = [asyncio.create_task(self._cov(tag)) for tag in self.tags.values()
                           if tag.name in self.objects and self._prop(tag) == "present-value"]
        interval = max(int(s.get("poll_ms") or 5000), 100) / 1000
        while not self._stop:
            started = asyncio.get_running_loop().time()
            await self._poll()
            self.set_ready()
            await asyncio.sleep(max(0.0, interval - (asyncio.get_running_loop().time() - started)))

    async def _identify(self, device_id: Any) -> str:
        """The device's instance, name and vendor; "" when it does not say.

        Without ``device_id`` the device is asked with a Who-Is sent to its address, then with the
        wildcard instance 4194303 (which most devices answer as themselves).
        """
        if device_id in (None, ""):
            with contextlib.suppress(*_failures()):
                iams = await self.app.who_is(address=self.target, timeout=2)
                if iams:
                    device_id = iams[0].iAmDeviceIdentifier[1]
        oid = f"device,{int(device_id) if device_id not in (None, '') else WILDCARD_INSTANCE}"
        try:
            name = await self.app.read_property(self.target, oid, "object-name")
        except _failures() as exc:
            if _no_answer(exc):
                raise ConnectionError(_("no answer from {where}").format(where=self.target)) from None
            return ""
        parts = [str(name)]
        if device_id not in (None, ""):
            parts.append(_("device {n}").format(n=device_id))
        with contextlib.suppress(*_failures()):
            parts.append(str(await self.app.read_property(self.target, oid, "vendor-name")))
        return "  ".join(parts)

    async def _poll(self) -> None:
        """Read every tag once, ``max_per_request`` objects per ReadPropertyMultiple.

        A refused RPM switches to single reads for the rest of the session; no answer at all raises
        ConnectionError so the session reconnects and the values turn "old".
        """
        tags = [t for t in self.tags.values() if t.name in self.objects]
        size = max(1, int(self.settings.get("max_per_request") or 20))
        for i in range(0, len(tags), size):
            chunk = tags[i:i + size]
            if self.rpm and len(chunk) > 1:
                try:
                    await self._read_multiple(chunk)
                    continue
                except _failures() as exc:   # not supported, answer too big …: read one by one from now on
                    if _no_answer(exc):
                        raise ConnectionError(_("no answer from {where}").format(where=self.target)) from None
                    self.rpm = False
            for tag in chunk:
                await self._read_one(tag)

    async def _read_multiple(self, tags: list[Tag]) -> None:
        """Read *tags* with one ReadPropertyMultiple and publish them; a property answered with an error is "bad"."""
        params: list[Any] = []
        for tag in tags:
            otype, instance = self.objects[tag.name]
            params += [f"{otype},{instance}", [self._prop(tag)]]
        result = await self.app.read_property_multiple(self.target, params)
        from bacpypes3.primitivedata import ErrorType

        by_key = {(str(oid[0]), int(oid[1]), str(prop)): value for oid, prop, _index, value in result}
        for tag in tags:
            otype, instance = self.objects[tag.name]
            value = by_key.get((otype, instance, self._prop(tag)))
            if value is None or isinstance(value, ErrorType):
                self._bad(tag)
            else:
                self.publish(tag, from_bacnet(value))

    async def _read_one(self, tag: Tag) -> None:
        """Read one tag with ReadProperty and publish it (an error answer marks it "bad", no answer reconnects)."""
        otype, instance = self.objects[tag.name]
        try:
            value = await self.app.read_property(self.target, f"{otype},{instance}", self._prop(tag))
        except _failures() as exc:
            if _no_answer(exc):
                raise ConnectionError(_("no answer from {where}").format(where=self.target)) from None
            self._bad(tag)   # unknown object or property, …
            return
        self.publish(tag, from_bacnet(value))

    def _bad(self, tag: Tag) -> None:
        """Mark *tag* as "bad" but keep its last value for display (bad values are never passed to gateways)."""
        rec = self.hub.values.get(f"{self.name}/{tag.name}")
        self.hub.update(self.name, tag.name, rec["v"] if rec else None, "bad")

    async def _cov(self, tag: Tag) -> None:
        """Follow the present value of one object with COV notifications (polling goes on as a fallback)."""
        from bacpypes3.primitivedata import ObjectIdentifier

        otype, instance = self.objects[tag.name]
        lifetime = int(self.settings.get("cov_lifetime_s") or 300)
        try:
            async with self.app.change_of_value(self.target, ObjectIdentifier(f"{otype},{instance}"), None,
                                                False, lifetime) as sub:
                while not self._stop:
                    prop, value = await sub.get_value()
                    if str(prop) == "present-value":
                        self.publish(tag, from_bacnet(value))
        except asyncio.CancelledError:
            raise
        except _failures() as exc:   # the device does not do COV for it: polled instead
            self.hub.event("info", _("{connection}: no COV for {tag} ({error}) – it is polled").format(
                connection=self.name, tag=tag.name, error=exc))

    # -- server -----------------------------------------------------------------------------------
    def _numbers_file(self) -> Path:
        """``var/bacnet/<connection>.json`` next to the site's config: the object numbers given to targets."""
        return self.hub.server.cfg.resolve(f"var/bacnet/{self.name}.json")

    def _load_numbers(self) -> None:
        """Read the kept object numbers; a missing or broken file starts afresh (numbers are then handed out again)."""
        try:
            self._numbers = json.loads(self._numbers_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._numbers = {}

    def _save_numbers(self) -> None:
        """Write the object numbers (sorted, so the file diffs well). A failure is ignored: the device still runs,
        only the numbers may change after the next restart."""
        path = self._numbers_file()
        with contextlib.suppress(OSError):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(self._numbers, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")

    def _object_of(self, tag: Tag) -> tuple[str, int]:
        """The object of a server tag: given, kept from an earlier start, or the next free number of its type."""
        if tag.name in self.objects:
            return self.objects[tag.name]
        otype = {"bool": "binary-value", "str": "characterstring-value"}.get(tag.type, "analog-value")
        kept = self._numbers.get(tag.name)
        if kept:
            with contextlib.suppress(ValueError):
                k_type, k_inst = parse_object(kept)
                if k_type == otype and (k_type, k_inst) not in self.objects.values():
                    self.objects[tag.name] = (k_type, k_inst)
                    return k_type, k_inst
        # A number once given is never given to another target: the BMS may still have it in its list.
        used = {i for t, i in self.objects.values() if t == otype}
        for text in self._numbers.values():
            with contextlib.suppress(ValueError):
                t, i = parse_object(text)
                if t == otype:
                    used.add(i)
        instance = next(i for i in range(1, MAX_INSTANCE) if i not in used)
        self.objects[tag.name] = (otype, instance)
        self._numbers[tag.name] = object_text(otype, instance)
        self._save_numbers()
        return otype, instance

    async def _serve(self) -> None:
        """Be our BACnet device: create the application and one object per tag, then wait until stopped.

        Objects get the tag's last value from the hub (if any) so a BMS does not read zeros after the session
        is restarted; this only fills our own address space and sends nothing to any device.
        """
        s = self.settings
        instance = s.get("device_instance")
        if instance in (None, ""):
            raise ValueError(_("{connection}: device_instance is missing").format(connection=self.name))
        self._load_numbers()
        self.app = self._make_app(int(instance), str(s.get("device_name") or f"knxscada {self.name}"))
        for tag in list(self.tags.values()):
            self._add_object(tag)
        await asyncio.sleep(0)
        self.set_state(True)
        await self.wait_stopped()

    def _add_object(self, tag: Tag) -> None:
        """Make the local object of *tag* (the name may not be used twice in a device: it is the tag name)."""
        if tag.name in self.local or self.app is None:
            return
        otype, instance = self._object_of(tag)
        tag.address = object_text(otype, instance)
        try:
            obj = _local_class(otype)(self, tag, otype, instance)
            self.app.add_object(obj)
        except Exception as exc:  # noqa: BLE001 - name or number used twice, bad unit …
            self.hub.event("error", _("{connection}: object {object} for {tag}: {error}").format(
                connection=self.name, object=tag.address, tag=tag.name, error=exc))
            return
        self.local[tag.name] = obj
        rec = self.hub.values.get(f"{self.name}/{tag.name}")
        if rec is not None and rec.get("v") is not None:
            with contextlib.suppress(TypeError, ValueError):
                self._set_local(tag, tag.to_raw(rec["v"]))

    def _set_local(self, tag: Tag, raw: Any) -> None:
        """Set the present value of our object for *tag* to *raw* (COV subscribers are notified by bacpypes3).

        Binary objects take "active"/"inactive"; a multi-state value outside 1…numberOfStates raises ValueError
        rather than being clipped, so a wrong mapping is seen. Does nothing before the object exists.
        """
        obj = self.local.get(tag.name)
        if obj is None:
            return
        otype = self.objects[tag.name][0]
        value = to_bacnet(otype, "present-value", raw)
        if otype.startswith("binary"):
            value = "active" if value else "inactive"
        elif otype.startswith("multi-state") and not 1 <= value <= obj.numberOfStates:
            raise ValueError(_("{value} is not a state 1–{n}").format(value=value, n=obj.numberOfStates))
        obj.presentValue = value

    async def remote_write(self, tag: Tag, value: Any) -> None:
        """A BMS wrote the present value of one of our objects."""
        self.publish(tag, from_bacnet(value))

    # -- values -----------------------------------------------------------------------------------
    async def write(self, tag: Tag, value: Any, user: str = "", priority: int | None = None,
                    release: bool = False) -> None:
        """Write *value* to the device (client) or to our own object (server).

        Client: WriteProperty with a priority – *priority* when the user chose one, else the tag's or the
        connection's ``priority`` (none: the device's default, 16 for commandable objects). *release* writes Null
        into that slot, which removes our command: the value with the next lower priority, or the relinquish
        default, takes over. Our own objects (server) have no priority array, so a priority is ignored there and a
        release is refused.
        """
        if self.role == "server":
            if release:
                raise ValueError(_("{tag}: our own objects have no priority array to release").format(tag=tag.name))
            self._set_local(tag, tag.to_raw(value))
            return
        if self.app is None or not self.connected:
            raise ConnectionError(_("{connection} is not connected").format(connection=self.name))
        otype, instance = self.objects[tag.name]
        prop = self._prop(tag)
        if priority is None:
            priority = tag.extra.get("priority", self.settings.get("priority"))
        if release:
            from bacpypes3.primitivedata import Null

            value = Null(())
            # A release must name a slot: without a configured one it is the slot a plain write would have used.
            priority = priority if priority not in (None, "") else 16
        else:
            value = to_bacnet(otype, prop, tag.to_raw(value))
        from bacpypes3.apdu import ErrorRejectAbortNack

        try:
            result = await self.app.write_property(self.target, f"{otype},{instance}", prop, value,
                                                   priority=int(priority) if priority not in (None, "") else None)
        except ErrorRejectAbortNack as exc:
            if _no_answer(exc):
                raise ConnectionError(_("no answer from {where}").format(where=self.target)) from None
            raise RuntimeError(_("{tag}: the device refused the write ({error})").format(tag=tag.name, error=exc)) from None
        if result is not None:
            raise RuntimeError(_("{tag}: the device refused the write ({error})").format(tag=tag.name, error=result))


@functools.cache
def _local_class(otype: str):
    """A local object class of *otype* whose present value a BMS may write only when the tag is writable."""
    from bacpypes3.errors import ExecutionError
    from bacpypes3.local import analog, binary, multistate
    from bacpypes3.local.object import Object as LocalObject
    from bacpypes3.object import CharacterStringValueObject

    class CharacterStringValue(LocalObject, CharacterStringValueObject):
        """A local characterstring-value object (bacpypes3 has no ready-made local class for it)."""


    base = {"analog-value": analog.AnalogValueObject, "analog-input": analog.AnalogInputObject,
            "binary-value": binary.BinaryValueObject, "binary-input": binary.BinaryInputObject,
            "multi-state-value": multistate.MultiStateValueObject,
            "multi-state-input": multistate.MultiStateInputObject,
            "characterstring-value": CharacterStringValue}[otype]

    class Point(base):
        """Our object for one tag: named after the tag, with unit, COV increment and number of states from it."""

        def __init__(self, driver: Driver, tag: Tag, otype: str, instance: int) -> None:
            """Create the object (*otype*, *instance*) for *tag* of *driver*, with a neutral start value.

            The start value (0, inactive, state 1, "") only lives until :meth:`Driver._add_object` sets the
            last known value; ``cov_increment`` (default 0.1) and ``states`` (default 16) come from the tag.
            """
            kwargs: dict[str, Any] = {"objectIdentifier": (otype, instance), "objectName": tag.name,
                                      "statusFlags": [0, 0, 0, 0], "outOfService": False}
            if tag.description:
                kwargs["description"] = tag.description
            if otype.startswith("analog"):
                kwargs.update(presentValue=0.0, covIncrement=float(tag.extra.get("cov_increment") or 0.1),
                              units=UNITS.get(tag.unit.strip().lower(), "noUnits"))
            elif otype.startswith("binary"):
                kwargs.update(presentValue="inactive")
            elif otype.startswith("multi-state"):
                kwargs.update(presentValue=1, numberOfStates=int(tag.extra.get("states") or 16))
            else:
                kwargs.update(presentValue="")
            super().__init__(**kwargs)
            self._driver, self._tag = driver, tag

        async def write_property(self, attr, value, index=None, priority=None):
            """A BMS writes a property: only the present value of a writable tag is allowed.

            Everything else is answered with writeAccessDenied. An accepted write is passed to the driver,
            which publishes it, so the gateway sends it back to the source (``direction: both``).
            """
            name = attr if isinstance(attr, str) else self._property_identifier_class(attr).attr
            if name != "presentValue" or not self._tag.writable:
                raise ExecutionError(errorClass="property", errorCode="writeAccessDenied")
            await super().write_property(attr, value, index, priority)
            await self._driver.remote_write(self._tag, self.presentValue)

    Point.__name__ = base.__name__
    return Point


# -- scan (./scada bacnet-scan) -------------------------------------------------------------------

#: BACnet engineering unit → the unit text written in tags (the first spelling of UNITS, as shown in the TUI).
UNIT_TEXT = {"degrees-celsius": "°C", "degrees-kelvin": "K", "percent": "%", "percent-relative-humidity": "%RH",
             "watts": "W", "kilowatts": "kW", "watt-hours": "Wh", "kilowatt-hours": "kWh",
             "kilowatt-hours-reactive": "kvarh", "volt-amperes-reactive": "var", "volts": "V", "amperes": "A",
             "hertz": "Hz", "cubic-meters-per-hour": "m³/h", "liters-per-second": "l/s", "pascals": "Pa",
             "kilopascals": "kPa", "bars": "bar", "parts-per-million": "ppm", "luxes": "lx", "seconds": "s",
             "minutes": "min", "hours": "h", "meters-per-second": "m/s", "watts-per-square-meter": "W/m²"}

#: What the scan reads of each object.
SCAN_PROPERTIES = ("object-name", "present-value", "units", "description")


async def scan_devices(app, timeout_s: float = 3, low: int | None = None, high: int | None = None) -> list[dict]:
    """Who-Is on the network: ``[{instance, address, name, vendor}]`` sorted by instance."""
    found: dict[int, dict] = {}
    for iam in await app.who_is(low, high, timeout=timeout_s):
        instance = int(iam.iAmDeviceIdentifier[1])
        if instance in found:
            continue
        info = {"instance": instance, "address": str(iam.pduSource), "name": "", "vendor": ""}
        for key, prop in (("name", "object-name"), ("vendor", "vendor-name")):
            with contextlib.suppress(*_failures()):
                info[key] = str(await app.read_property(iam.pduSource, f"device,{instance}", prop))
        found[instance] = info
    return [found[i] for i in sorted(found)]


async def find_device(app, address: str = "", device_id: int | None = None, timeout_s: float = 3):
    """The address and instance of one device: from *address* (asked with a Who-Is to it) or *device_id*."""
    from bacpypes3.pdu import Address

    if address:
        target = Address(address)
        if device_id is None:
            iams = await app.who_is(address=target, timeout=timeout_s)
            if iams:
                device_id = int(iams[0].iAmDeviceIdentifier[1])
            else:
                # Many devices (Siemens Climatix, Belimo) answer a directed Who-Is with a *broadcast* I-Am, which
                # never comes back over a VPN or a routed network. Asking the wildcard instance 4194303 ("this
                # device") for its identifier is a plain unicast request/answer, so it works there too.
                try:
                    oid = await app.read_property(target, f"device,{WILDCARD_INSTANCE}", "object-identifier")
                except _failures():
                    raise ConnectionError(_("no answer from {where}").format(where=address)) from None
                device_id = int(oid[1])
        return target, device_id
    if device_id is None:
        raise ValueError(_("give an address or --device"))
    iams = await app.who_is(device_id, device_id, timeout=timeout_s)
    if not iams:
        raise ConnectionError(_("device {n} does not answer Who-Is").format(n=device_id))
    return iams[0].pduSource, device_id


async def scan_objects(app, target, device_id: int) -> list[dict]:
    """The objects of a device: ``[{object, type, instance, name, value, unit, description}]``.

    The object list is read at once, or entry by entry when the device cannot send it in one answer
    (no segmentation). Each object's name, present value, units and description are read with
    ReadPropertyMultiple, or one by one when the device does not support it.
    """
    dev = f"device,{device_id}"
    try:
        oids = list(await app.read_property(target, dev, "object-list"))
    except _failures() as exc:
        if _no_answer(exc):
            raise ConnectionError(_("no answer from {where}").format(where=target)) from None
        count = int(await app.read_property(target, dev, "object-list", 0))
        oids = [await app.read_property(target, dev, "object-list", i) for i in range(1, count + 1)]
    objects = [(str(oid[0]), int(oid[1])) for oid in oids if str(oid[0]) not in ("device", "network-port")]
    found: dict[tuple[str, int], dict[str, Any]] = {o: {} for o in objects}
    rpm = True
    for i in range(0, len(objects), 10):
        chunk = objects[i:i + 10]
        if rpm:
            try:
                result = await app.read_property_multiple(
                    target, [p for o in chunk for p in (f"{o[0]},{o[1]}", list(SCAN_PROPERTIES))])
                from bacpypes3.primitivedata import ErrorType

                for oid, prop, _index, value in result:
                    if not isinstance(value, ErrorType):
                        found.setdefault((str(oid[0]), int(oid[1])), {})[str(prop)] = value
                continue
            except _failures() as exc:
                if _no_answer(exc):
                    raise ConnectionError(_("no answer from {where}").format(where=target)) from None
                rpm = False
        for o in chunk:
            for prop in SCAN_PROPERTIES:
                with contextlib.suppress(*_failures()):
                    found[o][prop] = await app.read_property(target, f"{o[0]},{o[1]}", prop)
    rows = []
    for otype, instance in objects:
        props = found[(otype, instance)]
        units = str(props["units"]) if "units" in props else ""
        rows.append({"object": object_text(otype, instance), "type": otype, "instance": instance,
                     "name": str(props.get("object-name", "")),
                     "value": from_bacnet(props["present-value"]) if "present-value" in props else None,
                     "unit": UNIT_TEXT.get(units, "" if units in ("", "no-units") else units),
                     "description": str(props.get("description", ""))})
    return rows


def tags_yaml(rows: list[dict]) -> dict[str, dict]:
    """``tags:`` for a client connection from :func:`scan_objects` – objects with a present value, named
    after their object name (made unique)."""
    tags: dict[str, dict] = {}
    for row in rows:
        if row["value"] is None:
            continue
        name = row["name"] or row["object"]
        if name in tags:
            name = f"{name} {row['object']}"
        spec: dict[str, Any] = {"object": row["object"]}
        if row["unit"]:
            spec["unit"] = row["unit"]
        if row["description"]:
            spec["description"] = row["description"]
        tags[name] = spec
    return tags
