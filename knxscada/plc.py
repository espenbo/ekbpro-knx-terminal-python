# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""The PLC mapping: which KNX group addresses are exchanged with a PLC, and how.

The mapping lives in ``plc_map.yaml`` (``plc.map`` in ``config.yaml``) and is
created from the ETS project by ``./scada plc-map``. Every usable group
address gets one line::

    - {ga: "1/1/5", use: false, dir: both, type: BOOL, var: GA_1_1_5_Hall_light}  # Hall light · 1.001

``use``
    ``true`` = this address is exchanged with the PLC. With ``select: all`` at
    the top of the file every address is exchanged and ``use`` is ignored.
    (Not ``on``: YAML 1.1 reads the key ``on`` as the boolean true.)
``dir``
    ``to_plc`` – KNX values are written to the PLC (temperatures, feedback);
    ``to_knx`` – PLC changes are sent to KNX;
    ``both``   – both directions (e.g. a light switched from the wall *and* by the PLC).
``type``
    IEC 61131-3 type of the PLC variable (derived from the DPT).
``var``
    Name of the variable in the generated CODESYS GVL (``./scada plc-map`` writes it).
    With ``plc.tag_prefix`` set, an address linked to a PLC object named with a
    system tag in ETS (``M1_000_564_002_RT6101._01_Rom_SP``) gets the tag as its
    name: ``BY1234_564_002_RT6101_Rom_SP`` (see :func:`tag_name`); others get
    ``GA_<address>_<name>``.

Running ``./scada plc-map`` again keeps ``use``, ``dir`` and ``var`` of existing
lines, adds new addresses from the project and drops removed ones.

