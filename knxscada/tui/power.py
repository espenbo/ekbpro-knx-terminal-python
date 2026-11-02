# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""Power limiter card for the "Systems" view (``ui.systems`` entries with ``type: power``).

A power limiter ("maksvokter") in a PLC switches loads off – water heater, floor heating, EV charger – when
the consumption of the building gets close to a set limit, and back on when there is room again. The card
shows the consumption as a bar against the limit and one line per load (switched off or in, how often it
was switched off and its timing)::

    - name: Power limiter
      type: power
      connection: plc2
      tags:
        consumption: {connection: knx, tag: "0/1/30"}   # the same meter value is on KNX: read it there
        setpoint: forbrukSetpunkt
        overuse: overforbruk
        shed_count: PRG_iTotalLoadsDisconnected
        peak: {connection: plc2_bacnet, tag: rkWMaxMoth}
      loads: 9                                          # or a list: names, or {name:, tags:} per load
      load_tags:                                        # {n} = 1, 2, … – one set of tags per load
        name: "sLast_{n}_Navn"
        shed: "Last_{n}_kobletUt"
        status: "Last_{n}_status"
        count: "Last_{n}_gangerUtkoblet"
        min_off: "Last_{n}_minUtkoblingstid"
        max_off: "Last_{n}_maksUtkoblingstid"
        min_on: "Last_{n}_minInnkoblingstid"
      time_unit: min

Where the loads are electric heaters, each load can show its room: ``loads`` as a list with ``tags`` per load
(merged over ``load_tags``) for the relay's status, the room temperature and the thermostat's heat demand::

      loads:
        - {}                                            # 1: water heater – no room
        - {tags: {relay: {connection: knx, tag: "0/3/4"}, temp: {connection: knx, tag: "3/0/1"},
                  heat: {connection: knx, tag: "3/2/15"}}}
        - {relay_inverted: true, tags: {relay: {connection: knx, tag: "0/3/1"}}}   # feedback on = load cut

A load that is switched off while its room asks for heat is marked, so it is seen when shedding makes a room cold;
a load that is switched off while its relay still reports on is marked as a fault (the shedding did not work).

A tag is either a name of the entry's connection (with its ``prefix``) or ``{connection: …, tag: …}`` for
another connection – values that also exist on KNX are read from KNX, so the card and the overview show the
same number. Every role is optional; a missing one is left out of the card.

The card is only read: switching loads is the PLC's job, and its settings are written on the Settings tab
(``writable`` tags, with confirmation) like any other system. :func:`render_power` is a pure function of the
configuration and a value lookup, so it is tested without a terminal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from rich.text import Text

from ..i18n import _

if TYPE_CHECKING:
    from collections.abc import Callable

    from .systems import Colors

#: Roles of the whole limiter (keys under ``tags:``): consumption and limit in the same unit (kW), the
#: consumption above the limit (negative = room left), the number of loads switched off now and the highest
#: consumption of the month.
POWER_ROLES = ("consumption", "setpoint", "overuse", "shed_count", "peak")

#: Roles of one load (keys under ``load_tags:`` or a load's ``tags:``): its name, switched off by the limiter, its
#: own status (in operation), how many times it was switched off, its timing (minimum/maximum time off, minimum
#: time on – in ``time_unit``), the status of the relay that switches it (the actuator's feedback: is it really on),
#: and for a heater the temperature of its room and the room's heat demand.
LOAD_ROLES = ("name", "shed", "status", "count", "min_off", "max_off", "min_on", "relay", "temp", "heat")

#: The bar's scale: the limit sits at this share of the bar, so some overuse still fits on it.
LIMIT_AT = 0.8

#: Share of the limit from which the bar turns to the warning colour.
WARN_AT = 0.8

#: A value as the views keep it: (value, unit, quality).
Value = tuple[Any, str, str]


@dataclass
class Load:
    """One load of the limiter: a fixed name (or "") and the hub key ``conn/tag`` per role in :data:`LOAD_ROLES`."""

    number: int
    name: str = ""
    keys: dict[str, str] = field(default_factory=dict)
    #: The relay's feedback is on when the load is OFF – a contactor that cuts a water heater when energised
    #: (``relay_inverted: true`` on the load). The Relay column always shows whether the load gets power.
    relay_inverted: bool = False


