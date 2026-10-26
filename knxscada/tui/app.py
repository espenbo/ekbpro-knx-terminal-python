# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""The Textual application: menu, top bar, key bar, navigation and message handling.

Layout::

    ┌ TopBar: title · KNX status · telegrams/s · alarms · user · clock ────────┐
    │ Tree menu │ ContentSwitcher with one View per menu entry                │
    └ KeyBar: F-keys and hints for the current view ───────────────────────────┘

The app is a client of the SCADA server (see :mod:`knxscada.client`). Every
message from the server goes through :meth:`ScadaApp._handle`, which updates
the local copy of the state and notifies the widgets:

* widgets that show a group address register with :meth:`watch_ga` and get
  ``on_state()`` when that address changes;
* every view gets ``on_telegram()`` for every telegram.

Important Textual details (learned the hard way):

* ``App.query_one()`` searches the *active* screen. While a dialog, the
  splash or the screensaver is on top, our widgets live on the bottom
  screen – always use :attr:`main` to reach them.
* Do not use attribute names Textual uses internally (``_closing`` once made
  the app ignore its own exit request).
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from collections import deque
from typing import Any

from rich.text import Text
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.color import Color
from textual.containers import Horizontal
from textual.css.query import NoMatches
from textual.widgets import ContentSwitcher, Tree

from .. import PRODUCT
from .. import dpt as dptlib
from ..client import ScadaClient
from ..config import Config
from ..i18n import N_, _, get_language
from ..prefs import save_prefs, save_site_theme, site_theme
from ..project import load_project
from .dialogs import ConfirmDialog, GADialog, LanguageDialog, ThemeDialog, ValueDialog
from .home import HomeModel
from .plan_view import PlanView
from .screensaver import DAYS, ScreenSaver
from .splash import AboutDialog, SplashScreen
from .themes import THEMES, resolve_theme
from .views import AlarmView, EventView, GroupAddressView, MonitorView, OverviewView, RoomView, TrendView, View
from .widgets import LineLog, LiveStatic

#: Menu entries: (id, icon, English label, key). Order = order in the menu.
MENU = [
    ("overview", "▣", N_("Overview"), "f1"),
    ("rooms", "⌂", N_("Floors and rooms"), "f2"),
    ("gas", "☰", N_("Group addresses"), "f3"),
    ("monitor", "⇄", N_("Bus monitor"), "f4"),
    ("alarms", "⚠", N_("Alarms"), "f5"),
    ("events", "✎", N_("Event log"), "f6"),
    ("trends", "∿", N_("Trends"), "f7"),
    ("plan", "▦", N_("Floor plan"), "f8"),
    ("theme", "◐", N_("Colour theme"), "ctrl+t"),
    ("language", "⚑", N_("Language"), "ctrl+l"),
    ("about", "?", N_("About"), "f12"),
    ("quit", "✕", N_("Quit"), "f10"),
]

#: Menu entries that open a dialog or act immediately instead of showing a view.
ACTIONS = ("quit", "theme", "language", "about")

#: Short labels for the key bar at the bottom (the full labels do not fit).
KEYBAR_LABELS = {
    "overview": N_("Overview"), "rooms": N_("Rooms"), "gas": N_("Addresses"), "monitor": N_("Monitor"),
    "alarms": N_("Alarms"), "events": N_("Log"), "trends": N_("Trends"), "plan": N_("Plan"),
    "theme": N_("Colours"), "language": N_("Lang."), "about": N_("About"), "quit": N_("Quit"),
}

#: Key hints shown on the right of the key bar for the current view.
VIEW_HINTS = {
    "overview": N_("Enter: open/switch off"),
    "rooms": N_("Tab: next control  Space: operate"),
    "gas": N_("/: search  u: show unused  Enter: read/write/trend"),
    "monitor": N_("/: filter  p: pause  c: clear  Enter: open"),
    "alarms": N_("k: acknowledge  K: acknowledge all"),
    "events": N_("/: filter"),
    "trends": N_("/: search  Enter: add/remove  Del: remove all"),
    "plan": N_("←→/PgUp/PgDn/1–9: floor  ↑↓: room  Enter: open"),
}


