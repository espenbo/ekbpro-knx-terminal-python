# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""A summary of the building for the overview, the floor plan and the screensaver.

The project model is organised as floors → rooms → functions → datapoints.
Several views need the same per-room facts – "what is the room temperature,
which lights are on, is the heating running, is there an alarm?" – so this
module extracts them once into :class:`Room` objects and computes the live
status from the current state with :meth:`HomeModel.room_status`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .. import dpt as dptlib
from ..alarms import normalize_severity


@dataclass
class Room:
    """Group addresses of interest in one room (None when the room has no such point)."""

    id: str
    name: str
    floor: str
    temp_ga: str | None = None   # actual room temperature
    sp_ga: str | None = None     # temperature setpoint
    mode_ga: str | None = None   # HVAC operating mode
    heat_ga: str | None = None   # heating output on/off (or valve)
    lights: list[dict] = field(default_factory=list)  # {"name", "status", "ga", "dpt", "level"} per light
    gas: set[str] = field(default_factory=set)        # every GA of the room (to attribute alarms)


def _first_ga(gas: dict, pattern: str, dpt_main: int | None = None, dpt: str | None = None) -> str | None:
    """First used group address whose name matches *pattern* (optionally with a given DPT)."""
    rx = re.compile(pattern, re.I)
    for addr, g in gas.items():
        if not g["used"] or g["placeholder"]:
            continue
        if dpt and g["dpt"] != dpt:
            continue
        if dpt_main is not None and dptlib.split(g["dpt"])[0] != dpt_main:
            continue
        if rx.search(g["name"]):
            return addr
    return None


class HomeModel:
    """Rooms plus a few building-wide points (outdoor temperature, power, energy, CO₂, humidity)."""

    def __init__(self, model: dict[str, Any], ui: dict[str, Any]) -> None:
        gas = model["gas"]
        self.rooms: list[Room] = []
        for floor in model["floors"]:
            for r in floor["rooms"]:
                room = Room(r["id"], r["name"], floor["name"])
                for fid in r["functions"]:
                    f = model["functions"][fid]
                    for d in f["datapoints"]:
                        for ga in (d["ga"], d["status"]):
                            if ga:
                                room.gas.add(ga)
                    # Lights: functions whose ETS usage mentions "light" and that have a switch.
                    sw = next((d for d in f["datapoints"] if d["kind"] == "switch"), None)
                    if sw and "light" in f["usage"]:
                        pc = next((d for d in f["datapoints"] if d["kind"] == "percent"), None)
                        room.lights.append({
                            "name": f["name"], "status": sw["status"] or sw["ga"], "ga": sw["ga"], "dpt": sw["dpt"],
                            # Brightness feedback of a dimmer (None for plain on/off lights).
                            "level": (pc["status"] or pc["ga"]) if pc else None,
                        })
                    # Heating functions provide temperature, setpoint, mode and output.
                    if f["usage"].startswith("heating"):
                        for d in f["datapoints"]:
                            if d["role"] == "TempRoom" and not room.temp_ga:
                                room.temp_ga = d["status"]
                            elif d["role"] == "TempRoomSetpoint" and not room.sp_ga:
                                room.sp_ga = d["ga"]
                            elif d["kind"] == "mode" and not room.mode_ga:
                                room.mode_ga = d["status"] or d["ga"]
                            elif re.search(r"(?i)varmeutgang|heating output|heat output", d["label"]) and not room.heat_ga:
                                room.heat_ga = d["status"]
                        if not room.temp_ga:
                            # No TempRoom role: use the first temperature value of the function.
                            room.temp_ga = next(
                                (d["status"] for d in f["datapoints"] if d["kind"] == "value" and dptlib.split(d["dpt"])[0] == 9),
                                None,
                            )
                        if not room.heat_ga:
                            room.heat_ga = next((d["status"] for d in f["datapoints"] if d["role"] == "ValvePosition"), None)
                self.rooms.append(room)

        # Building-wide points: configured explicitly, or found by name.
        self.outdoor_ga = ui.get("outdoor_ga") or _first_ga(gas, r"^(ute ?temp|outdoor|outside)", 9)
        self.power_ga = ui.get("power_ga") or _first_ga(gas, r"power$|effekt|\bW$", dpt="14.056")
        self.energy_ga = ui.get("energy_ga") or _first_ga(gas, r"kwh|energy", 13)
        self.co2 = [(a, g["name"]) for a, g in gas.items() if g["used"] and g["dpt"] == "9.008" and re.search("co2", g["name"], re.I)]
        self.humidity = [
            (a, g["name"]) for a, g in gas.items()
            if g["used"] and g["dpt"] == "9.007" and re.search("fukt|humid", g["name"], re.I)
        ]

    def trend_gas(self) -> list[str]:
        """Addresses whose 24 h history the overview needs (room temperatures, outdoor, power)."""
        out = [r.temp_ga for r in self.rooms if r.temp_ga]
        out += [g for g in (self.outdoor_ga, self.power_ga) if g]
        return out

    @staticmethod
    def value(state: dict, ga: str | None) -> Any:
        """Current decoded value of *ga* (None if unknown)."""
        return state.get(ga or "", {}).get("value")

    @staticmethod
    def text(state: dict, ga: str | None) -> str:
        """Current display text of *ga* (``"–"`` if unknown)."""
        st = state.get(ga or "")
        return st["text"] if st else "–"

    def room_status(self, room: Room, state: dict, alarms: list[dict]) -> dict[str, Any]:
        """Live status of a room.

        Returns ``{"temp", "sp", "mode", "heating", "lights_on", "lights_total", "alarm"}``
        where ``alarm`` is None, ``"warning"`` or ``"critical"``. An alarm
        belongs to the room when its group address is one of the room's
        addresses or the alarm text mentions the room name.
        """
        lights_on = sum(1 for light in room.lights if state.get(light["status"] or "", {}).get("value") is True)
        severity = None
        for a in alarms:
            if a["cleared_ts"] is not None and a["acked_ts"] is not None:
                continue  # closed alarm
            if a["ga"] in room.gas or room.name.lower() in a["text"].lower():
                if normalize_severity(a["severity"]) == "critical":
                    severity = "critical"
                elif severity is None:
                    severity = "warning"
        temp = self.value(state, room.temp_ga)
        return {
            "temp": temp if isinstance(temp, (int, float)) and not isinstance(temp, bool) else None,
            "sp": self.value(state, room.sp_ga),
            "mode": self.text(state, room.mode_ga) if room.mode_ga else "",
            "heating": self.value(state, room.heat_ga) is True,
            "lights_on": lights_on,
            "lights_total": len(room.lights),
            "alarm": severity,
        }