This module has no OPC UA dependency; the transport is in :mod:`knxscada.opcua_bridge`.
"""

from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from . import PRODUCT
from . import dpt as dptlib

#: Directions an address can have.
DIRECTIONS = ("to_plc", "to_knx", "both")

#: KNX DPT 20.102 (HVAC operating mode) as it is encoded on the bus: the PLC sees the number.
HVAC_CODES = {"AUTO": 0, "COMFORT": 1, "STANDBY": 2, "ECONOMY": 3, "BUILDING_PROTECTION": 4}
HVAC_NAMES = {v: k for k, v in HVAC_CODES.items()}

#: Valid IEC identifier (CODESYS: letters, digits, single underscores; must not start with a digit).
IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: Maximum length of generated variable names (readable in CODESYS and in OPC UA clients).
VAR_MAX = 60

#: A system tag in a communication object text, as written by a CODESYS KNX code generator (e.g. ST_KNX):
#: "KNX_Linje_M1.M1_000_564_002_RT6101._01_Rom_SP" → 564, 002, RT6101, Rom_SP
#: "KNX_Linje_M1.M1_031_564_001_XG101_Un"           → 564, 001, XG101, Un
TAG_RE = re.compile(r"(\d{3})_(\d{3})_([A-Z]{2,3}\d+)(?:\._\d+)?_(\w+)$")


def plc_type(dpt: str | None) -> str | None:
    """IEC 61131-3 type for a DPT, or None when the DPT cannot be exchanged with the PLC."""
    main, sub = dptlib.split(dpt)
    if main == 1:
        return "BOOL"
    if main == 5:
        return "REAL" if sub in (1, 3, None) else "USINT"   # percent and angle as REAL, counters as USINT
    if main == 6:
        return "SINT"
    if main == 7:
        return "UINT"
    if main == 8:
        return "INT"
    if main in (9, 14):
        return "REAL"
    if main == 12:
        return "UDINT"
    if main == 13:
        return "DINT"
    if main == 16:
        return "STRING(14)"
    if main == 17:
        return "USINT"                                         # scene number 0–63
    if main == 20 and sub == 102:
        return "USINT"                                         # HVAC mode as 0–4
    return None


def var_name(address: str, name: str) -> str:
    """CODESYS variable name for a group address: ``GA_1_1_5_Hall_ceiling_light``.

    The address prefix makes every name unique and sorts the GVL like the
    ETS project. Nordic letters are folded (``ø`` → ``o``) because IEC
    identifiers are ASCII.
    """
    text = name.translate(str.maketrans({"æ": "ae", "Æ": "Ae", "ø": "o", "Ø": "O", "å": "a", "Å": "A"}))
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")
    out = "GA_" + re.sub(r"[^A-Za-z0-9]+", "_", address) + ("_" + text if text else "")  # S:1/2/3 → S_1_2_3
    return out[:VAR_MAX].rstrip("_")


def tag_name(objects: list[str], prefix: str) -> str | None:
    """Variable name from a tagged object text: ``<prefix>_564_002_RT6101_Rom_SP``, or None."""
    for text in objects:
        m = TAG_RE.search(text)
        if m:
            name = f"{prefix}_{m[1]}_{m[2]}_{m[3]}_{m[4]}"
            return re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9_]", "_", name))[:VAR_MAX].rstrip("_")
    return None


def default_direction(address: str, model: dict) -> str:
    """``both`` for addresses that control something on a card (switching, setpoint, mode), else ``to_plc``.

    Everything else – feedback, sensors, values received by other devices – is
    only read by the PLC by default: letting the PLC write to the bus is a
    deliberate choice (change ``dir`` in the mapping).
    """
    for f in model["functions"].values():
        for dp in f["datapoints"]:
            if dp["ga"] == address:
                return "both"
    return "to_plc"


@dataclass
class PlcEntry:
    """One line of the mapping."""

    ga: str
    on: bool
    dir: str
    type: str
    var: str
    name: str = ""
    dpt: str | None = None


@dataclass
class PlcMap:
    """The whole mapping file."""

    select: str                 # "marked" (only lines with use: true) or "all"
    gvl: str                    # name of the GVL in CODESYS
    entries: list[PlcEntry]

    def active(self) -> list[PlcEntry]:
        """The entries that are exchanged with the PLC."""
        return [e for e in self.entries if self.select == "all" or e.on]


def build_map(model: dict, old: PlcMap | None = None, gvl: str = "GVL_KNX", tag_prefix: str | None = None,
              rename: bool = False) -> tuple[PlcMap, list[str], list[str]]:
    """Create the mapping from the project, keeping the choices of an existing one.

    *tag_prefix* names addresses after the system tag of the PLC object they
    are linked to (:func:`tag_name`). *rename* gives every line a fresh name
    instead of keeping the ``var`` of an existing line.

    Returns ``(map, unsupported, removed)``: addresses whose DPT cannot be
    exchanged, and addresses of *old* that are no longer in the project.
    """
    previous = {e.ga: e for e in old.entries} if old else {}
    entries, unsupported = [], []
    taken: set[str] = set()
    for addr, g in model["gas"].items():
        if not g["used"] or g["placeholder"]:
            continue
        typ = plc_type(g["dpt"])
        if typ is None:
            unsupported.append(addr)
            continue
        prev = previous.get(addr)
        tagged = tag_name(g.get("objects") or [], tag_prefix) if tag_prefix else None
        if prev and not rename:
            var = prev.var
        else:
            # A tag used by two addresses would give two equal names: the second keeps the address name.
            var = tagged if tagged and tagged.lower() not in taken else var_name(addr, g["name"])
        taken.add(var.lower())
        if prev:
            direction = prev.dir
        elif tagged:
            direction = "to_plc"  # the PLC's own KNX object already writes it on the bus: only read it here
        else:
            direction = default_direction(addr, model)
        entries.append(PlcEntry(
            ga=addr,
            on=prev.on if prev else False,
            dir=direction,
            type=typ,
            var=var,
            name=g["name"],
            dpt=g["dpt"],
        ))
    kept = {e.ga for e in entries}
    removed = [ga for ga in previous if ga not in kept]
    return PlcMap(old.select if old else "marked", old.gvl if old else gvl, entries), unsupported, removed


def load_map(path: Path, gas: dict | None = None) -> PlcMap:
    """Read and validate ``plc_map.yaml``. *gas* (the project's addresses) adds names and DPTs.

    Raises :class:`ValueError` with all problems found (bad direction, invalid
    or duplicate variable names, unknown addresses).
    """
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    select = str(data.get("select", "marked")).lower()
    errors = []
    if select not in ("marked", "all"):
        errors.append(f"select: {select!r} (use 'marked' or 'all')")
    entries, seen = [], set()
    for item in data.get("addresses") or []:
        ga = str(item.get("ga", ""))
        # "on" as a key is read by YAML 1.1 as the boolean True – accept it as an alias of "use".
        use = item.get("use", item.get(True, item.get("on", False)))
        e = PlcEntry(ga=ga, on=bool(use), dir=str(item.get("dir", "to_plc")),
                     type=str(item.get("type", "")), var=str(item.get("var", "")))
        if gas is not None:
            if ga not in gas:
                errors.append(f"{ga}: not in the ETS project")
                continue
            e.name, e.dpt = gas[ga]["name"], gas[ga]["dpt"]
        if e.dir not in DIRECTIONS:
            errors.append(f"{ga}: dir {e.dir!r} (use {', '.join(DIRECTIONS)})")
        if not IDENT_RE.match(e.var) or "__" in e.var:
            errors.append(f"{ga}: var {e.var!r} is not a valid IEC identifier")
        if e.var.lower() in seen:
            errors.append(f"{ga}: var {e.var!r} is used twice")
        seen.add(e.var.lower())
        entries.append(e)
    if errors:
        raise ValueError(f"{path}:\n  " + "\n  ".join(errors))
    return PlcMap(select, str(data.get("gvl") or "GVL_KNX"), entries)


def write_map(pmap: PlcMap, path: Path) -> None:
    """Write the mapping, one address per line with its name and DPT as a comment."""
    lines = [
        f"# PLC mapping for {PRODUCT} – created with ./scada plc-map, see docs/10-plc-opcua.md",
        "#",
        "# select: marked = only lines with use: true are exchanged with the PLC; all = every line.",
        "# dir:    to_plc = KNX → PLC (read in the PLC) · to_knx = PLC → KNX (written by the PLC) · both",
        "# Change use/dir (and var, if you like), then run ./scada plc-map again: it keeps your choices",
        "# and writes the CODESYS GVL to import into the PLC project.",
        "",
        f"select: {pmap.select}",
        f"gvl: {pmap.gvl}",
        "addresses:",
    ]
    for e in pmap.entries:
        on = "true " if e.on else "false"
        ga = f'"{e.ga}",'
        lines.append(f"  - {{ga: {ga:<10} use: {on}, dir: {e.dir + ',':<7} type: {e.type + ',':<11} var: {e.var}}}"
                     f"  # {e.name} · {e.dpt or '?'}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def gvl_text(pmap: PlcMap) -> str:
    """CODESYS global variable list (structured text) for the active entries."""
    active = pmap.active()
    width = max((len(e.var) for e in active), default=10)
    out = [
        f"// {pmap.gvl} – KNX group addresses exchanged with {PRODUCT} over OPC UA.",
        f"// Generated by ./scada plc-map on {time.strftime('%Y-%m-%d %H:%M')} – do not edit by hand:",
        "// change plc_map.yaml and generate it again. Add the GVL to the Symbol Configuration (read/write).",
        "// dir: to_plc = written by KNX (read it in the PLC) · to_knx = write it in the PLC · both",
        "// linkalways: CODESYS leaves out a GVL that no code uses – then it is missing in the Symbol Configuration.",
        "{attribute 'linkalways'}",
        "{attribute 'qualified_only'}",
        "{attribute 'symbol' := 'readwrite'}",
        "VAR_GLOBAL",
    ]
    for e in active:
        out.append(f"    {e.var:<{width}} : {e.type};{'':<{max(1, 12 - len(e.type))}}// {e.ga} {e.dir:<6} {e.name} ({e.dpt})")
    out.append("END_VAR")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- value conversion
def to_plc_value(entry: PlcEntry, value: Any) -> Any:
    """Decoded KNX value → value of the PLC variable (None if it cannot be converted)."""
    if value is None:
        return None
    t = entry.type
    try:
        if t == "BOOL":
            return bool(value)
        if dptlib.split(entry.dpt)[0] == 20:
            return HVAC_CODES.get(str(value)) if isinstance(value, str) else int(value)
        if t == "REAL":
            return float(value)
        if t.startswith("STRING"):
            return str(value)
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def to_knx_value(entry: PlcEntry, value: Any) -> Any:
    """Value of the PLC variable → value accepted by :func:`knxscada.dpt.encode`."""
    if value is None:
        raise ValueError("no value")  # e.g. the PLC is loading: never send it as FALSE/0
    if dptlib.split(entry.dpt)[0] == 20:
        if int(value) not in HVAC_NAMES:
            raise ValueError(f"HVAC mode {value} (use 0–4)")  # never turn an invalid value into AUTO
        return HVAC_NAMES[int(value)]
    if entry.type == "BOOL":
        return bool(value)
    if entry.type == "REAL":
        return float(value)
    if entry.type.startswith("STRING"):
        return str(value)
    return int(value)


def same_value(a: Any, b: Any) -> bool:
    """Equal for the purpose of echo suppression (REAL is 32-bit in the PLC: compare loosely)."""
    if isinstance(a, float) or isinstance(b, float):
        try:
            return abs(float(a) - float(b)) <= 1e-4 * max(1.0, abs(float(a)), abs(float(b)))
        except (TypeError, ValueError):
            return False
    return a == b
