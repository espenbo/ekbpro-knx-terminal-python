# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""Monitor › <protocol>: the log entries, the menu under Monitor and a write from the monitor against a hub."""

import asyncio
import itertools
import time

from knxscada.config import Config
from knxscada.protocols.hub import Hub
from knxscada.tui.protomon import entries_from_points, entry_from_event
from tests.test_protocols import FakeServer, Mem, until


def test_entries():
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
    from textual.widgets import Button

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
        app._handle({"type": "event", "event": {"id": next(ids), "ts": time.time(), "level": level, "kind": kind,
                                                 "text": text, "user": user}})

    hub.server.event = event

    class Connected(Mem):
        async def session(self):
            self.set_state(True)
            await self.wait_stopped(60)

    plc = Connected("plc", {"tags": {"T1": {"type": "float", "unit": "°C", "description": "Supply air", "writable": False},
                                     "SW": {"type": "bool", "writable": True, "description": "Pump"}}},
                    hub, server=False)

    async def fake_request(cmd, **kw):
        if cmd in ("protocols", "points", "point_write"):
            return await hub.command({"cmd": cmd, **kw}, "test")
        return []

    async def fake_connect():
        app._handle({"type": "hello", "state": {}, "status": {"connected": True, "connection": "x"},
                     "alarms": [], "stats": {}, "knx": False})

    monkeypatch.setattr(app.client, "request", fake_request)
    monkeypatch.setattr(app.client, "connect", fake_connect)
    monkeypatch.setattr(type(app.client), "connected", property(lambda self: True))
    monkeypatch.setattr(app.client, "close_nowait", lambda: None)

    async def run():
        hub.data, hub.load = {"connections": {"plc": {"protocol": "mem"}}, "gateways": []}, (lambda: None)
        try:
            async with app.run_test(size=(180, 50)) as pilot:
                hub._add(plc)
                await hub.start()
                await until(lambda: app.protocol_status, 5)
                await pilot.pause(0.2)
                assert [str(n.label) for n in app.menu_nodes["monitor"].children] == ["mem (1)"]
                await pilot.press("f4")
                view = app.query_one("#protomon", ProtocolMonitorView)
                await until(lambda: view.display and view.protocol == "mem", 5)
                assert view.query_one("#pm-conns").row_count == 1
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
        finally:
            await hub.stop()

    asyncio.run(run())
