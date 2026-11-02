# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""KNX as a protocol module: the group addresses of the ETS project are its tags.

The KNX connection itself (xknx, several IP interfaces, KNX Secure, the demo
simulator) stays in the server, set up in ``config.yaml``; this driver only
connects it to the gateways:

* every group address is a tag named after its address (``1/2/3``, or
  ``S:1/2/3`` with several projects); a gateway may also name it by its ETS
  name when that name is unique;
* values arrive through the server's telegram handling (``ScadaServer.ingest``);
* writes are encoded with the address's DPT and sent like writes from the TUI,
  and logged in the event log with the gateway as the user.

KNX never gets *initial* values from a gateway (see :attr:`Driver.initial_allowed`).
With ``server.read_on_start`` (default ``status``) the connection is *ready* only
when the server has read the statuses of the bus, so the answers are statuses,
not changes. The values in the database from the last run are never passed on.
"""

from __future__ import annotations

from typing import Any

from .. import dpt as dptlib
from ..i18n import _
from .base import Driver as BaseDriver
from .base import Tag

#: DPT 20.102 numbers → names, for values from number-only protocols.
HVAC_NAMES = {0: "AUTO", 1: "COMFORT", 2: "STANDBY", 3: "ECONOMY", 4: "BUILDING_PROTECTION"}


def tag_type(dpt: str | None) -> str:
    """The value type of a DPT (``""`` for values that are passed on as they are, e.g. colours)."""
    main, sub = dptlib.split(dpt)
    if main == 1:
        return "bool"
    if main == 5:
        return "float" if sub in (1, 3) else "int"
    if main in (6, 7, 8, 12, 13, 17, 18):
        return "int"
    if main in (9, 14):
        return "float"
    if main in (16, 20):
        return "str"
    return ""


class Driver(BaseDriver):
    """The KNX bus as a connection called ``knx``: a thin layer over the server's own KNX connection.

    It opens nothing itself. The server reports the bus connection (:meth:`set_state`), the end of the start-up
    read (:meth:`set_ready`) and every received group value (``hub.update("knx", …)``).
    """

    protocol = "knx"
    # Safety rule: a start-up or reconnect must never put telegrams on the bus.
    initial_allowed = False
    echoes = True   # every write comes back through the server's telegram handling

    def __init__(self, name: str, settings: dict[str, Any], hub) -> None:
        """One tag per group address of the loaded ETS project(s), typed and given a unit from its DPT."""
        super().__init__(name, settings, hub)
        self.server = hub.server
        self._by_name: dict[str, str | None] = {}   # ETS name (lower case) → address, None when not unique
        for addr, ga in self.server.gas.items():
            self.tags[addr] = Tag(name=addr, address=addr, type=tag_type(ga.get("dpt")),
                                  unit=ga.get("unit") or dptlib.unit(ga.get("dpt")), description=ga.get("name") or "",
                                  extra={"dpt": ga.get("dpt")})
            key = (ga.get("name") or "").strip().lower()
            if key:
                self._by_name[key] = None if key in self._by_name else addr

    def resolve(self, ref: str) -> Tag | None:
        """The group address *ref*, or the address whose ETS name is *ref* (case-insensitive, only if unique)."""
        tag = self.tags.get(ref)
        if tag is None:
            addr = self._by_name.get(ref.strip().lower())
            tag = self.tags.get(addr) if addr else None
        return tag

    def create_tag(self, name: str, spec: dict[str, Any]) -> Tag:
        """Refused: only group addresses from the ETS project exist on the bus (the DPT is needed to encode)."""
        raise ValueError(_("{address} is not in the ETS project").format(address=name))

    def describe(self) -> str:
        """The KNX interface the server is connected through, for the TUI."""
        return str(self.server.status.get("gateway") or "")

    @property
    def reads_first(self) -> bool:  # type: ignore[override]
        """True until the server's start-up read (``read_on_start``) is done; then the server calls set_ready."""
        # Only the first time: after a reconnect the bus is not read again, so telegrams are changes at once.
        return not self.server.start_read_done

    async def session(self) -> None:
        """Take over the server's current bus state and wait until stopped (the server pushes all changes)."""
        # The values in the database from the last run are old: they never go to the gateways.
        # Only telegrams received from now on (also the answers to read_on_start) do.
        self.set_state(bool(self.server.status.get("connected")))
        await self.wait_stopped()

    async def write(self, tag: Tag, value: Any, user: str = "") -> None:
        """Send a GroupValueWrite to *tag* (encoded with its DPT) and log it as an operation by *user*.

        HVAC mode (DPT 20.102) also accepts the number 0–4 from number-only protocols. Raises when the value
        cannot be encoded or the bus is not connected (``ScadaServer._send``).
        """
        dpt = tag.extra.get("dpt")
        if dptlib.split(dpt) == (20, 102) and isinstance(value, (int, float, str)) and str(value).isdigit():
            value = HVAC_NAMES.get(int(value), value)
        payload = dptlib.encode(dpt, value)
        await self.server._send(tag.name, "write", payload)
        shown = dptlib.fmt(dpt, dptlib.decode(dpt, payload), tag.unit)
        self.server.event("info", "operation", f"{tag.name} {tag.description} ← {shown}", user or "gateway")
