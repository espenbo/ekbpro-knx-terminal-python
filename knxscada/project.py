# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""Read the ETS project(s) (``.knxproj`` / ``.knxprojarchive``) and build the model the UI is made from.

Parsing a project with xknxproject takes several seconds, so the result is
cached as JSON (``var/project_cache.json``) and only rebuilt when a project
file is newer than the cache, the project list or the ``rooms`` setting
changed, or the cache format changes.

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

3. **Room names in the group address names** (:func:`build_from_names`) – used
   *instead of* 2 when ``project.rooms`` is set and the project has no ETS functions: for
   projects structured by function ("Er Verdi Kontor 1", "Dim Kontor 1 tak"),
   a regular expression finds the room in each name, and all addresses of a
   room end up in that room – one card per trade (main group) and fixture.

Addresses without an ETS role are classified by heuristics (name patterns,
DPT and the write flag); see :func:`build_datapoints`.

Several projects
----------------
A site can consist of several ETS projects (``project.files``), e.g. a main
building and an annex with their own KNX installations. Each is parsed on its
own and :func:`merge_models` joins them. The projects may use the same group
addresses for different things, so every address of the second and later
projects gets a prefix: ``S:1/2/3`` (see :func:`ga_key`). That key is used
everywhere – state, database, alarms, PLC mapping; only the server strips it
again when it talks to the bus (each KNX interface belongs to one project).
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
CACHE_VERSION = 7


def ga_key(prefix: str, address: str) -> str:
    """The key of a group address in the model: ``1/2/3``, or ``S:1/2/3`` in a project with a prefix."""
    return f"{prefix}:{address}" if prefix else address


def split_key(key: str) -> tuple[str, str]:
    """``"S:1/2/3"`` → ``("S", "1/2/3")``; ``"1/2/3"`` → ``("", "1/2/3")``."""
    prefix, sep, address = key.rpartition(":")
    return (prefix, address) if sep else ("", key)

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
    projects = cfg.projects
    cache = cfg.cache_path
    sources = [[p["name"], str(p["file"]), p["prefix"], p["label"], p.get("rooms")] for p in projects]
    sources = json.loads(json.dumps(sources))  # as stored in the cache (e.g. YAML int keys become strings)
    if not force and cache.exists():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            # Without the .knxproj (e.g. copied installation) the cache is still usable.
            fresh = all(not p["file"].exists() or cache.stat().st_mtime >= p["file"].stat().st_mtime for p in projects)
            if data.get("cache_version") == CACHE_VERSION and fresh and data.get("sources", sources) == sources:
                add_extra_rooms(data, cfg.project.get("extra_rooms"))
                return data
        except (OSError, json.JSONDecodeError):
            pass  # unreadable cache: rebuild below
    from xknxproject import XKNXProj  # imported lazily: only needed when (re)building

    models = []
    for p in projects:
        src = p["file"]
        if not src.exists():
            raise FileNotFoundError(_("Cannot find the project file {path}").format(path=src))
        log.info(_("Parsing {name} (this takes a while)…").format(name=src.name))
        models.append(build_model(XKNXProj(str(src), password=p.get("password") or None).parse(), rooms=p.get("rooms")))
    model = models[0] if len(models) == 1 else merge_models(projects, models)
    model["sources"] = sources
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(model, ensure_ascii=False, indent=1), encoding="utf-8")
    add_extra_rooms(model, cfg.project.get("extra_rooms"))  # not cached: read from config.yaml every time
    return model


#: ``usage`` of an extra card → the ETS usage text the views look for.
USAGES = {"light": "switchable light", "dimmer": "dimmable light", "heating": "heating radiator", "other": "custom"}


