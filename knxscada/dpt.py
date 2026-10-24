# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""Datapoint type (DPT) helpers: decoding, encoding and display text.

KNX telegrams carry raw bytes; the *datapoint type* defined in ETS says how
to interpret them (``1.001`` = on/off, ``9.001`` = temperature in °C, …).
The actual conversion is done by xknx's transcoder classes; this module adds
the glue the rest of the program needs:

* DPTs are passed around as compact strings: ``"9.001"``, or just ``"1"``
  when ETS only knows the main type.
* :func:`decode` turns a payload into a JSON-friendly Python value
  (bool, int, float, str, dict) so it can be sent to clients and stored.
* :func:`encode` does the reverse for values typed by the user, accepting
  loose input such as ``"21,5"``, ``"on"`` or ``"comfort"``.
* :func:`fmt` produces the human-readable text shown in the UI
  (``"21.5 °C"``, ``"On"``, ``"Comfort"``), translated via :mod:`knxscada.i18n`.
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
from typing import Any

from xknx.dpt import DPTArray, DPTBase, DPTBinary

from .i18n import N_, _

#: Display text for (False, True) for each DPT 1.x subtype. Translated at display time.
DPT1_LABELS: dict[int, tuple[str, str]] = {
    1: (N_("Off"), N_("On")),
    2: (N_("False"), N_("True")),
    3: (N_("Disabled"), N_("Enabled")),
    4: (N_("No ramp"), N_("Ramp")),
    5: (N_("OK"), N_("ALARM")),
    6: (N_("Low"), N_("High")),
    7: (N_("Decrease"), N_("Increase")),
    8: (N_("Up"), N_("Down")),
    9: (N_("Open"), N_("Closed")),
    10: (N_("Stop"), N_("Start")),
    11: (N_("Inactive"), N_("Active")),
    12: (N_("Not inverted"), N_("Inverted")),
    15: (N_("No action"), N_("Reset")),
    16: (N_("No action"), N_("Acknowledge")),
    17: (N_("Trigger"), N_("Trigger")),
    18: (N_("Not occupied"), N_("Occupied")),
    19: (N_("Closed"), N_("Open")),
    21: (N_("OR"), N_("AND")),
    22: (N_("Scene A"), N_("Scene B")),
    23: (N_("Up/Down"), N_("Up/Down+Step")),
    24: (N_("Day"), N_("Night")),
}

#: Display names for the HVAC operating modes of DPT 20.102 (xknx enum names → text).
HVAC_LABELS = {
    "AUTO": N_("Auto"),
    "COMFORT": N_("Comfort"),
    "STANDBY": N_("Standby"),
    "ECONOMY": N_("Night/Eco"),
    "BUILDING_PROTECTION": N_("Frost protection"),
}

#: Subtype to assume when ETS only provides the main type (e.g. "9" → 9.001).
_MAIN_FALLBACK = {1: 1, 3: 7, 5: 10, 7: 1, 8: 1, 9: 1, 12: 1, 13: 1, 14: 56, 17: 1, 18: 1, 20: 102}

#: Words accepted as "true" when the user types a value for a DPT 1.x address.
_TRUE_WORDS = ("1", "true", "on", "yes", "på", "pa", "sann", "ja")


def hvac_label(name: str) -> str:
    """Translated display name for an HVAC mode enum name such as ``"COMFORT"``."""
    return _(HVAC_LABELS.get(name, name.title()))


def dpt_str(dpt: dict | None) -> str | None:
    """Convert xknxproject's ``{"main": 9, "sub": 1}`` into ``"9.001"`` (or None)."""
    if not dpt or dpt.get("main") is None:
        return None
    sub = dpt.get("sub")
    return f"{dpt['main']}" if sub is None else f"{dpt['main']}.{sub:03d}"


def split(dpt: str | None) -> tuple[int | None, int | None]:
    """Split ``"9.001"`` into ``(9, 1)``; ``"1"`` gives ``(1, None)``."""
    if not dpt:
        return None, None
    main, _sep, sub = dpt.partition(".")
    return int(main), (int(sub) if sub else None)


@lru_cache(maxsize=None)
def transcoder(dpt: str | None) -> type[DPTBase] | None:
    """Find the xknx transcoder class for a DPT, or None if xknx does not know it.

    When the exact subtype is unknown we fall back to a sensible default
    subtype for the main type, so that e.g. a bare ``"9"`` still decodes.
    The result is cached because the lookup walks xknx's class tree.
    """
    main, sub = split(dpt)
    if main is None:
        return None
    t = DPTBase.parse_transcoder({"main": main, "sub": sub})
    if t is None and main in _MAIN_FALLBACK:
        t = DPTBase.parse_transcoder({"main": main, "sub": _MAIN_FALLBACK[main]})
    if t is None:
        t = DPTBase.parse_transcoder({"main": main, "sub": None})
    return t


def unit(dpt: str | None) -> str:
    """Unit of measurement for a DPT (``"°C"``, ``"%"``, ``"W"`` …), or ``""``."""
    main, sub = split(dpt)
    if main == 5 and sub == 1:  # xknx reports no unit for scaling, but it is a percentage
        return "%"
    t = transcoder(dpt)
    return (getattr(t, "unit", None) or "") if t else ""


