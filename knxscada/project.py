# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""Read the ETS project (``.knxproj``) and build the model the UI is made from.

Parsing a ``.knxproj`` with xknxproject takes several seconds, so the result
is cached as JSON (``var/project_cache.json``) and only rebuilt when the
project file is newer than the cache or the cache format changes.

The model
---------
``gas``
    Every group address with name, DPT, flags (readable/writable), whether
    it is linked to any device ("used") and its position in the group range tree.
``ranges``
    The main/middle group tree, used by the group address view.
``floors`` → ``rooms`` → ``functions`` → ``datapoints``
    What the room views show. A *function* becomes a card; each *datapoint*
    becomes a row on that card (switch, dimmer, setpoint, value …).

Where floors/rooms/functions come from
--------------------------------------
1. **ETS functions** (preferred). If the project has a building structure
   with functions in the rooms, those are used directly, and the ETS
   *roles* (SwitchOnOff, InfoOnOff, TempRoomSetpoint …) decide which group
   address is the control and which is the status feedback.
2. **Group address structure** (fallback, :func:`build_from_ranges`). Main
   group → floor, middle group → room, and addresses whose names only differ
   by words like *switching/status/value* are merged into one card.

Addresses without an ETS role are classified by heuristics (name patterns,
DPT and the write flag); see :func:`build_datapoints`.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from . import dpt as dptlib
from .config import Config
from .i18n import N_, _

log = logging.getLogger(__name__)

#: Bump when the structure of the cached model changes, so old caches are rebuilt.
CACHE_VERSION = 4

# --- Name heuristics -----------------------------------------------------------------
# The patterns include common English and Norwegian words used in ETS projects.

#: Names that indicate a status/feedback address.
STATUS_RE = re.compile(r"\b(status|tilbakemelding|feedback|state)\b", re.I)
#: Names that indicate a setpoint (writing a temperature makes sense).
SETPOINT_RE = re.compile(r"setpoint|setpunkt|settpunkt|skal|ønsket|onsket|basis", re.I)
#: Names that indicate a switching (control) address even without the write flag.
CONTROL_RE = re.compile(r"\b(switching|av/på|av/paa)\b", re.I)
#: Placeholder names such as "-------" used to reserve addresses.
PLACEHOLDER_RE = re.compile(r"^[-_\s.x]*$", re.I)
#: Words removed when comparing names, so "Hall light switching" and
#: "Hall light status" are recognised as belonging together.
PAIR_WORDS_RE = re.compile(r"\b(status|tilbakemelding|feedback|state|switching|switch|value|verdi)\b", re.I)

#: ETS function role → (role on the card, English label shown for it).
#: Labels are translated when displayed.
ROLE_MAP = {
    "SwitchOnOff": ("control", N_("On/Off")),
    "InfoOnOff": ("status", N_("On/Off")),
    "ActualDimmingValue": ("control", N_("Brightness")),
    "RelativeSetvalueControl": ("skip", N_("Dimming")),  # relative dimming (DPT 3) has no useful UI here
    "TempRoom": ("value", N_("Room temperature")),
    "TempRoomSetpoint": ("control", N_("Setpoint")),
    "HVACMode": ("control", N_("Operating mode")),
    "WindowStatus": ("value", N_("Window")),
    "ValvePosition": ("value", N_("Valve")),
}

#: When the remainder of a GA name (after removing room/function name) is just
#: one of these words, the English label on the right is used instead.
LABEL_WORDS = {"switching": N_("On/Off"), "switch": N_("On/Off"), "status": N_("Status"), "value": N_("Brightness"),
               "value status": N_("Brightness")}

#: Generic label per datapoint kind (used for single-row cards in the fallback model).
KIND_LABEL = {"switch": N_("On/Off"), "percent": N_("Level"), "setpoint": N_("Setpoint"), "mode": N_("Operating mode"),
              "scene": N_("Scene"), "bool": N_("Status"), "value": N_("Value")}


def load_project(cfg: Config, force: bool = False) -> dict[str, Any]:
    """Return the project model, from cache when it is up to date.

    *force* ignores the cache (used by ``./scada import`` and ``init``).
    """
    src = cfg.project_file
    cache = cfg.cache_path
    if not force and cache.exists():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            # Without the .knxproj (e.g. copied installation) the cache is still usable.
            fresh = not src.exists() or cache.stat().st_mtime >= src.stat().st_mtime
            if data.get("cache_version") == CACHE_VERSION and fresh:
                return data
        except (OSError, json.JSONDecodeError):
            pass  # unreadable cache: rebuild below
    if not src.exists():
        raise FileNotFoundError(_("Cannot find the project file {path}").format(path=src))
    log.info(_("Parsing {name} (this takes a while)…").format(name=src.name))
    from xknxproject import XKNXProj  # imported lazily: only needed when (re)building

    raw = XKNXProj(str(src), password=cfg.project.get("password") or None).parse()
    model = build_model(raw)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(model, ensure_ascii=False, indent=1), encoding="utf-8")
    return model


