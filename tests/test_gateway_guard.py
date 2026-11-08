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


def _lasting(drv):
    """Keep a memory connection up until stopped (the default Mem session ends at once and reconnects)."""
    async def session():
        """Connected until the test stops it or sets it down."""
        drv.set_state(True)
        await drv.wait_stopped(60)

    drv.session = session


def test_held_change_is_dropped_when_the_source_disconnects(tmp_path):
    """A PLC change waiting in the restart guard (settle time) is not written to KNX when the PLC connection is lost
    meanwhile – the review's reproduction: False → True, held, PLC disconnected, True must not reach KNX."""
    async def run():
        """Statuses, one change, then the PLC drops out within the settle time."""
        knx, plc, hub_coro = setup(tmp_path, settle_ms=200)
        _lasting(knx)
        _lasting(plc)
        hub = await hub_coro
        try:
            await until(lambda: knx.connected and plc.connected, 2)
            for i in range(N):
                hub.update("knx", f"k{i}", False)
                hub.update("plc", f"v{i}", False)
            knx.set_ready()
            plc.set_ready()
            await asyncio.sleep(0.05)
            hub.update("plc", "v1", True)
            await asyncio.sleep(0.02)
            plc.set_state(False)                            # lost within the settle time
            await asyncio.sleep(0.4)
            assert knx.writes == []
        finally:
            await hub.stop()

    asyncio.run(run())


def test_rate_limited_change_is_dropped_when_the_source_disconnects(tmp_path):
    """A change delayed by min_interval_ms is dropped when its source is lost before it is due."""
    async def run():
        """Two quick changes (the second waits for the rate limit), then the source drops out."""
        knx, plc, hub_coro = setup(tmp_path, settle_ms=0, min_interval_ms=300)
        _lasting(knx)
        _lasting(plc)
        hub = await hub_coro
        try:
            await until(lambda: knx.connected and plc.connected, 2)
            for i in range(N):
                hub.update("knx", f"k{i}", 0)
                hub.update("plc", f"v{i}", 0)
            knx.set_ready()
            plc.set_ready()
            await asyncio.sleep(0.05)
            hub.update("plc", "v1", 1)
            await until(lambda: ("k1", 1) in knx.writes, 1)
            hub.update("plc", "v1", 2)                      # within min_interval: waits
            await asyncio.sleep(0.02)
            plc.set_state(False)
            await asyncio.sleep(0.5)
            assert ("k1", 2) not in knx.writes
        finally:
            await hub.stop()

    asyncio.run(run())


def test_change_is_not_written_when_the_source_turned_bad(tmp_path):
    """A held change is not written when the source's value has turned bad before the settle time is over."""
    async def run():
        """One change, then a bad status of the same tag within the settle time."""
        knx, plc, hub_coro = setup(tmp_path, settle_ms=200)
        _lasting(knx)
        _lasting(plc)
        hub = await hub_coro
        try:
            await until(lambda: knx.connected and plc.connected, 2)
            for i in range(N):
                hub.update("knx", f"k{i}", 0)
                hub.update("plc", f"v{i}", 0)
            knx.set_ready()
            plc.set_ready()
            await asyncio.sleep(0.05)
            hub.update("plc", "v1", 1)
            await asyncio.sleep(0.02)
            hub.update("plc", "v1", 1, "bad")
            await asyncio.sleep(0.4)
            assert knx.writes == []
        finally:
            await hub.stop()

    asyncio.run(run())


