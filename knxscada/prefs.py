# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""Per-user preferences (colour theme and language).

Stored as JSON in ``~/.config/knxscada/ui.json`` (``$XDG_CONFIG_HOME`` is
respected), so every SSH user can have their own choices, independent of
the shared ``config.yaml``. The language is one choice per user; the colour
theme is chosen per installation (``sites``, keyed by the path of its
``config.yaml``), so several installations can be told apart by their colours::

    {"language": "nb", "sites": {"/opt/knx/config.yaml": {"theme": "storm"}}}
"""

from __future__ import annotations

import json
import os
from pathlib import Path

#: Per-user preferences file.
PREFS = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "knxscada" / "ui.json"


def load_prefs() -> dict:
    """The user's saved preferences (empty dict if none or unreadable)."""
    try:
        data = json.loads(PREFS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_prefs(**changes) -> None:
    """Merge *changes* into the preferences file. Failures are ignored (read-only home etc.)."""
    prefs = load_prefs()
    prefs.update(changes)
    try:
        PREFS.parent.mkdir(parents=True, exist_ok=True)
        PREFS.write_text(json.dumps(prefs, indent=1), encoding="utf-8")
    except OSError:
        pass


def site_theme(site: Path | str, configured: str | None) -> str | None:
    """The colour theme for an installation: the user's choice for it > *configured* (``ui.theme``) >
    the user's choice from before themes were per installation."""
    prefs = load_prefs()
    own = (prefs.get("sites") or {}).get(str(site), {}).get("theme")
    return own or configured or prefs.get("theme")


def save_site_theme(site: Path | str, theme: str) -> None:
    """Remember the user's colour theme for this installation."""
    sites = load_prefs().get("sites") or {}
    sites.setdefault(str(site), {})["theme"] = theme
    save_prefs(sites=sites)


def ui_language(configured: str | None, override: str | None = None) -> str | None:
    """The UI language to use: *override* (``--language``) > the user's choice > *configured*."""
    return override or load_prefs().get("language") or configured