def add_extra_rooms(model: dict[str, Any], rooms: list[dict] | None) -> None:
    """Add rooms defined in ``project.extra_rooms`` – for addresses no ETS room holds.

    ::

        extra_rooms:
          - floor: Loft                       # an existing floor, or a new one
            name: Attic                       # also the name plan.yaml links to
            cards:
              - name: Temperature
                addresses: ["3/0/35"]
              - name: Store room light
                addresses: ["1/0/5", "1/0/7"]
                status: {"1/0/5": "1/4/81"}   # control → feedback, when the names do not pair them
                usage: light                  # light | dimmer | heating | other (default: guessed)

    The cards are built like the others (:func:`build_datapoints`). Raises
    :class:`ValueError` for addresses that are not in the project.
    """
    if not rooms:
        return
    gas = model["gas"]
    unknown = []
    for i, spec in enumerate(rooms):
        floor_name = str(spec.get("floor") or N_("Other"))
        floor = next((f for f in model["floors"] if f["name"] == floor_name), None)
        if floor is None:
            floor = {"name": floor_name, "rooms": []}
            model["floors"].append(floor)
        room = {"id": f"X-{i}", "name": str(spec.get("name") or f"Room {i + 1}"), "functions": []}
        for j, card in enumerate(spec.get("cards") or []):
            addrs = [str(a) for a in card.get("addresses") or []]
            explicit = {str(k): str(v) for k, v in (card.get("status") or {}).items()}
            unknown += [a for a in [*addrs, *explicit.values()] if a not in gas]
            fake = {"name": str(card.get("name") or ""), "group_addresses": {a: {"role": ""} for a in addrs if a in gas}}
            dps = build_datapoints(fake, gas, room["name"])
            if explicit:
                for d in dps:
                    if d["ga"] in explicit:
                        d["status"] = explicit[d["ga"]]
                # A feedback address listed on its own is now shown on its control row.
                dps = [d for d in dps if d["ga"] or d["status"] not in explicit.values()]
            if not dps:
                continue
            kinds = {d["kind"] for d in dps}
            usage = USAGES.get(str(card.get("usage") or ""))
            if usage is None:
                if {"switch", "percent"} <= kinds:
                    usage = "dimmable light"
                elif "switch" in kinds:
                    usage = "switchable light"
                elif any(dptlib.split(d["dpt"])[0] == 9 for d in dps):
                    usage = "heating radiator"  # a temperature: shown as the room temperature
                else:
                    usage = "custom"
            fid = f"X-{i}-{j}"
            model["functions"][fid] = {"id": fid, "name": fake["name"], "usage": usage, "type": "",
                                       "room": room["name"], "datapoints": dps}
            room["functions"].append(fid)
        floor["rooms"].append(room)
    if unknown:
        raise ValueError(_("project.extra_rooms: not in the ETS project: {addresses}").format(
            addresses=", ".join(sorted(set(unknown)))))


def merge_models(projects: list[dict[str, Any]], models: list[dict[str, Any]]) -> dict[str, Any]:
    """Join the models of several projects into one (see "Several projects" above).

    Group addresses, function ids and room ids of every project with a prefix
    get that prefix; floors are named "<label>: <floor>" and the group range
    tree gets one top node per project.
    """
    gas: dict[str, dict] = {}
    ranges: list[dict] = []
    floors: list[dict] = []
    functions: dict[str, dict] = {}
    for p, m in zip(projects, models):
        pre = p["prefix"]

        def key(a: str | None, pre: str = pre) -> str | None:
            return ga_key(pre, a) if a else a

        for a, g in m["gas"].items():
            gas[key(a)] = {**g, "address": key(a)}

        def ranges_with_prefix(rs: list[dict], key=key) -> list[dict]:
            return [{**r, "address": key(r["address"]), "gas": [key(a) for a in r["gas"]],
                     "children": ranges_with_prefix(r["children"])} for r in rs]

        # Top node per project; its "project" field is the prefix the address view filters on.
        ranges.append({"address": "", "name": p["label"], "project": pre, "gas": [],
                       "children": ranges_with_prefix(m["ranges"])})
        fid_map = {}
        for fid, f in m["functions"].items():
            new = key(fid)
            fid_map[fid] = new
            functions[new] = {**f, "id": new, "datapoints": [
                {**d, "ga": key(d.get("ga")), "status": key(d.get("status"))} for d in f["datapoints"]]}
        for fl in m["floors"]:
            floors.append({**fl, "name": f"{p['label']}: {_(fl['name'])}", "rooms": [
                {**r, "id": key(r["id"]), "functions": [fid_map[f] for f in r["functions"]]} for r in fl["rooms"]]})
    info = {**models[0]["info"], "name": " + ".join(str(m["info"].get("name") or p["label"])
                                                     for p, m in zip(projects, models))}
    return {
        "cache_version": CACHE_VERSION,
        "info": info,
        "projects": [{"name": p["name"], "label": p["label"], "prefix": p["prefix"], "info": m["info"]}
                     for p, m in zip(projects, models)],
        "gas": gas,
        "ranges": ranges,
        "floors": floors,
        "functions": functions,
        "source": models[0].get("source", "ets-functions"),
    }


