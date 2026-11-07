# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""Monitor › <protocol>: the log entries, the menu under Monitor and a write from the monitor against a hub."""

import asyncio
import itertools
import time

import pytest

from knxscada.config import Config
from knxscada.protocols.hub import Hub
from knxscada.tui.protomon import entries_from_points, entry_from_event
from tests.test_protocols import FakeServer, Mem, until


def test_entries():
    """Value updates and gateway write events become monitor lines; events for unknown connections and plain messages
    give none.
    """
    e = entries_from_points({"plc/Room 1/T": [20.5, 100.0, "good"]})
    assert e == [{"ts": 100.0, "conn": "plc", "tag": "Room 1/T", "value": 20.5, "q": "good", "write": False}]
    w = entry_from_event({"ts": 5.0, "kind": "gateway", "text": "plc/Light 2 On/Off ← False", "user": "ola"}, {"plc"})
    assert w == {"ts": 5.0, "conn": "plc", "tag": "Light 2 On/Off", "value": "False", "q": "", "write": True,
                 "user": "ola"}
    assert entry_from_event({"text": "other/x ← 1"}, {"plc"}) is None           # not a connection we know
    assert entry_from_event({"text": "Connections and gateways changed"}, {"plc"}) is None


def test_tui_protocol_monitor(tmp_path, monkeypatch):
    """The menu lists the protocols in use under Monitor; F4 opens the first one without KNX; value changes
    appear live; the filter works; Enter on a writable tag writes after confirmation and the write is logged."""
    from textual.widgets import Button, Select

    from knxscada.tui.app import ScadaApp
    from knxscada.tui.protomon import ProtocolMonitorView
    from knxscada.tui.widgets import LineLog

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cfg = Config(path=tmp_path / "config.yaml", server={"socket": "s.sock"}, knx={"enabled": False},
                 ui={"splash": False, "language": "en"})
    app = ScadaApp(cfg)
    hub = Hub(FakeServer(tmp_path))
    hub.server._broadcast = lambda msg: app._handle(msg)
    hub.server.clients = {object()}
    ids = itertools.count(1)

    def event(level, kind, text, user=None):
        """Pass the hub's events straight to the app as the server would broadcast them."""
        app._handle({"type": "event", "event": {"id": next(ids), "ts": time.time(), "level": level, "kind": kind,
                                                 "text": text, "user": user}})

    hub.server.event = event

    class Connected(Mem):
        """A memory driver that reports itself connected and runs until stopped."""
        async def session(self):
            """Mark the connection as up and wait, like a real driver's session."""
            self.set_state(True)
            await self.wait_stopped(60)

    class Prioritised(Connected):
        """Like a BACnet client: writes take a priority and can be released."""

        priorities = True

        async def write(self, tag, value, user="", priority=None, release=False):
            """Record the write with its priority and release flag instead of sending it."""
            self.writes.append((tag.name, value, priority, release))

    bac = Prioritised("bac", {"priority": 16, "tags": {"LT": {"type": "bool", "description": "Light"}}},
                      hub, server=False)
    plc = Connected("plc", {"tags": {"T1": {"type": "float", "unit": "°C", "description": "Supply air", "writable": False},
                                     "SW": {"type": "bool", "writable": True, "description": "Pump"}}},
                    hub, server=False)

    async def fake_request(cmd, **kw):
        """Answer the protocol commands from the real hub; everything else is empty."""
        if cmd in ("protocols", "points", "point_write"):
            return await hub.command({"cmd": cmd, **kw}, "test")
        return []

    async def fake_connect():
        """Act as a server without KNX that has just said hello."""
        app._handle({"type": "hello", "state": {}, "status": {"connected": True, "connection": "x"},
                     "alarms": [], "stats": {}, "knx": False})

    monkeypatch.setattr(app.client, "request", fake_request)
    monkeypatch.setattr(app.client, "connect", fake_connect)
    monkeypatch.setattr(type(app.client), "connected", property(lambda self: True))
    monkeypatch.setattr(app.client, "close_nowait", lambda: None)

    async def run():
        """Start the hub inside the running app and drive the monitor: live values, filter, writes and priorities."""
        hub.data, hub.load = {"connections": {"plc": {"protocol": "mem"}, "bac": {"protocol": "mem"}},
                              "gateways": []}, (lambda: None)
        try:
            async with app.run_test(size=(180, 50)) as pilot:
                hub._add(plc)
                hub._add(bac)
                await hub.start()
                await until(lambda: app.protocol_status, 5)
                await pilot.pause(0.2)
                assert [str(n.label) for n in app.menu_nodes["monitor"].children] == ["mem (2)"]
                await pilot.press("f4")
                view = app.query_one("#protomon", ProtocolMonitorView)
                await until(lambda: view.display and view.protocol == "mem", 5)
                assert view.query_one("#pm-conns").row_count == 2
                log = view.query_one("#pm-log", LineLog)
                hub.update("plc", "T1", 20.5)
                hub.update("plc", "SW", False)
                await until(lambda: log.line_count == 2, 5)
                await until(lambda: "Supply air" in view._line(log.items[0]).plain, 5)   # descriptions loaded
                assert "20.5 °C" in view._line(log.items[0]).plain
                view.query_one("#pm-filter").value = "pump"
                await until(lambda: log.line_count == 1, 5)
                view._write_dialog(log.items[0])
                await pilot.pause(0.2)
                app.screen.query_one("#p-true", Button).press()                  # PointDialog: On
                await pilot.pause(0.2)
                app.screen.query_one("#yes", Button).press()                     # ConfirmDialog: Yes
                await until(lambda: ("SW", True) in plc.writes, 5)
                await until(lambda: any(e["write"] for e in log.items), 5)
                line = view._line(next(e for e in log.items if e["write"])).plain
                assert "→" in line and "test" in line                             # the write, with the user
                screen = app.screen
                view._write_dialog({"conn": "plc", "tag": "T1", "value": 1, "write": False})   # read-only tag
                await pilot.pause(0.1)
                assert app.screen is screen                                          # no dialog
                # No priorities on this connection: the dialog has no choice, and the hub refuses one.
                with pytest.raises(ValueError, match="no write priorities"):
                    await hub.write("plc", "SW", True, priority=8)
                with pytest.raises(ValueError, match="reserved"):
                    await hub.command({"cmd": "point_write", "conn": "bac", "tag": "LT", "value": True,
                                       "priority": 1}, "test")
                with pytest.raises(ValueError, match="1 to 16"):
                    await hub.write("bac", "LT", True, priority=17)
                # A BACnet-like tag: the dialog offers the priority, preset to the connection's 16.
                await until(lambda: view.tags.get("bac/LT", {}).get("priority") == 16, 5)
                view._write_dialog({"conn": "bac", "tag": "LT", "value": False, "write": False})
                await pilot.pause(0.2)
                assert app.screen.query_one("#p-priority", Select).value == 16
                offered = [v for _label, v in app.screen.query_one("#p-priority", Select)._options]
                assert 8 in offered and not {1, 2, 5} & set(offered)       # life safety is never offered
                app.screen.query_one("#p-priority", Select).value = 8
                await pilot.pause(0.1)
                assert "overrides the automatic control" in str(app.screen.query_one("#p-priority-note").render())
                app.screen.query_one("#p-true", Button).press()
                await pilot.pause(0.2)
                assert "Priority 8 (instead of 16)" in app.screen.text
                app.screen.query_one("#yes", Button).press()
                await until(lambda: ("LT", True, 8, False) in bac.writes, 5)
                # Release priority 8 again: Null into the slot, logged as "released (priority 8)".
                view._write_dialog({"conn": "bac", "tag": "LT", "value": True, "write": False})
                await pilot.pause(0.2)
                app.screen.query_one("#p-priority", Select).value = 8
                app.screen.query_one("#p-release", Button).press()
                await pilot.pause(0.2)
                assert "Release «Light» at priority 8?" in app.screen.text
                app.screen.query_one("#yes", Button).press()
                await until(lambda: ("LT", None, 8, True) in bac.writes, 5)
                assert hub.values["bac/LT"]["v"] is True             # a release does not blank the value
                view.query_one("#pm-filter").value = "released"
                await until(lambda: log.line_count == 1, 5)
                # The default priority is not sent: the server uses its own (here the connection's 16).
                await hub.command({"cmd": "point_write", "conn": "bac", "tag": "LT", "value": False}, "test")
                assert bac.writes[-1] == ("LT", False, None, False)
        finally:
            await hub.stop()

    asyncio.run(run())