@dataclass
class Power:
    """The parsed ``type: power`` part of an entry: role keys, loads and the unit of the load times."""

    keys: dict[str, str] = field(default_factory=dict)    # role → hub key conn/tag
    loads: list[Load] = field(default_factory=list)
    time_unit: str = "min"

    def all_keys(self) -> list[str]:
        """Every hub key the card reads, in order and without duplicates (for loading values)."""
        keys = list(self.keys.values()) + [k for load in self.loads for k in load.keys.values()]
        return list(dict.fromkeys(keys))

    def connections(self) -> list[str]:
        """The connections of :meth:`all_keys` (the entry's own and the ones set per tag)."""
        return list(dict.fromkeys(k.split("/", 1)[0] for k in self.all_keys()))


def tag_key(spec: Any, conn: str, prefix: str) -> str | None:
    """The hub key of one tag setting: a plain name of the entry's connection, or ``{connection:, tag:}``.

    Args:
        spec: the value under ``tags:``/``load_tags:``.
        conn: the entry's connection.
        prefix: the entry's ``prefix`` (only for plain names; another connection takes ``prefix`` from *spec*).

    Returns:
        ``conn/tag``, or None when *spec* names no tag.
    """
    if isinstance(spec, dict):
        tag = spec.get("tag")
        if not tag:
            return None
        return f"{spec.get('connection') or conn}/{spec.get('prefix') or ''}{tag}"
    return f"{conn}/{prefix}{spec}" if spec not in (None, "") else None


def parse_power(e: dict, conn: str, prefix: str) -> tuple[Power, list[str]]:
    """Read the ``type: power`` keys of one ``ui.systems`` entry.

    Args:
        e: the entry.
        conn: its connection; prefix: its ``prefix``.

    Returns:
        (:class:`Power`, problems) – the problems are short English texts without the entry number.
    """
    problems: list[str] = []
    p = Power(time_unit=str(e.get("time_unit") or "min"))
    for role, spec in (e.get("tags") or {}).items():
        if role not in POWER_ROLES:
            problems.append(_("unknown role {role}").format(role=repr(role)))
        elif (key := tag_key(spec, conn, prefix)) is not None:
            p.keys[str(role)] = key
    loads = e.get("loads")
    own: list[dict] = []                    # per load: its own tags (a list entry {name:, tags:})
    inverted: list[bool] = []               # per load: relay_inverted
    if isinstance(loads, int) and not isinstance(loads, bool):
        names = [""] * max(0, loads)
    elif isinstance(loads, list):
        names = [str((n.get("name") if isinstance(n, dict) else n) or "") for n in loads]
        own = [dict(n.get("tags") or {}) if isinstance(n, dict) else {} for n in loads]
        inverted = [isinstance(n, dict) and n.get("relay_inverted") is True for n in loads]
    else:
        names = []
        problems.append(_("loads: a number or a list of names"))
    for tags in own:
        problems += [_("unknown load role {role}").format(role=repr(r)) for r in tags if r not in LOAD_ROLES]
    templates = e.get("load_tags") or {}
    for role in templates:
        if role not in LOAD_ROLES:
            problems.append(_("unknown load role {role}").format(role=repr(role)))
    for n, name in enumerate(names, 1):
        keys = {}
        # A load's own tags win over the templates.
        for role, spec in {**templates, **(own[n - 1] if n <= len(own) else {})}.items():
            if role not in LOAD_ROLES:
                continue
            # {n} in the tag name (or in the tag of a {connection:, tag:} setting) is the load's number.
            spec = {**spec, "tag": str(spec.get("tag", "")).replace("{n}", str(n))} if isinstance(spec, dict) \
                else str(spec).replace("{n}", str(n))
            if (key := tag_key(spec, conn, prefix)) is not None:
                keys[str(role)] = key
        p.loads.append(Load(n, name, keys, n <= len(inverted) and inverted[n - 1]))
    return p, problems


def _num(v: Value | None) -> float | None:
    """The value as a float; None when unknown or not a number (a bool is not a measurement here)."""
    if v is None or isinstance(v[0], bool) or not isinstance(v[0], (int, float)):
        return None
    return float(v[0])


