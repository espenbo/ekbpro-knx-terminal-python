# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""Several KNX IP interfaces: configuration, routing of outgoing telegrams and de-duplication.

A building can have more than one KNX IP interface – for example one per TP
line or per building part, without an IP backbone between them. The server
then opens one connection (tunnel or routing) per interface and treats them as
one bus:

``config.yaml``::

    knx:
      interfaces:
        - name: house
          gateway_ip: 192.168.1.50
        - name: garage
          gateway_ip: 192.168.1.51
          addresses: ["4/*", "5/1/*"]     # group addresses sent through this interface

With several ETS projects (``project.files``) each interface names the project
whose bus it is connected to (``project: annex``; default: the first project).
Telegrams from that interface get the project's address prefix (``S:1/2/3``),
and a telegram for ``S:1/2/3`` is only sent through an interface of that project.

The old single-interface form (``knx: {connection, gateway_ip, …}``) is still
valid and gives one interface named ``knx``. Keys at the ``knx:`` level next to
``interfaces`` (e.g. ``keyring_file``) are defaults for every interface.

Outgoing telegrams (writes from the TUI or the PLC, reads at start-up) go
through one interface, chosen by :meth:`Router.route`:

1. the first interface (of the address's project) whose ``addresses`` patterns match;
2. else the interface the address was last *received* on (learned while running);
3. else the project's default interface (``default: true``, or its first one).

When the interfaces are on lines that are coupled (line couplers or an IP
backbone), the same telegram arrives through several interfaces.
:class:`Deduplicator` drops the copies, so it is logged and counted once.

This module has no xknx dependency apart from :func:`connection_type`.
"""

from __future__ import annotations

import fnmatch
import re
import time
from typing import Any

from .i18n import _
from .project import split_key

#: Names of ``connection`` in the configuration.
CONNECTION_TYPES = ("tunneling", "tunneling_tcp", "tunneling_secure", "routing", "routing_secure", "automatic")

#: Keys that only make sense per interface: not inherited from the ``knx:`` level.
_OWN_KEYS = {"interfaces", "name", "addresses", "default", "gateway_ip", "gateway_port", "individual_address",
             "project"}

#: Interface names are shown in the bus monitor and the event log: keep them short and plain.
NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,16}$")

#: Default for ``knx.dedup_ms``.
DEDUP_MS = 1000


def interface_configs(knx: dict[str, Any], projects: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """The interfaces of the ``knx:`` section, each a complete dict with a ``name``.

    *projects* maps project names to their address prefix (first project: ``""``);
    every interface gets ``project`` and ``prefix``.

    Raises :class:`ValueError` listing every problem (unknown connection type,
    duplicate or invalid names, bad ``addresses``).
    """
    items = knx.get("interfaces")
    if not items:
        items = [{**knx, "name": knx.get("name") or "knx"}]
        defaults: dict[str, Any] = {}
    else:
        if not isinstance(items, list):
            raise ValueError(_("knx.interfaces must be a list"))
        defaults = {k: v for k, v in knx.items() if k not in _OWN_KEYS}
    out, errors, names = [], [], set()
    for i, item in enumerate(items, 1):
        if not isinstance(item, dict):
            errors.append(f"knx.interfaces[{i}]: {item!r}")
            continue
        c = {**defaults, **item}
        name = str(c.get("name") or c.get("gateway_ip") or f"knx{i}")
        c["name"] = name
        if not NAME_RE.match(name):
            errors.append(_("knx interface {name}: the name may only have letters, digits, - _ . (max 16)").format(
                name=repr(name)))
        if name.lower() in names:
            errors.append(_("knx interface {name} is listed twice").format(name=repr(name)))
        names.add(name.lower())
        ctype = str(c.get("connection") or "automatic").lower()
        if ctype not in CONNECTION_TYPES:
            errors.append(_("Unknown knx.connection {name} in config.yaml – use one of: {valid}").format(
                name=repr(ctype), valid=", ".join(CONNECTION_TYPES)))
        c["connection"] = ctype
        addresses = c.get("addresses") or []
        if isinstance(addresses, str):
            addresses = [addresses]
        if not isinstance(addresses, list):
            errors.append(f"{name}: addresses: {addresses!r}")
            addresses = []
        c["addresses"] = [str(a) for a in addresses]
        c["default"] = bool(c.get("default"))
        projects = projects or {"main": ""}
        c["project"] = str(c.get("project") or next(iter(projects)))
        if c["project"] not in projects:
            errors.append(_("knx interface {name}: unknown project {project} – use one of: {valid}").format(
                name=repr(name), project=repr(c["project"]), valid=", ".join(projects)))
        c["prefix"] = projects.get(c["project"], "")
        out.append(c)
    for prefix in {c["prefix"] for c in out}:
        if sum(c["default"] for c in out if c["prefix"] == prefix) > 1:
            errors.append(_("Only one KNX interface can have default: true"))
    if errors:
        raise ValueError("\n".join(errors))
    return out


def connection_type(name: str):
    """The xknx ``ConnectionType`` for a ``connection`` name (validated by :func:`interface_configs`)."""
    from xknx.io import ConnectionType

    return {
        "tunneling": ConnectionType.TUNNELING,
        "tunneling_tcp": ConnectionType.TUNNELING_TCP,
        "tunneling_secure": ConnectionType.TUNNELING_TCP_SECURE,
        "routing": ConnectionType.ROUTING,
        "routing_secure": ConnectionType.ROUTING_SECURE,
        "automatic": ConnectionType.AUTOMATIC,
    }[name]


def describe(c: dict[str, Any]) -> str:
    """Short text for the status: ``192.168.1.50`` or ``auto``."""
    return str(c.get("gateway_ip") or "auto")


class Router:
    """Chooses the interface an outgoing telegram for a group address is sent through."""

    def __init__(self, configs: list[dict[str, Any]]) -> None:
        self.names = [c["name"] for c in configs]
        self.prefix = {c["name"]: c.get("prefix", "") for c in configs}
        self.patterns = [(c["name"], c["addresses"]) for c in configs if c["addresses"]]
        self.default: dict[str, str] = {}  # project prefix → default interface
        for c in configs:
            self.default.setdefault(c.get("prefix", ""), c["name"])  # first interface of the project …
        for c in configs:
            if c["default"]:
                self.default[c.get("prefix", "")] = c["name"]       # … unless one has default: true
        self.learned: dict[str, str] = {}  # address key → interface it was last received on

    def learn(self, key: str, name: str) -> None:
        """Note that a telegram for *key* came in through interface *name*."""
        self.learned[key] = name

    def configured(self, key: str) -> str | None:
        """The interface whose ``addresses`` match *key* (in the key's project), or None."""
        prefix, ga = split_key(key)
        for name, patterns in self.patterns:
            if self.prefix[name] == prefix and any(fnmatch.fnmatchcase(ga, p) for p in patterns):
                return name
        return None

    def route(self, key: str) -> str | None:
        """Interface for *key*: configured patterns, then learned, then the project's default.

        None when no interface belongs to the key's project.
        """
        if len(self.names) == 1:
            return self.names[0] if self.prefix[self.names[0]] == split_key(key)[0] else None
        return self.configured(key) or self.learned.get(key) or self.default.get(split_key(key)[0])


class Deduplicator:
    """Drops a telegram that already came through *another* interface a moment ago.

    The key is (group address, type, payload): the source address cannot be
    used, because our own telegram arrives at the other interface with the
    individual address of the tunnel it was sent through. Two equal telegrams
    on the *same* interface are both kept – they really were sent twice.
    """

    def __init__(self, window_ms: int = DEDUP_MS) -> None:
        self.window = window_ms / 1000
        self.recent: dict[tuple, tuple[float, str]] = {}  # key → (time, interface)
        self._last_cleanup = 0.0

    def duplicate(self, name: str, dst: str, kind: str, raw: str | None, now: float | None = None) -> bool:
        """True if this telegram is a copy of one just seen through another interface."""
        if self.window <= 0:
            return False
        now = time.monotonic() if now is None else now
        key = (dst, kind, raw)
        seen = self.recent.get(key)
        if seen and seen[1] != name and now - seen[0] <= self.window:
            return True
        self.recent[key] = (now, name)
        if now - self._last_cleanup > 10 * self.window:
            self._last_cleanup = now
            self.recent = {k: v for k, v in self.recent.items() if now - v[0] <= self.window}
        return False