def test_counter_step_passes_and_a_coarse_knx_echo_does_not(tmp_path):
    """A large counter (147 912 → 147 913, float) is a change and reaches the target – the review's reproduction –
    while the read-back of a value written to a coarse KNX DPT 9 (21.37 comes back as 21.36) is recognised as an echo
    and not sent back to the source."""
    from knxscada import dpt as dptlib

    async def run():
        """Meter → PLC (float), and PLC ↔ KNX with a DPT 9 tag."""
        meter = Mem("meter", {"tags": {"kwh": {"type": "float"}}}, None, server=False)
        plc = Mem("plc", {"tags": {"kwh": {"type": "float"}, "t": {"type": "float"}}}, None, server=False)
        knx = Mem("knx", {"tags": {"3/0/0": {"type": "float", "dpt": "9.001"}}}, None, initial_allowed=False,
                  server=False)
        knx.normalise = lambda tag, v: dptlib.decode("9.001", dptlib.encode("9.001", v)) if v is not None else v
        for drv in (meter, plc, knx):
            _lasting(drv)
        gws = [{"name": "m", "from": "meter", "to": "plc", "map": ["kwh"]},
               {"name": "k", "from": "plc", "to": "knx", "direction": "both", "settle_ms": 0, "map": ["t -> 3/0/0"]}]
        hub = await make_hub(tmp_path, {"gateways": gws}, {"meter": meter, "plc": plc, "knx": knx})
        try:
            await until(lambda: meter.connected and plc.connected and knx.connected, 2)
            hub.update("meter", "kwh", 147912.0)
            hub.update("plc", "kwh", 147912.0)
            hub.update("plc", "t", 20.0)
            hub.update("knx", "3/0/0", 20.0)
            for drv in (meter, plc, knx):
                drv.set_ready()
            await asyncio.sleep(0.05)
            hub.update("meter", "kwh", 147913.0)
            await until(lambda: ("kwh", 147913.0) in plc.writes, 2)
            hub.update("plc", "t", 21.37)
            await until(lambda: ("3/0/0", 21.37) in knx.writes, 2)
            hub.update("knx", "3/0/0", 21.36)                # the bus echo of our write
            await asyncio.sleep(0.2)
            assert ("t", 21.36) not in plc.writes
        finally:
            await hub.stop()

    asyncio.run(run())


def test_change_held_for_a_reading_target_is_dropped_when_the_source_reconnects(tmp_path):
    """The second review's reproduction for ``link.held`` (a target connected but still reading): the source goes
    False → True (held), is lost, comes back reporting False as its status, then the target finishes reading. The old
    True must not be written – it belongs to the source's previous connection."""
    async def run():
        """Source and target both read first, so the test decides when each is ready."""
        src = Mem("src", {"tags": {"a": {"type": "bool"}}}, None, server=False, reads_first=True)
        dev = Mem("dev", {"tags": {"b": {"type": "bool", "writable": True}}}, None, server=False, reads_first=True)
        _lasting(src)
        _lasting(dev)
        hub = await make_hub(tmp_path, {"gateways": [{"name": "g", "from": "src", "to": "dev", "map": ["a -> b"]}]},
                             {"src": src, "dev": dev})
        try:
            await until(lambda: src.connected and dev.connected, 2)
            hub.update("src", "a", False)                   # status
            src.set_ready()
            hub.update("src", "a", True)                    # a change; dev still reading → held
            assert hub.engine.links[0].held
            src.set_state(False)                            # the source is lost …
            assert not hub.engine.links[0].held
            src.set_state(True)                             # … and back, with False as its status
            hub.update("src", "a", False)
            src.set_ready()
            dev.set_ready()                                 # the target is ready now
            await asyncio.sleep(0.1)
            assert ("b", True) not in dev.writes
        finally:
            await hub.stop()

    asyncio.run(run())


def test_held_change_is_dropped_when_the_source_turns_bad(tmp_path):
    """A change held for a reading target is not written when the source reports a bad status before the target is
    ready."""
    async def run():
        """Change, held, bad, target ready."""
        src = Mem("src", {"tags": {"a": {"type": "int"}}}, None, server=False, reads_first=True)
        dev = Mem("dev", {"tags": {"b": {"type": "int", "writable": True}}}, None, server=False, reads_first=True)
        _lasting(src)
        _lasting(dev)
        hub = await make_hub(tmp_path, {"gateways": [{"name": "g", "from": "src", "to": "dev", "map": ["a -> b"]}]},
                             {"src": src, "dev": dev})
        try:
            await until(lambda: src.connected and dev.connected, 2)
            hub.update("src", "a", 1)
            src.set_ready()
            hub.update("src", "a", 2)                       # held
            hub.update("src", "a", 2, "bad")
            dev.set_ready()
            await asyncio.sleep(0.1)
            assert dev.writes == []
        finally:
            await hub.stop()

    asyncio.run(run())


class QualityMem(Mem):
    """A server connection that records the qualities the gateway engine gives its tags."""

    def __init__(self, *args, **kw):
        """As :class:`Mem`, plus the list of (tag, quality) calls."""
        super().__init__(*args, **kw)
        self.qualities: list[tuple[str, str]] = []

    async def set_quality(self, tag, quality):
        """Record instead of showing it to clients."""
        self.qualities.append((tag.name, quality))


