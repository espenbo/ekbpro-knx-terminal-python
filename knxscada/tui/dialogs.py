# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""Modal dialogs: enter a value, confirm, group address details, theme and language picker.

Every dialog is a :class:`~textual.screen.ModalScreen` that is opened with
``app.push_screen(dialog, callback)``; the callback receives the result
passed to ``dismiss()`` (None when cancelled with Esc).
"""

from __future__ import annotations

from typing import Any

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static

from .. import dpt as dptlib
from ..i18n import LANGUAGES, N_, _, get_language
from .widgets import ago


class ValueDialog(ModalScreen[Any]):
    """Ask for a value for a given DPT. Returns the value (text, bool or raw dict) or None.

    * DPT 1.x shows two buttons with the subtype's texts (e.g. On / Off).
    * Other DPTs show an input field; the text is validated by trying to
      encode it, so the user gets an error here instead of a failed write.
    * Unknown DPT: the input is taken as raw hex bytes.
    """

    BINDINGS = [Binding("escape", "dismiss(None)", _("Cancel"))]

    def __init__(self, title: str, dpt: str | None, current: Any = None) -> None:
        super().__init__()
        self.title_text = title
        self.dpt = dpt
        self.current = current

    def compose(self) -> ComposeResult:
        main, _sub = dptlib.split(self.dpt)
        with Vertical(classes="dialog"):
            yield Label(self.title_text, classes="dialog-title", markup=False)
            yield Static(
                f"DPT {self.dpt or _('unknown')}  •  {_('now:')} {dptlib.fmt(self.dpt, self.current)}",
                classes="muted", markup=False,
            )
            if main == 1:
                with Horizontal(classes="dialog-buttons"):
                    off, on_ = dptlib.DPT1_LABELS.get(dptlib.split(self.dpt)[1] or 1, ("0", "1"))
                    yield Button(_(on_), id="v-true", variant="success")
                    yield Button(_(off), id="v-false", variant="error")
            else:
                hint = ""
                opts = dptlib.enum_options(self.dpt)
                if opts:
                    hint = _("Valid:") + " " + ", ".join(dptlib.hvac_label(o) for o in opts)
                elif self.dpt is None:
                    hint = _("Unknown DPT – enter raw bytes in hex, e.g. 0C 33")
                value = "" if self.current is None or isinstance(self.current, dict) else str(self.current)
                yield Input(value=value, placeholder=_("value"), id="value")
                if hint:
                    yield Static(hint, classes="muted")
                with Horizontal(classes="dialog-buttons"):
                    yield Button(_("Write"), id="ok", variant="primary")
                    yield Button(_("Cancel"), id="cancel")

    @on(Button.Pressed, "#v-true")
    def _true(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#v-false")
    def _false(self) -> None:
        self.dismiss(False)

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#ok")
    @on(Input.Submitted)
    def _ok(self) -> None:
        text = self.query_one("#value", Input).value.strip()
        if not text:
            return
        if self.dpt is None:
            self.dismiss({"raw": text})
            return
        try:
            dptlib.encode(self.dpt, text)  # validation only – the server encodes again
        except Exception as exc:  # noqa: BLE001
            self.notify(_("Invalid value: {error}").format(error=exc), severity="error")
            return
        self.dismiss(text)


class ThemeDialog(ModalScreen[str | None]):
    """Pick a colour theme. The theme is previewed while browsing; Esc reverts."""

    BINDINGS = [Binding("escape", "cancel", _("Cancel"))]

    def __init__(self, current: str) -> None:
        super().__init__()
        self.original = current

    def compose(self) -> ComposeResult:
        from textual.widgets import OptionList
        from textual.widgets.option_list import Option

        from .themes import THEME_CHOICES, THEMES

        by_name = {t.name: t for t in THEMES}
        options = []
        for name, label, desc in THEME_CHOICES:
            t = by_name[name]
            # A row of colour blocks gives an impression of the theme.
            swatch = "".join(
                f"[{c}]██[/]" for c in (t.background, t.primary, t.accent, t.success, t.warning, t.error)
            )
            options.append(Option(f"[b]{_(label)}[/b]  {swatch}\n  [dim]{_(desc)}[/dim]", id=name))
        with Vertical(classes="dialog wide"):
            yield Label(_("Colour theme"), classes="dialog-title")
            yield OptionList(*options, id="theme-list")
            yield Static(_("↑↓ preview · Enter select · Esc cancel"), classes="muted")

    def on_mount(self) -> None:
        from textual.widgets import OptionList

        from .themes import THEME_CHOICES

        ol = self.query_one(OptionList)
        names = [n for n, _label, _desc in THEME_CHOICES]
        if self.original in names:
            ol.highlighted = names.index(self.original)
        ol.focus()

    def on_option_list_option_highlighted(self, event) -> None:
        if event.option.id:
            self.app.theme = event.option.id  # live preview

    def on_option_list_option_selected(self, event) -> None:
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.app.theme = self.original
        self.dismiss(None)


class LanguageDialog(ModalScreen[str | None]):
    """Pick the user interface language. Returns the language code or None (Esc)."""

    BINDINGS = [Binding("escape", "dismiss(None)", _("Cancel"))]

    def compose(self) -> ComposeResult:
        from textual.widgets import OptionList
        from textual.widgets.option_list import Option

        # Language names are shown in their own language, so they are never translated.
        options = [Option(f"[b]{name}[/b]  [dim]({code})[/dim]", id=code) for code, name in LANGUAGES.items()]
        with Vertical(classes="dialog"):
            yield Label(_("Language"), classes="dialog-title")
            yield OptionList(*options, id="language-list")
            yield Static(_("Enter select · Esc cancel. The user interface restarts in the new language."), classes="muted")

    def on_mount(self) -> None:
        from textual.widgets import OptionList

        ol = self.query_one(OptionList)
        codes = list(LANGUAGES)
        ol.highlighted = codes.index(get_language()) if get_language() in codes else 0
        ol.focus()

    def on_option_list_option_selected(self, event) -> None:
        self.dismiss(event.option.id)


class ExportDialog(ModalScreen[dict | None]):
    """Bus monitor export: format and period. Returns ``{"fmt", "hours"}`` (hours None = what is shown) or None."""

    BINDINGS = [Binding("escape", "dismiss(None)", _("Cancel"))]
    #: (format, label) and (hours, label); labels are translated when shown.
    FORMATS = (("xml", N_("ETS group monitor (.xml) – opens in ETS")), ("csv", N_("CSV (.csv) – Excel, LibreOffice")),
               ("json", N_("JSON lines (.jsonl) – scripts")))
    PERIODS = ((None, N_("The telegrams shown in the monitor")), (1, N_("Last hour (from the server's log)")),
               (24, N_("Last 24 hours")), (168, N_("Last 7 days")))

    def __init__(self, filter_text: str) -> None:
        super().__init__()
        self.filter_text = filter_text

    def compose(self) -> ComposeResult:
        from textual.widgets import RadioButton, RadioSet

        with Vertical(classes="dialog"):
            yield Label(_("Export telegrams"), classes="dialog-title")
            yield Label(_("Format"))
            with RadioSet(id="exp-fmt"):
                for i, (_f, label) in enumerate(self.FORMATS):
                    yield RadioButton(_(label), value=i == 0)
            yield Label(_("Period"))
            with RadioSet(id="exp-period"):
                for i, (_h, label) in enumerate(self.PERIODS):
                    yield RadioButton(_(label), value=i == 0)
            filt = self.filter_text or _("none – all telegrams")
            yield Static(_("Filter: {filter}").format(filter=filt), classes="muted", markup=False)
            with Horizontal(classes="dialog-buttons"):
                yield Button(_("Export"), id="exp-ok", variant="primary")
                yield Button(_("Cancel"), id="exp-cancel")

    def on_mount(self) -> None:
        from textual.widgets import RadioSet

        self.query_one("#exp-fmt", RadioSet).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        from textual.widgets import RadioSet

        if event.button.id != "exp-ok":
            self.dismiss(None)
            return
        fmt = self.FORMATS[max(0, self.query_one("#exp-fmt", RadioSet).pressed_index)][0]
        hours = self.PERIODS[max(0, self.query_one("#exp-period", RadioSet).pressed_index)][0]
        self.dismiss({"fmt": fmt, "hours": hours})


class ConfirmDialog(ModalScreen[bool]):
    """Yes/no question. Returns True for yes."""

    BINDINGS = [Binding("escape", "dismiss(False)", _("Cancel"))]

    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(_("Confirm"), classes="dialog-title")
            yield Static(self.text, markup=False)
            with Horizontal(classes="dialog-buttons"):
                yield Button(_("Yes"), id="yes", variant="warning")
                yield Button(_("No"), id="no")

    @on(Button.Pressed)
    def _pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")


class GADialog(ModalScreen[str | None]):
    """Details of one group address with write/read/trend actions.

    Returns the chosen action (``"write"``, ``"read"``, ``"trend"``) or None;
    the caller performs it (see ``ScadaApp.open_ga``).
    """

    BINDINGS = [
        Binding("escape", "dismiss(None)", _("Close")),
        Binding("w", "pick('write')", _("Write")),
        Binding("r", "pick('read')", _("Read")),
        Binding("t", "pick('trend')", _("Trend")),
    ]

    def __init__(self, ga: dict[str, Any], state: dict[str, Any] | None) -> None:
        super().__init__()
        self.ga = ga
        self.st = state or {}

    def compose(self) -> ComposeResult:
        ga, st = self.ga, self.st
        flags = " ".join(f for f, on_ in ((_("writable"), ga.get("writable")), (_("readable"), ga.get("readable"))) if on_)
        with Vertical(classes="dialog wide"):
            yield Label(f"{ga['address']}  {ga['name']}", classes="dialog-title", markup=False)
            with Grid(classes="kv"):
                for k, v in (
                    (_("Description"), ga.get("description") or "–"),
                    (_("Group"), ga.get("range") or "–"),
                    ("DPT", ga.get("dpt") or _("unknown")),
                    (_("Value"), st.get("text") or "–"),
                    (_("Raw"), st.get("raw") or "–"),
                    (_("Last changed"), ago(st.get("ts"))),
                    (_("Source"), st.get("src") or "–"),
                    (_("Flags"), flags or "–"),
                ):
                    yield Label(k, classes="kv-key")
                    yield Label(str(v), classes="kv-val", markup=False)
            with Horizontal(classes="dialog-buttons"):
                yield Button(_("Write") + " (w)", id="write", variant="primary")
                yield Button(_("Read") + " (r)", id="read")
                # Trends only make sense for numbers and on/off values.
                yield Button(_("Trend") + " (t)", id="trend",
                             disabled=not dptlib.is_numeric(ga.get("dpt")) and dptlib.split(ga.get("dpt"))[0] != 1)
                yield Button(_("Close"), id="close")

    def action_pick(self, what: str) -> None:
        self.dismiss(what)

    @on(Button.Pressed)
    def _pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None if event.button.id == "close" else event.button.id)
