# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""Piping diagrams for the "Systems" view (``ui.systems`` entries with ``type: diagram``).

Air handling units have a fixed drawing (:func:`.systems.render_vent`). Water-borne plants – a boiler room,
floor heating, tap water – look different on every site, so their drawing is written in ``config.yaml`` as
text, the way an HMI page is drawn, and the live values are put into it::

    - name: "=320.002 Floor heating"
      type: diagram
      connection: plc1
      prefix: "320_002_"
      diagram: |
        ━━━━━━━━━━━{p1  }━━━━━━━ {t_sup  } supply
        ═══════════════════════  {t_ret  } return
      fields:
        p1: {kind: pump, run: JP401_V, alarm: JP401_AL, safety: JP401_SB}
        t_sup: {tag: RT401_PV, alarm: [RT401_L_AL, RT401_H_AL]}
        t_ret: {tag: RT501_PV}
        valve: {tag: RelPos, connection: valve1, decimals: 0}   # a tag of another connection

Rules of the drawing:

* A placeholder ``{key}`` is replaced by the field *key*, padded or cut to the width of the placeholder itself
  (braces included). Spaces inside the braces widen the field, so the drawing keeps its columns: ``{t_sup  }``
  is a 9-character field.
* Heavy box characters (``━ ┃ ┏ ┓ ┗ ┛ ┣ ┫ ┳ ┻ ╋``) are drawn in the "hot" colour (supply), double ones
  (``═ ║ ╔ ╗ ╚ ╝ ╠ ╣ ╦ ╩ ╬``) in the "cold" colour (return, cold water), everything else in the normal colour.
  Flow arrows ``▶ ◀ ▲ ▼`` take the colour of the pipe character before them on the line.
* Field kinds: ``value`` (default: the value with its unit, red and bold when an ``alarm`` tag is on),
  ``pump`` (a pump symbol: green running, red alarm, yellow safety switch, dim off), ``state`` (``on``/``off``
  texts for an on/off tag) and ``text`` (``texts`` for the numbers of a multi-state tag). ``align`` (left /
  center / right) places the text inside the field; centred by default.
* Tag names get the entry's ``prefix``; a field with its own ``connection`` reads that connection (no prefix
  unless the field sets ``prefix``).

:func:`render_diagram` is a pure function of the template, the fields and a value lookup, so it is tested
without a terminal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from rich.text import Text

from ..i18n import _

if TYPE_CHECKING:
    from collections.abc import Callable

#: Box-drawing characters of supply pipes (hot) and return pipes (cold); see the module docstring.
HOT = set("━┃┏┓┗┛┣┫┳┻╋")
COLD = set("═║╔╗╚╝╠╣╦╩╬")
#: Flow arrows: they take the colour of the pipe they sit on.
ARROWS = set("▶◀▲▼►◄")

#: A placeholder: ``{key}`` with optional spaces before the closing brace that only widen the field.
PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*) *\}")

#: The field kinds and what they show.
KINDS = ("value", "pump", "state", "text")

#: A value as the views keep it: (value, unit, quality).
Value = tuple[Any, str, str]


@dataclass
class Field:
    """One field of a diagram: what it shows and which tags it reads (tag names without the prefix)."""

    kind: str = "value"
    tag: str = ""                               # value/state/text: the tag shown
    connection: str = ""                        # another connection than the entry's ("" = the entry's)
    prefix: str | None = None                   # None: the entry's prefix (or none with another connection)
    decimals: int | None = None                 # value: digits after the point (None: 1 for floats)
    unit: bool = True                           # value: show the unit
    alarm: list[str] = field(default_factory=list)   # any of these on → alarm colour
    run: str = ""                               # pump: running feedback
    safety: str = ""                            # pump: safety switch (on = switched off at the pump)
    on: str = ""                                # state: text when on (default "on")
    off: str = ""                               # state: text when off (default "off")
    texts: dict[int, str] = field(default_factory=dict)   # text: number → text
    align: str = "center"                       # left / center / right inside the placeholder's width