def _ga_sort_key(addr: str) -> tuple[int, ...]:
    """Sort "1/2/10" after "1/2/9" (numeric, not alphabetical)."""
    return tuple(int(x) for x in re.split(r"[/.]", addr) if x.isdigit())


def build_model(raw: dict[str, Any]) -> dict[str, Any]:
    """Turn xknxproject's parse result into the model described in the module docstring."""
    cos = raw["communication_objects"]

    # --- 1. Group addresses ----------------------------------------------------------
    gas: dict[str, dict[str, Any]] = {}
    for addr, ga in raw["group_addresses"].items():
        links = [cos[c] for c in ga["communication_object_ids"] if c in cos]
        dpt = dptlib.dpt_str(ga["dpt"])
        if dpt is None:
            # ETS often has no DPT on the address itself; inherit it from a linked object.
            for co in links:
                if co.get("dpts"):
                    dpt = dptlib.dpt_str(co["dpts"][0])
                    break
        gas[addr] = {
            "address": addr,
            "name": ga["name"],
            "description": ga.get("description") or "",
            "dpt": dpt,
            # W flag on any linked object: some device accepts writes → we may send to it.
            "writable": any(co["flags"].get("write") for co in links),
            # R flag on any linked object: some device answers a read request.
            "readable": any(co["flags"].get("read") for co in links),
            "used": bool(links),
            "placeholder": bool(PLACEHOLDER_RE.match(ga["name"] or "")),
            "range": "",  # filled in below, e.g. "Lighting / Switching"
        }

    # --- 2. Group range tree -----------------------------------------------------------
    def walk(ranges: dict, path: list[str]) -> list[dict]:
        out = []
        for addr, r in sorted(ranges.items(), key=lambda kv: _ga_sort_key(kv[0])):
            p = path + [r["name"]]
            for a in r["group_addresses"]:
                if a in gas:
                    gas[a]["range"] = " / ".join(p)
            out.append(
                {
                    "address": addr,
                    "name": r["name"],
                    "gas": sorted((a for a in r["group_addresses"] if a in gas), key=_ga_sort_key),
                    "children": walk(r["group_ranges"], p),
                }
            )
        return out

    ranges = walk(raw["group_ranges"], [])

    # --- 3. Floors and rooms from the ETS building structure ------------------------------
    space_names: dict[str, str] = {}
    floors: list[dict] = []

    def collect(space: dict, floor: dict | None) -> None:
        """Walk the location tree; every space with functions below a floor becomes a room."""
        space_names[space["identifier"]] = space["name"]
        if space["type"] in ("Building", "BuildingPart"):
            for s in space["spaces"].values():
                collect(s, None)
            return
        if space["type"] == "Floor" and floor is None:
            floor = {"name": space["name"], "rooms": []}
            floors.append(floor)
        elif floor is not None and space.get("functions"):
            # Rooms, corridors, distribution boards … are all treated as "rooms".
            floor["rooms"].append({"id": space["identifier"], "name": space["name"], "functions": list(space["functions"])})
        for s in space["spaces"].values():
            collect(s, floor)

    for loc in raw["locations"].values():
        collect(loc, None)

    # --- 4. ETS functions → cards ---------------------------------------------------------
    functions: dict[str, dict] = {}
    for fid, f in raw["functions"].items():
        room_name = space_names.get(f.get("space_id") or "", "")
        dps = build_datapoints(f, gas, room_name)
        if not dps:
            continue  # e.g. scene functions without group addresses
        functions[fid] = {
            "id": fid,
            "name": f["name"],
            "usage": f.get("usage_text") or "",  # "dimmable light", "heating radiator", …
            "type": f.get("function_type") or "",
            "room": room_name,
            "datapoints": dps,
        }

    # Drop empty rooms/floors; collect functions that are not placed in any room.
    for floor in floors:
        for room in floor["rooms"]:
            room["functions"] = [fid for fid in room["functions"] if fid in functions]
        floor["rooms"] = [r for r in floor["rooms"] if r["functions"]]
    floors = [f for f in floors if f["rooms"]]

    assigned = {fid for fl in floors for r in fl["rooms"] for fid in r["functions"]}
    rest = [fid for fid in functions if fid not in assigned]
    if rest:
        # Names are translated when displayed.
        floors.append({"name": N_("Other"), "rooms": [{"id": "rest", "name": N_("No room"), "functions": rest}]})

    source = "ets-functions"
    if not functions:
        # The project has no ETS functions: build "rooms" from the group address structure.
        floors, functions = build_from_ranges(ranges, gas)
        source = "group-addresses"

    return {
        "cache_version": CACHE_VERSION,
        "info": raw["info"],
        "gas": dict(sorted(gas.items(), key=lambda kv: _ga_sort_key(kv[0]))),
        "ranges": ranges,
        "floors": floors,
        "functions": functions,
        "source": source,
    }


