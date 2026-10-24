# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""Floor plan: reading ``plan.yaml`` and exporting it to DXF.

``plan.yaml`` describes each floor as a set of rectangles (rooms) in
millimetres with the origin in the top-left corner – the same orientation as
a paper drawing. A room can be linked to an ETS room by name (``room:``) so
the terminal view can show live temperature, lights and alarms inside it.
See ``docs/06-floor-plan.md`` for the format.

The DXF export writes a minimal AutoCAD R12 ASCII file that practically every
CAD program can open (LibreCAD, QCAD, FreeCAD, AutoCAD …). It is one-way: the
YAML file stays the source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .config import Config


@dataclass
class PlanRoom:
    """A room rectangle. Coordinates in mm, origin top-left."""

    label: str
    x: float
    y: float
    w: float
    h: float
    room: str | None = None  # name of the linked ETS room (None = no KNX in this room)


@dataclass
class PlanFloor:
    """One floor: outer dimensions and its rooms."""

    name: str
    width: float
    depth: float
    rooms: list[PlanRoom] = field(default_factory=list)


def plan_path(cfg: Config) -> Path:
    """Location of the floor plan file (``ui.plan``, default ``plan.yaml``)."""
    return cfg.resolve(cfg.ui.get("plan", "plan.yaml"))


def load_plan(cfg: Config) -> list[PlanFloor]:
    """Read the floor plan; returns ``[]`` when the file does not exist."""
    path = plan_path(cfg)
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    floors = []
    for f in data.get("floors") or []:
        rooms = [
            PlanRoom(str(r.get("label", "")), float(r["x"]), float(r["y"]), float(r["w"]), float(r["h"]), r.get("room"))
            for r in f.get("rooms") or []
        ]
        floors.append(PlanFloor(str(f.get("name", "")), float(f.get("width", 8000)), float(f.get("depth", 8000)), rooms))
    return floors


def export_dxf(floors: list[PlanFloor], path: Path) -> None:
    """Write a simple DXF (R12) with all floors placed side by side.

    Layers: ``OUTER_WALL`` (floor outline), ``ROOM`` (room rectangles) and
    ``TEXT`` (floor names, room labels and areas). The y axis is flipped
    because DXF has y pointing up while the plan has y pointing down.
    """
    out: list[str] = ["0", "SECTION", "2", "ENTITIES"]

    # DXF is a list of (group code, value) pairs, one per line.
    def line(layer: str, x1: float, y1: float, x2: float, y2: float) -> None:
        out.extend(["0", "LINE", "8", layer, "10", f"{x1:.1f}", "20", f"{y1:.1f}", "30", "0",
                    "11", f"{x2:.1f}", "21", f"{y2:.1f}", "31", "0"])

    def rect(layer: str, x: float, y: float, w: float, h: float) -> None:
        line(layer, x, y, x + w, y)
        line(layer, x + w, y, x + w, y + h)
        line(layer, x + w, y + h, x, y + h)
        line(layer, x, y + h, x, y)

    def text(x: float, y: float, height: float, value: str, center: bool = True) -> None:
        out.extend(["0", "TEXT", "8", "TEXT", "10", f"{x:.1f}", "20", f"{y:.1f}", "30", "0", "40", f"{height:.1f}", "1", value])
        if center:  # 72/73 = horizontal/vertical alignment, 11/21 = alignment point
            out.extend(["72", "1", "73", "2", "11", f"{x:.1f}", "21", f"{y:.1f}", "31", "0"])

    offset = 0.0
    for floor in floors:
        d = floor.depth
        rect("OUTER_WALL", offset, 0, floor.width, d)
        text(offset + floor.width / 2, d + 400, 250, floor.name)
        for r in floor.rooms:
            # Flip y: in the plan y=0 is at the top, in DXF at the bottom.
            rect("ROOM", offset + r.x, d - r.y - r.h, r.w, r.h)
            cx, cy = offset + r.x + r.w / 2, d - r.y - r.h / 2
            text(cx, cy + 120, 160, r.label)
            text(cx, cy - 180, 120, f"{r.w * r.h / 1e6:.2f} m2")
        offset += floor.width + 2000  # 2 m gap between floors

    out.extend(["0", "ENDSEC", "0", "EOF"])
    # R12 files are 8-bit; cp1252 keeps Nordic letters (æøå) readable in most CAD programs.
    path.write_text("\n".join(out) + "\n", encoding="cp1252", errors="replace")
