# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""Widgets: datapoint rows (switch, dimmer, setpoint …) and fast building blocks.

Two groups of widgets live here:

**Datapoint rows** – one row per datapoint on a room card. Each row watches
its status group address (via :class:`GAWatcher`) and updates itself when a
new value arrives. Writing goes through ``ScadaApp.write`` → server → bus;
the row does *not* change its own state optimistically, it waits for the
status telegram. That way the screen always shows what the bus says.

**Performance building blocks** – replacements for Textual widgets that
were too slow for a live SCADA, especially over SSH:

* :class:`LiveStatic` – like ``Static`` but repaints only on real change
  and never triggers a full-screen layout.
* :class:`FastTable` – a ``DataTable`` that skips per-cell measuring when
  all columns have a fixed width.
* :class:`LineLog` – a scrolling log that formats and renders only the
  visible lines (bus monitor, activity log).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.geometry import Region, Size
from textual.message import Message
from textual.scroll_view import ScrollView
from textual.strip import Strip
from textual.widget import Widget
from textual.widgets import Button, DataTable, Input, Label, Select, Switch
from textual.widgets.data_table import CellDoesNotExist, ColumnKey, RowKey

from .. import dpt as dptlib
from ..i18n import N_, _

if TYPE_CHECKING:
    from .app import ScadaApp

#: ETS usage text → short English description shown on card borders (translated when shown).
USAGE_TEXT = {
    "dimmable light": N_("dimmable light"),
    "switchable light": N_("light"),
    "heating radiator": N_("radiator"),
    "heating floor": N_("floor heating"),
    "sun protection": N_("sun protection"),
    "custom": "",
}

#: Words in a label that mean "True is bad" – such values are shown red instead of green.
ALARM_WORDS = ("alarm", "feil", "fault", "failure", "short", "malfunction", "battery", "warning", "overload")


def ago(ts: float | None) -> str:
    """Human-readable age of a timestamp: "12 s ago", "5 min ago", or a date."""
    if not ts:
        return _("never")
    d = time.time() - ts
    if d < 60:
        return _("{n} s ago").format(n=f"{d:.0f}")
    if d < 3600:
        return _("{n} min ago").format(n=f"{d / 60:.0f}")
    if d < 86400:
        return _("{n} h ago").format(n=f"{d / 3600:.1f}")
    return time.strftime("%d.%m %H:%M", time.localtime(ts))


# ======================================================================= fast widgets
class FastTable(DataTable):
    """A ``DataTable`` that is cheaper to fill and to update.

    * **Adding rows:** the standard DataTable measures every cell to compute
      automatic column widths. With hundreds of rows that made the group
      address view slow to open. When every column has an explicit
      ``width=``, only the virtual size is updated.
    * **Updating a cell:** ``DataTable.update_cell`` invalidates the render
      caches of the *whole* table and repaints it, so a live value column
      re-rendered every visible row for every telegram (tens of percent CPU
      on a small machine). Here only the cache entries of the changed row are
      dropped and only that row is repainted.

    * **Plain text:** DataTable parses string cells as markup, so a name with
      brackets – from ETS, an alarm text or a user name sent by a client –
      could break the rendering (``[/x]`` raises an error and ends the TUI).
      Strings are shown as they are.

    Note: this relies on private DataTable internals (cache layout, the
    ``_update_count`` key) and must be checked when upgrading Textual. If
    they change, :meth:`update_cell` falls back to the standard method.
    """

    def add_row(self, *cells, **kw):
        return super().add_row(*(Text(c) if isinstance(c, str) else c for c in cells), **kw)

    def update_cell(self, row_key, column_key, value, *, update_width: bool = False) -> None:
        if isinstance(value, str):
            value = Text(value)
        if update_width:
            super().update_cell(row_key, column_key, value, update_width=True)
            return
        try:
            row_key = RowKey(row_key) if isinstance(row_key, str) else row_key
            column_key = ColumnKey(column_key) if isinstance(column_key, str) else column_key
            if row_key not in self._row_locations or column_key not in self._column_locations:
                raise CellDoesNotExist(f"No cell exists for row_key={row_key!r}, column_key={column_key!r}.")
            self._data[row_key][column_key] = value
            row_index = self._row_locations.get(row_key)
            # Drop only what was rendered from this row (all caches are keyed by row or line).
            self._row_renderable_cache.discard((self._update_count, row_index))
            for cache in (self._cell_render_cache, self._row_render_cache):
                for key in [k for k in cache.keys() if k[0] == row_key]:
                    cache.discard(key)
            for key in list(self._line_cache.keys()):
                try:
                    if self._get_offsets(key[0])[0] == row_key:
                        self._line_cache.discard(key)
                except LookupError:
                    pass
        except (AttributeError, TypeError):  # Textual internals changed: use the safe, slow path
            super().update_cell(row_key, column_key, value)
            return
        self.refresh_row(row_index)

    def _update_dimensions(self, new_rows) -> None:
        if any(c.auto_width for c in self.columns.values()) or self._labelled_row_exists:
            super()._update_dimensions(new_rows)
            return
        width = sum(c.get_render_width(self) for c in self.columns.values()) + self._row_label_column_width
        header = self.header_height if self.show_header else 0
        self.virtual_size = Size(width, self._total_row_height + header)