def build_from_ranges(ranges: list[dict], gas: dict[str, dict]) -> tuple[list[dict], dict[str, dict]]:
    """Fallback for projects without ETS functions.

    Main group → "floor", middle group → "room". Group addresses whose names
    are equal after removing words like *status/switching/value* become one
    card, so "Living ceiling switching" and "Living ceiling status" end up
    together as a switch with feedback.
    """
    floors: list[dict] = []
    functions: dict[str, dict] = {}

    def usable(addr: str) -> bool:
        g = gas.get(addr)
        return bool(g and g["used"] and not g["placeholder"] and g["dpt"])

    for main in ranges:
        floor = {"name": main["name"] or main["address"], "rooms": []}
        middles = main["children"] or [main]
        # Group across the whole main group, so that "… switching" (1/1/x) and
        # "… status" (1/4/x) land on the same card even when the project is
        # structured by function (main = Lighting, middle = Switching/Status).
        # The card is placed in the middle group of the first address of the group.
        groups: dict[str, list[str]] = {}
        home_of: dict[str, dict] = {}
        for mid in middles:
            addrs = [a for a in mid["gas"] if usable(a)]
            for sub in mid["children"]:  # free address structures can be deeper
                addrs += [a for a in sub["gas"] if usable(a)]
            for a in addrs:
                key = _norm(gas[a]["name"]) or gas[a]["name"].lower()
                if key not in groups:
                    groups[key] = []
                    home_of[key] = mid
                groups[key].append(a)
        rooms: dict[str, dict] = {}
        for i, (key, members) in enumerate(groups.items()):
            fake = {"name": "", "group_addresses": {a: {"role": ""} for a in members}}
            dps = build_datapoints(fake, gas, "")
            if not dps:
                continue
            mid = home_of[key]
            title = _strip_suffix_words(gas[members[0]]["name"])
            fid = f"GA-{main['address']}-{i}"
            kinds = {d["kind"] for d in dps}
            # Guess the usage so the overview can count lights.
            if {"switch", "percent"} <= kinds:
                usage = "dimmable light"
            elif "switch" in kinds and re.search(r"(?i)lys|light|lamp|spot", title):
                usage = "switchable light"
            else:
                usage = "custom"
            # Row labels: whatever is left of the GA name after the card title.
            for d in dps:
                lab = _strip_prefix(d["label"], title).strip(" -:")
                lab = LABEL_WORDS.get(lab.lower(), lab)
                d["label"] = lab[:1].upper() + lab[1:] if lab and len(dps) > 1 else KIND_LABEL.get(d["kind"], N_("Value"))
            functions[fid] = {"id": fid, "name": title, "usage": usage, "type": "", "room": mid["name"], "datapoints": dps}
            room = rooms.setdefault(mid["address"], {"id": f"GR-{mid['address']}", "name": mid["name"] or mid["address"], "functions": []})
            room["functions"].append(fid)
        floor["rooms"] = [rooms[m["address"]] for m in middles if m["address"] in rooms]
        if floor["rooms"]:
            floors.append(floor)
    return floors, functions


def _strip_suffix_words(name: str) -> str:
    """Card title from a GA name: remove words like status/switching/value."""
    out = re.sub(r"\s+", " ", PAIR_WORDS_RE.sub("", name)).strip(" -:")
    return out or name


def _norm(name: str) -> str:
    """Name used to match control and status addresses (lower case, without status words)."""
    return re.sub(r"\s+", " ", PAIR_WORDS_RE.sub("", name)).strip().lower()


def _fold(text: str) -> str:
    """Loose comparison key: lower case, Nordic letters folded, no spaces ("Kjøkken" == "kjokken")."""
    return text.lower().replace("ø", "o").replace("æ", "ae").replace("å", "a").replace(" ", "")


def _strip_prefix(name: str, token: str) -> str:
    """Remove *token* (e.g. the room name) from the start of a GA name.

    Tolerant to case, Nordic letters and spacing, so "Sov2 Anna CO2" with
    room "Sov 2 Anna" gives "CO2". Returns *name* unchanged if it does not
    start with the token.
    """
    target = _fold(token)
    words = name.split()
    acc = ""
    for i, w in enumerate(words):
        acc += _fold(w)
        if acc == target:
            return " ".join(words[i + 1:])
        if not target.startswith(acc):
            break
    return name


