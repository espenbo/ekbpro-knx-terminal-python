# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""Starting the server or the TUI must not switch anything on the bus.

Background: reading a *control* address at start-up was answered by another
device with a stale "off", and the actuator switched the light off.
"""

import asyncio

import pytest

from knxscada import project
from knxscada.server import read_mode, read_targets
from tests.test_project import raw_project


@pytest.mark.parametrize(("value", "mode"), [(None, "off"), (False, "off"), ("false", "off"), ("off", "off"),
                                              (True, "status"), ("status", "status"), ("all", "all"), ("ALL", "all")])
def test_read_mode(value, mode):
    assert read_mode(value) == mode


def model_and_gas():
    model = project.build_model(raw_project())
    return model, model["gas"]


def test_default_reads_nothing():
    model, gas = model_and_gas()
    assert read_targets(model, gas, [], read_mode(None)) == []


def test_status_mode_never_reads_control_addresses():
    model, gas = model_and_gas()
    controls = {d["ga"] for f in model["functions"].values() for d in f["datapoints"] if d["ga"]}
    targets = read_targets(model, gas, ["3/0/4", "1/1/1"], "status")
    assert "1/1/2" in targets              # switch feedback
    assert "3/0/1" in targets              # room temperature (sensor)
    assert "3/0/4" in targets              # alarm/status address
    assert not controls & set(targets)     # 1/1/1 is a control address: never read, not even for an alarm rule


def test_all_mode_includes_control_addresses():
    model, gas = model_and_gas()
    targets = read_targets(model, gas, [], "all")
    assert "1/1/2" in targets
    assert "3/0/2" in targets              # setpoint (control, readable)


def test_unreadable_addresses_are_never_read():
    model, gas = model_and_gas()
    assert "1/1/1" not in read_targets(model, gas, [], "all")   # switch input has no R flag


def test_tui_start_sends_no_write_or_read(tmp_path, monkeypatch):
    """Connecting the TUI must only fetch data from the server – no bus telegrams."""
    import json

    from knxscada.config import Config
    from knxscada.tui.app import ScadaApp

    model = project.build_model(raw_project())
    (tmp_path / "cache.json").write_text(json.dumps(model), encoding="utf-8")
    cfg = Config(path=tmp_path / "config.yaml", project={"file": "none.knxproj", "cache": "cache.json"},
                 ui={"splash": False, "language": "en"})
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    sent: list[str] = []
    state = {ga: {"value": v, "text": str(v), "raw": None, "ts": 1.0, "src": "1.1.1"}
             for ga, v in {"1/1/2": True, "3/0/1": 21.0, "3/0/2": 21.0, "3/0/3": "COMFORT", "3/0/4": False}.items()}

    async def fake_request(cmd, **kw):
        sent.append(cmd)
        return [] if cmd != "hello" else None

    async def fake_connect():
        app.client._writer = object()  # pretend to be connected
        app._handle({"type": "hello", "state": state, "status": {"connected": True, "connection": "connected"},
                     "alarms": [], "stats": {}})

    async def run():
        async with app.run_test(size=(160, 50)) as pilot:
            await pilot.pause(1)
            for view in ("rooms", "overview", "gas", "trends", "plan"):
                app.action_menu(view)
                await pilot.pause(0.3)
            # Status changes arriving while the room is shown (as when another device
            # answers a read) update the controls, but must never be written back.
            app.action_menu("rooms")
            await pilot.pause(0.5)
            for ga, value in (("1/1/2", False), ("1/1/2", True), ("3/0/3", "ECONOMY"), ("3/0/2", 19.5)):
                t = {"ts": 2.0, "dir": "in", "src": "1.1.24", "dst": ga, "type": "response", "raw": None,
                     "value": value, "text": str(value)}
                app._handle({"type": "telegram", "t": t, "state": {"value": value, "text": str(value), "raw": None,
                                                                  "ts": 2.0, "src": "1.1.24"}})
                await pilot.pause(0.2)
            await pilot.pause(0.5)

    app = ScadaApp(cfg)
    monkeypatch.setattr(app.client, "request", fake_request)
    monkeypatch.setattr(app.client, "connect", fake_connect)
    monkeypatch.setattr(type(app.client), "connected", property(lambda self: True))
    monkeypatch.setattr(app.client, "close_nowait", lambda: None)
    asyncio.run(run())
    assert "write" not in sent and "read" not in sent, sent


def test_audit_user_cannot_inject_markup_or_pretend():
    from knxscada.server import _audit_user

    assert _audit_user(None, "[/x]bob@10.0.0.5") == "xbob@10.0.0.5"          # no markup characters
    assert _audit_user(None, "a" * 500) == "a" * 64                          # bounded
    assert _audit_user(None, {"not": "a string"}) == "?"
    # The kernel's account name wins; only the SSH address is taken from the client.
    assert _audit_user("scada", "admin@192.168.1.20") == "scada@192.168.1.20"
    assert _audit_user("scada", "admin") == "scada"
