# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""The "Systems" view (Ctrl+E): one air handling unit at a time, from protocols.yaml.

What is shown comes from ``ui.systems`` in ``config.yaml``. Each entry names a connection of
``protocols.yaml`` and binds its tags to *roles* (supply temperature, fan output …)::

    ui:
      systems:
        - name: "=360.001 Ventilation 1st floor"
          short: "360.001"                # menu entry under "Systems" (default: =NNN.NNN from the name)
          type: ventilation
          connection: vent1
          prefix: BY_360_001_             # put in front of every tag name below (optional)
          meter: meter1                   # energy meter of the unit (a connection, optional)
          tags: {outdoor: RT900_PV, supply: RT401_PV, extract: RT501_PV, setpoint: AktReg_SP,
                 supply_fan: JV401_OP, extract_fan: JV501_OP, recovery: LX001_OP, heating: LV001_OP,
                 cooling: LK001_OP, alarm: SYS_AL, mode: SD_CMD}
          mode_texts: {0: Auto, 1: Manual off, 2: Manual on}
          settings: ["*_SP", "*Set*"]     # tags on the "Settings" tab (glob, default: every tag)
          hide: [Act_Time]                # tags never shown there (glob, without the prefix)
          groups:                         # sections of the "Settings" tab (glob, without the prefix)
            - {title: Supply fan, tags: ["JV401_*"]}
        - name: Energy
          type: energy
          meters:
            - {name: Ventilation 1st floor, connection: meter1}   # tags of the WAGO/Carlo Gavazzi templates

Every unit gets an entry under "Systems" in the menu. A unit page has the air-flow drawing, charts of
the last 24 hours (temperatures and outputs, from the log: ``log`` in protocols.yaml) and a "Settings"
tab with the tags of the connection. A role without a tag is left out of the drawing. The meters are
shown in their own view (:mod:`.energy`).
"""

from __future__ import annotations

import asyncio
import fnmatch
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Input,
    Label,
    Static,
    TabbedContent,
    TabPane,
)

from ..i18n import N_, _
from .charts import CachedPlot, draw, palette
from .gateways import PointDialog, PointWrite, confirm_point_write
from .views import View
from .widgets import FastTable, LiveStatic

if TYPE_CHECKING:
    from collections.abc import Callable

#: Roles of an air handling unit (keys under ``tags:``).
VENT_ROLES = (
    "outdoor", "supply", "extract", "exhaust", "setpoint", "supply_fan", "extract_fan", "supply_fan_run",
    "extract_fan_run", "recovery", "heating", "cooling", "supply_flow", "extract_flow", "supply_pressure",
    "extract_pressure", "running", "alarm", "filter_supply", "filter_extract", "mode", "status",
    # Week program: arrays with one value per day, Monday first (CODESYS calendars), and the overtime switch.
    "week_start_hour", "week_start_minute", "week_stop_hour", "week_stop_minute", "week_enabled", "overtime",
)

#: Curves of the two charts on a unit page: (role, English legend).
TEMP_CURVES = (("outdoor", N_("Outdoor")), ("supply", N_("Supply")), ("extract", N_("Extract")),
               ("exhaust", N_("Exhaust")), ("setpoint", N_("Setpoint")))
OUTPUT_CURVES = (("supply_fan", N_("Supply fan")), ("extract_fan", N_("Extract fan")),
                 ("recovery", N_("Heat recovery")), ("heating", N_("Heat")), ("cooling", N_("Cool")))

#: Day names of the week program, Monday first.
DAYS = (N_("Mon"), N_("Tue"), N_("Wed"), N_("Thu"), N_("Fri"), N_("Sat"), N_("Sun"))

#: Roles of an energy meter and the tag names of the device templates (WAGO 879-30xx, Carlo Gavazzi).
METER_ROLES = {
    "power": "Total active power", "energy": "Total active energy",
    "i1": "L1 current", "i2": "L2 current", "i3": "L3 current",
    "u1": "L1 voltage", "u2": "L2 voltage", "u3": "L3 voltage",
    "pf": "Power factor", "freq": "Frequency",
}

#: A value as the views keep it: (value, unit, quality).
Value = tuple[Any, str, str]


@dataclass
class Vent:
    """One air handling unit from ``ui.systems``."""

    name: str
    conn: str
    tags: dict[str, str] = field(default_factory=dict)   # role → tag name (with the prefix)
    meter: str = ""                                      # connection of its energy meter
    mode_texts: dict[int, str] = field(default_factory=dict)
    short: str = ""                                      # menu entry
    settings: list[str] = field(default_factory=list)    # glob patterns of the "Settings" tab
    prefix: str = ""
    groups: list[tuple[str, list[str]]] = field(default_factory=list)   # (title, glob patterns without prefix)
    hide: list[str] = field(default_factory=list)        # glob patterns without prefix
    kind: str = "ventilation"                            # "panel": no drawing, only the tags in sections

    def key(self, role: str) -> str | None:
        """The hub key ``conn/tag`` of *role*, or None when the role has no tag (it is then left out of the drawing)."""
        tag = self.tags.get(role)
        return f"{self.conn}/{tag}" if tag else None

    def label(self) -> str:
        """The menu entry: ``short``, else the ``=360.001`` part of the name, else the name."""
        if self.short:
            return self.short
        m = re.match(r"\s*=?(\d+\.\d+)", self.name)
        return m.group(1) if m else self.name

    def shows_setting(self, tag: str) -> bool:
        """True when *tag* (full name, with the prefix) belongs on the "Settings" tab.

        ``hide`` (matched without the prefix) always wins; without ``settings`` patterns every other tag is shown,
        otherwise only the tags matching one of them (matched with the prefix, as written in ``protocols.yaml``).
        Matching is case-sensitive, like the tag names of the PLC.
        """
        if any(fnmatch.fnmatchcase(self.short_name(tag), p) for p in self.hide):
            return False
        return not self.settings or any(fnmatch.fnmatchcase(tag, p) for p in self.settings)

    def short_name(self, tag: str) -> str:
        """*tag* without the unit's ``prefix``: the shorter name shown in the table and matched by ``groups``/``hide``."""
        return tag[len(self.prefix):] if self.prefix and tag.startswith(self.prefix) else tag


