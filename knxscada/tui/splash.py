# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""Start-up splash screen and the About dialog, both showing the EKBPRO logo.

The logo is stored as plain text in ``logo.txt`` (block characters ``█``
with ``▒`` shading) and coloured with the current theme: solid blocks in
the primary colour, shading in a dimmed version of it.
"""

from __future__ import annotations

import platform
import time
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from rich.style import Style
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.color import Color
from textual.containers import Grid, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Label, Static

from .. import PRODUCT, __version__
from ..i18n import LANGUAGES, _, get_language

if TYPE_CHECKING:
    from .app import ScadaApp

LOGO_FILE = Path(__file__).with_name("logo.txt")

#: Characters drawn in the solid logo colour (full and half blocks); ▒ is the shade.
SOLID = set("█▀▄▌▐▖▗▘▝▙▚▛▜▞▟")


@lru_cache(maxsize=4)
def logo_lines(path: Path = LOGO_FILE) -> list[str]:
    """Lines of a logo file (empty list if it is missing)."""
    try:
        return [line.rstrip() for line in path.read_text(encoding="utf-8").splitlines()]
    except OSError:
        return []


def logo_path(app) -> Path:
    """``ui.logo_file`` (e.g. a company logo, see docs/04-configuration.md) or the program's own logo."""
    custom = app.cfg.ui.get("logo_file") if getattr(app, "cfg", None) else None
    return app.cfg.resolve(str(custom)) if custom else LOGO_FILE


def logo(app, width: int, rows: int | None = None, height: int | None = None) -> Text:
    """The logo coloured with the theme, fitted to *width* columns (and *height* lines).

    If the full logo is too big, only its first block (lines up to the first
    empty line) is used; if even that does not fit, a one-line text is
    returned. *rows* limits the number of lines (used for the line-by-line
    reveal of the splash).
    """
    lines = logo_lines(logo_path(app))

    def fits(block: list[str]) -> bool:
        return max(len(line) for line in block) <= width and (height is None or len(block) <= height)

    if lines and not fits(lines):
        first = lines[: lines.index("")] if "" in lines else lines  # blocks are separated by an empty line
        lines = first if first and fits(first) else []
    theme = app.current_theme
    if not lines:
        return Text(PRODUCT, style=Style(color=theme.primary, bold=True))
    if rows is not None:
        lines = lines[:rows]
    shade = Color.parse(theme.background or "#000").blend(Color.parse(theme.primary or "#0f0"), 0.45).hex
    solid = Style(color=theme.primary, bold=True)
    dim = Style(color=shade)
    out = Text(no_wrap=True, overflow="crop")
    for i, line in enumerate(lines):
        if i:
            out.append("\n")
        for ch in line:
            out.append(ch, solid if ch in SOLID else dim if ch == "▒" else None)
    return out


class SplashScreen(Screen):
    """Shown at start-up for :attr:`DURATION` seconds. The logo is revealed line by line.

    Any key or click skips it; F10 skips it and quits.
    """

    DURATION = 5.0

    def __init__(self) -> None:
        super().__init__()
        self.started = time.monotonic()
        self.shown = 0  # logo lines revealed so far

    @property
    def scada(self) -> ScadaApp:
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        with Vertical(id="splash-box"):
            yield Static("", id="splash-logo")
            yield Static("", id="splash-sub")
            yield Static("", id="splash-status")

    def on_mount(self) -> None:
        app = self.scada
        project = app.model["info"].get("name", "")
        self.query_one("#splash-sub", Static).update(
            f"[b]SCADA for KNX[/b]   {_('version')} {__version__}\n[dim]{project}[/dim]"
        )
        self.set_interval(0.06, self._tick)

    def _tick(self) -> None:
        app = self.scada
        total = len(logo_lines(logo_path(app))) or 1
        if self.shown < total:
            self.shown += 1
            self.query_one("#splash-logo", Static).update(logo(app, self.size.width - 4, self.shown, self.size.height - 8))
        elapsed = time.monotonic() - self.started
        st = app.status
        # Show the connection progress while the splash is visible.
        if app.server_ok:
            if st.get("connected"):
                knx = "  ·  [$success]●[/] KNX"
            else:
                knx = "  ·  [$warning]●[/] KNX " + _(str(st.get("connection", "")))
            status = f"[$success]●[/] {_('connected to the SCADA server')}{knx}"
        else:
            status = f"[$warning]●[/] {_('connecting to the SCADA server …')}"
        dots = "▰" * int(min(1, elapsed / self.DURATION) * 30)
        self.query_one("#splash-status", Static).update(f"{status}\n[$primary]{dots.ljust(30, '▱')}[/]")
        if elapsed >= self.DURATION:
            self.dismiss()

    def on_key(self, event: events.Key) -> None:
        event.stop()
        self.dismiss()
        if event.key == "f10":
            self.scada.action_quit()

    def on_click(self) -> None:
        self.dismiss()


class AboutDialog(ModalScreen[None]):
    """F12: logo, version, project, connection, licence and platform."""

    BINDINGS = [Binding("escape", "dismiss", _("Close")), Binding("enter", "dismiss", show=False)]

    @property
    def scada(self) -> ScadaApp:
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        app = self.scada
        model = app.model
        gas = model["gas"]
        st = app.status
        clients = app.stats.get("clients", 0)
        info = [
            (_("Program"), f"{PRODUCT} {__version__}"),
            (_("Licence"), _("GPL-2.0-or-later – free software, see LICENSE")),
            (_("Project"), f"{model['info'].get('name', '')}  (ETS {model['info'].get('tool_version', '?')})"),
            (_("Content"), _("{gas} group addresses ({used} in use), {cards} cards, {rooms} rooms").format(
                gas=len(gas), used=sum(1 for g in gas.values() if g["used"]),
                cards=len(model["functions"]), rooms=sum(len(f["rooms"]) for f in model["floors"]))),
            *([("KNX", f"{st.get('gateway', '–')}  ·  {_(str(st.get('connection', '–')))}")]
              if len(st.get("interfaces") or []) < 2 else
              [(f"KNX {i['name']}", f"{i['gateway']}  ·  {_(str(i['connection']))}") for i in st["interfaces"]]),
            (_("Traffic"), _("{n} telegrams/min").format(n=app.stats.get("per_min", 0)) + "  ·  "
             + (_("1 client connected") if clients == 1 else _("{n} clients connected").format(n=clients))),
            (_("Alarms"), _("{active} active, {unacked} unacknowledged").format(
                active=sum(1 for a in app.alarms if a["cleared_ts"] is None),
                unacked=sum(1 for a in app.alarms if a["acked_ts"] is None))),
            (_("User"), app.client.user),
            (_("Theme"), app.theme),
            (_("Language"), LANGUAGES.get(get_language(), get_language())),
            (_("Platform"), f"Python {platform.python_version()} · {platform.system()} {platform.release()}"),
            (_("Built with"), "Textual, xknx, xknxproject, plotext"),
        ]
        with Vertical(classes="dialog about"):
            yield Static(logo(app, 124), id="about-logo")
            with Grid(classes="kv"):
                for k, v in info:
                    yield Label(k, classes="kv-key")
                    yield Label(v, classes="kv-val", markup=False)
            yield Static(f"[dim]{_('F1–F10 for the menus, ^T for colour theme, F9 for the screensaver.')}[/dim]")
            yield Button(_("Close"), id="about-close", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss()
