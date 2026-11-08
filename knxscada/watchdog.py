# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""Keeping the server's internal tasks alive, and telling systemd that they are.

Without this, a background task that stopped with an unexpected error was gone for good and nobody knew: one failed
commit in the housekeeping loop (a full disk) meant nothing was saved any more, one error in the hub's flush loop
froze every TUI, and a bacpypes3 ``ErrorRejectAbortNack`` (a ``BaseException``) ended a connection's reconnect loop.

* :func:`supervised` runs a task and starts it again (with back-off) when it ends with an error, logging the error
  with its traceback and as an event (F6). One-shot tasks (the start-up read of KNX) are only reported, never
  repeated: they may send telegrams.
* :class:`Watchdog` checks every :data:`CHECK_S` seconds that the event loop is not blocked and that the loops that
  must run all the time (housekeeping, the hub's flush loop) have beaten their heart recently. When everything is
  alive it pings systemd (``WATCHDOG=1``); when the loop hangs or a heart stops, the pings stop and systemd restarts
  the service after ``WatchdogSec`` (``deploy/knxscada.service``). A restart sends nothing to KNX or devices (safety
  rule 1), so it is the safe way out of a hang.
* :func:`sd_notify` speaks systemd's notify protocol without a package; it does nothing outside systemd (no
  ``NOTIFY_SOCKET``), e.g. in tests, in Docker or under an older unit with ``Type=simple``.

Used by :class:`knxscada.server.ScadaServer` (``run``) and :class:`knxscada.protocols.hub.Hub`.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import time
from collections.abc import Awaitable, Callable
from typing import Any

from .i18n import _

log = logging.getLogger(__name__)

#: Seconds between two checks of the watchdog (and two pings to systemd while all is well).
CHECK_S = 5.0
#: A check that comes this many seconds later than planned means the event loop was blocked (logged).
LAG_WARN_S = 2.0
#: Seconds between two "event loop was blocked" events, so a slow machine does not fill the event log.
LAG_REPORT_EVERY_S = 300.0
#: First and longest pause (s) before a task that failed is started again.
RESTART_MIN_S, RESTART_MAX_S = 1.0, 60.0
#: A task that ran at least this long before it failed starts again after the shortest pause.
RESTART_RESET_S = 120.0

#: Errors that must end the process rather than be retried.
FATAL = (KeyboardInterrupt, SystemExit, GeneratorExit)

Report = Callable[[str, str], None]


def sd_notify(state: str) -> bool:
    """Send *state* (``READY=1``, ``WATCHDOG=1``, ``STOPPING=1`` …) to systemd. False when not run by systemd with
    ``Type=notify`` (no ``NOTIFY_SOCKET``) or when the socket cannot be reached – never raises."""
    path = os.environ.get("NOTIFY_SOCKET")
    if not path:
        return False
    if path.startswith("@"):
        path = "\0" + path[1:]          # an abstract socket
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.connect(path)
            s.sendall(state.encode())
        return True
    except OSError:
        return False


async def supervised(name: str, factory: Callable[[], Awaitable[Any]], report: Report, *, forever: bool = False,
                     retry: bool = True, stopping: Callable[[], bool] = lambda: False) -> None:
    """Run ``await factory()`` and keep it running as asked.

    * It ends with an error: reported (event log and traceback in the log); with *retry* it is started again after a
      pause that doubles from :data:`RESTART_MIN_S` up to :data:`RESTART_MAX_S`.
    * It returns: done – unless *forever* (a loop that must never end, e.g. housekeeping): then that is reported and
      it is started again too.
    * *stopping()* true (the server or the connection is being stopped): nothing is started again.

    *report(level, text)* puts a message into the event log. Cancelling ends it at once (server shutdown).
    """
    delay = RESTART_MIN_S
    while True:
        started = time.monotonic()
        try:
            await factory()
            if not forever or stopping():
                return
            report("error", _("{task} stopped without an error – started again").format(task=name))
        except asyncio.CancelledError:
            raise
        except FATAL:
            raise
        # BaseException: bacpypes3 raises BaseException subclasses (ErrorRejectAbortNack); every one is reported.
        except BaseException as exc:
            log.exception("%s failed", name)
            error = str(exc) or type(exc).__name__
            if not retry or stopping():
                report("error", _("{task} failed: {error}").format(task=name, error=error))
                return
            report("error", _("{task} failed: {error} – started again in {delay} s").format(
                task=name, error=error, delay=int(delay)))
        if time.monotonic() - started >= RESTART_RESET_S:
            delay = RESTART_MIN_S
        await asyncio.sleep(delay)
        delay = min(delay * 2, RESTART_MAX_S)


class Watchdog:
    """Heartbeats of the loops that must always run, an event-loop lag check, and the systemd watchdog ping.

    The loops call :meth:`beat` each round; :meth:`watch` registers how old a beat may get. :meth:`run` is a task of
    the server (started in ``ScadaServer.run``).
    """

    def __init__(self, report: Report) -> None:
        """*report(level, text)* writes to the event log (the server's ``event`` with kind "system")."""
        self.report = report
        #: Loop name → (monotonic time of its last beat, seconds it may stay silent).
        self.hearts: dict[str, list[float]] = {}
        #: Loops reported as stopped (reported once, and once more when they beat again).
        self.silent: set[str] = set()
        #: Monotonic time of the last "event loop blocked" event.
        self._lag_reported = -LAG_REPORT_EVERY_S
        #: Longest delay of a check seen (s) – for the status and the tests.
        self.max_lag = 0.0

    def watch(self, name: str, max_age_s: float) -> None:
        """Expect a :meth:`beat` of *name* at least every *max_age_s* seconds from now on."""
        self.hearts[name] = [time.monotonic(), max_age_s]

    def beat(self, name: str) -> None:
        """*name* is alive (called by the loop itself, every round). Unknown names are ignored."""
        heart = self.hearts.get(name)
        if heart is not None:
            heart[0] = time.monotonic()

    def check(self, lag: float = 0.0) -> bool:
        """One check: report a blocked event loop (*lag* seconds late) and loops that stopped beating, and those that
        beat again. Returns True when every loop is alive (then systemd may be pinged)."""
        now = time.monotonic()
        self.max_lag = max(self.max_lag, lag)
        if lag >= LAG_WARN_S and now - self._lag_reported >= LAG_REPORT_EVERY_S:
            self._lag_reported = now
            self.report("error", _("The server was blocked for {s:.1f} s (nothing was handled meanwhile)").format(s=lag))
        alive = True
        for name, (last, max_age) in self.hearts.items():
            if now - last > max_age:
                alive = False
                if name not in self.silent:
                    self.silent.add(name)
                    self.report("error", _("{task} has not run for {s} s").format(task=name, s=int(now - last)))
            elif name in self.silent:
                self.silent.discard(name)
                self.report("info", _("{task} runs again").format(task=name))
        return alive

    async def run(self) -> None:
        """Check every :data:`CHECK_S` seconds and ping systemd while everything is alive. Runs until cancelled."""
        while True:
            planned = time.monotonic() + CHECK_S
            await asyncio.sleep(CHECK_S)
            if self.check(max(0.0, time.monotonic() - planned)):
                sd_notify("WATCHDOG=1")