def parse_fields(spec: Any) -> tuple[dict[str, Field], list[str]]:
    """``fields:`` of a diagram entry → (key → :class:`Field`, problems). Unknown kinds are reported."""
    out: dict[str, Field] = {}
    problems: list[str] = []
    for key, f in (spec or {}).items() if isinstance(spec, dict) else ():
        if not isinstance(f, dict):
            problems.append(_("diagram field {key}: expected {{tag: …}}").format(key=key))
            continue
        kind = str(f.get("kind") or "value")
        if kind not in KINDS:
            problems.append(_("diagram field {key}: unknown kind {kind}").format(key=key, kind=repr(kind)))
            continue
        alarm = f.get("alarm") or []
        texts = {}
        for k, v in (f.get("texts") or {}).items():
            try:
                texts[int(k)] = str(v)
            except (TypeError, ValueError):
                problems.append(_("diagram field {key}: texts: {value} is not a number").format(key=key, value=repr(k)))
        decimals = f.get("decimals")
        # YAML 1.1 reads unquoted ``on:``/``off:`` keys as the booleans True/False, so accept both spellings.
        on_text = f.get("on", f.get(True))
        off_text = f.get("off", f.get(False))
        out[str(key)] = Field(
            kind=kind, tag=str(f.get("tag") or ""), connection=str(f.get("connection") or ""),
            prefix=str(f["prefix"]) if f.get("prefix") is not None else None,
            decimals=int(decimals) if isinstance(decimals, int) else None, unit=f.get("unit", True) is not False,
            alarm=[str(a) for a in (alarm if isinstance(alarm, list) else [alarm])], run=str(f.get("run") or ""),
            safety=str(f.get("safety") or ""), on=str(on_text or ""), off=str(off_text or ""), texts=texts)
        align = str(f.get("align") or "center")
        if align not in ("left", "center", "right"):
            problems.append(_("diagram field {key}: align must be left, center or right").format(key=key))
            align = "center"
        out[str(key)].align = align
        if kind != "pump" and not out[str(key)].tag:
            problems.append(_("diagram field {key}: tag is missing").format(key=key))
        if kind == "pump" and not (out[str(key)].run or out[str(key)].tag):
            problems.append(_("diagram field {key}: a pump needs run (or tag)").format(key=key))
    return out, problems


def placeholders(template: str) -> list[str]:
    """The field keys used in a diagram template, in order (for checking them against ``fields``)."""
    return PLACEHOLDER.findall(template)


def field_keys(f: Field, conn: str, prefix: str) -> list[str]:
    """Every ``conn/tag`` key field *f* reads (for loading values and history)."""
    c = f.connection or conn
    p = f.prefix if f.prefix is not None else ("" if f.connection else prefix)
    names = [f.tag, f.run, f.safety, *f.alarm]
    return [f"{c}/{p}{n}" for n in names if n]


@dataclass
class DiagramColors:
    """Theme colours of a diagram (hex or style names)."""

    hot: str = "red"
    cold: str = "blue"
    ok: str = "green"
    warn: str = "yellow"
    bad: str = "red"
    dim: str = "dim"
    value: str = "bold"


def _is_on(v: Value | None) -> bool | None:
    """An on/off reading of *v*: bools as they are, numbers above 0 on; None when unknown."""
    if v is None or v[0] is None:
        return None
    if isinstance(v[0], bool):
        return v[0]
    if isinstance(v[0], (int, float)):
        return v[0] > 0
    return None


def _value_text(f: Field, v: Value | None) -> str:
    """The text of a ``value`` field: the number (``decimals``) and its unit, ``–`` when there is no value."""
    if v is None or v[0] is None:
        return "–"
    value, unit, _q = v
    if isinstance(value, bool):
        text = _("on") if value else _("off")
    elif isinstance(value, (int, float)):
        decimals = f.decimals if f.decimals is not None else (1 if isinstance(value, float) else 0)
        text = f"{value:.{decimals}f}"
    else:
        text = str(value)
    return f"{text} {unit}".strip() if f.unit and unit and not isinstance(value, bool) else text