@dataclass
class Meter:
    """One energy meter from ``ui.systems`` (``type: energy``)."""

    name: str
    conn: str
    tags: dict[str, str] = field(default_factory=dict)   # role → tag name

    def key(self, role: str) -> str | None:
        """The hub key ``conn/tag`` of *role* (e.g. ``power``), or None when the meter has no tag for it."""
        tag = self.tags.get(role)
        return f"{self.conn}/{tag}" if tag else None


def parse_systems(entries: Any) -> tuple[list[Vent], list[Meter], list[str]]:
    """Air handling units, energy meters and the problems found in ``ui.systems``."""
    vents: list[Vent] = []
    meters: list[Meter] = []
    problems: list[str] = []
    for i, e in enumerate(entries if isinstance(entries, list) else [], 1):
        if not isinstance(e, dict):
            problems.append(f"ui.systems[{i}]: {e!r}")
            continue
        kind = str(e.get("type") or "ventilation")
        name = str(e.get("name") or e.get("connection") or f"#{i}")
        if kind == "energy":
            for j, m in enumerate(e.get("meters") or [], 1):
                if not isinstance(m, dict) or not m.get("connection"):
                    problems.append(_("ui.systems[{i}].meters[{j}]: connection is missing").format(i=i, j=j))
                    continue
                tags = {**METER_ROLES, **{str(k): str(v) for k, v in (m.get("tags") or {}).items() if v}}
                meters.append(Meter(str(m.get("name") or m["connection"]), str(m["connection"]), tags))
            continue
        if kind not in ("ventilation", "panel"):
            problems.append(_("ui.systems[{i}]: unknown type {type} – use ventilation, panel or energy").format(
                i=i, type=repr(kind)))
            continue
        if not e.get("connection"):
            problems.append(_("ui.systems[{i}]: connection is missing").format(i=i))
            continue
        prefix = str(e.get("prefix") or "")
        tags: dict[str, str] = {}
        for role, tag in (e.get("tags") or {}).items():
            if role not in VENT_ROLES:
                problems.append(_("ui.systems[{i}]: unknown role {role}").format(i=i, role=repr(role)))
            elif tag:
                tags[str(role)] = prefix + str(tag)
        modes: dict[int, str] = {}
        for k, v in (e.get("mode_texts") or {}).items():
            try:
                modes[int(k)] = str(v)
            except (TypeError, ValueError):
                problems.append(_("ui.systems[{i}]: mode_texts: {key} is not a number").format(i=i, key=repr(k)))
        groups = []
        for j, g in enumerate(e.get("groups") or [], 1):
            if not isinstance(g, dict) or not g.get("title"):
                problems.append(_("ui.systems[{i}].groups[{j}]: title is missing").format(i=i, j=j))
                continue
            groups.append((str(g["title"]), _str_list(g.get("tags"))))
        vents.append(Vent(name, str(e["connection"]), tags, str(e.get("meter") or ""), modes,
                          str(e.get("short") or ""), _str_list(e.get("settings")), prefix, groups,
                          _str_list(e.get("hide")), kind))
    return vents, meters, problems


def _str_list(value: Any) -> list[str]:
    """A YAML value that may be one string or a list as a list of strings (None or empty → ``[]``)."""
    if not value:
        return []
    return [str(v) for v in (value if isinstance(value, list) else [value])]


# -- the settings tab: tags in sections ----------------------------------------------------------
@dataclass
class Group:
    """A section of the settings tab: title, TFM code (automatic groups) and (tag, label) rows."""

    title: str
    code: str = ""
    rows: list[tuple[dict, str]] = field(default_factory=list)


#: A TFM component code at the start of a tag name: two capitals and three digits (``JV401``, ``RT900``),
#: followed by ``_`` or the end of the name.
_TFM = re.compile(r"([A-Z]{2}\d{3})(?:_|$)")


def _auto_key(short: str) -> str:
    """``JV401`` for ``JV401_OP``, ``Sequence`` for ``Sequence_Kp1``, ``""`` without a structure."""
    if m := _TFM.match(short):
        return m.group(1)
    return short.split("_", 1)[0] if "_" in short else ""


def common_title(descriptions: list[str]) -> str:
    """The words the descriptions of a group start with (of the largest set sharing a first word)."""
    words = [d.replace("–", " ").split() for d in descriptions if d and d.split()]
    if not words:
        return ""
    by_first: dict[str, list[list[str]]] = {}
    for w in words:
        by_first.setdefault(w[0].lower(), []).append(w)
    best = max(by_first.values(), key=len)
    if len(best) < 2 and len(words) > 1:
        return ""
    title = []
    for i, word in enumerate(best[0]):
        if all(len(w) > i and w[i].lower() == word.lower() for w in best):
            title.append(word)
        else:
            break
    if len(best) == 1:          # one tag: its whole description is no title
        return ""
    return " ".join(title)