class LineLog(ScrollView, can_focus=True):
    """Fast scrolling log with a cursor – used by the bus monitor and the activity log.

    Stores the raw items (e.g. telegram dicts) and a *formatter* that turns
    an item into a :class:`rich.text.Text` line. Lines are formatted and
    rendered lazily – only when they become visible – and cached, so adding
    a telegram costs almost nothing even with thousands of lines.

    Enter or a click posts :class:`LineLog.Selected` with the item.
    The log follows new lines while the cursor is on the last line. New lines
    appear on screen in batches (at most twice a second) to limit repaints.
    """

    COMPONENT_CLASSES = {"linelog--cursor"}
    #: Seconds new lines may wait before they are shown (repaints are batched).
    APPEND_DELAY = 0.5
    DEFAULT_CSS = """
    LineLog > .linelog--cursor { background: $primary 30%; }
    LineLog:focus > .linelog--cursor { background: $primary 45%; }
    """
    BINDINGS = [
        Binding("up", "move(-1)", show=False),
        Binding("down", "move(1)", show=False),
        Binding("pageup", "move(-20)", show=False),
        Binding("pagedown", "move(20)", show=False),
        Binding("home", "first", show=False),
        Binding("end", "last", show=False),
        Binding("enter", "select", show=False),
    ]

    class Selected(Message):
        """An item was chosen with Enter or a click."""

        def __init__(self, log: LineLog, data: Any) -> None:
            super().__init__()
            self.log = log
            self.data = data

        @property
        def control(self) -> LineLog:
            # Needed so the message can be used with @on(LineLog.Selected, "#id").
            return self.log

    def __init__(self, formatter: Callable[[Any], Text], width: int, max_lines: int = 1000, **kw: Any) -> None:
        super().__init__(**kw)
        self.formatter = formatter
        self.line_width = width
        self.max_lines = max_lines
        self._items: list[Any] = []
        self._strips: dict[int, Strip] = {}  # rendered lines, keyed by absolute line number
        self._offset = 0  # lines removed from the start so far (keeps cache keys stable)
        self.cursor = -1
        self.follow = True
        self._show_pending = False  # a _show_appended call is scheduled

    # -- data -----------------------------------------------------------------------
    def clear(self) -> None:
        self.set_items([])

    def set_items(self, items: list[Any]) -> None:
        """Replace all lines (used when the filter changes)."""
        self._items = list(items)
        self._strips.clear()
        self._offset = 0
        self.follow = True
        self.cursor = len(self._items) - 1
        self._update_size()
        self.scroll_end(animate=False, immediate=True)
        self.refresh()

    def append(self, item: Any) -> None:
        """Add a line at the bottom; old lines are dropped in chunks beyond *max_lines*."""
        self._items.append(item)
        # Trim in chunks of 200 rather than one line at a time: cheaper.
        if len(self._items) > self.max_lines + 200:
            cut = len(self._items) - self.max_lines
            del self._items[:cut]
            self._offset += cut
            self._strips = {k: v for k, v in self._strips.items() if k >= self._offset}
            self.cursor = max(0, self.cursor - cut)
        if self.follow:
            self.cursor = len(self._items) - 1
        # Scrolling repaints the whole visible log. With a busy bus that happened for every
        # telegram; now the screen catches up at most every APPEND_DELAY seconds.
        if not self._show_pending:
            self._show_pending = True
            self.set_timer(self.APPEND_DELAY, self._show_appended)

    def _show_appended(self) -> None:
        """Update size and scroll position for the lines appended since the last call."""
        self._show_pending = False
        self._update_size()
        if self.follow:
            self.scroll_end(animate=False, immediate=True)
        self.refresh()

    def refresh_lines(self) -> None:
        """Re-format all lines (e.g. after a theme change – colours are baked into the cache)."""
        self._strips.clear()
        self.refresh()

    @property
    def line_count(self) -> int:
        return len(self._items)

    def _update_size(self) -> None:
        """Set the scrollable size without a layout of the whole screen.

        ``virtual_size`` is a reactive with ``layout=True``: assigning it
        lays out every widget on the screen – for every new log line. Our
        own size does not depend on the content, so (like Textual does in
        ``Widget._size_updated``) only the value and the scrollbars are updated.
        """
        size = Size(self.line_width, len(self._items))
        if size == self.virtual_size:
            return
        try:
            self.set_reactive(Widget.virtual_size, size)
            self._scroll_update(size)  # private Textual API: check when upgrading
        except AttributeError:
            self.virtual_size = size

    # -- rendering ----------------------------------------------------------------------
    def render_line(self, y: int) -> Strip:
        """Textual line API: return screen line *y* of the visible area."""
        scroll_x, scroll_y = self.scroll_offset
        idx = scroll_y + y
        width = self.scrollable_content_region.width
        base = self.rich_style
        if idx >= len(self._items):
            return Strip.blank(width, base)
        key = idx + self._offset
        strip = self._strips.get(key)
        if strip is None:
            segs = list(self.formatter(self._items[idx]).render(self.app.console, end=""))
            strip = Strip(segs).apply_style(base)
            self._strips[key] = strip
        strip = strip.extend_cell_length(scroll_x + width, base).crop(scroll_x, scroll_x + width)
        if idx == self.cursor:
            strip = strip.apply_style(self.get_component_rich_style("linelog--cursor"))
        return strip

    # -- navigation -------------------------------------------------------------------
    def _goto(self, idx: int) -> None:
        if not self._items:
            return
        self.cursor = max(0, min(len(self._items) - 1, idx))
        self.follow = self.cursor == len(self._items) - 1  # back at the bottom → follow again
        self.scroll_to_region(Region(0, self.cursor, 1, 1), animate=False, immediate=True)
        self.refresh()

    def action_move(self, delta: int) -> None:
        self._goto((self.cursor if self.cursor >= 0 else len(self._items) - 1) + delta)

    def action_first(self) -> None:
        self._goto(0)

    def action_last(self) -> None:
        self._goto(len(self._items) - 1)

    def action_select(self) -> None:
        if 0 <= self.cursor < len(self._items):
            self.post_message(self.Selected(self, self._items[self.cursor]))

    def on_click(self, event: events.Click) -> None:
        idx = self.scroll_offset.y + event.y
        if 0 <= idx < len(self._items):
            self._goto(idx)
            self.action_select()


