# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""SQLite store: last values, batched purge and alarm life cycle."""

import time

import pytest

from knxscada.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "test.db")
    yield s
    s.close()


def tel(ts, dst, value, kind="write"):
    return {"ts": ts, "dir": "in", "src": "1.1.1", "dst": dst, "type": kind, "raw": None, "value": value}


def test_last_values_ignore_reads(store):
    now = time.time()
    store.add_telegram(tel(now - 3, "1/1/1", True))
    store.add_telegram(tel(now - 2, "1/1/1", False))
    store.add_telegram(tel(now - 1, "1/1/1", None, kind="read"))
    store.add_telegram(tel(now, "3/0/1", 21.5))
    last = store.last_values()
    assert last["1/1/1"]["value"] is False
    assert last["3/0/1"]["value"] == 21.5


def test_last_values_uses_partial_index(store):
    plan = store.db.execute(
        "EXPLAIN QUERY PLAN SELECT dst, MAX(id) FROM telegrams WHERE type != 'read' GROUP BY dst").fetchall()
    assert any("idx_tel_last" in str(tuple(r)) for r in plan)


def test_history_and_numeric_values(store):
    now = time.time()
    store.add_telegram(tel(now - 10, "3/0/1", 20.0))
    store.add_telegram(tel(now - 5, "3/0/1", True))   # bools are stored as 0/1 for trends
    store.add_telegram(tel(now - 4, "3/0/1", "text"))  # not numeric: not in the history
    assert store.history("3/0/1", now - 60) == [(pytest.approx(now - 10), 20.0), (pytest.approx(now - 5), 1.0)]


def test_purge_in_batches(store):
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
    aid = store.raise_alarm("0/6/1", "Smoke", "critical", True)
    assert [a["id"] for a in store.open_alarms()] == [aid]
    store.ack_alarm(aid, "anna")
    store.ack_alarm(aid, "bob")  # only the first acknowledgement counts
    assert store.open_alarms()[0]["acked_by"] == "anna"
    store.clear_alarm(aid)
    assert store.open_alarms() == []          # cleared and acknowledged: closed
    assert store.alarm_history()[0]["id"] == aid


def test_last_values_per_address_gives_the_same(store):
    now = time.time()
    for i, v in enumerate((1.0, 2.0, 3.0)):
        store.add_telegram(tel(now + i, "3/0/1", v))
    store.add_telegram(tel(now + 5, "3/0/1", None, kind="read"))
    store.add_telegram(tel(now, "1/1/1", True))
    assert store.last_values(["3/0/1", "1/1/1", "9/9/9"]) == store.last_values()
    assert store.last_values(["3/0/1"])["3/0/1"]["value"] == 3.0


def test_long_history_is_thinned_but_keeps_the_peaks(store):
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
    now = time.time()
    for i in range(5):
        store.add_telegram(tel(now + i, "1/1/1", bool(i % 2)))
    store.add_telegram(tel(now + 10, "1/1/2", True))
    rows = store.telegrams(3, "1/1/1")
    assert [r["ts"] for r in rows] == [now + 2, now + 3, now + 4]