def strip_title(description: str, title: str) -> str:
    """``Tilluftsvifte – Alarm`` in the group «Tilluftsvifte JV401» → ``Alarm``.

    The whole title, or the title without a trailing TFM code, is removed when the description starts
    with it as whole words («Ferie og helligdager» does not make «Ferie aktiv» a bare «Aktiv»).
    """
    words = title.split()
    heads = [title]
    if len(words) > 1 and _TFM.fullmatch(words[-1] + "_"):
        heads.append(" ".join(words[:-1]))
    for head in heads:
        if description.lower().startswith(head.lower()) and description[len(head):len(head) + 1] in " –-:":
            rest = description[len(head):].strip(" –-:")
            if rest:
                return rest[0].upper() + rest[1:]
    return description


def group_tags(s: Vent, tags: list[dict]) -> list[Group]:
    """The tags of a unit in sections: the configured ``groups`` first, then automatic ones
    (TFM code or first part of the name, at least two tags), then the rest under «Other»."""
    items = [p for p in tags if s.shows_setting(p["name"])]
    used: set[str] = set()
    groups: list[Group] = []
    for title, patterns in s.groups:
        rows = [p for p in items if p["name"] not in used
                and any(fnmatch.fnmatchcase(s.short_name(p["name"]), pat) for pat in patterns)]
        used.update(p["name"] for p in rows)
        if rows:
            groups.append(Group(title, "", [(p, strip_title(p.get("description") or "", title)) for p in rows]))
    auto: dict[str, list[dict]] = {}
    for p in items:
        if p["name"] not in used:
            auto.setdefault(_auto_key(s.short_name(p["name"])), []).append(p)
    rest: list[dict] = []
    for key, rows in auto.items():
        if not key or len(rows) < 2:
            rest.extend(rows)
            continue
        title = common_title([p.get("description") or "" for p in rows])
        groups.append(Group(title or key, key if title and _TFM.fullmatch(key + "_") else "",
                            [(p, strip_title(p.get("description") or "", title)) for p in rows]))
    if rest:
        groups.append(Group(_("Other"), "", [(p, p.get("description") or "") for p in rest]))
    for g in groups:
        g.rows = [(p, label or s.short_name(p["name"])) for p, label in g.rows]
    return groups


# -- formatting ----------------------------------------------------------------------------------
def fmt(v: Value | None, decimals: int = 1) -> str:
    """``16.6 °C``; ``–`` when there is no value yet."""
    if v is None or v[0] is None:
        return "–"
    value, unit, _q = v
    if isinstance(value, bool):
        return _("on") if value else _("off")
    if isinstance(value, (int, float)):
        text = f"{value:.{decimals}f}" if isinstance(value, float) or decimals else str(value)
    elif isinstance(value, (list, tuple)):
        text = " ".join(str(x) for x in value)
    else:
        text = str(value)
    return f"{text} {unit}".strip() if unit else text


def on(v: Value | None) -> bool | None:
    """A value as on/off (numbers: > 0); None when unknown."""
    if v is None or v[0] is None:
        return None
    if isinstance(v[0], (bool, int, float)):
        return bool(v[0] > 0) if not isinstance(v[0], bool) else v[0]
    return None


@dataclass
class Colors:
    """Theme colours used in the drawing (hex or style names)."""

    ok: str = "green"
    warn: str = "yellow"
    bad: str = "red"
    dim: str = "dim"
    accent: str = "cyan"


# -- the air-flow drawing ------------------------------------------------------------------------
#: Box width (with the borders), the duct between two boxes and the text at the left end – and the
#: narrower set used when the drawing does not fit (side menu shown on a 120-column terminal).
WIDE = (12, 3, 12)
NARROW = (10, 1, 10)

#: Columns the wide drawing needs, the right-hand texts included.
WIDE_MIN = 114

#: A block of the drawing: four lines of equal width (border, label, value + duct, border).
Block = list[Text]


def _box(label: str, value: str, style: str, width: int) -> Block:
    """A framed component of the drawing: four lines, *label* and *value* centred and cut to the box width.

    Args:
        style: Rich style of the whole box (green when running, dim when off …).
        width: outer width including the two border characters.
    """
    inner = width - 2
    return [Text(f"┌{'─' * inner}┐", style), Text(f"│{label[:inner]:^{inner}}│", style),
            Text(f"│{value[:inner]:^{inner}}│", style), Text(f"└{'─' * inner}┘", style)]


def _duct(width: int, style: str, arrow: str = "") -> Block:
    """A straight duct; *arrow* ``▶`` at the right end, ``◀`` at the left end."""
    pipe = "━" * (width - len(arrow))
    line = pipe + arrow if arrow == "▶" else arrow + pipe
    return [Text(" " * width), Text(" " * width), Text(line, style), Text(" " * width)]


def _text_block(lines: list[str], width: int, style: str = "", styles: dict[int, str] | None = None) -> Block:
    """Up to four lines of plain text as a block (missing lines are blank).

    Args:
        width: pad and cut every line to this width; 0 keeps the lines as they are (the right-hand end of the
            drawing, where the text may run to the edge).
        style: style of every line, unless *styles* gives one for that line index.
    """
    styles = styles or {}
    return [Text(f"{lines[i] if i < len(lines) else '':<{width}}"[:width] if width else
                 (lines[i] if i < len(lines) else ""), styles.get(i, style)) for i in range(4)]


def _join(blocks: list[Block]) -> list[Text]:
    """Put blocks side by side: the four lines of each block are appended to the four output lines."""
    out = [Text() for _ in range(4)]
    for b in blocks:
        for i in range(4):
            out[i].append_text(b[i])
    return out