def raw_hex(payload: DPTArray | DPTBinary) -> str:
    """Raw payload as text, e.g. ``"0x01"`` (1-bit/small) or ``"0C 33"`` (bytes)."""
    if isinstance(payload, DPTBinary):
        return f"{payload.value:#04x}"
    return " ".join(f"{b:02X}" for b in payload.value)


def _jsonable(value: Any) -> Any:
    """Make an xknx value JSON-serialisable (enums → name, named tuples → dict)."""
    if isinstance(value, Enum):
        return value.name
    if isinstance(value, (bool, int, float, str)) or value is None:
        return value
    if hasattr(value, "as_dict"):
        try:
            return value.as_dict()
        except Exception:  # noqa: BLE001 - fall through to the generic conversions
            pass
    if hasattr(value, "_asdict"):
        return value._asdict()
    return str(value)


def decode(dpt: str | None, payload: DPTArray | DPTBinary) -> Any:
    """Decode a telegram payload into a JSON-friendly value, or None if impossible.

    DPT 1.x is always decoded as a plain bool (xknx would return subtype
    specific enums such as ``WindowDoor.OPEN``), which keeps switches,
    alarms and trend data uniform.
    """
    main, _sub = split(dpt)
    if main == 1 and isinstance(payload, DPTBinary):
        return bool(payload.value & 1)
    t = transcoder(dpt)
    if t is None:
        # Unknown DPT: small values still make sense as a number, bytes do not.
        if isinstance(payload, DPTBinary):
            return payload.value
        return None
    try:
        return _jsonable(t.from_knx(payload))
    except Exception:  # noqa: BLE001 - malformed payload for the configured DPT
        return None


def encode(dpt: str | None, value: Any) -> DPTArray | DPTBinary:
    """Encode a value coming from a client into a KNX payload.

    Accepted inputs:

    * ``{"raw": "0C 33"}`` – raw bytes in hex (works for any DPT, also unknown ones)
    * DPT 1.x – bool, or text like ``"on"``/``"på"``/``"1"``
    * DPT 20.x – enum name (``"COMFORT"``) or its display text (``"Comfort"``/``"Komfort"``)
    * numbers – int/float, or text with ``.`` or ``,`` as decimal separator

    Raises :class:`ValueError` when the DPT is unknown or the value is invalid.
    """
    if isinstance(value, dict) and "raw" in value:
        raw = str(value["raw"]).replace("0x", "").replace(",", " ").split()
        data = [int(b, 16) for b in raw]
        if len(data) == 1 and data[0] < 64 and value.get("binary", False):
            return DPTBinary(data[0])
        return DPTArray(tuple(data))
    main, _sub = split(dpt)
    if main == 1:
        if isinstance(value, str):
            value = value.strip().lower() in _TRUE_WORDS
        return DPTBinary(1 if value else 0)
    t = transcoder(dpt)
    if t is None:
        raise ValueError(_("Unknown DPT {dpt} – use a raw value").format(dpt=repr(dpt)))
    if main == 20 and isinstance(value, str):
        # Accept the enum name as well as the English or translated display text.
        wanted = value.strip().lower()
        for v in t.get_valid_values():
            names = {v.name.lower(), HVAC_LABELS.get(v.name, "").lower(), hvac_label(v.name).lower()}
            if wanted in names:
                value = v
                break
    if isinstance(value, str) and main not in (16, 24):  # 16 = text, 24 = long text
        try:
            value = float(value.replace(",", "."))
            if value.is_integer() and main in (5, 6, 7, 8, 12, 13, 17, 18):
                value = int(value)  # integer DPTs reject floats
        except ValueError:
            pass  # leave as text; the transcoder will raise if it is invalid
    return t.to_knx(value)


def enum_options(dpt: str | None) -> list[str]:
    """Enum names a DPT accepts (e.g. the HVAC modes), or ``[]`` for non-enum DPTs."""
    t = transcoder(dpt)
    if t is None or not hasattr(t, "get_valid_values"):
        return []
    try:
        return [v.name for v in t.get_valid_values()]
    except Exception:  # noqa: BLE001 - not an enum transcoder
        return []


def fmt(dpt: str | None, value: Any) -> str:
    """Human-readable, translated text for a decoded value (``"–"`` for no value)."""
    if value is None:
        return "–"
    main, sub = split(dpt)
    if main == 1 and isinstance(value, bool):
        off, on = DPT1_LABELS.get(sub or 1, ("0", "1"))
        return _(on) if value else _(off)
    if main == 20 and isinstance(value, str):
        return hvac_label(value)
    if isinstance(value, float):
        u = unit(dpt)
        if main == 9 and sub == 1:  # temperatures: always one decimal
            text = f"{value:.1f}"
        elif abs(value) >= 100:
            text = f"{value:.0f}"
        else:
            text = f"{value:.2f}".rstrip("0").rstrip(".")
        return f"{text} {u}".strip()
    if isinstance(value, int):
        return f"{value} {unit(dpt)}".strip()
    if isinstance(value, dict):  # e.g. RGB colour, date/time structures
        return ", ".join(f"{k}={v}" for k, v in value.items())
    return str(value)


def is_numeric(dpt: str | None) -> bool:
    """True for DPTs whose values are numbers that make sense in a trend chart."""
    main, _sub = split(dpt)
    return main in (5, 6, 7, 8, 9, 12, 13, 14)