def columns(*fields: tuple[str, int, str]) -> Text:
    """Build one line with fixed-width columns from ``(text, width, style)`` tuples.

    Over-long text is cut with "…". Used for LineLog lines and log headers.
    """
    out = Text(no_wrap=True, overflow="crop")
    for text, width, style in fields:
        cell = text if len(text) <= width else text[: max(0, width - 1)] + "…"
        out.append(cell.ljust(width) + " ", style=style or None)
    return out


class LiveStatic(Widget):
    """Like ``Static``, but repaints only on real change and without a new layout.

    ``Static.update()`` always asks for a layout of the whole screen. With a
    clock, status tiles and values updating every second that kept the app
    busy and made it sluggish over SSH. Give these widgets a fixed height in
    CSS; only ``width: auto`` widgets get a layout refresh, and only when the
    text length changes.
    """

    def __init__(self, content: str | Text = "", **kw: Any) -> None:
        super().__init__(**kw)
        self._content: str | Text = content

    def set(self, content: str | Text) -> None:
        """Set new content: a Textual markup string or a Rich Text."""
        if isinstance(content, Text) and isinstance(self._content, Text):
            if content.plain == self._content.plain and content.spans == self._content.spans and content.style == self._content.style:
                return
        elif content == self._content:
            return
        old = self._content
        self._content = content
        old_len = len(old.plain if isinstance(old, Text) else old)
        new_len = len(content.plain if isinstance(content, Text) else content)
        self.refresh(layout=old_len != new_len and self.styles.width is not None and self.styles.width.is_auto)

    def set_text(self, text: str) -> None:
        """Set plain text (no markup interpretation, safe for GA names and values)."""
        self.set(Text(text))

    def render(self) -> str | Text:
        return self._content


