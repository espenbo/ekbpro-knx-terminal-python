# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""SQLite store: last values, batched purge and alarm life cycle."""

import time

import pytest

from knxscada.store import Store


@pytest.fixture
def store(tmp_path):
    """A fresh store in a temporary database, closed after the test."""
    s = Store(tmp_path / "test.db")
    yield s
    s.close()


def tel(ts, dst, value, kind="write"):
    """A decoded telegram row as the server stores it."""
    return {"ts": ts, "dir": "in", "src": "1.1.1", "dst": dst, "type": kind, "raw": None, "value": value}


def test_last_values_ignore_reads(store):
    """Last values come from the newest write/response; a read request (no value) does not hide them."""
    now = time.time()
    store.add_telegram(tel(now - 3, "1/1/1", True))
    store.add_telegram(tel(now - 2, "1/1/1", False))
    store.add_telegram(tel(now - 1, "1/1/1", None, kind="read"))
    store.add_telegram(tel(now, "3/0/1", 21.5))
    last = store.last_values()
    assert last["1/1/1"]["value"] is False
    assert last["3/0/1"]["value"] == 21.5


def test_last_values_uses_partial_index(store):
    """The last-values query uses the partial index, so start-up stays fast on a large database."""
    plan = store.db.execute(
        "EXPLAIN QUERY PLAN SELECT dst, MAX(id) FROM telegrams WHERE type != 'read' GROUP BY dst").fetchall()
    assert any("idx_tel_last" in str(tuple(r)) for r in plan)


def test_history_and_numeric_values(store):
    """Trend history holds numbers only: bools become 0/1, text is left out."""
    now = time.time()
    store.add_telegram(tel(now - 10, "3/0/1", 20.0))
    store.add_telegram(tel(now - 5, "3/0/1", True))   # bools are stored as 0/1 for trends
    store.add_telegram(tel(now - 4, "3/0/1", "text"))  # not numeric: not in the history
    assert store.history("3/0/1", now - 60) == [(pytest.approx(now - 10), 20.0), (pytest.approx(now - 5), 1.0)]


def test_purge_in_batches(store):
    """Old telegrams and events are deleted in batches (so the server is not blocked), the recent ones stay."""
    now = time.time()
    for i in range(25):
        store.add_telegram(tel(now - 100 * 86400 + i, "1/1/1", i))
    store.add_telegram(tel(now, "1/1/1", 99))
    store.add_event("info", "system", "old")
    store.db.execute("UPDATE events SET ts = ?", (now - 100 * 86400,))
    store.commit()
    cutoff = now - 90 * 86400
    batches = []
    while (n := store.purge_batch(cutoff, limit=10)) >= 10:
        batches.append(n)
    batches.append(n)
    assert batches == [10, 10, 5]
    assert [t["value"] for t in store.telegrams()] == [99]
    assert store.events() == []


def test_alarm_life_cycle(store):
    """An alarm stays open until it is both cleared and acknowledged; only the first acknowledgement is recorded."""
    aid = store.raise_alarm("0/6/1", "Smoke", "critical", True)
    assert [a["id"] for a in store.open_alarms()] == [aid]
    store.ack_alarm(aid, "anna")
    store.ack_alarm(aid, "bob")  # only the first acknowledgement counts
    assert store.open_alarms()[0]["acked_by"] == "anna"
    store.clear_alarm(aid)
    assert store.open_alarms() == []          # cleared and acknowledged: closed
    assert store.alarm_history()[0]["id"] == aid


def test_last_values_per_address_gives_the_same(store):
    """Asking for last values of some addresses gives the same as the full query, unknown addresses are left out."""
    now = time.time()
    for i, v in enumerate((1.0, 2.0, 3.0)):
        store.add_telegram(tel(now + i, "3/0/1", v))
    store.add_telegram(tel(now + 5, "3/0/1", None, kind="read"))
    store.add_telegram(tel(now, "1/1/1", True))
    assert store.last_values(["3/0/1", "1/1/1", "9/9/9"]) == store.last_values()
    assert store.last_values(["3/0/1"])["3/0/1"]["value"] == 3.0


def test_long_history_is_thinned_but_keeps_the_peaks(store):
    """A long history is reduced to max_points but keeps short peaks and dips, in time order, also via a separate read
    connection.
    """
    now = time.time()
    for i in range(3000):                       # a value every 10 s for ~8 hours
        store.add_telegram(tel(now - 30000 + i * 10, "3/0/1", 20.0))
    store.add_telegram(tel(now - 15000 + 3, "3/0/1", 99.0))   # one short peak …
    store.add_telegram(tel(now - 5000 + 3, "3/0/1", -5.0))    # … and one dip
    store.commit()
    pts = store.history("3/0/1", now - 86400, max_points=200)
    assert len(pts) <= 200
    assert max(v for _t, v in pts) == 99.0 and min(v for _t, v in pts) == -5.0
    assert pts == sorted(pts)
    assert len(store.history("3/0/1", now - 86400, max_points=10_000)) == 3002   # small enough: everything
    assert store.read_history("3/0/1", now - 86400, max_points=200) == pts        # separate connection


def test_telegrams_for_one_address_newest_last(store):
    """The telegram list for one address gives the newest n, oldest first."""
    now = time.time()
    for i in range(5):
        store.add_telegram(tel(now + i, "1/1/1", bool(i % 2)))
    store.add_telegram(tel(now + 10, "1/1/2", True))
    rows = store.telegrams(3, "1/1/1")
    assert [r["ts"] for r in rows] == [now + 2, now + 3, now + 4]


def test_logged_tag_values_history_and_purge(store):
    """Values of protocols.yaml tags (point_values): trend history and purge like telegrams."""
    now = time.time()
    store.add_point_value("vent/RT401", 16.5, now - 7200)
    store.add_point_value("vent/RT401", 17.0, now - 60)
    store.add_point_value("vent/Drift", True, now - 30)
    store.add_point_value("vent/Status", "Stoppet av kalender", now - 30)
    store.add_telegram(tel(now, "vent/RT401", 99.0))   # a group address with the same text is not mixed in
    store.commit()
    assert store.history("vent/RT401", now - 3600, point=True) == [(now - 60, 17.0)]
    assert store.read_history("vent/Drift", now - 3600, point=True) == [(now - 30, 1.0)]
    assert store.history("vent/Status", now - 3600, point=True) == []      # text: no number
    assert store.logged_points() == ["vent/Drift", "vent/RT401", "vent/Status"]
    assert store.purge_batch(now - 3600) == 1
    assert store.history("vent/RT401", 0, point=True) == [(now - 60, 17.0)]


def test_history_of_a_knx_point_reads_the_telegrams():
    """``history`` with ``point: knx/<ga>`` (a view mixing KNX and other connections) reads that address's telegrams."""
    import asyncio
    from types import SimpleNamespace

    from knxscada.server import ScadaServer

    calls = []
    fake = SimpleNamespace(store=SimpleNamespace(read_history=lambda *a: calls.append(a) or []))
    asyncio.run(ScadaServer._command(fake, {"cmd": "history", "point": "knx/0/1/30", "hours": 1}, "u"))
    asyncio.run(ScadaServer._command(fake, {"cmd": "history", "point": "plc/x", "hours": 1}, "u"))
    assert calls[0][0] == "0/1/30" and calls[0][3] is False
    assert calls[1][0] == "plc/x" and calls[1][3] is True
