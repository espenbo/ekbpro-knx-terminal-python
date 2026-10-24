# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""Alarm rules: link group addresses to conditions defined in ``config.yaml``.

A rule selects group addresses (by address glob, name regex and/or main DPT)
and defines when the value counts as an alarm::

    - match_address: "0/6/*"
      match_name: "(?i)alarm$"
      exclude_name: "(?i)acknowledg"
      dpt_main: 1
      condition: "true"          # or "false", "> 30", "<= 5", ...
      severity: critical         # critical | warning (any text is allowed)
      text: "Smoke alarm: {name}"

Each group address is bound to the *first* rule that matches it, so more
specific rules should be listed before general ones. The server evaluates
the rule every time a new value arrives (see ``ScadaServer._check_alarm``).
"""

from __future__ import annotations

import fnmatch
import operator
import re
from dataclasses import dataclass
from typing import Any

from . import dpt as dptlib
from .i18n import N_

#: Comparison operators allowed in numeric conditions.
OPS = {">": operator.gt, "<": operator.lt, ">=": operator.ge, "<=": operator.le, "==": operator.eq, "!=": operator.ne}

#: A numeric condition: operator followed by a number (``.`` or ``,`` as decimal separator).
COND_RE = re.compile(r"^\s*(>=|<=|==|!=|>|<)\s*(-?[\d.,]+)\s*$")

#: Severities written in Norwegian by older configurations are mapped to the English names.
SEVERITY_ALIASES = {"kritisk": "critical", "advarsel": "warning"}

#: The standard severities (any other text is allowed too and shown as-is).
SEVERITIES = (N_("critical"), N_("warning"))


def normalize_severity(severity: str | None) -> str:
    """Return the canonical (English) severity name, e.g. ``"kritisk"`` → ``"critical"``."""
    s = (severity or "warning").strip().lower()
    return SEVERITY_ALIASES.get(s, s)


def is_critical(alarm: dict) -> bool:
    """True if an alarm record (from the server) has critical severity."""
    return normalize_severity(alarm.get("severity")) == "critical"


@dataclass
class Rule:
    """One alarm rule from the configuration (already compiled)."""

    match_address: str | None
    match_name: re.Pattern | None
    exclude_name: re.Pattern | None
    dpt_main: int | None
    condition: str
    severity: str
    text: str

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Rule:
        """Build a rule from its YAML mapping (``address`` is accepted as alias of ``match_address``)."""
        return cls(
            match_address=d.get("match_address") or d.get("address"),
            match_name=re.compile(d["match_name"]) if d.get("match_name") else None,
            exclude_name=re.compile(d["exclude_name"]) if d.get("exclude_name") else None,
            dpt_main=d.get("dpt_main"),
            condition=str(d.get("condition", "true")).strip().lower(),
            severity=normalize_severity(d.get("severity")),
            text=d.get("text", "{name}"),
        )

    def format_text(self, name: str, address: str, value: str) -> str:
        """The alarm text with ``{name}``, ``{address}`` and ``{value}`` filled in.

        A typo in the configured text (unknown placeholder, stray brace) must
        never stop the alarm from being raised, so the raw text plus the
        address name is used instead.
        """
        try:
            return self.text.format(name=name, address=address, value=value)
        except (KeyError, IndexError, ValueError, AttributeError):
            return f"{self.text} – {name}"

    def matches(self, ga: dict[str, Any]) -> bool:
        """True if the group address (from the project model) is selected by this rule.

        Placeholder addresses (``"----"``) and addresses without any linked
        communication object are never monitored – they cannot carry traffic.
        """
        if ga.get("placeholder") or not ga.get("used"):
            return False
        if self.match_address and not fnmatch.fnmatch(ga["address"], self.match_address):
            return False
        if self.match_name and not self.match_name.search(ga["name"]):
            return False
        if self.exclude_name and self.exclude_name.search(ga["name"]):
            return False
        if self.dpt_main is not None and dptlib.split(ga["dpt"])[0] != self.dpt_main:
            return False
        return True

    def active(self, value: Any) -> bool | None:
        """Evaluate the condition for a decoded value.

        Returns True (alarm), False (normal) or None when the value cannot
        be judged – e.g. a text value for a numeric condition. ``None`` leaves
        the current alarm state untouched.
        """
        if value is None:
            return None
        if self.condition in ("true", "on", "1"):
            return bool(value) if isinstance(value, (bool, int, float)) else None
        if self.condition in ("false", "off", "0"):
            return (not bool(value)) if isinstance(value, (bool, int, float)) else None
        m = COND_RE.match(self.condition)
        # bool is a subclass of int; a numeric comparison on on/off makes no sense.
        if m and isinstance(value, (int, float)) and not isinstance(value, bool):
            return OPS[m.group(1)](value, float(m.group(2).replace(",", ".")))
        return None


def build_rules(cfg_rules: list[dict], gas: dict[str, dict]) -> dict[str, Rule]:
    """Map every monitored group address to the first rule that matches it."""
    rules = [Rule.from_dict(r) for r in cfg_rules]
    out: dict[str, Rule] = {}
    for addr, ga in gas.items():
        for rule in rules:
            if rule.matches(ga):
                out[addr] = rule
                break
    return out