# ======================================================================= datapoint rows
class GAWatcher:
    """Mixin: :meth:`on_state` is called whenever one of :attr:`watched` changes.

    Subclasses set ``self.watched`` (a tuple of group addresses) before they
    are mounted. Registration with the app happens on mount and is removed
    on unmount, so rebuilt room views do not leak watchers.
    """

    watched: tuple[str, ...] = ()

    @property
    def scada(self) -> ScadaApp:
        return self.app  # type: ignore[attr-defined,return-value]

    def on_mount(self) -> None:
        for ga in self.watched:
            self.scada.watch_ga(ga, self)
        self.on_state()  # show the current value immediately

    def on_unmount(self) -> None:
        for ga in self.watched:
            self.scada.unwatch_ga(ga, self)

    def on_state(self) -> None:  # overridden by subclasses
        pass


class DatapointRow(GAWatcher, Horizontal):
    """Base class of a card row: label on the left, value/controls on the right."""

    DEFAULT_CLASSES = "dp-row"

    def __init__(self, dp: dict[str, Any]) -> None:
        super().__init__()
        self.dp = dp
        self.read_ga = dp["status"] or dp["ga"]  # show the feedback when there is one
        self.watched = (self.read_ga,) if self.read_ga else ()

    @property
    def value(self) -> Any:
        return self.scada.state.get(self.read_ga, {}).get("value")

    @property
    def text(self) -> str:
        st = self.scada.state.get(self.read_ga)
        return st["text"] if st else "–"

    def _tip_text(self) -> str:
        """Tooltip: control and status address, DPT and age of the value."""
        st = self.scada.state.get(self.read_ga, {})
        parts = []
        if self.dp["ga"]:
            parts.append(f"{_('Control:')} {self.dp['ga']}")
        if self.dp["status"]:
            parts.append(f"{_('Status:')}  {self.dp['status']}")
        parts.append(f"DPT {self.dp['dpt'] or '?'}  •  {ago(st.get('ts'))}")
        return "\n".join(parts)

    def label(self) -> Label:
        return Label(_(self.dp["label"]), classes="dp-label", markup=False)

    def write(self, value: Any) -> None:
        self.scada.write(self.dp["ga"], value, self.dp["dpt"])


class SwitchRow(DatapointRow):
    """On/off switch. The text left of the switch is the status feedback."""

    def compose(self) -> ComposeResult:
        yield self.label()
        yield LiveStatic("", classes="dp-value")
        yield Switch(animate=False)

    def on_state(self) -> None:
        v = self.value
        val = self.query_one(".dp-value", LiveStatic)
        val.set_text(self.text)
        val.set_class(bool(v), "on")
        sw = self.query_one(Switch)
        # Update the switch without firing Changed – otherwise we would write it back.
        with sw.prevent(Switch.Changed):
            sw.value = bool(v)
        self.tooltip = self._tip_text()

    def on_switch_changed(self, event: Switch.Changed) -> None:
        event.stop()
        self.write(event.value)


