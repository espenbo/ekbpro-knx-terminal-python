# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""Access levels on the server socket (``server.access``): who may read, operate and configure."""

from __future__ import annotations

import asyncio
import os

import pytest

from knxscada import access
from knxscada.client import ScadaClient
from knxscada.config import Config

OTHER_UID = os.getuid() + 12345    # an account that is not the server's own


@pytest.fixture
def groups(monkeypatch):
    """Fake Unix groups: ola is in "ops", kari in nothing."""
    table = {"ola": {"ola", "ops"}, "kari": {"kari"}}
    monkeypatch.setattr(access, "groups_of", lambda name: table.get(name, set()))


def test_levels(groups):
    """The highest level that lists an account wins; names, @groups and * all count; an unlisted account and an
    unknown peer get None; without server.access, and for the server's own account and root, everything is allowed."""
    rules = {"configure": ["caesar"], "operate": ["@ops"], "read": ["*"]}
    assert access.level_of(rules, OTHER_UID, "caesar") == "configure"
    assert access.level_of(rules, OTHER_UID, "ola") == "operate"
    assert access.level_of(rules, OTHER_UID, "kari") == "read"
    assert access.level_of({"operate": ["@ops"]}, OTHER_UID, "kari") is None
    assert access.level_of({"operate": "ola"}, OTHER_UID, "ola") == "operate"      # one name without a list
    assert access.level_of(rules, None, None) is None                             # the kernel could not tell
    assert access.level_of({}, OTHER_UID, "kari") == "configure"                  # no server.access: as before
    assert access.level_of(None, None, None) == "configure"
    assert access.level_of(rules, os.getuid(), None) == "configure"               # the server's own account
    assert access.level_of(rules, 0, "root") == "configure"


def test_commands_and_validation():
    """Reading needs read, writes/reads on the bus/acknowledging need operate, changing protocols.yaml needs
    configure; a broken server.access opens nothing; validation names unknown levels and wrong entries."""
    assert access.allowed("read", "history") and access.allowed("read", "points")
    assert not access.allowed("read", "write") and not access.allowed("read", "ack")
    assert access.allowed("operate", "point_write") and not access.allowed("operate", "protocols_save")
    assert access.allowed("configure", "protocols_reload")
    with pytest.raises(PermissionError, match="access: read, needed: operate"):
        access.check("read", "write", "kari")
    assert access.effective("caesar") == {"configure": []}
    assert access.level_of(access.effective("caesar"), OTHER_UID, "caesar") is None
    assert access.validate(None) == []
    assert access.validate({"configure": ["caesar"], "read": "*"}) == []
    assert any("unknown level" in p for p in access.validate({"admin": ["x"]}))
    assert any("write a list" in p for p in access.validate({"read": 3}))
    assert access.validate("caesar")


def test_server_enforces_levels(tmp_path, monkeypatch, groups):
    """Through the real socket handler: a reader can look but not write or acknowledge, an operator cannot change
    protocols.yaml, an account listed nowhere is refused at once (PermissionError in the client, logged)."""
    import knxscada.server as srv

    cfg = Config(path=tmp_path / "config.yaml", knx={"enabled": False},
                 server={"socket": "s.sock", "database": "db.sqlite",
                         "access": {"configure": ["caesar"], "operate": ["@ops"], "read": ["kari"]}})
    server = srv.ScadaServer(cfg)
    who = {"name": "kari"}
    monkeypatch.setattr(srv, "_peer", lambda writer: (OTHER_UID, who["name"]))

    async def run():
        """Serve the socket and connect as kari (read), ola (operate) and per (nobody)."""
        sock = tmp_path / "s.sock"
        listener = await asyncio.start_unix_server(server._client, path=str(sock))
        try:
            client = ScadaClient(sock, lambda msg: None)
            await client.connect()
            assert await client.request("events", limit=5) is not None              # reading is fine
            with pytest.raises(RuntimeError, match="may not"):
                await client.request("ack", all=True)
            with pytest.raises(RuntimeError, match="may not"):
                await client.request("point_write", conn="x", tag="y", value=1)
            client.close_nowait()

            who["name"] = "ola"
            client = ScadaClient(sock, lambda msg: None)
            await client.connect()
            assert await client.request("ack", all=True) == 0                        # operating is fine
            with pytest.raises(RuntimeError, match="needed: configure"):
                await client.request("protocols_save", data={})
            client.close_nowait()

            who["name"] = "per"
            client = ScadaClient(sock, lambda msg: None)
            with pytest.raises(PermissionError, match="per has no access"):
                await client.connect()
            texts = [e["text"] for e in server.store.events(20)]
            assert any("Connection refused: per" in t for t in texts)
        finally:
            listener.close()
            await listener.wait_closed()

    asyncio.run(run())