def render_vent(s: Vent, get: Callable[[str | None], Value | None], c: Colors, meter_power: Value | None = None,
                width: int = 0) -> Text:
    """The status line, the air-flow drawing and the week program (when set up) of one unit.

    Supply air runs left to right at the bottom, extract air right to left at the top; the heat
    recovery joins the two. Components without a tag become plain duct. *width*: the columns
    available (0 = unlimited); below :data:`WIDE_MIN` the narrow drawing is used.
    """
    box_w, gap_w, end_w = WIDE if not width or width >= WIDE_MIN else NARROW
    v = {role: get(s.key(role)) for role in VENT_ROLES}
    old = any(x is not None and x[2] not in ("good", "") for x in v.values())
    running = on(v["running"])
    if running is None:
        running = on(v["supply_fan_run"])
    if running is None:
        running = on(v["supply_fan"])
    alarm = on(v["alarm"])

    t = Text(no_wrap=True, overflow="crop")   # a wrapped line would tear the drawing apart
    if running is None:
        t.append("○ " + _("unknown"), style=c.dim)
    elif running:
        t.append("● " + _("running"), style=f"bold {c.ok}")
    else:
        t.append("○ " + _("stopped"), style=c.dim)
    t.append("   ")
    if alarm:
        t.append("⚠ " + _("ALARM"), style=f"bold reverse {c.bad}")
    elif alarm is False:
        t.append("✓ " + _("no alarm"), style=c.ok)
    if v["mode"] is not None and v["mode"][0] is not None:
        mode = v["mode"][0]
        text = s.mode_texts.get(int(mode), fmt(v["mode"], 0)) if isinstance(mode, (int, float)) else str(mode)
        t.append(f"   {_('Mode')}: {text}")
    if v["status"] is not None and v["status"][0] not in (None, ""):
        t.append(f"   {v['status'][0]}", style=c.accent)
    if v["setpoint"] is not None:
        t.append(f"   {_('Setpoint')} {fmt(v['setpoint'])}")
    if meter_power is not None:
        t.append(f"   {_('Power')} {fmt(meter_power, 2)}")
    if old:
        t.append("   " + _("(no contact – old values)"), style=f"italic {c.warn}")
    t.append("\n\n")

    duct = "" if running else c.dim

    def fan(role: str, run_role: str, label: str) -> Block:
        """The box of a fan: its output in % as the value, green when running.

        The run feedback (*run_role*) decides "running" when it is set up; otherwise an output above 0 does.
        """
        running_fan = on(v[run_role]) if v[run_role] is not None else on(v[role])
        return _box(label, fmt(v[role], 0), f"bold {c.ok}" if running_fan else c.dim, box_w)

    def filt(role: str) -> Block:
        """A filter box (OK / ⚠ Alarm / – when unknown), or plain duct when the unit has no filter tag for *role*."""
        if role not in s.tags:
            return _duct(box_w, duct)
        bad = on(v[role])
        value = "⚠ " + _("Alarm") if bad else ("OK" if bad is False else "–")
        return _box(_("Filter"), value, f"bold {c.warn}" if bad else ("" if bad is False else c.dim), box_w)

    def coil(label: str, role: str, style_on: str) -> Block:
        """A heating or cooling coil box with its output, in *style_on* while active, or plain duct without a tag."""
        if role not in s.tags:
            return _duct(box_w, duct)
        return _box(label, fmt(v[role], 0), style_on if on(v[role]) else c.dim, box_w)

    def flows(flow: str, pressure: str) -> str:
        """Air flow and pressure of one side as text after the drawing (empty when neither is set up)."""
        parts = [fmt(v[r], 0) for r in (flow, pressure) if v[r] is not None]
        return "   " + "  ".join(parts) if parts else ""

    rec = on(v["recovery"])
    rec_style = f"bold {c.accent}" if rec else c.dim
    inner = box_w - 2
    rec_label = _("Recovery")[:inner]
    rec_top = [Text(f"┌{'─' * inner}┐", rec_style), Text(f"│{rec_label:^{inner}}│", rec_style),
               Text(f"│◀{'━' * (inner - 2)}◀│", rec_style), Text(f"│{'╲  ╱':^{inner}}│", rec_style)]
    rec_mid = Text(f"│{fmt(v['recovery'], 0)[:inner]:^{inner}}│", rec_style)
    rec_bot = [Text(f"│{'╱  ╲':^{inner}}│", rec_style), Text(f"│{'':^{inner}}│", rec_style),
               Text(f"│▶{'━' * (inner - 2)}▶│", rec_style), Text(f"└{'─' * inner}┘", rec_style)]
    left, right = _duct(gap_w, duct, "◀"), _duct(gap_w, duct, "▶")

    top = _join([
        _text_block(["", f" {_('Exhaust')}", f"{fmt(v['exhaust']):>9} "], end_w),
        left, fan("extract_fan", "extract_fan_run", _("Fan")), left, rec_top, left, _duct(box_w, duct),
        left, _duct(box_w, duct), left, filt("filter_extract"), left,
        _text_block(["", flows("extract_flow", "extract_pressure"), f" {fmt(v['extract'])}  {_('Extract')}"],
                    0, styles={1: c.dim}),
    ])
    bottom = _join([
        _text_block(["", f" {_('Outdoor')}", f"{fmt(v['outdoor']):>9} "], end_w),
        right, filt("filter_supply"), right, rec_bot, right, coil(_("Heat"), "heating", f"bold {c.bad}"),
        right, coil(_("Cool"), "cooling", f"bold {c.accent}"), right,
        fan("supply_fan", "supply_fan_run", _("Fan")), right,
        _text_block(["", "", f" {fmt(v['supply'])}  {_('Supply')}", flows("supply_flow", "supply_pressure")],
                    0, styles={3: c.dim}),
    ])
    for line in top:
        t.append_text(line)
        t.append("\n")
    t.append(" " * (end_w + gap_w + box_w + gap_w))
    t.append_text(rec_mid)
    for line in bottom:
        t.append("\n")
        t.append_text(line)
    if (week := render_week(v, c)) is not None:
        t.append("\n\n")
        t.append_text(week)
    return t