def test_room_card_row_priority_write():
    """``p`` on a room card row of a BACnet tag (project.point_rooms) opens the write dialog with the tag's default
    priority; Release at another priority is sent as point_write with priority and release after the confirmation.
    A tag without a priority (not BACnet) only gets a note, and the switch itself still writes the plain way."""
    from textual.app import App
    from textual.widgets import Button, Select

    from knxscada.tui.gateways import PointDialog
    from knxscada.tui.widgets import SwitchRow

    sent: list[tuple[str, dict]] = []

    class Client:
        """Records the requests the confirmation sends."""

        async def request(self, cmd, **kw):
            """Record the request instead of sending it to a server."""
            sent.append((cmd, kw))

    class Stub(App):
        """Just enough of ScadaApp for one card row: state, records, watchers and the client."""

        def __init__(self, records):
            """*records*: the pt: records as the model has them (with the priority from the server's points)."""
            super().__init__()
            self.records, self.client, self.notes = records, Client(), []
            self.state = {ga: {"value": False, "text": "Off", "ts": 0} for ga in records}

        def ga(self, addr):
            """The pt: record of *addr* (empty when unknown)."""
            return self.records.get(addr or "", {})

        def watch_ga(self, ga, widget):
            """Card rows register here; nothing to do in the test."""

        def unwatch_ga(self, ga, widget):
            """See watch_ga."""

        def notify(self, message, **kw):
            """Keep notifications for the asserts."""
            self.notes.append(message)

        def compose(self):
            """Two rows: a BACnet light and a tag without priority."""
            yield SwitchRow({"label": "Light", "ga": "pt:bac/LT", "status": None, "dpt": "1.001", "kind": "switch"})
            yield SwitchRow({"label": "Pump", "ga": "pt:plc/SW", "status": None, "dpt": "1.001", "kind": "switch"})

    records = {"pt:bac/LT": {"name": "Light", "dpt": "1.001", "writable": True, "priority": 16, "point": "bac/LT"},
               "pt:plc/SW": {"name": "Pump", "dpt": "1.001", "writable": True, "point": "plc/SW"}}

    async def run():
        """Press p on both rows; release priority 8 on the BACnet one."""
        app = Stub(records)
        async with app.run_test(size=(100, 30)) as pilot:
            light, pump = app.query(SwitchRow)
            pump.action_priority_write()
            await pilot.pause(0.1)
            assert not isinstance(app.screen, PointDialog) and "only for BACnet" in app.notes[-1]
            light.query_one("Switch").focus()
            await pilot.press("p")
            await pilot.pause(0.2)
            assert isinstance(app.screen, PointDialog)
            select = app.screen.query_one("#p-priority", Select)
            assert select.value == 16
            select.value = 8
            app.screen.query_one("#p-release", Button).press()
            await pilot.pause(0.2)
            app.screen.query_one("#yes", Button).press()                           # ConfirmDialog: Yes
            await pilot.pause(0.3)
        return sent

    # The dialog first reads the priority array (a read), then the confirmed release is sent.
    assert asyncio.run(run()) == [("point_priorities", {"conn": "bac", "tag": "LT"}),
                                  ("point_write", {"conn": "bac", "tag": "LT", "value": None, "priority": 8,
                                                   "release": True})]


