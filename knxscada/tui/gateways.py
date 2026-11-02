# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""F11 Gateways: protocol connections, their tags and the gateways between them.

The view shows what the server's protocol hub reports (see
:mod:`knxscada.protocols.hub`):

* top: one row per connection (KNX, Modbus, MQTT, OPC UA …) with its state,
* left: the tags of the selected connection with live values,
* right: the gateway links with how many values they passed on and their last error.

Connections and gateways are edited in forms built from the module registry
(:data:`knxscada.protocols.PROTOCOLS`). The server validates the result,
writes ``protocols.yaml`` and restarts only the connections that changed.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

import yaml
from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Input,
    Label,
    OptionList,
    Select,
    Static,
    TextArea,
)
from textual.widgets.option_list import Option

from ..i18n import N_, _
from ..protocols import PROTOCOLS, Field, devices, installed
from ..protocols.config import DIRECTIONS, link_text, parse_link
from .dialogs import ConfirmDialog
from .views import View, hms
from .widgets import FastTable

if TYPE_CHECKING:
    from .app import ScadaApp

#: Colours of the connection state.
STATE_STYLE = {"connected": "bold green", "reading": "bold yellow", "error": "bold red", "off": "dim"}


def conn_state(s: dict[str, Any]) -> Text:
    """The state of a connection (one line of the hub's status) as coloured text."""
    if s["connected"] and not s.get("ready", True):
        return Text(_("reading …"), style=STATE_STYLE["reading"])
    if s["connected"]:
        return Text(_("connected"), style=STATE_STYLE["connected"])
    if not s.get("running"):
        return Text(s.get("error") or _("off"), style=STATE_STYLE["error"])
    return Text(s.get("error") or _("connecting …"), style=STATE_STYLE["error" if s.get("error") else "off"])