def _label(name: str, room: str, func: str) -> str:
    """Row label for a GA name: strip room and function name, map plain words to labels."""
    out = name
    for token in (room, func):
        if token:
            out = _strip_prefix(out, token)
    out = out.strip(" -:")
    # "Kitchen spotlights switching" in function "Spotlights Kitchen" → "switching" → "On/Off"
    head, _sep, last = out.rpartition(" ")
    if head and last.lower() in LABEL_WORDS and _fold(head) in _fold(func):
        out = last
    out = LABEL_WORDS.get(out.lower(), out)
    return out[:1].upper() + out[1:] if out else name


def build_datapoints(func: dict, gas: dict[str, dict], room: str) -> list[dict]:
    """Turn the group addresses of one function into card rows (datapoints).

    Steps:

    1. Classify every address as *control*, *status*, *value* or *skip*, from
       its ETS role if it has one, otherwise from its name and write flag.
    2. Pair each status address with the control address it reports on
       (same main DPT and same name apart from status words), so the card
       shows one row with a control and its feedback.
    3. Choose the kind of row from the DPT: 1.x → switch, 5.001 → percent
       (dimmer), 9.x setpoint → setpoint, 20.x → mode, 17/18 → scene,
       everything else → read-only value.

    Each datapoint is ``{"label", "role", "kind", "ga", "status", "dpt"}``
    where ``ga`` is the address written to and ``status`` the one displayed.
    """
    items = []
    for addr, ref in func["group_addresses"].items():
        ga = gas.get(addr)
        if not ga:
            continue
        role, label = ROLE_MAP.get(ref.get("role", ""), (None, None))
        if role is None:
            if STATUS_RE.search(ga["name"]):
                role = "status"
            elif ga["writable"] or CONTROL_RE.search(ga["name"]):
                role = "control"
            else:
                role = "value"
        if role == "skip":
            continue
        items.append({"addr": addr, "role": role, "label": label, "ets_role": ref.get("role", ""), "ga": ga})

    # --- pair status addresses with their control address ---
    controls = [i for i in items if i["role"] == "control"]
    statuses = [i for i in items if i["role"] == "status"]
    pairs: dict[str, str] = {}  # control address → status address
    for s in statuses:
        smain = dptlib.split(s["ga"]["dpt"])[0]
        match = None
        if s["ets_role"] == "InfoOnOff":
            match = next((c for c in controls if c["ets_role"] == "SwitchOnOff" and c["addr"] not in pairs), None)
        if match is None:
            match = next(
                (
                    c
                    for c in controls
                    if c["addr"] not in pairs
                    and dptlib.split(c["ga"]["dpt"])[0] == smain
                    and _norm(c["ga"]["name"]) == _norm(s["ga"]["name"])
                ),
                None,
            )
        if match is not None:
            pairs[match["addr"]] = s["addr"]
    paired_status = set(pairs.values())

    # --- decide the kind of row ---
    dps = []
    for i in items:
        if i["addr"] in paired_status:
            continue  # shown as the feedback of its control row
        ga = i["ga"]
        dpt = ga["dpt"]
        main, sub = dptlib.split(dpt)
        is_control = i["role"] == "control"
        kind = "value"
        if main == 1:
            kind = "switch" if is_control else "bool"
        elif main == 3:
            continue  # relative dimming/blinds step – not shown as a control
        elif main == 5 and sub == 1 and is_control:
            kind = "percent"
        elif main == 9 and is_control and (i["ets_role"] == "TempRoomSetpoint" or SETPOINT_RE.search(ga["name"])):
            kind = "setpoint"
        elif main == 20 and is_control:
            kind = "mode"
        elif main in (17, 18) and is_control:
            kind = "scene"
        label = i["label"] or _label(ga["name"], room, func["name"])
        status = pairs.get(i["addr"])
        dps.append(
            {
                "label": label,
                "role": i["ets_role"] if i["ets_role"] in ROLE_MAP else "",
                "kind": kind,
                # Read-only rows have no write address; they display their own address.
                "ga": i["addr"] if kind not in ("value", "bool") else None,
                "status": status or (i["addr"] if kind in ("value", "bool") else None),
                "dpt": dpt,
            }
        )
    dps.sort(key=_dp_order)
    return dps


def _dp_order(dp: dict) -> int:
    """Row order on a card: switch, dimmer, room temperature, setpoint, mode, values, flags, scene."""
    if dp["role"] == "TempRoom":
        return 2
    return {"switch": 0, "percent": 1, "setpoint": 3, "mode": 4, "value": 5, "bool": 6, "scene": 7}.get(dp["kind"], 9)