def test_hub_counts_reads_changes_errors_and_drops(tmp_path):
    """Every value a driver reports is a read; only a different value is a change; bad quality is an error and a
    lost connection a drop. The counters are in the connection status the Monitor shows."""
    hub = Hub(FakeServer(tmp_path))
    hub.data = {"connections": {"plc": {"protocol": "mem"}}, "gateways": []}
    plc = Mem("plc", {"tags": {"T": {"type": "float"}}}, hub, server=False)
    hub.drivers["plc"] = plc
    for value in (20.0, 20.0, 21.0):
        hub.update("plc", "T", value)
    hub.update("plc", "T", 21.0, "bad")
    hub.connection_state("plc", False)
    st = next(s for s in hub.status() if s["name"] == "plc")
    assert (st["reads"], st["changes"], st["errors"], st["drops"]) == (4, 3, 1, 1)


def test_monitor_hides_fast_tags(tmp_path, monkeypatch):
    """f hides the tags that changed more than FAST_PER_MIN times in the last minute (a PLC clock) and shows the rest;
    writes are never hidden; f again shows everything."""
    from knxscada.tui.app import ScadaApp
    from knxscada.tui.protomon import ProtocolMonitorView
    from knxscada.tui.widgets import LineLog

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cfg = Config(path=tmp_path / "config.yaml", server={"socket": "s.sock"}, knx={"enabled": False},
                 ui={"splash": False, "language": "en"})
    app = ScadaApp(cfg)

    async def run():
        """Fill the app's buffer with a fast clock tag, a slow temperature and one write, then toggle the filter."""
        async with app.run_test(size=(180, 50)) as pilot:
            app.conn_protocol = {"plc": "mem"}
            now = time.time()
            app.point_log.extend({"ts": now - 50 + i, "conn": "plc", "tag": "Act_Time", "value": i, "q": "good",
                                  "write": False} for i in range(40))
            app.point_log.extend({"ts": now - 30 + i * 10, "conn": "plc", "tag": "Temp", "value": 20 + i, "q": "good",
                                  "write": False} for i in range(3))
            app.point_log.append({"ts": now, "conn": "plc", "tag": "Act_Time", "value": "0", "q": "", "write": True,
                                  "user": "ola"})
            view = app.query_one("#protomon", ProtocolMonitorView)
            view.display = True
            view.select("mem")
            await pilot.pause(0.1)
            log = view.query_one("#pm-log", LineLog)
            assert log.line_count == 44
            view.action_fast()
            await pilot.pause(0.1)
            assert view._fast == {"plc/Act_Time"} and log.line_count == 4     # 3 temperatures + the write
            assert "1 fast tags hidden" in str(view.query_one("#pm-state").render())
            view.action_fast()
            await pilot.pause(0.1)
            assert log.line_count == 44

    asyncio.run(run())
