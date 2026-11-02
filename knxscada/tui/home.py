# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""A summary of the building for the overview, the floor plan and the screensaver.

The project model is organised as floors → rooms → functions → datapoints.
Several views need the same per-room facts – "what is the room temperature,
which lights are on, is the heating running, is there an alarm?" – so this
module extracts them once into :class:`Room` objects and computes the live
status from the current state with :meth:`HomeModel.room_status`.

The app builds one :class:`HomeModel` at start-up (``ScadaApp.home``) and fills :attr:`HomeModel.meters` (and, without
a KNX outdoor address, ``outdoor_ga``) from ``ui.systems``. Config keys read here: ``ui.outdoor_ga``,
``ui.power_ga`` and ``ui.energy_ga`` (otherwise found by group address name and DPT).
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
    #: The room's other temperature sensors (a switch sensor, a smoke detector, a CO₂ sensor …) as (short label,
    #: address), besides ``temp_ga``; the overview shows them under the room. See :func:`extra_temps`.
    temps: list[tuple[str, str]] = field(default_factory=list)
    gas: set[str] = field(default_factory=set)        # every GA of the room (to attribute alarms)


#: Temperature rows that are not a measured room temperature: dew points, setpoints and wished temperatures.
NOT_A_SENSOR = re.compile(r"(?i)dew|dugg|set ?p|settp|setpunkt|ønsket|onsket|wish|min(imum)?\b|max(imum)?\b")

#: Words left out of a sensor's label in the overview (the room name is removed too): "Stue heating current TV
#: temperature" → "TV".
LABEL_NOISE = re.compile(r"(?i)\b(heating|current|room|temperature|temperatur|temp|er-?verdi|romverdi|value|"
                         r"actual|faktisk)\b")


def short_label(label: str, room: str) -> str:
    """A short name for a temperature sensor: the datapoint label without the room name and filler words.

    Args:
        label: the row label from the project (ETS name or description).
        room: the room's name, removed when the label starts with it.

    Returns:
        What is left (e.g. ``bryter``, ``Kjøkken bryter``, ``Smoke``), or ``°C`` when nothing is.
    """
    text = label.strip()
    if room and text.lower().startswith(room.lower()):
        text = text[len(room):]
    text = re.sub(r"\s+", " ", LABEL_NOISE.sub(" ", text)).strip(" -–·:")
    return text or "°C"


def extra_temps(functions: list[dict], room: str, main: str | None) -> list[tuple[str, str]]:
    """Every temperature sensor of a room's functions except the main one, in card order and without duplicates.

    A sensor is a ``value`` row of DPT 9.001 whose label is not a dew point, setpoint or wished temperature.
    A card from ``extra_rooms`` (usage ``custom``) with one temperature is labelled with the card's name – its row
    label is only the address name. Called once per room at start-up.

    Args:
        functions: the room's functions from the project model.
        room: the room's name (for :func:`short_label`).
        main: the room temperature already shown (left out).
    """
    out: list[tuple[str, str]] = []
    seen = {main}
    for f in functions:
        rows = [d for d in f["datapoints"] if d["kind"] == "value" and dptlib.split(d["dpt"]) == (9, 1)
                and not NOT_A_SENSOR.search(d["label"])]
        for d in rows:
            ga = d["status"] or d["ga"]
            if ga in seen:
                continue
            seen.add(ga)
            label = f["name"] if f.get("usage") == "custom" and len(rows) == 1 else short_label(d["label"], room)
            out.append((label, ga))
    return out