def key_label(key: str) -> str:
    """``"ctrl+t"`` → ``"^T"``, ``"f1"`` → ``"F1"``."""
    return key.upper().replace("CTRL+", "^")


class TopBar(LiveStatic):
    """One-line status bar at the top."""


class KeyBar(LiveStatic):
    """Lightweight replacement for Textual's Footer (which rebuilds itself on every focus change)."""


class ScadaApp(App):
    CSS_PATH = "app.tcss"
    TITLE = PRODUCT
    BINDINGS = [
        # Ctrl bindings get priority so they work even while an Input has focus.
        *[Binding(key, f"menu('{vid}')", _(label), key_display=key_label(key), priority=key.startswith("ctrl"))
          for vid, _icon, label, key in MENU],
        Binding("f9", "screensaver", _("Screensaver")),
        Binding("ctrl+b", "toggle_menu", _("Menu"), show=False),
        # Ctrl+Q is flow control (XON) in many terminals, so Ctrl+C and F10 quit as well.
        Binding("ctrl+q", "quit", _("Quit"), show=False, priority=True),
        Binding("ctrl+c", "quit", _("Quit"), show=False, priority=True),
    ]

    def __init__(self, cfg: Config) -> None:
        super().__init__()
        self.cfg = cfg
        self.ui: dict[str, Any] = cfg.ui
        self.model = load_project(cfg)
        self.title = self.ui.get("title") or f"{PRODUCT} – {self.model['info'].get('name', '')}"
        # Local copy of the server state (filled by the "hello" message).
        self.state: dict[str, dict] = {}
        self.status: dict[str, Any] = {"connected": False, "connection": N_("no server")}
        self.stats: dict[str, Any] = {}
        self.alarms: list[dict] = []
        self.events: deque[dict] = deque(maxlen=3000)
        self.telegrams: deque[dict] = deque(maxlen=6000)  # buffer for monitor/activity
        self.demo = False
        self._watchers: dict[str, set] = {}  # GA → widgets that want on_state()
        self._known_alarms: set[int] = set()
        self.client = ScadaClient(cfg.socket_path, self._on_server_message)
        self.server_ok = False
        self._quitting = False  # NOT "_closing": that name is used by Textual itself
        self.home = HomeModel(self.model, self.ui)
        self.history: dict[str, list[tuple[float, float]]] = {}  # 24 h history for overview charts
        self.traffic: list[int] = []  # telegrams per minute, last hour
        self.ascii = bool(self.ui.get("ascii", False))
        self.big_digits = bool(self.ui.get("big_digits", False))
        self._last_input = time.time()  # for the screensaver
        self._current_room: str | None = None
        self._views: list[View] = []  # all views, cached: telegrams are dispatched to them very often
        self.restart_requested = False  # set when the language changes (see run())

    # -- construction ----------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield TopBar(id="topbar")
        with Horizontal(id="body"):
            yield Tree(_("Menu"), id="menu")
            with ContentSwitcher(id="views", initial="overview"):
                yield OverviewView(id="overview")
                yield RoomView(id="rooms")
                yield GroupAddressView(id="gas")
                yield MonitorView(id="monitor")
                yield AlarmView(id="alarms")
                yield EventView(id="events")
                yield TrendView(id="trends")
                yield PlanView(id="plan")
        yield KeyBar(id="keybar")

    def on_mount(self) -> None:
        for theme in THEMES:
            self.register_theme(theme)
        # The user's own choice for this installation (Ctrl+T) wins over the configured default.
        theme = resolve_theme(site_theme(self.cfg.path, self.ui.get("theme"))) or "retro"
        try:
            self.theme = theme
        except Exception:  # noqa: BLE001 - unknown theme name
            self.theme = "retro"
        self.theme_changed_signal.subscribe(self, self._on_theme_changed)
        self._views = list(self.main.query(View))
        self._build_menu()
        self._update_keybar("overview")
        if self.big_digits:
            self.main.query_one("#tiles").add_class("big")
        self.set_interval(1, self._update_topbar)
        self.set_interval(5, self._check_idle)
        self.set_interval(30, self._load_traffic)
        self._update_topbar()
        self.connect()
        if self.ui.get("splash", True):
            self.push_screen(SplashScreen())

    def _build_menu(self) -> None:
        """Fill the menu tree: views, and floors → rooms under "Floors and rooms"."""
        tree = self.main.query_one("#menu", Tree)
        tree.show_root = False
        tree.guide_depth = 2
        root = tree.root
        self.menu_nodes = {}
        for vid, icon, label, _key in MENU:
            text = f"{icon} {_(label)}"
            if vid == "rooms":
                node = root.add(text, data=("view", "rooms"), expand=True)
                for floor in self.model["floors"]:
                    fnode = node.add(_(floor["name"]), data=("floor", floor), expand=False)
                    for room in floor["rooms"]:
                        fnode.add_leaf(_(room["name"]), data=("room", room, floor))
            else:
                node = root.add_leaf(text, data=("view", vid))
            self.menu_nodes[vid] = node
        tree.focus()

    # -- connection to the server -------------------------------------------------------------
    @work(exclusive=True, group="connect")
    async def connect(self) -> None:
        """Connect to the server, retrying with back-off until it is available."""
        delay = 1.0
        while True:
            try:
                await self.client.connect()
                self.server_ok = True
                self._load_history()
                return
            except (FileNotFoundError, ConnectionRefusedError, OSError):
                self.server_ok = False
                self.status = {"connected": False, "connection": N_("no server")}
                self._update_topbar()
                await asyncio.sleep(delay)
                delay = min(delay * 2, 10)

    @work(group="history")
    async def _load_history(self) -> None:
        """After connecting: fetch recent telegrams, events, 24 h history and traffic."""
        tels = await self.request("telegrams", limit=3000)
        if tels:
            ids = {t.get("id") for t in self.telegrams}
            old = [t for t in tels if t.get("id") not in ids]
            for t in old:
                t["text"] = self._fmt_tel(t)  # stored telegrams have no display text
            self.telegrams = deque(old + list(self.telegrams), maxlen=6000)
        evs = await self.request("events", limit=1000)
        if evs:
            # Merge with events that arrived live in the meantime, ordered by id.
            merged = {e["id"]: e for e in [*evs, *self.events]}
            self.events = deque(sorted(merged.values(), key=lambda e: e["id"]), maxlen=3000)
        current = self.main.query_one("#views", ContentSwitcher).current
        if current in ("monitor", "events"):
            self.main.query_one(f"#{current}", View).on_show_view()
        self.main.query_one("#overview", OverviewView).load_activity()
        since = time.time() - 86400
        for ga in self.home.trend_gas():
            pts = await self.request("history", ga=ga, hours=24)
            live = [p for p in self.history.get(ga, []) if p[0] >= since]
            self.history[ga] = sorted({p[0]: p for p in [*(tuple(x) for x in pts or []), *live]}.values())
        self.main.query_one("#overview", OverviewView).on_state()
        await self._fetch_traffic()
        self.main.query_one("#overview", OverviewView).replot()

    @work(exclusive=True, group="traffic")
    async def _load_traffic(self) -> None:
        await self._fetch_traffic()

    async def _fetch_traffic(self) -> None:
        if not self.client.connected:
            return
        try:
            self.traffic = await self.client.request("traffic", minutes=60) or []
        except Exception:  # noqa: BLE001 - an older server without the traffic command
            self.traffic = []

    def _fmt_tel(self, t: dict) -> str:
        if t["type"] == "read":
            return ""
        return dptlib.fmt(self.ga(t["dst"]).get("dpt"), t.get("value"))

    def _on_server_message(self, msg: dict) -> None:
        """Callback from the client for every pushed message."""
        if self._quitting:
            return
        try:
            self._handle(msg)
        except NoMatches:
            pass  # widgets not there yet (start-up) or already gone (shutdown)

    def action_quit(self) -> None:
        """Quit synchronously: an async quit that awaited the client made the app hang."""
        self._quitting = True
        self.client.close_nowait()
        self.exit()

    def _handle(self, msg: dict) -> None:
        kind = msg.get("type")
        if kind == "hello":
            self.state = msg["state"]
            self.status = msg["status"]
            self.stats = msg.get("stats", {})
            self.demo = msg.get("demo", False)
            self._set_alarms(msg["alarms"], initial=True)
            for ga in list(self._watchers):
                self._notify_watchers(ga)
            self.main.query_one("#gas", GroupAddressView).rebuild()
        elif kind == "telegram":
            t = msg["t"]
            self.telegrams.append(t)
            if "state" in msg:
                self.state[t["dst"]] = msg["state"]
                v = t.get("value")
                if t["dst"] in self.history and isinstance(v, (int, float)):
                    hist = self.history[t["dst"]]
                    hist.append((t["ts"], float(v)))
                    if len(hist) > 5000:
                        del hist[: len(hist) - 4000]
                self._notify_watchers(t["dst"])
            for view in self._views:
                view.on_telegram(t)
        elif kind == "status":
            self.status = msg["status"]
            self._update_topbar()
        elif kind == "stats":
            self.stats = msg["stats"]
        elif kind == "alarms":
            self._set_alarms(msg["alarms"])
        elif kind == "event":
            ev = msg["event"]
            self.events.append(ev)
            self.main.query_one("#events", EventView).add_event(ev)
        elif kind == "disconnected":
            self.server_ok = False
            self.status = {"connected": False, "connection": N_("server lost")}
            self._update_topbar()
            self.notify(_("Lost the connection to the SCADA server – reconnecting …"), severity="error")
            self.connect()

    def _set_alarms(self, alarms: list[dict], initial: bool = False) -> None:
        """New alarm list from the server: notify about new ones, update menu and views."""
        self.alarms = alarms
        for a in alarms:
            if a["id"] not in self._known_alarms:
                self._known_alarms.add(a["id"])
                if not initial and a["cleared_ts"] is None:
                    self.notify(a["text"], title=_("ALARM – {severity}").format(severity=_(a["severity"])),
                                severity="error", timeout=15)
                    self.bell()
        self.main.query_one("#alarms", AlarmView).refresh_alarms()
        self.main.query_one("#overview", OverviewView).on_alarms()
        unacked = sum(1 for a in alarms if a["acked_ts"] is None)
        node = self.menu_nodes.get("alarms")
        if node is not None:
            label = f"⚠ {_('Alarms')}"
            node.set_label(Text(f"{label} ({unacked})", style="bold red") if unacked else Text(label))
        self._update_topbar()

    @property
    def main(self):
        """The base screen – our views live there even when a dialog or the screensaver is on top."""
        if not self.screen_stack:
            raise NoMatches("no screen")
        return self.screen_stack[0]

    # -- state / watchers -----------------------------------------------------------------
    def watch_ga(self, ga: str, widget) -> None:
        self._watchers.setdefault(ga, set()).add(widget)

    def unwatch_ga(self, ga: str, widget) -> None:
        self._watchers.get(ga, set()).discard(widget)

    def _notify_watchers(self, ga: str) -> None:
        for w in list(self._watchers.get(ga, ())):
            if w.is_mounted:
                w.on_state()

    def ga(self, addr: str | None) -> dict:
        """Group address record from the model (empty dict if unknown)."""
        return self.model["gas"].get(addr or "", {})

    # -- commands to the server ----------------------------------------------------------------
    async def request(self, cmd: str, **kw: Any) -> Any:
        """Send a request; errors are shown as a notification and None is returned."""
        try:
            return await self.client.request(cmd, **kw)
        except Exception as exc:  # noqa: BLE001
            self.notify(f"{cmd}: {exc}", severity="error")
            return None

    def run_request(self, cmd: str, **kw: Any) -> None:
        """Fire-and-forget request from synchronous code."""
        self.run_worker(self.request(cmd, **kw), group="cmd")

    def write(self, ga: str | None, value: Any, dpt: str | None = None) -> None:
        """Write a value to the bus (via the server, which also logs who did it)."""
        if not ga:
            return
        self.run_request("write", ga=ga, value=value, dpt=dpt)

    def open_ga(self, addr: str) -> None:
        """Open the group address dialog and perform the action chosen in it."""
        ga = self.ga(addr)
        if not ga:
            return

        def done(action: str | None) -> None:
            if action == "read":
                self.run_request("read", ga=addr)
            elif action == "trend":
                self.action_menu("trends")
                self.main.query_one("#trends", TrendView).add_series(addr)
            elif action == "write":
                self.push_screen(
                    ValueDialog(_("Write to {address} {name}").format(address=addr, name=ga["name"]),
                                ga["dpt"], self.state.get(addr, {}).get("value")),
                    lambda v: self._confirm_write(addr, ga, v),
                )

        self.push_screen(GADialog(ga, self.state.get(addr)), done)

    def _confirm_write(self, addr: str, ga: dict, value: Any) -> None:
        """Raw writes from the address view are confirmed first (``ui.confirm_raw_write``)."""
        if value is None:
            return
        if not self.ui.get("confirm_raw_write", True):
            self.write(addr, value, ga["dpt"])
            return
        self.push_screen(
            ConfirmDialog(_("Send the value «{value}» to {address} {name}?").format(value=value, address=addr, name=ga["name"])),
            lambda ok: ok and self.write(addr, value, ga["dpt"]),
        )

    # -- navigation ---------------------------------------------------------------------
    def action_menu(self, vid: str) -> None:
        """F-key or menu: show a view, or run one of the menu actions."""
        if vid == "quit":
            self.action_quit()
            return
        if vid == "theme":
            self.action_pick_theme()
            return
        if vid == "language":
            self.action_pick_language()
            return
        if vid == "about":
            self.push_screen(AboutDialog())
            return
        if vid == "rooms":
            # F2: open the first room of the first floor.
            node = self.menu_nodes["rooms"]
            first = next((c for c in node.children), None)
            if first is not None:
                first.expand()
                if first.children:
                    self._select(first.children[0].data)
                    tree = self.main.query_one("#menu", Tree)
                    self.call_after_refresh(tree.move_cursor, first.children[0])
            return
        self._show(vid)
        tree = self.main.query_one("#menu", Tree)
        self.call_after_refresh(tree.move_cursor, self.menu_nodes[vid])
        self.call_after_refresh(self._focus_view, vid)

    def _focus_view(self, vid: str) -> None:
        """Move focus into the view so its key bindings work immediately."""
        view = self.main.query_one(f"#{vid}", View)
        # The bus monitor's telegram list before its filter, so p/c/e work at once (/ goes to the filter).
        target = next((w for w in view.query("LineLog") if w.focusable), None)
        target = target or next((w for w in view.query("DataTable, PlotextPlot, Input") if w.focusable), None)
        if target is not None:
            target.focus()

    def action_pick_theme(self) -> None:
        def done(name: str | None) -> None:
            if name:
                save_site_theme(self.cfg.path, name)
                self.notify(_("Colour theme: {name} (saved for {user})").format(name=name, user=self.client.user.split("@")[0]))

        self.push_screen(ThemeDialog(self.theme), done)

    def action_pick_language(self) -> None:
        """Choose the UI language; it is saved per user and the UI restarts in it."""

        def done(code: str | None) -> None:
            if not code:
                return
            save_prefs(language=code)
            if code != get_language():
                self.restart_requested = True
                self.action_quit()

        self.push_screen(LanguageDialog(), done)

    def _on_theme_changed(self, theme) -> None:
        """Rich Text colours are baked into cached lines and cells – re-render them."""
        for log in self.main.query(LineLog):
            log.refresh_lines()
        for view in self._views:
            view.on_theme_changed()

    def action_toggle_menu(self) -> None:
        menu = self.main.query_one("#menu", Tree)
        menu.display = not menu.display

    def _update_keybar(self, vid: str) -> None:
        t = Text()
        for v, _icon, label, key in MENU:
            t.append(f" {key_label(key)} ", style="bold reverse" if v == vid else "bold")
            t.append(_(KEYBAR_LABELS.get(v, label)) + " ", style="" if v == vid else "dim")
        t.append(" F9 ", style="bold")
        t.append(_("Saver") + " ", style="dim")
        hint = VIEW_HINTS.get(vid)
        if hint:
            t.append("  │  " + _(hint), style="italic")
        self.main.query_one("#keybar", KeyBar).set(t)

    def _show(self, vid: str) -> None:
        self._update_keybar(vid)
        switcher = self.main.query_one("#views", ContentSwitcher)
        if switcher.current == vid and vid != "rooms":
            return  # already visible – avoid a double rebuild (F-key + menu cursor)
        switcher.current = vid
        self.main.query_one(f"#{vid}", View).on_show_view()

    @on(Tree.NodeSelected, "#menu")
    def _menu_selected(self, event: Tree.NodeSelected) -> None:
        self._select(event.node.data)

    @on(Tree.NodeHighlighted, "#menu")
    def _menu_highlighted(self, event: Tree.NodeHighlighted) -> None:
        # Moving the cursor over views and rooms shows them directly (no Enter needed),
        # but actions (quit, theme, about) require Enter.
        data = event.node.data
        if data and data[0] in ("room", "view") and data[1] not in ("rooms", *ACTIONS):
            self._select(data)

    def _select(self, data: tuple | None) -> None:
        if not data:
            return
        if data[0] == "view" and data[1] in ACTIONS:
            self.action_menu(data[1])
        elif data[0] == "view":
            if data[1] != "rooms":
                self._show(data[1])
        elif data[0] == "room":
            _kind, room, floor = data
            self._show_room(room, floor)
        elif data[0] == "floor":
            floor = data[1]
            funcs = [self.model["functions"][fid] for r in floor["rooms"] for fid in r["functions"]]
            self._show("rooms")
            title = _("{floor} – all rooms").format(floor=_(floor["name"]))
            self.run_worker(self.main.query_one("#rooms", RoomView).show(title, funcs, show_room=True),
                            exclusive=True, group="room")

    def _show_room(self, room: dict, floor: dict) -> None:
        if self._current_room == room["id"] and self.main.query_one("#views", ContentSwitcher).current == "rooms":
            return
        self._current_room = room["id"]
        funcs = [self.model["functions"][fid] for fid in room["functions"]]
        self._show("rooms")
        self.run_worker(self.main.query_one("#rooms", RoomView).show(f"{_(floor['name'])}  ›  {_(room['name'])}", funcs),
                        exclusive=True, group="room")

    def show_room_id(self, room_id: str) -> None:
        for floor in self.model["floors"]:
            for room in floor["rooms"]:
                if room["id"] == room_id:
                    self._show_room(room, floor)
                    return

    def color(self, name: str) -> str:
        """A colour of the active theme as a string for Rich styles.

        ``"muted"`` is a derived colour halfway between background and
        foreground, used for de-emphasised text.
        """
        theme = self.current_theme
        if name == "muted":
            fg = Color.parse(theme.foreground or "#cccccc")
            bg = Color.parse(theme.background or "#000000")
            return bg.blend(fg, 0.5).hex
        return getattr(theme, name, None) or theme.foreground or "white"

    # -- screensaver -----------------------------------------------------------------------
    async def on_event(self, event) -> None:
        # Every key press, click or scroll counts as activity.
        if isinstance(event, (events.Key, events.MouseDown, events.MouseScrollDown, events.MouseScrollUp)):
            self._last_input = time.time()
        await super().on_event(event)

    def _check_idle(self) -> None:
        minutes = float(self.ui.get("screensaver_minutes", 5) or 0)
        if minutes <= 0 or len(self.screen_stack) > 1:  # disabled, or a dialog is open
            return
        if time.time() - self._last_input > minutes * 60:
            self.action_screensaver()

    def action_screensaver(self) -> None:
        if not isinstance(self.screen, ScreenSaver):
            self.push_screen(ScreenSaver(), lambda _result: setattr(self, "_last_input", time.time()))

    def show_function_room(self, fid: str) -> None:
        for floor in self.model["floors"]:
            for room in floor["rooms"]:
                if fid in room["functions"]:
                    self._show_room(room, floor)
                    return

    # -- top bar --------------------------------------------------------------------------
    def _update_topbar(self) -> None:
        try:
            bar = self.main.query_one("#topbar", TopBar)
        except NoMatches:
            return
        st = self.status
        t = Text()
        t.append(f" {self.title} ", style="bold reverse")
        if self.demo:
            t.append(" ")
            t.append(" DEMO ", style="bold black on yellow")
        t.append("  ")
        ifaces = st.get("interfaces") or []
        if len(ifaces) > 1:  # several KNX IP interfaces: one dot each
            t.append("KNX", style="bold green" if all(i["connected"] for i in ifaces) else "bold red")
            for i in ifaces:
                t.append(f" ●{i['name']}", style="green" if i["connected"] else "bold red")
        elif st.get("connected"):
            t.append("● KNX  ", style="bold green")
            t.append(_(str(st.get("connection", ""))), style="dim")
        else:
            t.append("● KNX  ", style="bold red")
            t.append(_(str(st.get("connection", "disconnected"))), style="bold red")
        plcs = st.get("plcs") or []
        if len(plcs) > 1:  # several PLCs: one dot each
            t.append("   PLC", style="bold green" if all(p["connected"] for p in plcs) else "bold red")
            for p in plcs:
                t.append(f" ●{p['name']}", style="green" if p["connected"] else "bold red")
        elif "plc" in st:  # OPC UA bridge to a PLC is configured
            plc_ok = st["plc"] == "connected"
            t.append("   ● PLC", style="bold green" if plc_ok else "bold red")
            if not plc_ok:
                t.append(" " + _(str(st["plc"])), style="bold red")
        t.append(f"   {self.stats.get('per_min', 0) / 60:.1f} " + _("tlg/s"), style="dim")
        unacked = sum(1 for a in self.alarms if a["acked_ts"] is None)
        if unacked:
            blink = int(time.time()) % 2 == 0
            t.append(f"   ⚠ {unacked} " + _("ALARM") + " ", style="bold white on red" if blink else "bold red")
        t.append(f"   {self.client.user}", style="dim")
        now = time.localtime()
        t.append(f"   {_(DAYS[now.tm_wday])[:3]} {time.strftime('%d.%m.%Y %H:%M:%S', now)} ", style="bold")
        bar.set(t)


#: Reset attributes, clear the screen and the scrollback, cursor home.
CLEAR_SCREEN = "\x1b[0m\x1b[2J\x1b[3J\x1b[H"


#: Set when the TUI restarts itself after a language change (skips the splash screen).
RESTART_ENV = "KNXSCADA_RESTARTED"


def run(cfg: Config) -> None:
    """Run the TUI and leave a clean terminal behind, however it ends.

    After a language change the process replaces itself with a fresh
    ``python -m knxscada tui``: every text, including the ones Textual reads
    when the classes are defined, is then created in the new language.
    """
    if os.environ.pop(RESTART_ENV, None):
        cfg.ui["splash"] = False
    app = ScadaApp(cfg)
    try:
        app.run()
    finally:
        sys.stdout.write(CLEAR_SCREEN)
        sys.stdout.flush()
    if app.restart_requested:
        os.environ[RESTART_ENV] = "1"
        # PYTHONSAFEPATH: do not put the current directory on sys.path (Python 3.11+), so a module
        # planted in the directory the TUI was started from can never be imported.
        os.environ["PYTHONSAFEPATH"] = "1"
        os.execv(sys.executable, [sys.executable, "-m", "knxscada", "-c", str(cfg.path), "tui"])