def test_quality_travels_through_a_chain_of_gateways(tmp_path):
    """Source → server 1 → server 2 (the second review: bad stopped at the first server). A bad source makes both
    servers bad – in the hub and towards their clients; a lost source makes both old; the next good value makes
    both good again, even when it is the same value."""
    async def run():
        """Two gateways in a row between in-memory servers."""
        src = Mem("src", {"tags": {"t": {"type": "float"}}}, None, server=False)
        s1 = QualityMem("s1", {}, None)
        s2 = QualityMem("s2", {}, None)
        _lasting(src)
        gws = [{"name": "g1", "from": "src", "to": "s1", "map": ["t -> x"]},
               {"name": "g2", "from": "s1", "to": "s2", "map": ["x -> y"]}]
        hub = await make_hub(tmp_path, {"gateways": gws}, {"src": src, "s1": s1, "s2": s2})
        try:
            await until(lambda: src.connected, 2)
            hub.update("src", "t", 21.0)
            await until(lambda: hub.values.get("s2/y", {}).get("v") == 21.0, 2)

            def q():
                """The qualities of the two servers' values in the hub."""
                return hub.values["s1/x"]["q"], hub.values["s2/y"]["q"]

            hub.update("src", "t", 21.0, "bad")
            await asyncio.sleep(0.05)
            assert q() == ("bad", "bad")
            assert ("x", "bad") in s1.qualities and ("y", "bad") in s2.qualities
            hub.update("src", "t", 21.0)                    # good again, the same value
            await until(lambda: q() == ("good", "good"), 2)
            src.set_state(False)
            await asyncio.sleep(0.05)
            assert q() == ("old", "old")
            assert ("y", "old") in s2.qualities
        finally:
            await hub.stop()

    asyncio.run(run())


def test_quality_loop_of_both_way_lines_ends(tmp_path):
    """Two servers linked both ways: marking one bad marks the other and stops there (no endless walk)."""
    async def run():
        """src → s1 ↔ s2."""
        src = Mem("src", {"tags": {"t": {"type": "float"}}}, None, server=False)
        s1 = QualityMem("s1", {}, None)
        s2 = QualityMem("s2", {}, None)
        _lasting(src)
        gws = [{"name": "g1", "from": "src", "to": "s1", "map": ["t -> x"]},
               {"name": "g2", "from": "s1", "to": "s2", "direction": "both", "map": ["x -> y"]}]
        hub = await make_hub(tmp_path, {"gateways": gws}, {"src": src, "s1": s1, "s2": s2})
        try:
            await until(lambda: src.connected, 2)
            hub.update("src", "t", 5.0)
            await until(lambda: hub.values.get("s2/y", {}).get("v") == 5.0, 2)
            hub.update("src", "t", 5.0, "bad")
            await asyncio.sleep(0.05)
            assert hub.values["s1/x"]["q"] == hub.values["s2/y"]["q"] == "bad"
            assert len(s2.qualities) == 1
        finally:
            await hub.stop()

    asyncio.run(run())


def test_float32_echo_is_an_echo_and_a_counter_step_is_a_change(tmp_path):
    """Echo suppression follows the target's own resolution (Driver.normalise), not a size-dependent tolerance: the
    read-back of 21.123456789 written to a float32 tag (21.12346) is an echo, while 1 000 000 → 1 000 001 Wh is a
    change in both directions of the line."""
    from knxscada.protocols.base import float32

    async def run():
        """meter ↔ dev (float32) both ways."""
        meter = Mem("meter", {"tags": {"e": {"type": "float", "writable": True}}}, None, server=False)
        dev = Mem("dev", {"tags": {"e": {"type": "float", "writable": True}}}, None, server=False)
        dev.normalise = lambda tag, v: dev.normalise_raw(tag, v)          # a float32 device
        _lasting(meter)
        _lasting(dev)
        hub = await make_hub(tmp_path, {"gateways": [{"name": "g", "from": "meter", "to": "dev", "direction": "both",
                                                      "map": ["e"]}]}, {"meter": meter, "dev": dev})
        try:
            await until(lambda: meter.connected and dev.connected, 2)
            hub.update("meter", "e", 1_000_000.0)
            hub.update("dev", "e", 1_000_000.0)
            await asyncio.sleep(0.05)
            hub.update("meter", "e", 1_000_001.0)
            await until(lambda: ("e", 1_000_001.0) in dev.writes, 2)
            hub.update("dev", "e", 1_000_001.0)                           # echo
            hub.update("meter", "e", 21.123456789)
            await until(lambda: ("e", 21.123456789) in dev.writes, 2)
            hub.update("dev", "e", float32(21.123456789))                 # the device's rounded read-back
            await asyncio.sleep(0.1)
            assert meter.writes == []                                     # neither echo went back
            hub.update("dev", "e", 21.5)                                  # a real change on the device
            await until(lambda: ("e", 21.5) in meter.writes, 2)
        finally:
            await hub.stop()

    asyncio.run(run())
