# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""Monitor › Modbus / OPC UA / BACnet / MQTT: the connections of one protocol and their traffic, live.

The KNX bus monitor (F4) shows telegrams. The other protocols poll or subscribe, so this monitor shows what
the hub passes on: every value change (``points`` from the server) and every write (``conn/tag ← value``
in the event log, with the user). The app keeps one buffer for all protocols (``ScadaApp.point_log``);
the view filters it by protocol and by the words in the filter field.

For troubleshooting, ``e`` lists instead the tags whose value is not good *now* (from the server's tag list, with
the reason the driver gave: a device's error answer, BACnet ``fault``/``out-of-service`` …); the column "Errors" of
the connection table counts them.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, ClassVar

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Input, Static

from ..i18n import N_, _
from .gateways import PointDialog, confirm_point_write, conn_state, show_value
from .views import View, hms
from .widgets import FastTable, LineLog, LiveStatic, columns

if TYPE_CHECKING:
    from .app import ScadaApp


def entries_from_points(values: dict[str, list]) -> list[dict[str, Any]]:
    """Log entries from a ``points`` message (``conn/tag → [value, ts, quality]``)."""
    out = []
    for key, (value, ts, q) in values.items():
        conn, _sep, tag = key.partition("/")
        out.append({"ts": ts or time.time(), "conn": conn, "tag": tag, "value": value, "q": q, "write": False})
    return out


def entry_from_event(ev: dict[str, Any], conns: set[str] | dict[str, Any]) -> dict[str, Any] | None:
    """A write from the event log (``conn/tag ← value``, logged by the hub) as a log entry, else None."""
    text = str(ev.get("text") or "")
    if " ← " not in text:
        return None
    key, value = text.split(" ← ", 1)
    conn, _sep, tag = key.partition("/")
    if not tag or conn not in conns:
        return None
    return {"ts": ev.get("ts") or time.time(), "conn": conn, "tag": tag, "value": value, "q": "",
            "write": True, "user": ev.get("user") or ""}


class ProtocolMonitorView(View):
    """The connections of one protocol on top, below every value change and write of their tags.

    One instance serves all protocols: the menu (Monitor › Modbus …) and F4 call :meth:`select` with the
    protocol, and the view filters the app's shared buffer ``ScadaApp.point_log`` by the connections of that
    protocol (``ScadaApp.conn_protocol``). Like the KNX bus monitor, the log is only appended to while the view
    is visible; when it is shown again it is rebuilt from the buffer, which keeps hidden views cheap.
    """

    #: Lines kept in the log widget (the app's buffer holds more; older lines are dropped from the top).
    MAX_ROWS = 2000
    #: Minimum seconds between two loads of the tag list of a connection (descriptions, units, writable).
    TAGS_MAX_AGE = 300
    #: (English column title, width).
    #: Value is wide enough for a date and time (a PLC clock ``2026-11-08-16:27:31.250``); Quality holds the reason
    #: of a bad value (``bad: fault, in-alarm``).
    COLS = ((N_("Time"), 8), ("", 1), (N_("Connection"), 16), (N_("Tag"), 32), (N_("Description"), 36),
            (N_("Value"), 24), (N_("Quality"), 30))
    #: A tag that changed more often than this in the last minute is "fast" (a PLC clock, a counter) and hidden by f.
    FAST_PER_MIN = 20
    #: Seconds between two recounts of the fast tags while entries arrive.
    FAST_RECOUNT_S = 5.0
    #: Keys of the view (the same as the KNX bus monitor where they mean the same thing, plus w = write).
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("p", "pause", _("Pause/resume")),
        Binding("f", "fast", _("Hide fast tags")),
        Binding("e", "bad", _("Bad tags")),
        Binding("c", "clear", _("Clear")),
        Binding("w", "write", _("Write")),
        Binding("slash", "focus_filter", _("Filter")),
    ]

    def __init__(self, **kw: Any) -> None:
        """Start empty: no protocol is chosen until :meth:`select` is called."""
        super().__init__(**kw)
        self.protocol = ""                              # "modbus", "opcua", "bacnet" … – "" before the first select
        self.paused = False                             # p: new entries are not shown (the buffer still fills)
        self.words: list[str] = []                      # filter words, lower case; all must match
        # "conn/tag" → the tag as the server describes it (description, unit, writable, type, value). Loaded per
        # connection by load(); kept across protocol switches so switching back is instant.
        self.tags: dict[str, dict[str, Any]] = {}
        self._tags_at: dict[str, float] = {}           # connection → time.time() of its last tag load
        # f: hide tags that change all the time – on from the start: a PLC clock (Act_Time) changing every second
        # would otherwise fill the log and push every other change out of sight.
        self.hide_fast = True
        self.show_bad = False                           # e: list the tags that are not good now, instead of the log
        self._fast: set[str] = set()                    # "conn/tag" of the fast tags (counted by _recount_fast)
        self._fast_at = 0.0                             # time.time() of the last recount
        # connection → (time, reads) of the previous status, for the reads per minute
        self._prev_reads: dict[str, tuple[float, int]] = {}
        self._rate: dict[str, float] = {}               # connection → reads per minute

    def compose(self) -> ComposeResult:
        """Filter field and LIVE/PAUSED state, the table of connections, and the log with its column header."""
        with Horizontal(classes="toolbar"):
            yield Input(placeholder=_("Filter: connection, tag, description or value  (/)"), id="pm-filter")
            yield Static("", id="pm-state")
        conns = FastTable(id="pm-conns", cursor_type="row", zebra_stripes=True)
        # The cursor row is only drawn while the table has the focus: its colours would otherwise hide the red error
        # count of the first connection (with one connection, always).
        conns.show_cursor = False
        conns.border_title = _("Connections")
        yield conns
        with Vertical(id="pm-box", classes="logbox"):
            yield LiveStatic("", id="pm-head", classes="loghead")
            yield LineLog(self._line, sum(w + 1 for _title, w in self.COLS), max_lines=self.MAX_ROWS, id="pm-log")

    def on_mount(self) -> None:
        """Columns of the connection table and the log header (fixed widths: FastTable skips measuring cells)."""
        t = self.query_one("#pm-conns", FastTable)
        for key, label, width in (("name", N_("Connection"), 18), ("role", N_("Role"), 8),
                                  ("where", N_("Address"), 30), ("tags", N_("Tags"), 6), ("state", N_("State"), 26),
                                  ("reads", N_("Reads"), 10), ("rate", N_("/min"), 7), ("changes", N_("Changes"), 9),
                                  ("errors", N_("Errors"), 7), ("drops", N_("Lost"), 5)):
            t.add_column(_(label), key=key, width=width)
        self.query_one("#pm-head", LiveStatic).set(columns(*((_(t) if t else "", w, "bold") for t, w in self.COLS)))
        self._update_state()

    @property
    def scada(self) -> ScadaApp:
        """The app with its buffers (``point_log``, ``protocol_status``) – typed for the editor."""
        return self.app  # type: ignore[return-value]

    def connections(self) -> list[dict[str, Any]]:
        """The hub's status lines of the connections of this protocol."""
        return [s for s in self.scada.protocol_status if s.get("protocol") == self.protocol]

    def select(self, protocol: str) -> None:
        """Show the monitor of *protocol* (``modbus``, ``opcua`` …): title, connections and the matching entries.

        Called by the menu and by F4 after the view has been made visible. The log is only rebuilt when the
        protocol changed – selecting the same protocol again keeps the cursor and the scroll position.
        """
        changed = protocol != self.protocol
        self.protocol = protocol
        title = next((s.get("title") for s in self.connections() if s.get("title")), protocol)
        self.query_one("#pm-box").border_title = _("{protocol} – value changes and writes").format(protocol=title)
        self.on_protocols()
        if changed:
            self.rebuild()
        self.load()

    def on_show_view(self) -> None:
        """Shown again: entries that arrived while hidden were not appended, so rebuild from the buffer."""
        self.rebuild()
        self.load()

    @work(exclusive=True, group="protomon")
    async def load(self) -> None:
        """Descriptions, units and writable flags of the tags (for the log and the write dialog).

        ``points`` messages carry only value, time and quality, so the rest comes from the server's tag list,
        fetched at most every TAGS_MAX_AGE seconds per connection. Afterwards the visible lines are formatted
        again, so descriptions also appear on lines that were drawn before the list arrived.
        """
        if not self.scada.client.connected:
            return
        now = time.time()
        for s in self.connections():
            conn = s["name"]
            if now - self._tags_at.get(conn, 0) < self.TAGS_MAX_AGE:
                continue
            self._tags_at[conn] = now
            for p in await self.scada.request("points", conn=conn, limit=5000) or []:
                self.tags[f"{conn}/{p['name']}"] = p
        self.query_one("#pm-log", LineLog).refresh_lines()

    # -- connections ------------------------------------------------------------------------------
    def on_protocols(self) -> None:
        """New connection states from the server (``protocols`` message, or after a protocol switch).

        The table is rebuilt: there are only a handful of connections. Its height follows the number of rows
        (at most 8), so the log below gets the rest of the screen.
        """
        t = self.query_one("#pm-conns", FastTable)
        row = t.cursor_row
        t.clear()
        now = time.time()
        for s in self.connections():
            reads = int(s.get("reads") or 0)
            prev = self._prev_reads.get(s["name"])
            if prev is not None and now - prev[0] >= 5 and reads >= prev[1]:
                self._rate[s["name"]] = (reads - prev[1]) * 60 / (now - prev[0])
            if prev is None or now - prev[0] >= 5:
                self._prev_reads[s["name"]] = (now, reads)
            rate = self._rate.get(s["name"])
            # Tags that are bad now (not the count of bad reads since start, which only ever grows).
            errors = int(s.get("bad") or 0)
            t.add_row(s["name"], _(s.get("role") or ""), s.get("where") or "", str(s.get("tags", "")), conn_state(s),
                      str(reads), "–" if rate is None else f"{rate:.0f}", str(s.get("changes") or 0),
                      Text(str(errors), style=self.scada.color("error") if errors else ""),
                      str(s.get("drops") or 0), key=s["name"])
        t.styles.height = min(t.row_count, 8) + 3
        if t.row_count:
            t.move_cursor(row=min(max(row, 0), t.row_count - 1))
        if self.show_bad and self.display:
            self.load_bad()          # the list of bad tags follows the status (every 10 s)

    def on_descendant_focus(self, event) -> None:
        """Show the cursor of the connection table while it has the focus (see :meth:`compose`)."""
        if getattr(event.widget, "id", None) == "pm-conns":
            event.widget.show_cursor = True

    def on_descendant_blur(self, event) -> None:
        """Hide the cursor of the connection table again when it loses the focus."""
        if getattr(event.widget, "id", None) == "pm-conns":
            event.widget.show_cursor = False

    # -- the log ----------------------------------------------------------------------------------
    def _match(self, e: dict[str, Any], fast: bool = True) -> bool:
        """True if entry *e* belongs to this protocol and contains every filter word.

        The words are searched in connection, tag, description, value and user, and in the word "write" for
        writes – so the filter "write" lists only what operators have written.
        """
        if self.scada.conn_protocol.get(e["conn"]) != self.protocol:
            return False
        if fast and self.hide_fast and not e["write"] and f"{e['conn']}/{e['tag']}" in self._fast:
            return False
        if not self.words:
            return True
        info = self.tags.get(f"{e['conn']}/{e['tag']}") or {}
        hay = (f"{e['conn']} {e['tag']} {info.get('description') or ''} {show_value(e['value'])} {e.get('user', '')}"
               f" {_('write') if e['write'] else ''}").lower()
        return all(w in hay for w in self.words)

    def _line(self, e: dict[str, Any]) -> Text:
        """LineLog formatter for one entry: a value change (←) or a write (→, with the user).

        A value change shows the value with the tag's unit and its quality only when it is not "good" (in the
        error colour), so problems stand out. A write shows the value as the server logged it (text, no unit)
        and the user in the quality column, both in the accent colour.
        """
        app = self.scada
        info = self.tags.get(f"{e['conn']}/{e['tag']}") or {}
        muted = app.color("muted")
        value = e["value"] if e["write"] else show_value(e["value"])
        unit = info.get("unit") or ""
        if unit and not e["write"] and value != "":
            value = f"{value} {unit}"
        if e["write"]:
            quality, q_style = e.get("user") or _("write"), app.color("accent")
        else:
            quality = e["q"] if e["q"] != "good" else ""
            if quality and e.get("why"):
                quality = f"{quality}: {e['why']}"
            q_style = app.color("error") if e["q"] not in ("good", "") else muted
        vals = (hms(e["ts"]), "→" if e["write"] else "←", e["conn"], e["tag"], info.get("description") or "",
                str(value), quality)
        styles = (muted, f"bold {app.color('accent')}" if e["write"] else muted, muted, "", muted,
                  f"bold {app.color('accent')}" if e["write"] else "bold", q_style)
        return columns(*((v, w, s) for v, (_title, w), s in zip(vals, self.COLS, styles)))

    def _recount_fast(self) -> None:
        """Find the tags that changed more than :attr:`FAST_PER_MIN` times in the last minute (app buffer)."""
        since = time.time() - 60
        counts: dict[str, int] = {}
        for e in reversed(self.scada.point_log):
            if e["ts"] < since:
                break
            if not e["write"]:
                key = f"{e['conn']}/{e['tag']}"
                counts[key] = counts.get(key, 0) + 1
        self._fast = {k for k, n in counts.items() if n > self.FAST_PER_MIN}
        self._fast_at = time.time()

    def rebuild(self) -> None:
        """Re-apply the protocol and the filter to the app's buffer (newest MAX_ROWS matches); with ``e`` on, load
        the bad tags instead (:meth:`load_bad`)."""
        self.words = self.query_one("#pm-filter", Input).value.lower().split()
        if self.show_bad:
            self.load_bad()
            return
        if self.hide_fast:
            self._recount_fast()
            self._update_state()     # the badge says how many are hidden
        items: list[dict[str, Any]] = []
        for e in reversed(self.scada.point_log):
            if self._match(e):
                items.append(e)
                if len(items) >= self.MAX_ROWS:
                    break
        items.reverse()
        log = self.query_one("#pm-log", LineLog)
        log.set_items(items)
        self._count()

    @work(exclusive=True, group="protomon-bad")
    async def load_bad(self) -> None:
        """``e``: one line per tag of this protocol whose value is not good now, with its reason, from the server's
        tag list (the log only shows changes, so a tag that has been bad since start-up appears there once at most).
        The filter words apply as in the log."""
        if not self.scada.client.connected:
            return
        items: list[dict[str, Any]] = []
        for s in self.connections():
            conn = s["name"]
            for p in await self.scada.request("points", conn=conn, limit=5000) or []:
                self.tags[f"{conn}/{p['name']}"] = p
                if p.get("q") in ("good", "", None):
                    continue
                e = {"ts": p.get("ts") or time.time(), "conn": conn, "tag": p["name"], "value": p.get("value"),
                     "q": p.get("q"), "why": p.get("why") or "", "write": False}
                if self._match(e, fast=False):
                    items.append(e)
        if not self.show_bad:
            return                                       # switched back while the list was loading
        self.query_one("#pm-log", LineLog).set_items(items)
        self.query_one("#pm-box").border_subtitle = _("{n} bad tags now (e: back to the log)").format(n=len(items))

    def _count(self) -> None:
        """The number of lines in the log, on the bottom border of the log box."""
        log = self.query_one("#pm-log", LineLog)
        self.query_one("#pm-box").border_subtitle = _("{n} entries").format(n=log.line_count)

    def on_entries(self, entries: list[dict[str, Any]]) -> None:
        """New entries in the app's buffer, called by the app for every ``points`` or write event.

        Called also while the view is hidden, so it must stay cheap: hidden or paused, nothing is done (the view
        is rebuilt from the buffer when shown or resumed). LineLog batches the repaints itself.
        """
        if self.paused or self.show_bad or not self.display or not self.protocol:
            return
        if self.hide_fast and time.time() - self._fast_at > self.FAST_RECOUNT_S:
            # A tag that just became fast is hidden from now on; lines already shown stay until the next rebuild.
            self._recount_fast()
            self._update_state()
        log = self.query_one("#pm-log", LineLog)
        for e in entries:
            if self._match(e):
                log.append(e)
        self._count()

    def _update_state(self) -> None:
        """The LIVE / PAUSED badge next to the filter field."""
        if self.show_bad:
            state = Text(" ⚠ " + _("BAD TAGS") + " ", style="reverse bold")
            self.query_one("#pm-state", Static).update(state)
            return
        state = Text(" ⏸ " + _("PAUSED") + " " if self.paused else " ● " + _("LIVE") + " ", style="reverse bold")
        if self.hide_fast:
            state.append("  " + _("{n} fast tags hidden (f)").format(n=len(self._fast)), style="dim")
        self.query_one("#pm-state", Static).update(state)

    @on(Input.Changed, "#pm-filter")
    def _filter(self) -> None:
        """Every keystroke in the filter field re-filters the buffer (fast enough for 10 000 entries)."""
        self.rebuild()

    @on(Input.Submitted, "#pm-filter")
    def _to_log(self) -> None:
        """Enter in the filter field moves to the log, so p/c/w work at once."""
        self.query_one("#pm-log", LineLog).focus()

    def action_focus_filter(self) -> None:
        """/: jump to the filter field."""
        self.query_one("#pm-filter", Input).focus()

    def action_pause(self) -> None:
        """p: stop or resume the live log. On resume, what arrived meanwhile is shown from the buffer."""
        self.paused = not self.paused
        self._update_state()
        if not self.paused:
            self.rebuild()

    def action_fast(self) -> None:
        """f: hide or show the tags that change all the time (more than FAST_PER_MIN per minute)."""
        self.hide_fast = not self.hide_fast
        self.rebuild()
        self._update_state()

    def action_bad(self) -> None:
        """e: list the tags that are not good now (troubleshooting), or go back to the live log."""
        self.show_bad = not self.show_bad
        self._update_state()
        self.rebuild()

    def action_clear(self) -> None:
        """c: empty the log on screen. The app's buffer is kept, so the lines return when the view is rebuilt."""
        self.query_one("#pm-log", LineLog).clear()
        self._count()

    # -- writing ----------------------------------------------------------------------------------
    @on(LineLog.Selected, "#pm-log")
    def _selected(self, event: LineLog.Selected) -> None:
        """Enter or a click on a line: offer to write to its tag."""
        self._write_dialog(event.data)

    def action_write(self) -> None:
        """w: the same as Enter – LineLog posts Selected for the line under the cursor."""
        self.query_one("#pm-log", LineLog).action_select()

    def _write_dialog(self, e: dict[str, Any]) -> None:
        """Enter or ``w`` on a line: write a new value to its tag (with confirmation), if the tag is writable.

        Only tags with ``writable: true`` in protocols.yaml can be written (safety rule: tags towards equipment
        are read-only until writing has been agreed and tested); the server checks this again. The dialog
        starts from the latest value in the buffer, else the value from the tag list. Two steps – value (and for
        BACnet the priority), then "Write X to «tag»? Now: Y" – so a value is never sent by a single key press.
        """
        conn, name = e["conn"], e["tag"]
        info = self.tags.get(f"{conn}/{name}") or {}
        if not info.get("writable"):
            self.scada.notify(_("{tag} is read-only").format(tag=f"{conn}/{name}"), severity="warning")
            return
        title = f"{info.get('description') or name}  ({conn}/{name})"
        current = next((x["value"] for x in reversed(self.scada.point_log)
                        if x["conn"] == conn and x["tag"] == name and not x["write"]), info.get("value"))
        old = f"{show_value(current)} {info.get('unit') or ''}".strip()
        self.app.push_screen(PointDialog(title, {**info, "value": current}, (conn, name)),
                             lambda w: confirm_point_write(self.scada, conn, name, info, w, old))