class StepRow(DatapointRow):
    """Dimmer (%) and setpoint (°C): − value + …  (… opens a dialog for an exact value)."""

    def __init__(self, dp: dict[str, Any], step: float, lo: float, hi: float, bar: bool) -> None:
        super().__init__(dp)
        self.step, self.lo, self.hi, self.bar = step, lo, hi, bar

    def compose(self) -> ComposeResult:
        yield self.label()
        if self.bar:
            yield LiveStatic("", classes="dp-bar")
        yield Button("−", classes="dp-btn minus", compact=True)
        yield LiveStatic("", classes="dp-value")
        yield Button("+", classes="dp-btn plus", compact=True)
        yield Button("…", classes="dp-btn set", compact=True, tooltip=_("Set exact value"))

    def on_state(self) -> None:
        self.query_one(".dp-value", LiveStatic).set_text(self.text)
        if self.bar:
            v = self.value if isinstance(self.value, (int, float)) else 0
            frac = max(0.0, min(1.0, (v - self.lo) / (self.hi - self.lo)))
            width = 12
            full = int(round(frac * width))
            self.query_one(".dp-bar", LiveStatic).set_text("█" * full + "░" * (width - full))
        self.tooltip = self._tip_text()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.has_class("set"):
            from .dialogs import ValueDialog

            ga = self.scada.ga(self.dp["ga"])
            self.scada.push_screen(
                ValueDialog(f"{_(self.dp['label'])} – {ga.get('name', '')}", self.dp["dpt"], self.value),
                lambda v: v is not None and self.write(v),
            )
            return
        cur = self.value if isinstance(self.value, (int, float)) and not isinstance(self.value, bool) else None
        if cur is None:
            cur = self.lo if self.bar else 21.0  # no value yet: start from a sensible default
        delta = self.step if event.button.has_class("plus") else -self.step
        # Snap to the step grid and clamp to the allowed range.
        new = max(self.lo, min(self.hi, round((cur + delta) / self.step) * self.step))
        self.write(int(new) if self.bar else round(new, 1))


class ModeRow(DatapointRow):
    """HVAC operating mode as a drop-down list."""

    def compose(self) -> ComposeResult:
        yield self.label()
        opts = [(dptlib.hvac_label(o), o) for o in dptlib.enum_options(self.dp["dpt"])]
        yield Select(opts, prompt="–", compact=True, classes="dp-select")

    def on_state(self) -> None:
        sel = self.query_one(Select)
        with sel.prevent(Select.Changed):
            v = self.value
            sel.value = v if v in dptlib.enum_options(self.dp["dpt"]) else Select.NULL
        self.tooltip = self._tip_text()

    def on_select_changed(self, event: Select.Changed) -> None:
        event.stop()
        if event.value is not Select.NULL:
            self.write(event.value)


class SceneRow(DatapointRow):
    """Scene number input (1–64) with a Send button."""

    def compose(self) -> ComposeResult:
        yield self.label()
        yield Input(placeholder="1-64", type="integer", compact=True, classes="dp-input")
        yield Button(_("Send"), classes="dp-btn", compact=True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        v = self.query_one(Input).value
        if v:
            self.write(int(v))

    def on_state(self) -> None:
        self.tooltip = self._tip_text()


class ValueRow(DatapointRow):
    """Read-only value. On/off values are green when on, red when they mean an alarm."""

    def compose(self) -> ComposeResult:
        yield self.label()
        yield LiveStatic("", classes="dp-value wide")

    def on_state(self) -> None:
        val = self.query_one(".dp-value", LiveStatic)
        val.set_text(self.text)
        v = self.value
        if isinstance(v, bool):
            is_alarm = dptlib.split(self.dp["dpt"])[1] == 5 or any(w in self.dp["label"].lower() for w in ALARM_WORDS)
            val.set_class(v and is_alarm, "alarm")
            val.set_class(v and not is_alarm, "on")
        self.tooltip = self._tip_text()


def make_row(dp: dict[str, Any], ui: dict[str, Any]) -> DatapointRow:
    """Create the right row widget for a datapoint kind."""
    kind = dp["kind"]
    if kind == "switch":
        return SwitchRow(dp)
    if kind == "percent":
        return StepRow(dp, float(ui.get("dim_step", 10)), 0, 100, bar=True)
    if kind == "setpoint":
        return StepRow(dp, float(ui.get("setpoint_step", 0.5)), 5, 35, bar=False)
    if kind == "mode" and dptlib.enum_options(dp["dpt"]):
        return ModeRow(dp)
    if kind == "scene":
        return SceneRow(dp)
    return ValueRow(dp)


class FunctionCard(Vertical):
    """A bordered card for one function (e.g. "Ceiling light") with its rows."""

    DEFAULT_CLASSES = "card"

    def __init__(self, func: dict[str, Any], ui: dict[str, Any], show_room: bool = False) -> None:
        super().__init__()
        self.func = func
        self.ui = ui
        # Function and room names come from ETS; _() only changes the generic English ones.
        self.border_title = _(func["name"]) + (f" · {func['room']}" if show_room and func["room"] else "")
        usage = USAGE_TEXT.get(func["usage"], func["usage"])
        self.border_subtitle = _(usage) if usage else ""

    def compose(self) -> ComposeResult:
        for dp in self.func["datapoints"]:
            yield make_row(dp, self.ui)
