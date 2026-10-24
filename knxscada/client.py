# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""Asynchronous client for the SCADA server's Unix-socket API.

The protocol is newline-delimited JSON (see ``docs/08-architecture.md``):

* The client sends requests ``{"id": n, "cmd": "...", "user": "...", ...}``.
* The server answers with ``{"type": "reply", "id": n, "ok": true, "data": ...}``.
* In between, the server pushes unsolicited messages (telegrams, status,
  alarms, events). Those are handed to the ``on_message`` callback.

The client runs inside the Textual event loop, so ``on_message`` is called
on the UI thread and may touch widgets directly.
"""

from __future__ import annotations

import asyncio
import getpass
import itertools
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .i18n import _


def current_user() -> str:
    """Identify the operator for the audit log: ``user`` or ``user@ssh-client-ip``."""
    user = getpass.getuser()
    ssh = os.environ.get("SSH_CLIENT", "").split()  # "client-ip client-port server-port"
    return f"{user}@{ssh[0]}" if ssh else user


class ScadaClient:
    """One connection to the server, with request/reply matching by id."""

    #: Seconds to wait for a reply before a request fails.
    TIMEOUT = 30

    def __init__(self, socket_path: Path, on_message: Callable[[dict], None]) -> None:
        self.socket_path = socket_path
        self.on_message = on_message
        self.user = current_user()
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future] = {}  # request id → future waiting for the reply
        self._task: asyncio.Task | None = None

    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    async def connect(self) -> None:
        """Open the socket, start the reader task and announce ourselves.

        Raises ``FileNotFoundError``/``ConnectionRefusedError`` when no server
        is running; the TUI catches those and retries.
        """
        # The initial "hello" snapshot contains the state of every group
        # address and can be large, hence the generous line limit.
        self._reader, self._writer = await asyncio.open_unix_connection(str(self.socket_path), limit=64 * 2**20)
        self._task = asyncio.create_task(self._read_loop())
        await self.request("hello")

    def close_nowait(self) -> None:
        """Tell the server we are leaving and close immediately (used when quitting).

        Deliberately synchronous: waiting for the reply while the UI is
        shutting down caused the application to hang.
        """
        if self.connected:
            assert self._writer is not None
            try:
                self._writer.write((json.dumps({"id": 0, "cmd": "bye", "user": self.user}) + "\n").encode())
            except Exception:  # noqa: BLE001 - the socket may already be gone
                pass
            self._writer.close()
        if self._task:
            self._task.cancel()

    async def _read_loop(self) -> None:
        """Dispatch incoming lines: replies resolve futures, everything else goes to ``on_message``."""
        assert self._reader is not None
        try:
            while line := await self._reader.readline():
                msg = json.loads(line)
                if msg.get("type") == "reply":
                    fut = self._pending.pop(msg.get("id"), None)
                    if fut and not fut.done():
                        if msg.get("ok"):
                            fut.set_result(msg.get("data"))
                        else:
                            fut.set_exception(RuntimeError(msg.get("error", _("error"))))
                else:
                    self.on_message(msg)
        finally:
            # Fail every outstanding request and let the UI know (it reconnects).
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(ConnectionError(_("Lost the connection to the server")))
            self._pending.clear()
            self.on_message({"type": "disconnected"})

    async def request(self, cmd: str, **kwargs: Any) -> Any:
        """Send a command and wait for its reply data (raises on error or timeout)."""
        if not self.connected:
            raise ConnectionError(_("Not connected to the server"))
        assert self._writer is not None
        rid = next(self._ids)
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        self._writer.write((json.dumps({"id": rid, "cmd": cmd, "user": self.user, **kwargs}) + "\n").encode())
        await self._writer.drain()
        return await asyncio.wait_for(fut, self.TIMEOUT)

    async def write(self, ga: str, value: Any, dpt: str | None = None) -> None:
        """Write *value* to group address *ga* (encoded by the server according to the DPT)."""
        await self.request("write", ga=ga, value=value, dpt=dpt)

    async def read(self, ga: str) -> None:
        """Send a GroupValueRead; the answer arrives later as a normal telegram."""
        await self.request("read", ga=ga)