def _flag(v: Value | None) -> bool | None:
    """The value as on/off (numbers: > 0); None when unknown."""
    if v is None or v[0] is None:
        return None
    if isinstance(v[0], bool):
        return v[0]
    return bool(v[0] > 0) if isinstance(v[0], (int, float)) else None


def _kw(v: Value | None) -> str:
    """A power value with two decimals and its unit (kW when the tag has none); ``–`` when unknown."""
    x = _num(v)
    return "–" if x is None else f"{x:.2f} {(v[1] if v else '') or 'kW'}"


def bar(consumption: float | None, limit: float | None, width: int, c: Colors) -> Text:
    """The consumption bar: filled up to the consumption, ``│`` at the limit, coloured by the share of the limit.

    The limit sits at :data:`LIMIT_AT` of the width, so an overuse is still drawn (in the alarm colour) up to
    25 % above it. Without a limit the bar is scaled to the consumption and has no mark.

    Args:
        width: characters of the bar.
    """
    if width <= 0:
        return Text()
    if consumption is None:
        return Text("░" * width, c.dim)
    scale = (limit / LIMIT_AT) if limit and limit > 0 else max(consumption, 1e-9)
    filled = max(0, min(width, round(consumption / scale * width)))
    mark = min(width - 1, round(LIMIT_AT * width)) if limit and limit > 0 else -1
    share = consumption / limit if limit and limit > 0 else 0
    style = c.bad if share > 1 else c.warn if share >= WARN_AT else c.ok
    out = Text()
    for i in range(width):
        if i == mark:
            out.append("│", "bold")
        elif i < filled:
            out.append("█", style)
        else:
            out.append("░", c.dim)
    return out


def render_power(p: Power, get: Callable[[str | None], Value | None], c: Colors, width: int = 100) -> Text:
    """The card: a consumption bar with the limit, a summary line and a table of the loads.

    Args:
        p: the parsed limiter.
        get: value lookup by hub key (``None`` → ``None``).
        c: theme colours.
        width: the card's inner width.

    Returns:
        A Rich text of ``3 + 1 + len(loads)`` lines (the card height is set from that in the view).
    """
    val = lambda role: get(p.keys.get(role))   # role → value of the limiter
    cons, limit = _num(val("consumption")), _num(val("setpoint"))
    out = Text()
    label = f"{_('Consumption'):<12}"
    figures = f"  {_kw(val('consumption'))} / {_kw(val('setpoint'))}"
    if cons is not None and limit:
        # A limit set very low (a test, a typo) gives silly shares like 9500 %: above 999 % the figure says only that.
        figures += f"  ({cons / limit:.0%})" if cons / limit < 10 else "  (>999%)"
    out.append(label, "bold")
    out.append_text(bar(cons, limit, max(10, width - len(label) - len(figures) - 1), c))
    out.append(figures + "\n")

    over = _num(val("overuse"))
    if cons is not None and limit:
        # Computed from the bar's own two figures: a PLC may keep its overuse at 0 while there is room left (it only
        # counts consumption above the limit), which would read "room left 0".
        over = cons - limit
    shed = _num(val("shed_count"))
    if shed is None:
        shed = float(sum(1 for load in p.loads if _flag(get(load.keys.get("shed")))))
    parts = []
    if over is not None:
        # The PLC's overuse is consumption − limit: negative means room left, which is what the user wants to see.
        unit = (val("setpoint") or val("overuse") or (None, "kW", ""))[1]
        parts.append((_("Room left {value}").format(value=_kw((abs(over), unit, ""))) if over <= 0
                      else _("Over the limit by {value}").format(value=_kw((over, unit, ""))),
                      c.ok if over <= 0 else c.bad))
    if p.loads:
        parts.append((_("{n} of {total} loads switched off").format(n=int(shed), total=len(p.loads)),
                      c.warn if shed else c.ok))
    if (peak := val("peak")) is not None and _num(peak) is not None:
        parts.append((_("Highest this month {value}").format(value=_kw(peak)), ""))
    for i, (text, style) in enumerate(parts):
        out.append("  ·  " if i else " " * 12, c.dim)
        out.append(text, style)
    out.append("\n\n")

    if not p.loads:
        return out
    unit = p.time_unit
    name_w = max(8, *(len(_load_name(load, get)) for load in p.loads)) + 2
    # The room columns only when some load has a room (heaters); a limiter of other loads keeps the narrow table.
    rooms = any("temp" in load.keys or "heat" in load.keys for load in p.loads)
    relays = any("relay" in load.keys for load in p.loads)
    # The PLC's own status column only when a load has a status tag (what it means differs between PLC programs).
    running = any("status" in load.keys for load in p.loads)
    head = f"{'#':>2}  {_('Load'):<{name_w}}{_('State'):<17}"
    if running:
        head += f"{_('Running'):<9}"
    if relays:
        head += f"{_('Relay'):<10}"
    if rooms:
        head += f"{_('Room'):>8}  {_('Heat demand'):<13}"
    head += f"{_('Times off'):>10}{_('Min off'):>10}{_('Max off'):>10}{_('Min on'):>10}"
    out.append(head[:width] + "\n", "bold")
    for load in p.loads:
        out.append_text(_load_line(load, get, c, name_w, unit, width, rooms, relays, running))
        out.append("\n")
    out.rstrip()
    return out