def render_week(v: dict[str, Value | None], c: Colors) -> Text | None:
    """``Week  Mon 05:00–21:00  Tue …  Sun off  Overtime on`` – None without a week program."""
    def day_list(role: str) -> list | None:
        """The value of *role* as a list (one entry per day), or None when it is missing or not an array."""
        x = v.get(role)
        return list(x[0]) if x is not None and isinstance(x[0], (list, tuple)) else None

    sh, sm, eh, em = (day_list(r) for r in ("week_start_hour", "week_start_minute", "week_stop_hour",
                                             "week_stop_minute"))
    if sh is None or eh is None:
        return None
    enabled = day_list("week_enabled")
    today = time.localtime().tm_wday
    t = Text(f" {_('Week')} ", style="bold")
    for i, day in enumerate(DAYS):
        if i >= len(sh) or i >= len(eh):
            break
        active = enabled is None or (i < len(enabled) and bool(enabled[i]))
        start = f"{int(sh[i]):02d}:{int(sm[i]) if sm and i < len(sm) else 0:02d}"
        stop = f"{int(eh[i]):02d}:{int(em[i]) if em and i < len(em) else 0:02d}"
        style = ("bold " if i == today else "") + ("" if active else c.dim)
        t.append(f" {_(day)} ", style=style)
        t.append(f"{start}–{stop}" if active else _("off"), style=style or None)
    if on(v.get("overtime")):
        t.append("   " + _("Overtime on"), style=f"bold {c.warn}")
    return t


def theme_colors(app: Any) -> Colors:
    """The drawing colours from the active Textual theme (success/warning/error/accent), with plain fallbacks.

    Called at every redraw, so a theme change (Ctrl+T) is picked up without caching anything.
    """
    tv = getattr(app, "theme_variables", {}) or {}
    return Colors(ok=tv.get("success", "green"), warn=tv.get("warning", "yellow"), bad=tv.get("error", "red"),
                  dim="dim", accent=tv.get("accent", "cyan"))