def show_value(value: Any) -> str:
    """A tag value as text for the table."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return _("on") if value else _("off")
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".")
    return str(value)


def tags_text(tags: dict[str, Any]) -> str:
    """The ``tags:`` of a connection as YAML, one tag per line."""
    lines = []
    for name, spec in (tags or {}).items():
        spec = {k: v for k, v in (spec or {}).items() if v not in (None, "")}
        lines.append(yaml.safe_dump({str(name): spec}, default_flow_style=None, allow_unicode=True, width=1000,
                                    sort_keys=False).strip())
    return "\n".join(lines)


def map_text(lines: list[Any]) -> str:
    """The mapping lines of a gateway, one per line (short form, or a one-line dict)."""
    out = []
    for item in lines or []:
        try:
            text = link_text(parse_link(item))
        except ValueError:
            continue
        if isinstance(text, dict):
            text = yaml.safe_dump(text, default_flow_style=True, allow_unicode=True, width=1000, sort_keys=False).strip()
        out.append(text)
    return "\n".join(out)


def parse_map(text: str) -> list[Any]:
    """The text of the mapping box → mapping lines (ValueError with the line number)."""
    lines: list[Any] = []
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("{"):
            try:
                item = yaml.safe_load(line)
            except yaml.YAMLError as exc:
                raise ValueError(_("line {n}: {error}").format(n=n, error=exc)) from None
            if not isinstance(item, dict):
                raise ValueError(_("line {n}: {error}").format(n=n, error=line))
            lines.append(item)
        else:
            lines.append(line)
    return lines


# ======================================================================================= dialogs
class ProtocolDialog(ModalScreen[str | None]):
    """Pick the protocol of a new connection."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "dismiss(None)", _("Cancel"))]

    def compose(self) -> ComposeResult:
        """List the protocols a new connection can use; KNX (set up in ``config.yaml``) and planned ones are left out.

        Protocols whose optional Python module is missing are still listed, marked "(not installed)", so the
        user learns what to install (``./scada modules install …``).
        """
        with Vertical(classes="dialog"):
            yield Label(_("New connection – protocol"), classes="dialog-title")
            options = []
            for p in PROTOCOLS.values():
                if p.planned or p.name == "knx":
                    continue
                note = "" if installed(p.name) else "  " + _("(not installed)")
                options.append(Option(f"{p.title:<10} {_(p.description)}{note}", id=p.name))
            yield OptionList(*options, id="proto-list")

    def on_mount(self) -> None:
        """Focus the protocol list so the arrow keys work at once."""
        self.query_one("#proto-list", OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Return the chosen protocol name (the option id) to the caller."""
        self.dismiss(event.option.id)


class ConnectionDialog(ModalScreen[dict | None]):
    """The settings and tags of one connection. Returns ``{"name", "conn"}``, ``{"delete": True}`` or None."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "dismiss(None)", _("Cancel"))]

    def __init__(self, protocol: str, name: str = "", conn: dict[str, Any] | None = None) -> None:
        """Prepare the form for a new or an existing connection.

        Args:
            protocol: key in :data:`PROTOCOLS`; decides which settings and tag fields the form shows.
            name: the connection's current name, empty for a new connection.
            conn: the connection's settings from ``protocols.yaml`` (copied, the original is not changed).
        """
        super().__init__()
        self.p = PROTOCOLS[protocol]
        self.name_ = name
        self.conn = dict(conn or {"protocol": protocol})
        self.new = not name
        self._model = str(self.conn.get("model") or "")   # device template in the form

    def _value(self, f: Field) -> Any:
        """The value of setting *f* in the connection, or the field's default when it is not set."""
        v = self.conn.get(f.key)
        return f.default if v is None else v

    def compose(self) -> ComposeResult:
        """Build the form: name, one row per setting of the protocol (with help text) and the tags as YAML.

        Each setting row has the id ``row-<key>`` so :meth:`_update_visible` can hide settings that do not apply
        (e.g. serial settings for Modbus TCP). Delete is only offered for an existing connection.
        """
        with Vertical(classes="dialog form"):
            title = _("New {protocol} connection") if self.new else _("{protocol} connection")
            yield Label(title.format(protocol=self.p.title), classes="dialog-title")
            if not installed(self.p.name):
                yield Static(_("The {protocol} module is not installed. Install it with: ./scada modules install {name}")
                             .format(protocol=self.p.title, name=self.p.name), classes="warning", markup=False)
            with VerticalScroll(classes="form-body"):
                with Horizontal(classes="form-row"):
                    yield Label(_("Name"), classes="form-label")
                    yield Input(self.name_, id="f-name", placeholder=_("e.g. plc1, meter1, scada"))
                for f in self.p.settings:
                    with Vertical(classes="form-field", id=f"row-{f.key}"):
                        with Horizontal(classes="form-row"):
                            yield Label(_(f.label), classes="form-label")
                            yield self._widget(f)
                        if f.help:
                            yield Static(_(f.help), classes="form-help", markup=False)
                if self.p.tag_fields:
                    yield Label(_("Tags – one per line:  Name: {setting: value, …}"), classes="form-label wide")
                    keys = ", ".join(f.key for f in self.p.tag_fields)
                    yield Static(_("Settings: {keys}").format(keys=keys), classes="form-help", markup=False)
                    address = next((f for f in self.p.tag_fields if f.required or f.key in ("address", "node", "topic")),
                                   None)
                    if address is not None and address.help:
                        yield Static(_(address.help), classes="form-help", markup=False)
                    yield TextArea(tags_text(self.conn.get("tags") or {}), id="f-tags", classes="form-text")
            with Horizontal(classes="dialog-buttons"):
                if not self.new:
                    yield Button(_("Delete"), id="f-delete", variant="error")
                yield Button(_("Save"), id="f-ok", variant="primary")
                yield Button(_("Cancel"), id="f-cancel")

    def _widget(self, f: Field):
        """The input widget for setting *f*: Select for choices, Checkbox for bool, Input for the rest.

        Lists are edited as comma-separated text; passwords are masked. The widget id is ``f-<key>``, which
        :meth:`_read` and :meth:`_fill_from_model` rely on.
        """
        value = self._value(f)
        wid = f"f-{f.key}"
        if f.kind == "choice":
            choices = list(f.choices)
            if value not in (None, "") and str(value) not in choices:
                choices.append(str(value))   # e.g. a model written another way in the file: keep it
            blank = _(f.blank) if f.blank else _("automatic")
            return Select([(c or blank, c) for c in choices], value=str(value) if value is not None else
                          Select.NULL, allow_blank=False, id=wid)
        if f.kind == "bool":
            return Checkbox("", bool(value), id=wid)
        if f.kind == "list":
            value = ", ".join(value) if isinstance(value, list) else (value or "")
        return Input("" if value is None else str(value), id=wid, password=f.kind == "password")

    def on_mount(self) -> None:
        """Hide the settings that do not apply to the initial values, then focus the name field."""
        self._update_visible()
        self.query_one("#f-name", Input).focus()

    @on(Select.Changed)
    def _changed(self, event: Select.Changed) -> None:
        """A Select changed: apply a newly chosen device template, then show/hide the dependent settings.

        Textual also sends Changed for the initial value when the form is mounted; comparing with
        :attr:`_model` keeps that from overwriting the saved settings with the template's.
        """
        if event.select.id == "f-model":
            model = "" if event.value is Select.NULL else str(event.value)
            if model != self._model:   # chosen by the user (not the initial value)
                self._model = model
                self._fill_from_model(model)
        self._update_visible()

    def _fill_from_model(self, model: Any) -> None:
        """A device was chosen: put its settings (serial settings, poll interval) in the form."""
        try:
            tpl = devices.find(model)
        except ValueError:
            return
        if tpl is None:
            return
        for key, value in (tpl.get("settings") or {}).items():
            # A template setting the form does not show (no field with that id) is skipped.
            for w in self.query(f"#f-{key}"):
                if isinstance(w, (Select, Input)):
                    w.value = str(value)
        self.notify(_("{model}: {n} tags and the serial settings come from the device template").format(
            model=tpl["model"], n=len(tpl.get("tags") or {})))

    def _read(self) -> dict[str, Any]:
        """The settings as they are in the form (empty fields left out)."""
        out: dict[str, Any] = {"protocol": self.p.name}
        for f in self.p.settings:
            w = self.query_one(f"#f-{f.key}")
            if isinstance(w, Select):
                value: Any = w.value if w.value is not Select.NULL else None
            elif isinstance(w, Checkbox):
                value = w.value
            else:
                text = w.value.strip()
                if not text:
                    continue
                if f.kind == "int":
                    try:
                        value = int(text)
                    except ValueError:
                        value = text   # the server reports it
                elif f.kind == "float":
                    try:
                        value = float(text.replace(",", "."))
                    except ValueError:
                        value = text
                elif f.kind == "list":
                    value = [x.strip() for x in text.split(",") if x.strip()]
                else:
                    value = text
            out[f.key] = value
        return out

    def _update_visible(self) -> None:
        """Show only the settings whose ``Field.visible`` condition holds for the values now in the form."""
        values = self._read()
        for f in self.p.settings:
            self.query_one(f"#row-{f.key}").display = f.visible(values)

    @on(Button.Pressed)
    def _pressed(self, event: Button.Pressed) -> None:
        """Save, Delete or Cancel.

        Save collects the visible settings, keeps keys of the file the form does not know about, and parses the
        tags box as YAML. A YAML error is shown and the dialog stays open; validation of the values themselves
        is left to the server when the result is saved.
        """
        if event.button.id == "f-cancel":
            self.dismiss(None)
            return
        if event.button.id == "f-delete":
            self.dismiss({"delete": True})
            return
        name = self.query_one("#f-name", Input).value.strip()
        values = self._read()
        fields = {f.key: f for f in self.p.settings}
        conn = {k: v for k, v in values.items() if k == "protocol" or fields[k].visible(values)}
        # Settings of the file that the form does not know (e.g. certificate paths) are kept.
        known = {f.key for f in self.p.settings} | {"protocol", "tags"}
        conn.update({k: v for k, v in self.conn.items() if k not in known})
        if self.p.tag_fields:
            try:
                tags = yaml.safe_load(self.query_one("#f-tags", TextArea).text) or {}
                if not isinstance(tags, dict):
                    # ValueError: shown to the user like a YAML error, by the handler right below.
                    raise ValueError(_("expected Name: {…} on each line"))  # noqa: TRY004
            except (yaml.YAMLError, ValueError) as exc:
                self.notify(_("Tags: {error}").format(error=exc), severity="error")
                return
            if tags:
                conn["tags"] = {str(k): v or {} for k, v in tags.items()}
        self.dismiss({"name": name, "conn": conn})


class GatewayDialog(ModalScreen[dict | None]):
    """One gateway: from, to, direction and its mapping lines. Returns ``{"gw"}``, ``{"delete": True}`` or None."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "dismiss(None)", _("Cancel"))]

    def __init__(self, names: list[str], gw: dict[str, Any] | None = None) -> None:
        """Prepare the form for a new gateway (*gw* None) or an existing one.

        Args:
            names: the connection names offered in From and To.
            gw: the gateway from ``protocols.yaml`` (copied).
        """
        super().__init__()
        self.names = names
        self.gw = dict(gw or {})
        self.new = gw is None

    def compose(self) -> ComposeResult:
        """Build the form: name, from, to, direction, deadband, min. interval, start values, on, mapping lines.

        "Send values at start" maps to ``initial`` (None = auto, True, False); the help text states that KNX
        never gets start values whatever is chosen here – a safety rule enforced by the gateway engine.
        """
        gw = self.gw
        opts = [(n, n) for n in self.names]
        with Vertical(classes="dialog form"):
            yield Label(_("New gateway") if self.new else _("Gateway"), classes="dialog-title")
            with VerticalScroll(classes="form-body"):
                for key, label, widget in (
                    ("name", N_("Name"), Input(str(gw.get("name") or ""), id="g-name")),
                    ("from", N_("From"), Select(opts, value=gw.get("from") or Select.NULL, id="g-from")),
                    ("to", N_("To"), Select(opts, value=gw.get("to") or Select.NULL, id="g-to")),
                    ("direction", N_("Direction"), Select([(_("one way (from → to)"), "forward"),
                                                           (_("both ways"), "both")],
                                                          value=gw.get("direction") or "forward", allow_blank=False,
                                                          id="g-direction")),
                    ("deadband", N_("Deadband"), Input(str(gw.get("deadband") or ""), id="g-deadband",
                                                       placeholder=_("e.g. 0.2 – smaller changes are not sent"))),
                    ("min_interval_ms", N_("Min. interval (ms)"),
                     Input(str(gw.get("min_interval_ms") or ""), id="g-min_interval_ms",
                           placeholder=_("e.g. 1000 – at most one value per second"))),
                    ("initial", N_("Send values at start"),
                     Select([(_("only to own servers"), "auto"), (_("yes, also to devices (not KNX)"), "yes"),
                             (_("no"), "no")],
                            value={None: "auto", True: "yes", False: "no"}.get(gw.get("initial"), "auto"),
                            allow_blank=False, id="g-initial")),
                    ("enabled", N_("On"), Checkbox("", gw.get("enabled", True) is not False, id="g-enabled")),
                ):
                    del key
                    with Horizontal(classes="form-row"):
                        yield Label(_(label), classes="form-label")
                        yield widget
                yield Static(_("Never to KNX: a start-up or reconnect does not put telegrams on the bus."),
                             classes="form-help", markup=False)
                yield Label(_("Mapping – one line each:"), classes="form-label wide")
                yield Static(_("source  ·  source -> new name  ·  Lights* -> Lys/{name}  ·  "
                               "{from: hr:100, to: Ute.Temp, scale: 0.1, direction: both}"),
                             classes="form-help", markup=False)
                yield TextArea(map_text(gw.get("map") or []), id="g-map", classes="form-text")
            with Horizontal(classes="dialog-buttons"):
                if not self.new:
                    yield Button(_("Delete"), id="g-delete", variant="error")
                yield Button(_("Save"), id="g-ok", variant="primary")
                yield Button(_("Cancel"), id="g-cancel")

    def on_mount(self) -> None:
        """Focus the name field."""
        self.query_one("#g-name", Input).focus()

    @on(Button.Pressed)
    def _pressed(self, event: Button.Pressed) -> None:
        """Save, Delete or Cancel.

        Save starts from the gateway's existing keys (unknown ones are kept), writes only values that differ
        from the defaults (``direction`` forward, ``initial`` auto, ``enabled`` true) so the YAML stays short,
        and stops with a notification if a mapping line cannot be parsed.
        """
        if event.button.id == "g-cancel":
            self.dismiss(None)
            return
        if event.button.id == "g-delete":
            self.dismiss({"delete": True})
            return
        try:
            lines = parse_map(self.query_one("#g-map", TextArea).text)
        except ValueError as exc:
            self.notify(_("Mapping: {error}").format(error=exc), severity="error")
            return
        gw = {k: v for k, v in self.gw.items() if k not in ("name", "from", "to", "direction", "deadband",
                                                             "min_interval_ms", "initial", "enabled", "map")}
        gw["name"] = self.query_one("#g-name", Input).value.strip()
        for key in ("from", "to"):
            v = self.query_one(f"#g-{key}", Select).value
            gw[key] = None if v is Select.NULL else v
        direction = self.query_one("#g-direction", Select).value
        if direction in DIRECTIONS and direction != "forward":
            gw["direction"] = direction
        for key in ("deadband", "min_interval_ms"):
            text = self.query_one(f"#g-{key}", Input).value.strip().replace(",", ".")
            if text:
                try:
                    gw[key] = float(text) if key == "deadband" else int(float(text))
                except ValueError:
                    gw[key] = text   # the server reports it
        initial = self.query_one("#g-initial", Select).value
        if initial in ("yes", "no"):
            gw["initial"] = initial == "yes"
        if not self.query_one("#g-enabled", Checkbox).value:
            gw["enabled"] = False
        gw["map"] = lines
        self.dismiss({"gw": gw})


@dataclass
class PointWrite:
    """What the write dialog returns: the value, and for BACnet the chosen priority or a release."""

    value: Any = None
    priority: int | None = None     # None: the tag's/connection's default (protocols without priorities: always)
    release: bool = False           # BACnet: write Null into the priority slot instead of a value


#: BACnet priorities offered in the write dialog with their names from the standard (ASHRAE 135, 19.2.1.1).
#: 1, 2 and 5 are for life safety and critical equipment and are left out on purpose: an operator in this
#: program must never be able to override a fire or smoke control command.
PRIORITY_NAMES = {3: N_("available"), 4: N_("available"), 6: N_("minimum on/off"), 7: N_("available"),
                  8: N_("manual operator"), 9: N_("available"), 10: N_("available"), 11: N_("available"),
                  12: N_("available"), 13: N_("available"), 14: N_("available"), 15: N_("available"),
                  16: N_("available (automatic control)")}


class PointDialog(ModalScreen["PointWrite | None"]):
    """Write a value to a tag. Returns a :class:`PointWrite`, or None when cancelled.

    *tag* is the tag as the server lists it (``points``). When it has a ``priority`` (BACnet), the dialog also
    offers the write priority – preset to that default – and a **Release** button for the chosen priority.
    A priority above the default (a lower number) gets a warning: the value then overrides the automatic
    control until it is released.
    """

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "dismiss(None)", _("Cancel"))]

    def __init__(self, title: str, tag: dict[str, Any]) -> None:
        """*title* is shown on top (description and ``conn/tag``); *tag* gives value, unit, type and priority."""
        super().__init__()
        self.title_ = title
        self.tag = tag
        self.default_priority: int | None = tag.get("priority")

    def compose(self) -> ComposeResult:
        """Current value, the priority (BACnet only), and an input or on/off buttons depending on the type."""
        with Vertical(classes="dialog"):
            yield Label(self.title_, classes="dialog-title", markup=False)
            yield Static(_("Now: {value} {unit}").format(value=show_value(self.tag.get("value")),
                                                          unit=self.tag.get("unit") or ""), markup=False)
            if self.default_priority is not None:
                options = [(f"{p} – {_(name)}", p) for p, name in PRIORITY_NAMES.items()]
                if self.default_priority not in PRIORITY_NAMES:   # configured 1, 2 or 5: still shown, not offered
                    options.insert(0, (str(self.default_priority), self.default_priority))
                with Horizontal(classes="dialog-row"):
                    yield Label(_("Priority"), classes="dialog-label")
                    yield Select(options, value=self.default_priority, allow_blank=False, id="p-priority")
                yield Static("", id="p-priority-note", classes="dialog-note")
            if self.tag.get("type") == "bool":
                with Horizontal(classes="dialog-buttons"):
                    if self.default_priority is not None:
                        yield Button(_("Release"), id="p-release")
                    yield Button(_("On"), id="p-true", variant="primary")
                    yield Button(_("Off"), id="p-false")
                    yield Button(_("Cancel"), id="p-cancel")
            else:
                yield Input(show_value(self.tag.get("value")), id="p-value")
                with Horizontal(classes="dialog-buttons"):
                    if self.default_priority is not None:
                        yield Button(_("Release"), id="p-release")
                    yield Button(_("Write"), id="p-ok", variant="primary")
                    yield Button(_("Cancel"), id="p-cancel")

    def on_mount(self) -> None:
        """Focus the input (or the On button), and explain the preset priority."""
        self._note()
        for w in self.query(Input):
            w.focus()
            return
        self.query_one("#p-true", Button).focus()

    def priority(self) -> int | None:
        """The chosen priority, or None when the tag has none (not BACnet)."""
        if self.default_priority is None:
            return None
        return int(self.query_one("#p-priority", Select).value)  # type: ignore[arg-type]

    @on(Select.Changed, "#p-priority")
    def _note(self) -> None:
        """Explain the chosen priority; warn when it is above the default (it then overrides the automatic)."""
        if self.default_priority is None:
            return
        p = self.priority()
        note = self.query_one("#p-priority-note", Static)
        if p is not None and p < self.default_priority:
            note.update(Text(_("Priority {priority} overrides the automatic control (priority {default}) until "
                               "the value is released again with Release.").format(priority=p,
                                                                                  default=self.default_priority),
                             style="bold yellow"))
        else:
            note.update(Text(_("Lower number = higher priority. The highest active priority decides the value."),
                             style="dim"))

    def _result(self, value: Any = None, release: bool = False) -> PointWrite:
        """The dialog's answer; a priority only when it differs from the default (the server then uses its own)."""
        p = self.priority()
        return PointWrite(value, p if (release or p != self.default_priority) else None, release)

    @on(Input.Submitted)
    def _submitted(self) -> None:
        """Enter in the input field is the same as Write."""
        self.dismiss(self._result(self.query_one("#p-value", Input).value))

    @on(Button.Pressed)
    def _pressed(self, event: Button.Pressed) -> None:
        """Write, On/Off, Release or Cancel."""
        bid = event.button.id
        if bid == "p-ok":
            self.dismiss(self._result(self.query_one("#p-value", Input).value))
        elif bid in ("p-true", "p-false"):
            self.dismiss(self._result(bid == "p-true"))
        elif bid == "p-release":
            self.dismiss(self._result(release=True))
        else:
            self.dismiss(None)


def confirm_point_write(app: ScadaApp, conn: str, name: str, info: dict[str, Any], w: PointWrite | None,
                        old: str, new: str | None = None) -> None:
    """Ask "Write X to «tag»?" (or "Release …?") and send the write to the server after Yes.

    Shared by F11, Systems › Settings and Monitor, so every write of a protocol tag is confirmed the same way.
    *old* is the current value as text; *new* the new value as text (default: the value with the tag's unit).
    The server logs the write with the user; errors from the server or the device are shown as a notification.
    """
    if w is None:
        return
    tag = info.get("description") or name
    address = f"{conn}/{name}"
    if w.release:
        shown = _("released (priority {priority})").format(priority=w.priority)
        text = _("Release «{tag}» at priority {priority}?\nThe value with the next lower priority takes over.\n"
                 "Now: {old}\n({address})").format(tag=tag, priority=w.priority, old=old, address=address)
    else:
        shown = new or f"{show_value(w.value) if not isinstance(w.value, str) else w.value} {info.get('unit') or ''}".strip()
        text = _("Write {new} to «{tag}»?\nNow: {old}\n({address})").format(new=shown, tag=tag, old=old,
                                                                         address=address)
        if w.priority is not None:
            text += "\n" + _("Priority {priority} (instead of {default})").format(priority=w.priority,
                                                                                default=info.get("priority"))

    async def send() -> None:
        """The write itself, after Yes."""
        extra: dict[str, Any] = {}
        if w.priority is not None:
            extra["priority"] = w.priority
        if w.release:
            extra["release"] = True
        try:
            await app.client.request("point_write", conn=conn, tag=name, value=w.value, **extra)
        except Exception as exc:  # noqa: BLE001 - the server's message is shown as it is
            app.notify(f"{address}: {exc}", severity="error")
            return
        app.notify(_("Written: {tag} = {value}").format(tag=address, value=shown))

    app.push_screen(ConfirmDialog(text), lambda ok: app.run_worker(send(), group="point-write") if ok else None)


# ========================================================================================= view
class GatewayView(View):
    """F11: connections, tags and gateways."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("a", "add_connection", _("New connection")),
        Binding("e", "edit_connection", _("Edit connection")),
        Binding("g", "add_gateway", _("New gateway")),
        Binding("G", "edit_gateway", _("Edit gateway")),
        Binding("w", "write", _("Write value")),
        Binding("r", "reload", _("Re-read protocols.yaml")),
    ]

    def __init__(self, **kw: Any) -> None:
        """Create the empty view; the data comes from the server when the view is shown (:meth:`load`)."""
        super().__init__(**kw)
        self.snap: dict[str, Any] = {}
        self.conn = ""                        # the selected connection
        self.points: dict[str, dict] = {}     # tag name → point of the selected connection

    @property
    def scada(self) -> ScadaApp:
        """The app, typed as :class:`ScadaApp` for the helpers (``request``, ``notify``, ``state``)."""
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        """Three tables (connections on top, tags and gateway links below) and a line for config problems."""
        conns = FastTable(id="gw-conns", cursor_type="row")
        conns.border_title = _("Connections   (a: new · e/Enter: edit)")
        yield conns
        with Horizontal(id="gw-bottom"):
            tags = FastTable(id="gw-tags", cursor_type="row", zebra_stripes=True)
            tags.border_title = _("Tags   (w/Enter: write)")
            yield tags
            links = FastTable(id="gw-links", cursor_type="row", zebra_stripes=True)
            links.border_title = _("Gateways   (g: new · G/Enter: edit)")
            yield links
        yield Static("", id="gw-problems", markup=False)

    def on_mount(self) -> None:
        """Add the columns of the three tables (labels translated here, widths in characters)."""
        for tid, cols in (
            ("#gw-conns", (("name", N_("Name"), 16), ("protocol", N_("Protocol"), 10), ("role", N_("Role"), 7),
                           ("where", N_("Address"), 30), ("tags", N_("Tags"), 6), ("state", N_("State"), 40))),
            ("#gw-tags", (("name", N_("Tag"), 22), ("address", N_("Address"), 14), ("value", N_("Value"), 12),
                          ("unit", N_("Unit"), 5), ("q", N_("Quality"), 6), ("desc", N_("Description"), 30))),
            ("#gw-links", (("gw", N_("Gateway"), 16), ("from", N_("From"), 22), ("dir", "", 1), ("to", N_("To"), 22),
                           ("count", N_("Sent"), 6), ("ts", N_("Last"), 8), ("error", N_("Error"), 40))),
        ):
            t = self.query_one(tid, DataTable)
            for key, label, width in cols:
                t.add_column(_(label) if label else "", key=key, width=width)

    # -- data from the server -------------------------------------------------------------------
    def on_show_view(self) -> None:
        """Called when F11 is shown: fetch a fresh snapshot of the protocol configuration and state."""
        self.load()

    @work(exclusive=True, group="gateways")
    async def load(self) -> None:
        """Worker: ask the server for the ``protocols`` snapshot (config, status, links, problems) and show it.

        None means the request failed (already notified by ``request``) – most likely an older server.
        """
        snap = await self.scada.request("protocols")
        if snap is None:
            self.query_one("#gw-problems", Static).update(
                _("The server does not answer – or it is older than this version (restart it)."))
            return
        self._apply(snap)

    def _apply(self, snap: dict[str, Any]) -> None:
        """Show a ``protocols`` snapshot: connection table, gateway links and up to six problems.

        Args:
            snap: ``{"path", "config", "status", "links", "problems"}`` as sent by the server.
        """
        self.snap = snap
        self.on_protocols(snap.get("status") or [])
        self._fill_links()
        problems = list(snap.get("problems") or [])
        if not snap.get("status"):
            problems.insert(0, _("No connections yet. Press a to add one, or edit {path}.").format(path=snap.get("path")))
        self.query_one("#gw-problems", Static).update("\n".join(problems[:6]))

    def on_protocols(self, status: list[dict[str, Any]]) -> None:
        """New connection states (pushed by the server, or from a snapshot)."""
        if self.snap:
            self.snap["status"] = status
        t = self.query_one("#gw-conns", FastTable)
        if not t.columns:   # pushed before on_mount added the columns; load() fills the table when shown
            return
        row = t.cursor_row
        t.clear()
        for s in status:
            t.add_row(s["name"], s["title"], _(s["role"]), s.get("where") or "", str(s.get("tags", "")), conn_state(s),
                      key=s["name"])
        if t.row_count:
            t.move_cursor(row=min(max(row, 0), t.row_count - 1))
            if not self.conn or self.conn not in {s["name"] for s in status}:
                self._select(status[min(max(row, 0), t.row_count - 1)]["name"])

    def _fill_links(self) -> None:
        """Fill the gateway table: one row per active link, then one row per gateway that has no link yet.

        Row keys are the link keys from the hub, or ``gw:<name>`` for unlinked gateways; :meth:`action_edit_gateway`
        reads the gateway name back from them.
        """
        t = self.query_one("#gw-links", FastTable)
        t.clear()
        for link in self.snap.get("links") or []:
            t.add_row(link["gateway"], link["from"], "↔" if link["both"] else "→", link["to"], str(link["count"]),
                      hms(link["ts"]) if link["ts"] else "", Text(link["error"], style="red") if link["error"] else "",
                      key=link["key"])
        # Gateways without any link yet (no tags matched) still show, so they can be edited.
        linked = {link["gateway"] for link in self.snap.get("links") or []}
        for gw in (self.snap.get("config") or {}).get("gateways") or []:
            name = str(gw.get("name") or "")
            if name not in linked:
                off = _("off") if gw.get("enabled") is False else _("no tags linked")
                t.add_row(name, str(gw.get("from") or ""), "", str(gw.get("to") or ""), "", "", Text(off, style="dim"),
                          key=f"gw:{name}")

    @on(DataTable.RowHighlighted, "#gw-conns")
    def _conn_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """The cursor moved to another connection: load its tags."""
        if event.row_key is not None and event.row_key.value != self.conn:
            self._select(str(event.row_key.value))

    def _select(self, conn: str) -> None:
        """Make *conn* the selected connection and load its tags in the background."""
        self.conn = conn
        self.load_points(conn)

    @work(exclusive=True, group="gateway-points")
    async def load_points(self, conn: str) -> None:
        """Worker: fetch the tags of *conn* with their current values and fill the tag table.

        The answer is dropped if the user has selected another connection meanwhile, so a slow connection
        cannot overwrite the tags of a quick one.
        """
        pts = await self.scada.request("points", conn=conn, limit=5000) or []
        if conn != self.conn:
            return
        self.points = {p["name"]: p for p in pts}
        t = self.query_one("#gw-tags", FastTable)
        t.clear()
        for p in pts:
            t.add_row(p["name"], p["address"] if p["address"] != p["name"] else "", show_value(p["value"]),
                      p.get("unit") or "", self._quality(p.get("q")), p.get("description") or "", key=p["name"])
        t.border_subtitle = _("{n} tags").format(n=len(pts))

    @staticmethod
    def _quality(q: str | None) -> Text:
        """The quality column: empty for good, "old"/"bad" (bad in red), other codes as they are."""
        return Text({"good": "", "old": _("old"), "bad": _("bad")}.get(q or "", q or ""),
                    style="red" if q == "bad" else "dim")

    def on_points(self, values: dict[str, list]) -> None:
        """Values pushed by the server (``conn/tag → [value, ts, quality]``)."""
        if not self.display or not self.conn:
            return
        prefix = self.conn + "/"
        t = self.query_one("#gw-tags", FastTable)
        for key, (value, _ts, q) in values.items():
            if not key.startswith(prefix):
                continue
            name = key[len(prefix):]
            if name in self.points:
                self.points[name].update(value=value, q=q)
                t.update_cell(name, "value", show_value(value))
                t.update_cell(name, "q", self._quality(q))
            else:
                self.load_points(self.conn)   # a new tag (e.g. an MQTT topic)
                return

    def on_telegram(self, t: dict) -> None:
        """KNX telegram: update the value of the group address if the KNX connection is the one shown.

        KNX values come with telegrams, not with ``points``; the value is taken from the app's state, which
        the telegram has already updated.
        """
        if self.conn != "knx" or not self.display or "dst" not in t or t["dst"] not in self.points:
            return
        st = self.scada.state.get(t["dst"]) or {}
        self.points[t["dst"]]["value"] = st.get("value")
        self.query_one("#gw-tags", FastTable).update_cell(t["dst"], "value", show_value(st.get("value")))

    # -- editing --------------------------------------------------------------------------------
    def _config(self) -> dict[str, Any]:
        """A deep copy of the configuration from the last snapshot, safe to edit before saving."""
        return copy.deepcopy(self.snap.get("config") or {"connections": {}, "gateways": []})

    @work(exclusive=True, group="gateway-save")
    async def _save(self, data: dict[str, Any], done: str) -> None:
        """Worker: send the edited configuration (``protocols_save``) and show the new snapshot.

        The server validates, writes ``protocols.yaml`` and restarts only the changed connections. Validation
        errors come back as an exception and are shown for 15 s; nothing is changed then.

        Args:
            data: the whole configuration (``connections`` and ``gateways``).
            done: the notification shown on success.
        """
        try:
            snap = await self.scada.client.request("protocols_save", data=data)
        except Exception as exc:  # noqa: BLE001 - validation errors from the server
            self.scada.notify(str(exc), title=_("Not saved"), severity="error", timeout=15)
            return
        self.scada.notify(done)
        self._apply(snap)
        if self.conn:
            self.load_points(self.conn)

    def action_add_connection(self) -> None:
        """a: pick a protocol, then open an empty connection form for it."""
        def picked(protocol: str | None) -> None:
            """Open the connection form for the chosen protocol (None = cancelled)."""
            if protocol:
                self.app.push_screen(ConnectionDialog(protocol), self._connection_done(""))

        self.app.push_screen(ProtocolDialog(), picked)

    def action_edit_connection(self) -> None:
        """e/Enter: edit the selected connection. KNX is configured in ``config.yaml`` and cannot be edited here."""
        if not self.conn:
            return
        if self.conn == "knx":
            self.scada.notify(_("KNX is set up in config.yaml (knx: and project:)."))
            return
        conn = (self._config().get("connections") or {}).get(self.conn)
        if not conn or conn.get("protocol") not in PROTOCOLS:
            return
        self.app.push_screen(ConnectionDialog(conn["protocol"], self.conn, conn), self._connection_done(self.conn))

    def _connection_done(self, old: str):
        """Make the result callback for the connection form.

        Args:
            old: the connection's name before editing, empty for a new connection.

        Returns:
            A callback that applies the form's result (save, rename, delete) to a copy of the configuration
            and saves it.
        """
        def done(result: dict | None) -> None:
            """Apply the form's result: delete (refused while a gateway uses the connection), rename or add.

            A rename also changes ``from``/``to`` of the gateways that used the old name, so they keep working.
            """
            if not result:
                return
            data = self._config()
            conns = data.setdefault("connections", {})
            if result.get("delete"):
                used = [g.get("name") for g in data.get("gateways") or [] if old in (g.get("from"), g.get("to"))]
                if used:
                    self.scada.notify(_("{name} is used by the gateways {gateways} – change them first").format(
                        name=old, gateways=", ".join(map(str, used))), severity="error")
                    return
                conns.pop(old, None)
                self._save(data, _("Connection {name} deleted").format(name=old))
                return
            name = result["name"]
            if old and name != old:
                conns.pop(old, None)
                for gw in data.get("gateways") or []:   # gateways follow the new name
                    for key in ("from", "to"):
                        if gw.get(key) == old:
                            gw[key] = name
            elif not old and name in conns:
                self.scada.notify(_("There is already a connection called {name}").format(name=name), severity="error")
                return
            conns[name] = result["conn"]
            self.conn = name
            self._save(data, _("Connection {name} saved").format(name=name))

        return done

    def _names(self) -> list[str]:
        """The names of all connections (incl. KNX), offered as From/To in the gateway form."""
        return [s["name"] for s in self.snap.get("status") or []]

    def action_add_gateway(self) -> None:
        """g: open an empty gateway form."""
        self.app.push_screen(GatewayDialog(self._names()), self._gateway_done(None))

    def action_edit_gateway(self) -> None:
        """G/Enter: edit the gateway of the selected row (a link ``gateway|…`` or an unlinked ``gw:name``)."""
        t = self.query_one("#gw-links", FastTable)
        if not t.row_count:
            return
        key = str(t.coordinate_to_cell_key(t.cursor_coordinate).row_key.value)
        name = key[3:] if key.startswith("gw:") else key.split("|", 1)[0]
        gws = self._config().get("gateways") or []
        index = next((i for i, g in enumerate(gws) if str(g.get("name") or "") == name), None)
        if index is not None:
            self.app.push_screen(GatewayDialog(self._names(), gws[index]), self._gateway_done(index))

    def _gateway_done(self, index: int | None):
        """Make the result callback for the gateway form.

        Args:
            index: position of the gateway in ``gateways``, None for a new one.
        """
        def done(result: dict | None) -> None:
            """Apply the form's result: delete, replace or append the gateway, then save."""
            if not result:
                return
            data = self._config()
            gws = data.setdefault("gateways", [])
            if result.get("delete"):
                if index is not None:
                    name = gws.pop(index).get("name")
                    self._save(data, _("Gateway {name} deleted").format(name=name))
                return
            if index is None:
                gws.append(result["gw"])
            else:
                gws[index] = result["gw"]
            self._save(data, _("Gateway {name} saved").format(name=result["gw"]["name"]))

        return done

    def action_write(self) -> None:
        """w/Enter: write a value to the selected tag.

        KNX tags open the normal group address dialog; read-only tags (``writable: false``) are refused with a
        warning; others get the write dialog and then a confirmation (:func:`confirm_point_write`).
        """
        t = self.query_one("#gw-tags", FastTable)
        if not t.row_count or not self.conn:
            return
        name = str(t.coordinate_to_cell_key(t.cursor_coordinate).row_key.value)
        if self.conn == "knx":
            self.scada.open_ga(name)   # the usual KNX dialog: DPT-aware, with confirmation
            return
        point = self.points.get(name) or {}
        if not point.get("writable", True):
            self.scada.notify(_("{tag} is read-only").format(tag=f"{self.conn}/{name}"), severity="warning")
            return
        conn = self.conn
        old = f"{show_value(point.get('value'))} {point.get('unit') or ''}".strip()
        # Confirmed like everywhere else: a value is never sent to a device by a single key press.
        self.app.push_screen(PointDialog(f"{conn}/{name}", point),
                             lambda w: confirm_point_write(self.scada, conn, name, point, w, old))

    @on(DataTable.RowSelected, "#gw-conns")
    def _conn_selected(self) -> None:
        """Enter on a connection edits it."""
        self.action_edit_connection()

    @on(DataTable.RowSelected, "#gw-tags")
    def _tag_selected(self) -> None:
        """Enter on a tag writes to it."""
        self.action_write()

    @on(DataTable.RowSelected, "#gw-links")
    def _link_selected(self) -> None:
        """Enter on a gateway link edits the gateway."""
        self.action_edit_gateway()

    @work(exclusive=True, group="gateway-save")
    async def action_reload(self) -> None:
        """r: ask the server to re-read ``protocols.yaml`` (after editing the file by hand) and show the result.

        Same worker group as :meth:`_save`, so a reload and a save never run at the same time.
        """
        try:
            snap = await self.scada.client.request("protocols_reload")
        except Exception as exc:  # noqa: BLE001
            self.scada.notify(str(exc), severity="error")
            return
        self.scada.notify(_("protocols.yaml re-read"))
        self._apply(snap)
