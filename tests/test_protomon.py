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