def _first_ga(gas: dict, pattern: str, dpt_main: int | None = None, dpt: str | None = None) -> str | None:
    """First used group address whose name matches *pattern* (optionally with a given DPT)."""
    rx = re.compile(pattern, re.IGNORECASE)
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
        """Extract the rooms and building-wide points from the project *model* and the ``ui`` section *ui*.

        Called once by the app at start-up. Per room: the temperature, setpoint, mode and heating output (from ETS
        roles, else from row kinds and names) and every light (a function whose usage mentions "light" and that has a
        switch). ``ui.outdoor_ga``, ``ui.power_ga`` and ``ui.energy_ga`` override the addresses found by name.
        """
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
                            elif d["role"] == "HeatOutput" and not room.heat_ga:   # project.point_rooms
                                room.heat_ga = d["status"] or d["ga"]
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
                        # Cards built from names have no ETS roles: the setpoint row and the valve position (%).
                        if not room.sp_ga:
                            room.sp_ga = next((d["ga"] for d in f["datapoints"] if d["kind"] == "setpoint"), None)
                        if not room.heat_ga:
                            room.heat_ga = next(
                                (d["status"] for d in f["datapoints"] if d["kind"] == "value" and d["dpt"] == "5.001"), None
                            )
                room.temps = extra_temps([model["functions"][fid] for fid in r["functions"]], room.name, room.temp_ga)
                self.rooms.append(room)

        # Building-wide points: configured explicitly, or found by name.
        self.outdoor_ga = ui.get("outdoor_ga") or _first_ga(gas, r"^(ute ?temp|outdoor|outside)", 9)
        self.power_ga = ui.get("power_ga") or _first_ga(gas, r"power$|effekt|\bW$", dpt="14.056")
        self.energy_ga = ui.get("energy_ga") or _first_ga(gas, r"kwh|energy", 13)
        # Energy meters from ui.systems: (name, pt:conn/tag of the power), filled by the app.
        self.meters: list[tuple[str, str]] = []
        self.co2 = [(a, g["name"]) for a, g in gas.items() if g["used"] and g["dpt"] == "9.008" and re.search("co2", g["name"], re.IGNORECASE)]
        self.humidity = [
            (a, g["name"]) for a, g in gas.items()
            if g["used"] and g["dpt"] == "9.007" and re.search("fukt|humid", g["name"], re.IGNORECASE)
        ]

    def trend_gas(self) -> list[str]:
        """Addresses whose 24 h history the overview needs (room temperatures, outdoor, power)."""
        out = [r.temp_ga for r in self.rooms if r.temp_ga]
        out += [ga for r in self.rooms for _label, ga in r.temps]
        out += [g for g in (self.outdoor_ga, self.power_ga) if g]
        return out + [ga for _name, ga in self.meters]

    @staticmethod
    def value(state: dict, ga: str | None) -> Any:
        """Current decoded value of *ga* (None if unknown)."""
        return state.get(ga or "", {}).get("value")

    @staticmethod
    def is_old(state: dict, ga: str | None) -> bool:
        """True if the value of *ga* is from the database of the last run (not read since the start)."""
        return bool(state.get(ga or "", {}).get("old"))

    @staticmethod
    def text(state: dict, ga: str | None) -> str:
        """Current display text of *ga* (``"–"`` if unknown)."""
        st = state.get(ga or "")
        return st["text"] if st else "–"

    def room_status(self, room: Room, state: dict, alarms: list[dict]) -> dict[str, Any]:
        """Live status of a room.

        Returns ``{"temp", "sp", "mode", "heating", "lights_on", "lights_total", "alarm", "old", "temps"}``
        where ``alarm`` is None, ``"warning"`` or ``"critical"`` and ``old`` is the set of
        fields (``temp``, ``sp``, ``mode``, ``heating``, ``lights``) shown from the database of
        the last run – not read from the bus since the start. An alarm
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
        heat = self.value(state, room.heat_ga)
        old = {key for key, ga in (("temp", room.temp_ga), ("sp", room.sp_ga), ("mode", room.mode_ga),
                                   ("heating", room.heat_ga)) if self.is_old(state, ga)}
        if any(self.is_old(state, light["status"]) for light in room.lights):
            old.add("lights")
        return {
            "temp": temp if isinstance(temp, (int, float)) and not isinstance(temp, bool) else None,
            "sp": self.value(state, room.sp_ga),
            "mode": self.text(state, room.mode_ga) if room.mode_ga else "",
            # On/off output, or a valve position above 0 %.
            "heating": heat is True or (isinstance(heat, (int, float)) and not isinstance(heat, bool) and heat > 0),
            "lights_on": lights_on,
            "lights_total": len(room.lights),
            "alarm": severity,
            "old": old,
            # The other sensors: (label, value or None, old) – shown under the room in the overview.
            "temps": [(label, v if isinstance(v := self.value(state, ga), (int, float)) and not isinstance(v, bool)
                       else None, self.is_old(state, ga)) for label, ga in room.temps],
        }
