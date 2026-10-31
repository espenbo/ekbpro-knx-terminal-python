# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""Protocol modules and gateways between them.

Every protocol (KNX, Modbus, MQTT, OPC UA …) is a *module*: a driver class in
its own file plus the Python packages it needs. A module is only imported when
a connection in ``protocols.yaml`` uses it, and its packages are only
installed when you ask for it (``./scada modules install modbus``), so an
installation that only needs KNX stays small.

This file is the registry. It describes every module – its settings, its tag
fields and its packages – *without* importing the module, so the TUI can show
forms and the command line can list modules even when a package is missing.

Overview of the package::

    protocols/
      __init__.py   registry: PROTOCOLS, Field, module status, install command
      base.py       Tag, Driver (base class of every protocol), value conversion
      config.py     protocols.yaml: load, validate, save
      gateway.py    gateway engine: mappings between tags, renaming, scaling, echo suppression
      hub.py        runs the drivers inside the server, keeps the values, talks to the clients
      knx.py        KNX (uses the server's KNX connection and the ETS project)
      modbus.py     Modbus TCP and RTU (RS485), master and slave       – pymodbus
      mqtt.py       MQTT client (subscribe and publish)                – aiomqtt
      opcua.py      OPC UA client and server                           – asyncua

Guide: ``docs/11-protocols-gateways.md``.
"""

from __future__ import annotations

import importlib
import importlib.util
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any

from ..i18n import N_

#: Roles a connection can have. ``client`` = we talk to a device (master, client, subscriber);
#: ``server`` = other systems talk to us (slave, server).
ROLES = ("client", "server")


@dataclass(frozen=True)
class Field:
    """One setting in a form (TUI) and in ``protocols.yaml``.

    *kind*: ``str`` | ``int`` | ``float`` | ``bool`` | ``choice`` | ``password`` | ``list``.
    *show*: only relevant when another setting has one of the given values,
    e.g. ``{"transport": ("rtu",)}`` – the TUI hides the field otherwise.
    """

    key: str
    label: str
    kind: str = "str"
    default: Any = None
    choices: tuple[str, ...] = ()
    help: str = ""
    show: dict[str, tuple[str, ...]] = field(default_factory=dict)
    required: bool = False

    def visible(self, values: dict[str, Any]) -> bool:
        """Whether the field applies, given the other settings (*values* merged with the defaults)."""
        return all(str(values.get(k, "")) in v for k, v in self.show.items())


@dataclass(frozen=True)
class Protocol:
    """A protocol module: what it is called, what it needs and which settings it has."""

    name: str                      # key in protocols.yaml (protocol: modbus)
    title: str                     # shown in the TUI
    module: str | None             # Python module with the driver class ``Driver``; None = built in
    packages: tuple[str, ...] = ()  # pip requirements
    imports: tuple[str, ...] = ()  # import names that tell whether the packages are installed
    roles: tuple[str, ...] = ("client",)
    settings: tuple[Field, ...] = ()
    tag_fields: tuple[Field, ...] = ()
    planned: bool = False          # listed, but not implemented yet
    description: str = ""


# -- shared settings ----------------------------------------------------------------------------
_ROLE = Field("role", N_("Role"), "choice", "client", ROLES,
              N_("client: we read from and write to a device · server: other systems read from and write to us"))
_TYPE = Field("type", N_("Value type"), "choice", "", ("", "bool", "int", "float", "str"),
              N_("Empty = automatic"))
_SCALE = Field("scale", N_("Scale"), "float", None, help=N_("value = raw × scale + offset"))
_OFFSET = Field("offset", N_("Offset"), "float", None)
_UNIT = Field("unit", N_("Unit"), "str", "", help=N_("Shown in the TUI, e.g. °C"))
_WRITABLE = Field("writable", N_("Writable"), "bool", True)
_DESCRIPTION = Field("description", N_("Description"), "str", "")

MODBUS_TYPES = ("bool", "int16", "uint16", "int32", "uint32", "float32", "int64", "uint64", "float64")

PROTOCOLS: dict[str, Protocol] = {p.name: p for p in (
    Protocol(
        "knx", "KNX", "knxscada.protocols.knx",
        description=N_("KNX IP tunneling/routing with the ETS project – set up in config.yaml (knx: and project:)"),
    ),
    Protocol(
        "modbus", "Modbus", "knxscada.protocols.modbus",
        packages=("pymodbus>=3.16,<4", "pyserial>=3.5"), imports=("pymodbus", "serial"),
        roles=("client", "server"),
        description=N_("Modbus TCP and RTU (RS485) – master (client) or slave (server)"),
        settings=(
            _ROLE,
            Field("transport", N_("Transport"), "choice", "tcp", ("tcp", "rtu", "rtu_over_tcp"),
                  N_("tcp: Modbus TCP · rtu: RS485/serial · rtu_over_tcp: RTU frames through a TCP converter")),
            Field("host", N_("IP address"), "str", "", help=N_("Server: the address to listen on, 0.0.0.0 = all"),
                  show={"transport": ("tcp", "rtu_over_tcp")}),
            Field("port", N_("TCP port"), "int", 502, show={"transport": ("tcp", "rtu_over_tcp")}),
            Field("device", N_("Serial port"), "str", "/dev/ttyUSB0", show={"transport": ("rtu",)}),
            Field("baudrate", N_("Baud rate"), "choice", "9600",
                  ("1200", "2400", "4800", "9600", "19200", "38400", "57600", "115200"), show={"transport": ("rtu",)}),
            Field("parity", N_("Parity"), "choice", "N", ("N", "E", "O"), N_("N none · E even · O odd"),
                  show={"transport": ("rtu",)}),
            Field("stopbits", N_("Stop bits"), "choice", "1", ("1", "2"), show={"transport": ("rtu",)}),
            Field("unit_id", N_("Unit id (slave address)"), "int", 1,
                  help=N_("Client: default address of the device · server: our address, 0 = answer every address")),
            Field("poll_ms", N_("Poll interval (ms)"), "int", 1000, show={"role": ("client",)}),
            Field("timeout_s", N_("Timeout (s)"), "float", 3, show={"role": ("client",)}),
            Field("word_order", N_("Word order (32/64 bit)"), "choice", "big", ("big", "little"),
                  N_("big: high word first (most devices) · little: low word first")),
        ),
        tag_fields=(
            Field("address", N_("Address"), "str", "", help=N_("hr:100 holding register · ir:5 input register · "
                                                         "co:3 coil · di:7 discrete input · hr:100.3 bit 3 · "
                                                         "40101 = hr:100"), required=True),
            Field("type", N_("Data type"), "choice", "uint16", MODBUS_TYPES),
            _SCALE, _OFFSET, _UNIT,
            Field("unit_id", N_("Unit id"), "int", None, help=N_("Empty = the connection's unit id")),
            _WRITABLE, _DESCRIPTION,
        ),
    ),
    Protocol(
        "mqtt", "MQTT", "knxscada.protocols.mqtt",
        packages=("aiomqtt>=2.0",), imports=("aiomqtt",),
        description=N_("MQTT – subscribe to topics and publish values to a broker"),
        settings=(
            Field("host", N_("Broker"), "str", "127.0.0.1", required=True),
            Field("port", N_("Port"), "int", 1883),
            Field("username", N_("User name"), "str", ""),
            Field("password", N_("Password"), "password", ""),
            Field("tls", N_("TLS"), "bool", False, help=N_("Encrypted connection (port 8883)")),
            Field("client_id", N_("Client id"), "str", "", help=N_("Empty = automatic")),
            Field("topic_prefix", N_("Topic prefix"), "str", "", help=N_("Put in front of every topic, e.g. building/")),
            Field("subscribe", N_("Subscribe to"), "list", [],
                  help=N_("Topic filters whose topics become tags automatically, e.g. sensors/#")),
            Field("payload", N_("Payload format"), "choice", "plain", ("plain", "json"),
                  N_("plain: 21.5 · json: {\"value\": 21.5, \"ts\": …}")),
            Field("qos", N_("QoS"), "choice", "0", ("0", "1", "2")),
            Field("retain", N_("Retain published values"), "bool", True),
        ),
        tag_fields=(
            Field("topic", N_("Topic"), "str", "", help=N_("Empty = topic prefix + tag name")),
            _TYPE,
            Field("json_key", N_("JSON key"), "str", "", help=N_("Take the value from this key of a JSON payload")),
            Field("set_topic", N_("Topic for writing"), "str", "", help=N_("Empty = <topic>/set")),
            _SCALE, _OFFSET, _UNIT, _WRITABLE, _DESCRIPTION,
        ),
    ),
    Protocol(
        "opcua", "OPC UA", "knxscada.protocols.opcua",
        packages=("asyncua>=1.1",), imports=("asyncua",),
        roles=("client", "server"),
        description=N_("OPC UA – client of a PLC/server, or our own OPC UA server for SCADA systems"),
        settings=(
            _ROLE,
            Field("endpoint", N_("Endpoint"), "str", "opc.tcp://127.0.0.1:4840", required=True,
                  help=N_("Server: opc.tcp://0.0.0.0:4840 listens on every address")),
            Field("username", N_("User name"), "str", "", help=N_("Empty = anonymous")),
            Field("password", N_("Password"), "password", ""),
            Field("security", N_("Security"), "choice", "None",
                  ("None", "Basic256Sha256,Sign", "Basic256Sha256,SignAndEncrypt",
                   "Aes128Sha256RsaOaep,SignAndEncrypt", "Aes256Sha256RsaPss,SignAndEncrypt")),
            Field("namespace", N_("Namespace URI"), "str", "urn:ekbpro:gateway", show={"role": ("server",)}),
            Field("publish_interval_ms", N_("Publish interval (ms)"), "int", 500, show={"role": ("client",)}),
        ),
        tag_fields=(
            Field("node", N_("Node id"), "str", "", help=N_("Client: e.g. ns=4;s=|var|PLC.Application.GVL.rTemp · "
                                                     "server: empty = the tag name"), required=False),
            _TYPE, _SCALE, _OFFSET, _UNIT, _WRITABLE, _DESCRIPTION,
        ),
    ),
    Protocol("bacnet", "BACnet/IP", None, planned=True, description=N_("Planned")),
    Protocol("sparkplug", "MQTT Sparkplug B", None, planned=True, description=N_("Planned")),
    Protocol("matter", "Matter", None, planned=True, description=N_("Planned")),
)}


def protocol(name: str) -> Protocol:
    """The registry entry for *name* (ValueError for unknown names)."""
    try:
        return PROTOCOLS[name]
    except KeyError:
        from ..i18n import _

        raise ValueError(_("Unknown protocol {name} – use one of: {valid}").format(
            name=repr(name), valid=", ".join(p for p, v in PROTOCOLS.items() if not v.planned))) from None


def installed(name: str) -> bool:
    """True when every package of the module can be imported (without importing it)."""
    p = protocol(name)
    if p.planned:
        return False
    return all(importlib.util.find_spec(mod) is not None for mod in p.imports)


def missing_message(name: str) -> str:
    """A message telling how to install a module."""
    from ..i18n import _

    return _("The {protocol} module is not installed. Install it with: ./scada modules install {name}").format(
        protocol=protocol(name).title, name=name)


def driver_class(name: str) -> type:
    """Import the module of protocol *name* and return its ``Driver`` class.

    Raises :class:`ModuleNotFoundError` with an install hint when a package is missing.
    """
    p = protocol(name)
    if p.planned or p.module is None:
        from ..i18n import _

        raise ValueError(_("The {protocol} module is not available yet").format(protocol=p.title))
    if not installed(name):
        raise ModuleNotFoundError(missing_message(name))
    return importlib.import_module(p.module).Driver


def defaults(fields: tuple[Field, ...]) -> dict[str, Any]:
    """``{key: default}`` of *fields* (fields without a default are left out)."""
    return {f.key: f.default for f in fields if f.default not in (None, "")}


def install(names: list[str]) -> int:
    """``pip install`` the packages of the modules *names* into the running Python. Returns pip's exit code."""
    packages: list[str] = []
    for n in names:
        p = protocol(n)
        if p.planned:
            from ..i18n import _

            raise ValueError(_("The {protocol} module is not available yet").format(protocol=p.title))
        packages += [pkg for pkg in p.packages if pkg not in packages]
    if not packages:
        return 0
    return subprocess.call([sys.executable, "-m", "pip", "install", "--prefer-binary", *packages])