def _fit(text: str, width: int, align: str = "center") -> str:
    """*text* padded or cut to *width*: centred by default (a placeholder usually sits on or next to a pipe), or
    left/right aligned for text after a label."""
    text = text[:width]
    return text.ljust(width) if align == "left" else text.rjust(width) if align == "right" else text.center(width)


def render_field(f: Field, width: int, read: Callable[[str], Value | None], c: DiagramColors) -> Text:
    """One field as *width* characters of styled text. *read* takes a tag name of the field (prefix/connection
    are applied by the caller) and returns its value."""
    alarm = any(_is_on(read(a)) for a in f.alarm)
    if f.kind == "pump":
        run, bad, safety = _is_on(read(f.run or f.tag)), alarm, _is_on(read(f.safety)) if f.safety else None
        style = c.bad if bad else (c.warn if safety else (c.ok if run else c.dim))
        symbol = "(▶)" if width >= 3 else "▶"
        return Text(_fit(symbol, width, f.align), style=f"bold {style}")
    v = read(f.tag)
    old = v is not None and v[2] not in ("good", "")
    if f.kind == "state":
        state = _is_on(v)
        text = "–" if state is None else (f.on or _("on")) if state else (f.off or _("off"))
        style = c.ok if state else c.dim
    elif f.kind == "text":
        raw = v[0] if v else None
        text = "–" if raw is None else f.texts.get(int(raw), str(raw)) if isinstance(raw, (int, float)) else str(raw)
        style = c.value
    else:
        text, style = _value_text(f, v), c.value
    if alarm:
        style = f"bold reverse {c.bad}"
    elif old:
        style = f"italic {c.dim}"       # a value from before the connection was lost
    return Text(_fit(text, width, f.align), style=style)


def render_diagram(template: str, fields: dict[str, Field], get: Callable[[str], Value | None], conn: str,
                   prefix: str, c: DiagramColors) -> Text:
    """The diagram with live values.

    Args:
        template: the drawing (``diagram:`` in config.yaml).
        fields: from :func:`parse_fields`.
        get: ``conn/tag`` → (value, unit, quality) or None.
        conn: the entry's connection; prefix: its tag prefix.
        c: the colours.

    Placeholders without a field are left as they are, so a typo is visible in the drawing.
    """
    out = Text(no_wrap=True, overflow="crop")
    for n, line in enumerate(template.rstrip("\n").split("\n")):
        if n:
            out.append("\n")
        pos = 0
        last_pipe = ""
        for m in PLACEHOLDER.finditer(line):
            last_pipe = _append_pipes(out, line[pos:m.start()], c, last_pipe)
            f = fields.get(m.group(1))
            if f is None:
                out.append(m.group(0), style=c.bad)
            else:
                fc = f.connection or conn
                fp = f.prefix if f.prefix is not None else ("" if f.connection else prefix)
                out.append_text(render_field(f, len(m.group(0)), lambda name, fc=fc, fp=fp: get(f"{fc}/{fp}{name}")
                                             if name else None, c))
            pos = m.end()
        _append_pipes(out, line[pos:], c, last_pipe)
    return out


def _append_pipes(out: Text, segment: str, c: DiagramColors, last: str) -> str:
    """Append a piece of drawing, colouring pipes by kind; returns the style of its last pipe (for arrows)."""
    for ch in segment:
        if ch in HOT:
            last = c.hot
            out.append(ch, style=c.hot)
        elif ch in COLD:
            last = c.cold
            out.append(ch, style=c.cold)
        elif ch in ARROWS:
            out.append(ch, style=f"bold {last}" if last else "bold")
        else:
            out.append(ch)
    return last
