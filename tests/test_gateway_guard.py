# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""The gateway's restart guard towards KNX – the protection the old PLC bridge had, so a PLC download or reset
(its variables jump to their start values) never reaches the bus when KNX ↔ PLC runs through a gateway."""

import asyncio

from tests.test_protocols import Mem, make_hub, until

N = 10
GUARD = {"settle_ms": 50, "burst_limit": 8, "restart_quiet_s": 0.3}


def setup(tmp_path, **gw):
    """A KNX-like connection (never initial values) and a PLC-like client, linked both ways tag by tag."""
    knx = Mem("knx", {"tags": {f"k{i}": {} for i in range(N)}}, None, initial_allowed=False, server=False)
    plc = Mem("plc", {"tags": {f"v{i}": {} for i in range(N)}}, None, server=False)
    gateway = {"name": "knx-plc", "from": "knx", "to": "plc", "direction": "both",
               "map": [f"k{i} -> v{i}" for i in range(N)], **GUARD, **gw}
    return knx, plc, make_hub(tmp_path, {"gateways": [gateway]}, {"knx": knx, "plc": plc})


def test_single_change_goes_out_after_the_settle_time(tmp_path):
    """One PLC change reaches KNX, but only after settle_ms (time to recognise a burst); KNX → PLC is not held."""
    async def run():
        """Statuses first, then one change from each side."""
        knx, plc, hub_coro = setup(tmp_path)
        hub = await hub_coro
        try:
            for i in range(N):
                hub.update("knx", f"k{i}", 0)
                hub.update("plc", f"v{i}", 0)
            await asyncio.sleep(0.1)
            hub.update("plc", "v1", 5)
            await asyncio.sleep(0.01)
            assert knx.writes == []                         # held during the settle time
            await until(lambda: ("k1", 5) in knx.writes, 2)
            hub.update("knx", "k2", 7)                      # towards the PLC: at once, no guard
            await until(lambda: ("v2", 7) in plc.writes, 1)
        finally:
            await hub.stop()

    asyncio.run(run())


def test_burst_is_a_restart_nothing_reaches_knx(tmp_path):
    """More than burst_limit PLC changes within settle_ms: nothing is sent to KNX, changes in the quiet time are only
    remembered, and afterwards the PLC gets the KNX values again – as the old bridge did after a download."""
    async def run():
        """A PLC download: every variable jumps to 0 at once."""
        knx, plc, hub_coro = setup(tmp_path)
        hub = await hub_coro
        try:
            for i in range(N):
                hub.update("knx", f"k{i}", 1)
                hub.update("plc", f"v{i}", 1)
            await asyncio.sleep(0.1)
            for i in range(N):
                hub.update("plc", f"v{i}", 0)               # reset values after a download
            await asyncio.sleep(0.15)
            assert knx.writes == []
            assert any("restart" in text for _level, text in hub.server.events)
            hub.update("plc", "v3", 9)                      # in the quiet time: only remembered
            await asyncio.sleep(0.05)
            assert knx.writes == []
            await until(lambda: len(plc.writes) >= N, 2)    # quiet time over: the PLC gets the KNX values again
            assert sorted(plc.writes) == sorted((f"v{i}", 1) for i in range(N))
            assert knx.writes == []
        finally:
            await hub.stop()

    asyncio.run(run())


def test_bad_status_makes_the_next_value_a_status(tmp_path):
    """A bad status (the PLC loading an application) forgets the value: the next good one is not sent to KNX, and the
    quiet time starts."""
    async def run():
        """One variable goes bad, then comes back with its start value."""
        knx, _plc, hub_coro = setup(tmp_path)
        hub = await hub_coro
        try:
            for i in range(N):
                hub.update("knx", f"k{i}", 1)
                hub.update("plc", f"v{i}", 1)
            await asyncio.sleep(0.1)
            hub.update("plc", "v4", None, "bad")
            hub.update("plc", "v4", 0)                      # back with the start value: a status
            await asyncio.sleep(0.15)
            assert knx.writes == []
            assert hub.engine.quiet_until.get("plc")        # quiet time running
        finally:
            await hub.stop()

    asyncio.run(run())


def test_guard_off(tmp_path):
    """settle_ms: 0 turns the guard off: a change goes to KNX at once, even many at a time."""
    async def run():
        """Ten changes without the guard."""
        knx, _plc, hub_coro = setup(tmp_path, settle_ms=0)
        hub = await hub_coro
        try:
            for i in range(N):
                hub.update("knx", f"k{i}", 1)
                hub.update("plc", f"v{i}", 1)
            await asyncio.sleep(0.1)
            for i in range(N):
                hub.update("plc", f"v{i}", 2)
            await until(lambda: len(knx.writes) == N, 1)
        finally:
            await hub.stop()

    asyncio.run(run())