def _load_name(load: Load, get: Callable[[str | None], Value | None]) -> str:
    """The name of *load*: the fixed one, else the PLC's name tag, else "Load n"."""
    if load.name:
        return load.name
    v = get(load.keys.get("name"))
    return str(v[0]) if v and v[0] not in (None, "") else _("Load {n}").format(n=load.number)


def _load_line(load: Load, get: Callable[[str | None], Value | None], c: Colors, name_w: int, unit: str,
               width: int, rooms: bool = False, relays: bool = False, running: bool = True) -> Text:
    """One row of the load table (see :func:`render_power` for the columns).

    Args:
        rooms: add the room temperature and heat demand columns (some load of the limiter has them).
        relays: add the relay status column (some load has a ``relay`` tag).
        running: add the PLC's status column (some load has a ``status`` tag).
    """
    shed = _flag(get(load.keys.get("shed")))
    status = _flag(get(load.keys.get("status")))
    line = Text(f"{load.number:>2}  ")
    line.append(f"{_load_name(load, get)[:name_w - 1]:<{name_w}}", "bold" if shed else "")
    state = (("○ " + _("unknown"), c.dim) if shed is None else
             ("● " + _("SWITCHED OFF"), f"bold {c.bad}") if shed else ("● " + _("in"), c.ok))
    line.append(f"{state[0]:<17}", state[1])
    if running:
        text = "–" if status is None else _("on") if status else _("off")
        line.append(f"{text:<9}", c.ok if status else c.dim)
    if relays:
        relay = _flag(get(load.keys.get("relay")))
        if relay is not None and load.relay_inverted:
            relay = not relay              # feedback on = cut: show whether the load gets power
        if relay is None:
            line.append(f"{'–' if 'relay' in load.keys else '':<10}", c.dim)
        elif relay and shed:
            # Switched off by the limiter but the actuator still reports on: the shedding did not reach the relay.
            line.append(f"{'⚠ ' + _('on'):<10}", f"bold {c.bad}")
        else:
            line.append(f"{('● ' + _('on')) if relay else ('○ ' + _('off')):<10}", c.ok if relay else c.dim)
    if rooms:
        temp = _num(get(load.keys.get("temp")))
        line.append(f"{'–' if temp is None else f'{temp:.1f}°':>8}  ", "bold" if temp is not None else c.dim)
        demand = _flag(get(load.keys.get("heat")))
        if demand is None:
            line.append(f"{'–' if 'heat' in load.keys else '':<13}", c.dim)
        elif demand and shed:
            # The room asks for heat while the limiter holds its heater off: the case to watch.
            line.append(f"{'♨ ' + _('wanted'):<13}", f"bold {c.warn}")
        else:
            line.append(f"{('♨ ' + _('on')) if demand else _('off'):<13}", c.ok if demand else c.dim)

    def num(role: str, suffix: str) -> str:
        """A load figure right-aligned in its column (``–`` when unknown)."""
        x = _num(get(load.keys.get(role)))
        return f"{'–' if x is None else f'{x:.0f}{suffix}':>10}"

    count = num("count", "×")
    line.append(count, c.warn if (_num(get(load.keys.get("count"))) or 0) > 0 else c.dim)
    line.append(num("min_off", f" {unit}") + num("max_off", f" {unit}") + num("min_on", f" {unit}"))
    line.truncate(width)
    return line