class ModeDialog(ModalScreen["int | None"]):
    """The system switch of a unit: one button per ``mode_texts`` entry. Returns the mode or None."""

    BINDINGS: ClassVar = [Binding("escape", "dismiss(None)", _("Cancel"))]

    def __init__(self, title: str, modes: dict[int, str], current: Any) -> None:
        """Prepare the dialog.

        Args:
            title: dialog title (``Mode – <unit name>``).
            modes: the unit's ``mode_texts`` (value written → button text).
            current: the current mode value; its button is highlighted. None or a non-int shows "–".
        """
        super().__init__()
        self.title_ = title
        self.modes = modes
        self.current = current

    def compose(self) -> ComposeResult:
        """The current mode and one button per mode (the current one highlighted), then Cancel."""
        with Vertical(classes="dialog"):
            yield Label(self.title_, classes="dialog-title", markup=False)
            now = self.modes.get(self.current, "–") if isinstance(self.current, int) else "–"
            yield Static(_("Now: {value} {unit}").format(value=now, unit=""), markup=False)
            with Horizontal(classes="dialog-buttons"):
                for mode, text in self.modes.items():
                    yield Button(text, id=f"mode-{mode}", variant="primary" if mode == self.current else "default")
                yield Button(_("Cancel"), id="mode-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Return the mode of the pressed button (``mode-<n>``), or None for Cancel."""
        bid = event.button.id or ""
        self.dismiss(int(bid[5:]) if bid.startswith("mode-") and bid != "mode-cancel" else None)


# -- the view ------------------------------------------------------------------------------------
class SystemsView(View):
    """Ctrl+E: one air handling unit – drawing and charts, or the tags of its connection in sections.

    Switching units must feel instant (the menu cursor selects a unit on every move): tag lists,
    values and chart curves are kept per unit, the server is asked only for what is missing, and
    the settings table is filled only while its tab is shown.
    """

    BINDINGS: ClassVar = [
        Binding("pagedown", "unit(1)", _("Next unit"), show=False),
        Binding("pageup", "unit(-1)", _("Previous unit"), show=False),
        Binding("w", "write", _("Write"), show=False),
        Binding("m", "mode", _("Operating mode"), show=False),
    ]

    #: Columns of the settings table: (key, English heading, width).
    TAG_COLUMNS: ClassVar = (("description", N_("Description"), 46), ("value", N_("Value"), 20),
                             ("name", N_("Tag"), 30), ("writable", "✎", 2))

    #: Seconds after which the tag list of a connection is fetched again when the view is shown.
    POINTS_MAX_AGE = 300

    def __init__(self, **kw: Any) -> None:
        """Create the empty view; the units are parsed from ``ui.systems`` in :meth:`compose`."""
        super().__init__(**kw)
        self.vents: list[Vent] = []
        self.meters: list[Meter] = []
        self.problems: list[str] = []
        self.index = 0
        self.values: dict[str, Value] = {}           # "conn/tag" → (value, unit, quality)
        self.tags: dict[str, list[dict]] = {}        # connection → tag info from the server
        self._tags_at: dict[str, float] = {}
        self.curves: dict[int, dict[str, list[tuple[float, float]]]] = {}   # unit → chart curves
        self._curves_at: dict[int, float] = {}
        self._changed: set[str] = set()              # keys changed since the last refresh
        self._table_unit = -1                        # unit shown in the settings table (-1: must be filled)
        self._load_timer: Any = None
        self._dirty = True
        self._plot_dirty = False
        self._plot_at = 0.0

    @property
    def vent(self) -> Vent | None:
        """The unit shown now, or None when ``ui.systems`` has no air handling units or panels."""
        return self.vents[self.index] if self.vents else None

    @property
    def series(self) -> dict[str, list[tuple[float, float]]]:
        """The chart curves of the current unit."""
        return self.curves.get(self.index, {})

    def compose(self) -> ComposeResult:
        """Parse ``ui.systems`` and build two tabs: Overview (drawing + two charts) and Settings (filter + table).

        Without units only a hint is shown. Problems in ``ui.systems`` are listed below so a typo in
        ``config.yaml`` is visible instead of silently ignored.
        """
        self.vents, self.meters, self.problems = parse_systems(self.scada.ui.get("systems"))
        if not self.vents:
            yield Static(_("No air handling units set up. Add ui.systems to config.yaml – see the user guide "
                           "(docs/*/04-configuration.md, «Systems view»)."), classes="muted")
        else:
            with TabbedContent(id="sys-tabs"):
                with TabPane(_("Overview"), id="sys-tab-overview"):
                    yield LiveStatic("", id="sys-card", classes="card")
                    with Horizontal(id="sys-charts"):
                        yield CachedPlot(id="sys-temp-plot", classes="chart")
                        yield CachedPlot(id="sys-out-plot", classes="chart")
                with TabPane(_("Settings"), id="sys-tab-settings"):
                    yield Input(placeholder=_("Filter by tag or description …"), id="sys-filter")
                    yield FastTable(id="sys-tags", cursor_type="row", zebra_stripes=False)
        if self.problems:
            yield Static("\n".join(self.problems), classes="muted")

    def on_mount(self) -> None:
        """Add the table columns, set the card title and start the 1 s redraw timer (:meth:`_refresh_if_dirty`)."""
        if self.vents:
            t = self.query_one("#sys-tags", FastTable)
            for key, heading, width in self.TAG_COLUMNS:
                t.add_column(_(heading), key=key, width=width)
            self._title()
        self.set_interval(1, self._refresh_if_dirty)

    def _title(self) -> None:
        """Put the unit's name on the card border and fix the card height (see the comment inside)."""
        if (s := self.vent) is not None:
            card = self.query_one("#sys-card", LiveStatic)
            card.border_title = s.name
            # The same height for every unit: a new height costs a layout of the whole screen.
            height = 17 if any("week_start_hour" in v.tags for v in self.vents) else 15
            if card.styles.height is None or card.styles.height.value != height:
                card.styles.height = height

    def focus_target(self) -> Any:
        """The tab bar: focusing the settings table would switch to its tab."""
        return next(iter(self.query("#sys-tabs Tabs")), None)

    def _settings_shown(self) -> bool:
        """True when the view is visible and its Settings tab is the active one."""
        return self.display and self.query_one("#sys-tabs", TabbedContent).active == "sys-tab-settings"

    # -- unit selection ---------------------------------------------------------------------------
    def select(self, index: int) -> None:
        """Show unit *index* (from the menu): at once from what is cached, the rest shortly after."""
        if not self.vents:
            return
        index = max(0, min(index, len(self.vents) - 1))
        if index == self.index and self.index in self.curves:
            return
        self.index = index
        self._title()
        self._sync_tab()
        self._dirty = True
        self._refresh_if_dirty()
        self.replot()
        if self._settings_shown():
            self._fill_tags()
        # Moving the cursor through the menu selects every unit on the way: wait until it stops.
        if self._load_timer is not None:
            self._load_timer.stop()
        self._load_timer = self.set_timer(0.15, self.load)

    def action_unit(self, step: int) -> None:
        """PgUp/PgDn: the previous/next unit (wrapping), with the menu cursor following.

        Args:
            step: +1 or -1.
        """
        if self.vents:
            self.select((self.index + step) % len(self.vents))
            node = getattr(self.scada, "menu_nodes", {}).get(f"system{self.index}")
            if node is not None:
                self.scada.main.query_one("#menu").move_cursor(node)

    # -- data -------------------------------------------------------------------------------------
    def connections(self) -> list[str]:
        """All connections the units use (their own and their meters'), without duplicates, in config order.

        Used by :meth:`on_points` to drop values of connections this view does not show.
        """
        conns = [s.conn for s in self.vents] + [s.meter for s in self.vents if s.meter]
        return list(dict.fromkeys(conns))

    def on_show_view(self) -> None:
        """Called when the view becomes visible: pick the right tab and load what is missing or stale."""
        self._sync_tab()
        self.load()

    def _sync_tab(self) -> None:
        """A panel has no drawing: show its tags. Back on a unit, show the drawing again."""
        s = self.vent
        if s is None:
            return
        tabs = self.query_one("#sys-tabs", TabbedContent)
        shown = self.vents[self._table_unit] if 0 <= self._table_unit < len(self.vents) else None
        if s.kind == "panel":
            tabs.active = "sys-tab-settings"
        elif tabs.active == "sys-tab-settings" and shown is not None and shown.kind == "panel":
            tabs.active = "sys-tab-overview"

    @work(exclusive=True, group="systems")
    async def load(self) -> None:
        """Worker: fetch the tag lists of the current unit and its meter, and its 24 h chart curves.

        Only what is older than :attr:`POINTS_MAX_AGE` (tags) or 5 minutes (curves) is fetched, so walking
        through the units in the menu stays fast. The requests for several connections run in parallel.
        Nothing is done while the server is not connected.
        """
        s, index = self.vent, self.index
        if s is None or not self.scada.client.connected:
            return
        now = time.time()
        stale = [c for c in dict.fromkeys(c for c in (s.conn, s.meter) if c)
                 if now - self._tags_at.get(c, 0) > self.POINTS_MAX_AGE]
        for conn, points in zip(stale, await asyncio.gather(
                *(self.scada.request("points", conn=c, limit=5000) for c in stale))):
            self.tags[conn] = points or []
            self._tags_at[conn] = now
            for p in points or []:
                self.values[f"{conn}/{p['name']}"] = (p.get("value"), p.get("unit") or "", p.get("q") or "")
        if stale:
            self._dirty = True
            self._table_unit = -1
            self._refresh_if_dirty()
            if self._settings_shown():
                self._fill_tags()
        if now - self._curves_at.get(index, 0) > 300:
            self._curves_at[index] = now
            await self._load_history(s, index)

    async def _load_history(self, s: Vent, index: int) -> None:
        """Fetch the 24 h log of every chart role of unit *s* (at most 600 points each) and draw if still shown.

        Args:
            s: the unit.
            index: its position, used as the key of :attr:`curves`; the answer is kept even if the user moved on.
        """
        keys = [k for role, _name in (*TEMP_CURVES, *OUTPUT_CURVES) if (k := s.key(role))]
        results = await asyncio.gather(
            *(self.scada.request("history", point=k, hours=24, max_points=600) for k in keys))
        self.curves[index] = {k: [(float(p[0]), float(p[1])) for p in pts or [] if p[1] is not None]
                              for k, pts in zip(keys, results)}
        if index == self.index:
            self.replot()

    def on_points(self, values: dict[str, list]) -> None:
        """Live values (``points`` messages): only the keys of the configured units are kept."""
        conns = self.connections()
        for key, (v, ts, q) in values.items():
            old = self.values.get(key)
            if old is None and key.split("/", 1)[0] not in conns:
                continue
            self.values[key] = (v, old[1] if old else "", q)
            self._changed.add(key)
            self._dirty = True
            if isinstance(v, (int, float)):
                for i, curves in self.curves.items():
                    if key in curves:
                        curves[key].append((float(ts or time.time()), float(v)))
                        self._plot_dirty = self._plot_dirty or i == self.index

    def get(self, key: str | None) -> Value | None:
        """The kept value of *key* (``conn/tag``) as (value, unit, quality), or None when unknown or *key* is None.

        Passed to :func:`render_vent` as its value getter.
        """
        return self.values.get(key) if key else None

    # -- settings tab -----------------------------------------------------------------------------
    def _fill_tags(self) -> None:
        """Fill the settings table with the tags of the current unit in sections, filtered by the filter words.

        Every word must occur in the section title, the label or the tag name. Section rows get a key starting
        with ``#group#`` so they can never be mistaken for a tag (writing ignores them).
        """
        s = self.vent
        if s is None:
            return
        self._table_unit = self.index
        table = self.query_one("#sys-tags", FastTable)
        words = self.query_one("#sys-filter", Input).value.lower().split()
        head = f"bold {theme_colors(self.app).accent}"
        table.clear()
        count = 0
        for g in group_tags(s, self.tags.get(s.conn, [])):
            rows = [(p, label) for p, label in g.rows
                    if all(w in f"{g.title} {label} {p['name']}".lower() for w in words)]
            if not rows:
                continue
            title = Text(g.title, style=head)
            if g.code:
                title.append(f"  {g.code}", style="dim")
            table.add_row(title, "", "", "", key=f"#group#{g.title}#{count}")
            for p, label in rows:
                table.add_row(Text("  " + label), fmt(self.get(f"{s.conn}/{p['name']}"), 2),
                              Text(s.short_name(p["name"]), style="dim"), "✎" if p.get("writable") else "",
                              key=p["name"])
                count += 1
        table.border_title = _("{n} tags of {conn}").format(n=count, conn=s.conn)

    def on_input_changed(self, event: Input.Changed) -> None:
        """Typing in the filter field refills the table."""
        if event.input.id == "sys-filter":
            self._fill_tags()

    # -- writing (only tags with writable: true; every write is confirmed and logged by the server) -------
    def _point(self, conn: str, name: str) -> dict | None:
        """The tag info of *name* on *conn* as the server listed it (``writable``, ``priority`` …), or None."""
        return next((p for p in self.tags.get(conn, []) if p["name"] == name), None)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Enter on a tag row: write to it (same as w)."""
        if event.data_table.id == "sys-tags":
            self.action_write()

    def action_write(self) -> None:
        """w/Enter on the Settings tab: write a value to the selected tag.

        Only tags the server reports as ``writable`` can be written; the value goes through the write dialog and a
        confirmation (:func:`confirm_point_write`), and the server logs it with the user.
        """
        s = self.vent
        if s is None or not self._settings_shown():
            return
        table = self.query_one("#sys-tags", FastTable)
        if not table.row_count:
            return
        name = str(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value)
        p = self._point(s.conn, name)
        if p is None:
            return                               # a section row
        if not p.get("writable"):
            self.scada.notify(_("{tag} is read-only").format(tag=f"{s.conn}/{name}"), severity="warning")
            return
        title = f"{p.get('description') or name}  ({s.conn}/{name})"
        point = {**p, "value": (self.get(f"{s.conn}/{name}") or (None,))[0]}
        old = fmt(self.get(f"{s.conn}/{name}"), 2)
        self.app.push_screen(PointDialog(title, point),
                             lambda w: confirm_point_write(self.scada, s.conn, name, p, w, old))

    def action_mode(self) -> None:
        """m: set the unit's operating mode (``mode`` tag) with a button per ``mode_texts`` entry, then confirm.

        Refused with a warning when the unit has no mode set up or the tag is read-only.
        """
        s = self.vent
        if s is None or not s.mode_texts or "mode" not in s.tags:
            self.scada.notify(_("No operating mode (mode, mode_texts) set up for this unit"), severity="warning")
            return
        name = s.tags["mode"]
        p = self._point(s.conn, name) or {}
        if not p.get("writable"):
            self.scada.notify(_("{tag} is read-only").format(tag=f"{s.conn}/{name}"), severity="warning")
            return
        current = (self.get(s.key("mode")) or (None,))[0]
        current = int(current) if isinstance(current, (int, float)) and not isinstance(current, bool) else None
        modes = s.mode_texts

        def chosen(mode: int | None) -> None:
            """Confirm and write the chosen mode; choosing the current mode (or Cancel) does nothing."""
            if mode is not None and mode != current:
                # The mode is written with the default priority: the mode dialog has no priority choice.
                confirm_point_write(self.scada, s.conn, name, {**p, "description": f"{_('Mode')} – {s.name}"},
                                    PointWrite(mode), fmt(self.get(f"{s.conn}/{name}"), 2),
                                    new=modes.get(mode, str(mode)))

        self.app.push_screen(ModeDialog(f"{_('Mode')} – {s.name}", modes, current), chosen)

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        """Settings tab shown: fill the table if it belongs to another unit. Overview shown: redraw everything."""
        if event.pane.id == "sys-tab-settings" and self._table_unit != self.index:
            self._fill_tags()
        elif event.pane.id == "sys-tab-overview":
            self._dirty = True
            self.replot()

    # -- drawing ----------------------------------------------------------------------------------
    def on_theme_changed(self) -> None:
        """Ctrl+T: colours are baked into the drawing, the section titles and the charts – redraw all of them."""
        self._dirty = True
        self._table_unit = -1
        self.replot()

    def on_resize(self) -> None:
        """The width decides between the wide and the narrow drawing: redraw after the new size is known."""
        self._dirty = True           # wide or narrow drawing
        self.call_after_refresh(self.replot)

    def _refresh_if_dirty(self) -> None:
        """Timer (1 s): redraw what changed since the last tick.

        Live values only mark the view dirty (:meth:`on_points`); drawing here at most once a second keeps a
        burst of values from redrawing the card for each one. On the Settings tab only the changed cells are
        updated. The charts are redrawn at most every 30 s, since one value hardly changes a 24 h chart.
        """
        if not self.display or (s := self.vent) is None:
            return
        if self._settings_shown():
            self._update_table(s)
            return
        if self._plot_dirty and time.time() - self._plot_at > 30:
            self.replot()
        if not self._dirty:
            return
        self._dirty = False
        self._changed.clear()
        power = self.get(f"{s.meter}/{METER_ROLES['power']}") if s.meter else None
        card = self.query_one("#sys-card", LiveStatic)
        if s.kind == "panel":
            card.set(Text(_("No drawing for this system – its tags are on the Settings tab."), style="dim"))
        else:
            card.set(render_vent(s, self.get, theme_colors(self.app), power, card.size.width - 2))
        card.set_class(bool(on(self.get(s.key("alarm")))), "bad")

    def _update_table(self, s: Vent) -> None:
        """Update the value cells of the tags that changed (filling the table first if it shows another unit)."""
        if self._table_unit != self.index:
            self._fill_tags()
        table = self.query_one("#sys-tags", FastTable)
        prefix = s.conn + "/"
        for key in self._changed:
            if key.startswith(prefix) and key[len(prefix):] in table.rows:
                table.update_cell(key[len(prefix):], "value", fmt(self.values[key], 2))
        self._changed.clear()
        self._dirty = True               # the drawing is redrawn when the overview tab is back

    def replot(self) -> None:
        """Draw the two charts of the current unit (after loading the log and every 30 s with new values)."""
        s = self.vent
        if s is None or not self.display or self._settings_shown():
            return
        self._plot_at = time.time()
        self._plot_dirty = False
        now = time.time()
        start = now - 86400
        pal = palette(self.app)
        curves = self.series
        for plot_id, roles, title in (("#sys-temp-plot", TEMP_CURVES, _("Temperatures – last 24 hours")),
                                      ("#sys-out-plot", OUTPUT_CURVES, _("Outputs – last 24 hours"))):
            plot = self.query_one(plot_id, CachedPlot)
            # Back on a unit whose curves did not change: its figure is kept, nothing to draw.
            key = (self.index, tuple(len(curves.get(s.key(r) or "", ())) for r, _n in roles))
            if plot.cached(key):
                plot.changed(key)
                continue
            plt = plot.plt
            plt.clear_data()
            plt.clear_figure()
            series = []
            for i, (role, name) in enumerate(roles):
                key = s.key(role)
                pts = [p for p in curves.get(key or "", []) if p[0] >= start - 3600]
                if pts:
                    series.append((_(name), pts, pal[i % len(pal)]))
            draw(plt, series, "line", start, now, plot.size.width)
            if series or self.index in self.curves:
                plt.title(title if series else title + "  " + _("(not logged)"))
            else:
                plt.title(title)
            plot.changed(key)
