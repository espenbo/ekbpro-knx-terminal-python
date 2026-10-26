# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""Colour themes – three retro, two modern and one in the Storm Elektro colours.

Design rule for all themes: the base colours (foreground, borders, panels)
are muted, and only the status colours (success/warning/error) and the focus
colour (primary) are strong. That way an alarm or a warning stands out
instead of drowning in decoration.

The theme a user picks with Ctrl+T is stored in the per-user preferences
(see :mod:`knxscada.prefs`), so every SSH user can have their own.
"""

from __future__ import annotations

from textual.theme import Theme

from ..i18n import N_

#: Muted green (default): calm base colour, status colours stand out.
RETRO = Theme(
    name="retro",
    primary="#3ddc6f",
    secondary="#2b5236",
    accent="#ffe08a",
    foreground="#a9c2ad",
    background="#070b08",
    surface="#0c130e",
    panel="#132019",
    success="#3ddc6f",
    warning="#f2c14e",
    error="#ff5a5a",
    dark=True,
)

#: Green phosphor like a VT100 – everything in bright green.
RETRO_CLASSIC = Theme(
    name="retro-classic",
    primary="#33ff66",
    secondary="#1f9e40",
    accent="#b8ff5c",
    foreground="#33ff66",
    background="#020602",
    surface="#071108",
    panel="#0f2a14",
    success="#33ff66",
    warning="#e8ff5c",
    error="#ff4d4d",
    dark=True,
)

#: Amber phosphor like an IBM 3278 / Wyse terminal.
AMBER = Theme(
    name="amber",
    primary="#ffb000",
    secondary="#7a5200",
    accent="#ffd27f",
    foreground="#ffb000",
    background="#0a0700",
    surface="#140e02",
    panel="#2e2107",
    success="#ffcc33",
    warning="#fff07a",
    error="#ff5544",
    dark=True,
)

#: Modern dark – neutral grey with clear signal colours.
MODERN = Theme(
    name="modern",
    primary="#58a6ff",
    secondary="#30363d",
    accent="#d2a8ff",
    foreground="#c9d1d9",
    background="#0d1117",
    surface="#11161d",
    panel="#1c2330",
    success="#3fb950",
    warning="#d29922",
    error="#f85149",
    dark=True,
)

#: Solarized dark – low contrast, easy on the eyes.
SOLARIZED = Theme(
    name="solarized",
    primary="#268bd2",
    secondary="#2d4a54",
    accent="#b58900",
    foreground="#93a1a1",
    background="#002b36",
    surface="#04313d",
    panel="#073642",
    success="#859900",
    warning="#b58900",
    error="#dc322f",
    dark=True,
)

#: Storm Elektro AS / Storm Automasjon – the colours of the design manual: deep blue (PMS 534C, #19365E)
#: as the panel colour on a darker blue background, the grey (PMS 7534C, #CDCBBE) for text, white for
#: focus and logo (as the negative logo), green (PMS 2287C, #4EC409) for OK and the red/orange
#: (PMS 172C, #FC4A14) for alarms. Lights use a warm yellow, so they are never mistaken for an alarm.
STORM = Theme(
    name="storm",
    primary="#ffffff",
    secondary="#19365e",
    accent="#ffd166",
    foreground="#cdcbbe",
    background="#0a1628",
    surface="#0f2038",
    panel="#19365e",
    success="#4ec409",
    warning="#f2c14e",
    error="#fc4a14",
    dark=True,
)

THEMES = [RETRO, RETRO_CLASSIC, AMBER, MODERN, SOLARIZED, STORM]

#: (theme id, English display name, English description) – translated when shown.
THEME_CHOICES = [
    ("retro", N_("Muted green"), N_("default – calm base colour, status colours stand out")),
    ("retro-classic", N_("Green phosphor"), N_("retro VT100 terminal, everything in green")),
    ("amber", N_("Amber"), N_("retro amber IBM/Wyse terminal")),
    ("modern", N_("Modern dark"), N_("neutral dark with blue, purple and clear signal colours")),
    ("solarized", N_("Solarized"), N_("modern, low contrast – easy on the eyes")),
    ("storm", N_("Storm Automasjon"), N_("the colours of Storm Elektro: deep blue, grey, green and red/orange")),
]

#: Theme ids used by earlier versions (Norwegian names) → current ids.
THEME_ALIASES = {"retro-klassisk": "retro-classic", "moderne": "modern"}


def resolve_theme(name: str | None) -> str | None:
    """Map an old theme id to the current one; other names are returned unchanged."""
    return THEME_ALIASES.get(name or "", name)