def _ga_sort_key(addr: str) -> tuple[int, ...]:
    """Sort "1/2/10" after "1/2/9" (numeric, not alphabetical)."""
    return tuple(int(x) for x in re.split(r"[/.]", addr) if x.isdigit())


def build_model(raw: dict[str, Any], rooms: dict[str, Any] | None = None) -> dict[str, Any]:
    """Turn xknxproject's parse result into the model described in the module docstring.

    *rooms* is the ``project.rooms`` setting (see :func:`build_from_names`).
    """
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
            # Texts of the linked communication objects, e.g. a PLC's "M1_000_564_002_RT6101._01_Rom_SP"
            # (used for PLC variable names, see knxscada.plc.tag_name).
            "objects": sorted({co["text"] for co in links if co.get("text")}),
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
    if not functions and rooms and rooms.get("pattern"):
        # No ETS functions, but the room is part of the group address names.
        floors, functions = build_from_names(ranges, gas, rooms)
        source = "room-names"
    elif not functions:
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


def build_from_names(ranges: list[dict], gas: dict[str, dict], rule: dict[str, Any]) -> tuple[list[dict], dict[str, dict]]:
    """Rooms from the group address *names* (``project.rooms``).

    *rule*::

        pattern: "(?i)\\b(kontor\\s*\\d+|fellesrom|spiserom)\\b"   # finds the room in a name
        floors: {0: "2. etg", 3: "2. etg", 1: "1. etg", 2: "1. etg"}  # main group → floor (optional)
        names: {"2. etg/Kontor 6": "Office 6 · RT6206"}              # room names shown (optional)
        setpoint: "(?i)set temp"                                      # more setpoint names (optional)
        read_only: "(?i)onsket temp|varmepaadrag"                     # never written from a card (optional)

    The first group of *pattern* (or the whole match) is the room. The text
    before it is the row label ("Er Verdi", "av/pa Status"), the text after it
    the fixture ("tak", "nedhengt"); a name that *starts* with the room
    ("Kontor 1 Request") has its label after it. Within a room there is one
    card per main group and fixture, titled "<main group> <fixture>" without
    the words of the floor name ("Lys 2 etg" on "2. etg" → "Lys", "Lys tak").
    Without ``floors`` the floor is the name of the main group – set it when
    the main groups are trades (lighting, heating) rather than floors.

    Addresses whose name has no room are handled by :func:`build_from_ranges`.
    """
    pattern = re.compile(rule["pattern"])
    floor_of = {str(k): str(v) for k, v in (rule.get("floors") or {}).items()}
    names = {_fold(str(k)): str(v) for k, v in (rule.get("names") or {}).items()}
    setpoint = re.compile(rule["setpoint"]) if rule.get("setpoint") else None
    read_only = re.compile(rule["read_only"]) if rule.get("read_only") else None
    main_name = {r["address"]: r["name"] or r["address"] for r in ranges}

    def usable(addr: str) -> bool:
        g = gas[addr]
        return bool(g["used"] and not g["placeholder"] and g["dpt"])

    # (floor, room key) → {"name", fixture key → [(address, label)]}, in address order
    found: dict[tuple[str, str], dict] = {}
    matched: set[str] = set()
    for addr, g in gas.items():
        if not usable(addr):
            continue
        m = pattern.search(g["name"])
        if not m:
            continue
        matched.add(addr)
        room = re.sub(r"\s+", " ", m.group(1) if m.groups() else m.group(0)).strip()
        main = addr.split("/")[0]
        floor = floor_of.get(main, main_name.get(main, main))
        before = g["name"][: m.start()].strip(" -:/")
        after = re.sub(r"\s+", " ", g["name"][m.end():]).strip(" -:/")
        if not before:  # "Kontor 1 Request": the label comes after the room
            before, after = after, ""
        entry = found.setdefault((floor, room.lower()), {"name": room[:1].upper() + room[1:], "cards": {}})
        entry["cards"].setdefault((main, _norm(after)), {"fixture": _strip_suffix_words(after) if after else "",
                                                          "members": []})["members"].append((addr, before))

    floors: dict[str, dict] = {}
    functions: dict[str, dict] = {}
    for (floor, rkey), entry in found.items():
        rid = f"RM-{_fold(floor)}-{_fold(rkey)}"
        shown = names.get(_fold(f"{floor}/{entry['name']}"), entry["name"])
        room = {"id": rid, "name": shown, "functions": []}
        for i, ((main, _fx), card) in enumerate(entry["cards"].items()):
            fake = {"name": "", "group_addresses": {a: {"role": ""} for a, _l in card["members"]}}
            dps = build_datapoints(fake, gas, "", setpoint, read_only)
            if not dps:
                continue
            labels = {a: lab for a, lab in card["members"]}
            for d in dps:
                lab = labels.get(d["ga"] or d["status"] or "", "")
                lab = LABEL_WORDS.get(lab.lower(), lab)
                d["label"] = lab[:1].upper() + lab[1:] if lab else KIND_LABEL.get(d["kind"], N_("Value"))
            floor_words = {_fold(w.strip(".")) for w in floor.split()}
            trade = " ".join(w for w in main_name.get(main, main).split() if _fold(w.strip(".")) not in floor_words)
            title = " ".join(x for x in (trade or main_name.get(main, main), card["fixture"]) if x)
            kinds = {d["kind"] for d in dps}
            if {"switch", "percent"} <= kinds:
                usage = "dimmable light"
            elif "switch" in kinds and re.search(r"(?i)lys|light|lamp|spot", title):
                usage = "switchable light"
            elif "setpoint" in kinds:
                usage = "heating radiator"
            else:
                usage = "custom"
            fid = f"{rid}-{i}"
            functions[fid] = {"id": fid, "name": title, "usage": usage, "type": "", "room": shown,
                              "datapoints": dps}
            room["functions"].append(fid)
        if room["functions"]:
            floors.setdefault(floor, {"name": floor, "rooms": []})["rooms"].append(room)

    for fl in floors.values():
        fl["rooms"].sort(key=lambda r: [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", r["name"])])
    # Everything without a room in its name: as before, from the group address structure.
    rest = {a: g for a, g in gas.items() if a not in matched}
    rest_floors, rest_functions = build_from_ranges(ranges, rest)
    functions.update(rest_functions)
    return list(floors.values()) + rest_floors, functions


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


def build_datapoints(func: dict, gas: dict[str, dict], room: str,
                     setpoint: re.Pattern | None = None, read_only: re.Pattern | None = None) -> list[dict]:
    """Turn the group addresses of one function into card rows (datapoints).

    Steps:

    1. Classify every address as *control*, *status*, *value* or *skip*, from
       its ETS role if it has one, otherwise from its name and write flag.
       Names matching *read_only* are values even with the write flag.
    2. Pair each status address with the control address it reports on
       (same main DPT and same name apart from status words), so the card
       shows one row with a control and its feedback.
    3. Choose the kind of row from the DPT: 1.x → switch, 5.001 → percent
       (dimmer), 9.x setpoint → setpoint, 20.x → mode, 17/18 → scene,
       everything else → read-only value. *setpoint* adds names to the
       built-in setpoint words.

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
            if read_only and read_only.search(ga["name"]):
                role = "value"
            elif STATUS_RE.search(ga["name"]):
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
        elif main == 9 and is_control and (i["ets_role"] == "TempRoomSetpoint" or SETPOINT_RE.search(ga["name"])
                                          or (setpoint and setpoint.search(ga["name"]))):
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
