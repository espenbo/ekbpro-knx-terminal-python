# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""Supervised background tasks, the watchdog's heartbeats and lag check, and the systemd notify protocol."""

from __future__ import annotations

import asyncio
import socket

import pytest

from knxscada import watchdog
from knxscada.config import Config
from knxscada.watchdog import Watchdog, sd_notify, supervised

from .test_protocols import Mem, until


class Boom(BaseException):  # like bacpypes3's ErrorRejectAbortNack: a BaseException
    """A BaseException subclass, as bacpypes3 raises for error, reject and abort answers."""


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    """Restart pauses of a few milliseconds, so the tests do not wait for real back-off."""
    monkeypatch.setattr(watchdog, "RESTART_MIN_S", 0.01)
    monkeypatch.setattr(watchdog, "RESTART_MAX_S", 0.02)


def test_supervised_restarts_and_reports():
    """A task that fails (also with a BaseException) is reported and started again; a loop that must run forever is
    started again when it returns; retry=False only reports; stopping() prevents a restart; cancelling ends it."""
    events: list[tuple[str, str]] = []

    def report(level, text):
        """Collect the event-log lines."""
        events.append((level, text))

    async def run():
        """Each case with its own small task."""
        runs = {"n": 0}

        async def flaky():
            """Fail twice (an Exception, then a BaseException like bacpypes3's), then succeed."""
            runs["n"] += 1
            if runs["n"] == 1:
                raise OSError("disk full")
            if runs["n"] == 2:
                raise Boom("reject")

        await supervised("db", flaky, report)
        assert runs["n"] == 3
        assert [t for _l, t in events][:2] == ["db failed: disk full – started again in 0 s",
                                               "db failed: reject – started again in 0 s"]

        loops = {"n": 0}

        async def loop():
            """A 'forever' loop that returns: started again until stopping() says no."""
            loops["n"] += 1

        await supervised("housekeeping", loop, report, forever=True, stopping=lambda: loops["n"] >= 3)
        assert loops["n"] == 3 and any("stopped without an error" in t for _l, t in events)

        once = {"n": 0}

        async def one_shot():
            """A one-shot task (the KNX start-up read): never repeated."""
            once["n"] += 1
            raise RuntimeError("bus busy")

        await supervised("KNX start-up read", one_shot, report, retry=False)
        assert once["n"] == 1 and events[-1] == ("error", "KNX start-up read failed: bus busy")

        async def forever():
            """Never ends by itself."""
            await asyncio.sleep(60)

        task = asyncio.create_task(supervised("x", forever, report, forever=True))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())


def test_watchdog_hearts_and_lag(monkeypatch):
    """A loop that stops beating is reported once and again when it runs; while one is silent the check fails (no
    systemd ping); a blocked event loop is reported, at most every LAG_REPORT_EVERY_S."""
    events: list[tuple[str, str]] = []
    now = {"t": 1000.0}
    monkeypatch.setattr(watchdog.time, "monotonic", lambda: now["t"])
    dog = Watchdog(lambda level, text: events.append((level, text)))
    dog.watch("housekeeping", 30)
    assert dog.check() is True and events == []
    now["t"] += 45
    assert dog.check() is False and events == [("error", "housekeeping has not run for 45 s")]
    assert dog.check() is False and len(events) == 1                         # reported once
    dog.beat("housekeeping")
    assert dog.check() is True and events[-1] == ("info", "housekeeping runs again")
    dog.check(lag=3.2)
    dog.check(lag=4.0)                                                      # within LAG_REPORT_EVERY_S: not again
    assert [t for _l, t in events].count("The server was blocked for 3.2 s (nothing was handled meanwhile)") == 1
    assert dog.max_lag == 4.0


def test_sd_notify(tmp_path, monkeypatch):
    """With NOTIFY_SOCKET the message reaches systemd's socket; without it (tests, Docker, Type=simple) nothing
    happens and nothing raises – also when the socket is gone."""
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    assert sd_notify("READY=1") is False
    path = tmp_path / "notify"
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
        s.bind(str(path))
        monkeypatch.setenv("NOTIFY_SOCKET", str(path))
        assert sd_notify("WATCHDOG=1") is True
        assert s.recv(100) == b"WATCHDOG=1"
    path.unlink()
    assert sd_notify("READY=1") is False


def test_driver_reconnects_after_a_base_exception(tmp_path):
    """A BaseException from a session (bacpypes3's ErrorRejectAbortNack) reconnects like any other error instead of
    ending the connection's loop for good."""
    from knxscada.protocols.hub import Hub

    from .test_protocols import FakeServer

    async def run():
        """A memory driver whose first session raises Boom; the second one stays connected."""
        hub = Hub(FakeServer(tmp_path))
        hub.data, hub.load = {"connections": {}, "gateways": []}, (lambda: None)
        drv = Mem("dev", {"tags": {"t": {}}}, hub, server=False)
        sessions = {"n": 0}

        async def session():
            """First a BaseException, then a normal connected session."""
            sessions["n"] += 1
            if sessions["n"] == 1:
                raise Boom("abort")
            drv.set_state(True)
            await drv.wait_stopped(60)

        drv.session = session
        from knxscada.protocols import base

        real_sleep = asyncio.sleep
        base.asyncio.sleep = lambda s: real_sleep(min(s, 0.01))   # the reconnect pause, shortened
        try:
            hub._add(drv)
            await until(lambda: drv.connected, 5)
            assert sessions["n"] == 2
            assert any("abort" in t for _l, t in hub.server.events)
        finally:
            base.asyncio.sleep = real_sleep
            await hub.stop()

    asyncio.run(run())


def test_housekeeping_survives_a_failed_commit(tmp_path):
    """One failed database commit (a full disk) no longer ends the housekeeping loop: it is reported in the event
    log and the loop runs again, so later values are still saved."""
    from knxscada.server import ScadaServer

    cfg = Config(path=tmp_path / "config.yaml", knx={"enabled": False},
                 server={"socket": "s.sock", "database": "db.sqlite"})
    server = ScadaServer(cfg)
    commits = {"n": 0}
    real_commit = server.store.commit

    def commit():
        """Fail the first commit only."""
        commits["n"] += 1
        if commits["n"] == 1:
            raise OSError("database or disk is full")
        real_commit()

    server.store.commit = commit

    async def run():
        """Run the supervised housekeeping for a little over two rounds."""
        task = asyncio.create_task(supervised("housekeeping", server._housekeeping, server._task_event, forever=True))
        await until(lambda: commits["n"] >= 2, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    texts = [e["text"] for e in server.store.events(20)]
    assert any("housekeeping failed: database or disk is full" in t for t in texts)
