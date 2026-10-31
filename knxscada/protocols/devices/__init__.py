# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""Device templates: ready-made tags and settings for equipment we know.

A connection names its equipment with ``model:`` instead of listing every
register::

    meter1:
      protocol: modbus
      device: /dev/ttyUSB0
      model: WAGO 879-3000

Each YAML file in this folder describes one family of devices that share a
register map:

``manufacturer``, ``protocol``
    Who makes it and which protocol module reads it.
``models``
    Product number → short description. ``model:`` is written as
    ``<manufacturer> <product number>``; the product number alone is accepted too.
``settings``
    Defaults for the connection (baud rate, parity …). The connection's own settings win.
``identify``
    Registers read once after connecting to tell which device answers and how it
    is set up (``values`` turns a register value into text; ``hex_digits: true`` also
    accepts 0x1111 written as the decimal number 1111). The driver reports it
    and warns when it does not match the settings.
``tags``
    The tags, written like ``tags:`` in ``protocols.yaml``. The connection's own
    ``tags:`` are added to them and override a template tag with the same name.
    ``only: [ET112]`` limits a tag to some of the family's models, and a tag's own
    ``invalid: [65535]`` adds "no value" codes for that tag alone.
``invalid``
    Raw register values that mean "no value" (overflow, not measured); they are
    shown as bad quality. ``links`` and ``manual`` are for people only.
"""

from __future__ import annotations

import functools
import re
from pathlib import Path
from typing import Any

import yaml

from ...i18n import _

DIR = Path(__file__).parent


@functools.cache
def templates() -> dict[str, dict[str, Any]]:
    """Every device template: ``"WAGO 879-3000"`` → the family it belongs to (with ``model`` and ``title``)."""
    out: dict[str, dict[str, Any]] = {}
    for path in sorted(DIR.glob("*.yaml")):
        family = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        maker = str(family.get("manufacturer") or "").strip()
        for number, title in (family.get("models") or {}).items():
            name = f"{maker} {number}".strip()
            out[name] = {**family, "model": name, "number": str(number), "title": str(title or ""),
                         "file": path.name}
    return out


def _key(text: str) -> str:
    return re.sub(r"\s+", " ", str(text)).strip().lower()


def find(model: str | None) -> dict[str, Any] | None:
    """The template of *model* (``"WAGO 879-3000"`` or ``"879-3000"``, any case), None for an empty name.

    Raises ValueError for a name that is not known.
    """
    if model is None or not str(model).strip():
        return None
    key = _key(model)
    for name, tpl in templates().items():
        if key in (_key(name), _key(tpl["number"])):
            return tpl
    raise ValueError(_("unknown device {model} – known devices: {valid}").format(
        model=repr(str(model)), valid=", ".join(names())))


def names(protocol: str | None = None) -> list[str]:
    """The template names, optionally only those for *protocol*."""
    return [n for n, t in templates().items() if protocol is None or t.get("protocol") == protocol]


def apply(conn: dict[str, Any]) -> dict[str, Any]:
    """*conn* with its template filled in: the template's settings as defaults, its tags before the own ones.

    A connection without ``model`` is returned unchanged. ValueError for an unknown model.
    """
    tpl = find(conn.get("model"))
    if tpl is None:
        return conn
    out = {**(tpl.get("settings") or {}), **{k: v for k, v in conn.items() if v is not None}}
    tags = {}
    for name, spec in (tpl.get("tags") or {}).items():
        spec = dict(spec or {})
        only = spec.pop("only", None)
        if only is None or tpl["number"] in [str(m) for m in only]:
            tags[str(name)] = spec
    for name, spec in (conn.get("tags") or {}).items():
        tags[str(name)] = {**tags.get(str(name), {}), **(spec or {})}
    out["tags"] = tags
    return out


def describe(tpl: dict[str, Any], found: dict[str, Any]) -> str:
    """Text for what *identify* read, e.g. ``879-3000 (4PU …)  unit 1  9600 E  serial 12345678``."""
    parts = []
    if "meter_code" in found:
        number = found["meter_code"]
        title = (tpl.get("models") or {}).get(number, "")
        parts.append(f"{tpl.get('manufacturer', '')} {number}".strip() + (f" ({title})" if title else ""))
    if "unit_id" in found:
        parts.append(_("unit {n}").format(n=found["unit_id"]))
    if "baudrate" in found or "parity" in found:
        parts.append(f"{found.get('baudrate', '?')} {found.get('parity', '?')}")
    if "serial" in found:
        parts.append(_("serial {n}").format(n=found["serial"]))
    return "  ".join(parts)


def mismatches(tpl: dict[str, Any], found: dict[str, Any], settings: dict[str, Any]) -> list[str]:
    """What *identify* read that does not fit the connection (another model, other serial settings)."""
    out = []
    want = tpl.get("number")
    if found.get("meter_code") not in (None, want):
        out.append(_("the device is a {found}, not a {model}").format(found=found["meter_code"], model=want))
    for key in ("baudrate", "parity", "unit_id"):
        if key in found and settings.get(key) not in (None, "") and str(found[key]) != str(settings[key]):
            out.append(_("{key} is {found} in the device, {setting} in the settings").format(
                key=key, found=found[key], setting=settings[key]))
    return out
