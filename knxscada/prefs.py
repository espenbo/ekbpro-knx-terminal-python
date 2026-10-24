# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""Per-user preferences (colour theme and language).

Stored as JSON in ``~/.config/knxscada/ui.json`` (``$XDG_CONFIG_HOME`` is
respected), so every SSH user can have their own choices, independent of
the shared ``config.yaml``.
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


def ui_language(configured: str | None, override: str | None = None) -> str | None:
    """The UI language to use: *override* (``--language``) > the user's choice > *configured*."""
    return override or load_prefs().get("language") or configured
