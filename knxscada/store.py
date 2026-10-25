# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""SQLite storage for telegrams, events and alarms.

The database is only ever opened by the server process, so a single
connection is used without locking. WAL journal mode lets backup tools
(``sqlite3 .backup``) read the file while the server keeps writing.

Writes of telegrams and events are batched: they mark the store as
"dirty" and :meth:`Store.commit` is called once per second by the server's
housekeeping task. Alarm changes are committed immediately because they
must survive a crash.

Tables
------
``telegrams``
    Every group telegram seen on the bus (and every one we sent).
    ``value`` holds the decoded value as JSON; ``num`` holds the same value
    as a float (bools as 0/1) and is what trend charts read. ``via`` is the
    KNX interface it came through (only with several interfaces).
``events``
    Operator actions, alarm transitions and system messages (the event log).
``alarms``
    One row per alarm occurrence with raise/clear/acknowledge timestamps.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from .i18n import N_

#: Values of ``events.level``, ``events.kind`` and ``telegrams.type``. They are
#: stored in English and translated when displayed.
EVENT_LEVELS = (N_("info"), N_("error"), N_("alarm"))
EVENT_KINDS = (N_("system"), N_("knx"), N_("user"), N_("operation"), N_("alarm"), N_("plc"))
TELEGRAM_TYPES = (N_("write"), N_("response"), N_("read"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS telegrams (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    ts    REAL NOT NULL,          -- unix time
    dir   TEXT NOT NULL,          -- 'in' (from the bus) or 'out' (sent by us)
    src   TEXT NOT NULL,          -- individual address of the sender, e.g. 1.1.24
    dst   TEXT NOT NULL,          -- group address, e.g. 1/1/0
    type  TEXT NOT NULL,          -- 'write' | 'response' | 'read'
    raw   TEXT,                   -- payload in hex
    value TEXT,                   -- decoded value as JSON
    num   REAL,                   -- numeric value for trends (NULL if not numeric)
    via   TEXT                    -- KNX interface it came through (only with several interfaces)
);
CREATE INDEX IF NOT EXISTS idx_tel_dst_ts ON telegrams(dst, ts);
CREATE INDEX IF NOT EXISTS idx_tel_ts ON telegrams(ts);
-- Partial index for "last value per address" at start-up (see Store.last_values):
-- the GROUP BY can be answered from the index alone instead of scanning the table.
CREATE INDEX IF NOT EXISTS idx_tel_last ON telegrams(dst, id) WHERE type != 'read';

CREATE TABLE IF NOT EXISTS events (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    ts    REAL NOT NULL,
    level TEXT NOT NULL,          -- 'info' | 'error' | 'alarm'
    kind  TEXT NOT NULL,          -- 'system' | 'knx' | 'user' | 'operation' | 'alarm' | 'plc'
    user  TEXT,                   -- "user@ssh-client-ip" for operator actions
    text  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ev_ts ON events(ts);

CREATE TABLE IF NOT EXISTS alarms (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ga         TEXT NOT NULL,
    text       TEXT NOT NULL,
    severity   TEXT NOT NULL,     -- 'critical' | 'warning' | free text
    raised_ts  REAL NOT NULL,
    cleared_ts REAL,              -- NULL while the alarm condition is still active
    acked_ts   REAL,              -- NULL until someone acknowledges the alarm
    acked_by   TEXT,
    value      TEXT               -- value that raised the alarm (JSON)
);
CREATE INDEX IF NOT EXISTS idx_alarm_open ON alarms(acked_ts, cleared_ts);
"""


class Store:
    """Thin wrapper around the SQLite database."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")  # safe with WAL, much faster than FULL
        self.db.executescript(SCHEMA)
        self._migrate()
        self._dirty = False

    def _migrate(self) -> None:
        """Add columns that newer versions use to a database created by an older one."""
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(telegrams)")}
        if "via" not in cols:
            self.db.execute("ALTER TABLE telegrams ADD COLUMN via TEXT")
            self.db.commit()

    def commit(self) -> None:
        """Commit pending telegram/event inserts (called once per second)."""
        if self._dirty:
            self.db.commit()
            self._dirty = False

    def close(self) -> None:
        self.commit()
        self.db.close()

    # -- telegrams ---------------------------------------------------------------
    def add_telegram(self, t: dict[str, Any]) -> int:
        """Insert a telegram (dict as produced by ``ScadaServer.ingest``); returns its id."""
        value = t.get("value")
        num = None
        if isinstance(value, (bool, int, float)):
            num = float(value)  # bool → 0.0/1.0 so on/off can be plotted too
        cur = self.db.execute(
            "INSERT INTO telegrams(ts, dir, src, dst, type, raw, value, num, via) VALUES (?,?,?,?,?,?,?,?,?)",
            (t["ts"], t["dir"], t["src"], t["dst"], t["type"], t.get("raw"), json.dumps(value, ensure_ascii=False), num,
             t.get("via") or None),
        )
        self._dirty = True
        return cur.lastrowid

    def telegrams(self, limit: int = 500, ga: str | None = None, before_id: int | None = None) -> list[dict]:
        """The newest *limit* telegrams (optionally for one GA / older than an id), oldest first."""
        sql = "SELECT * FROM telegrams WHERE 1=1"
        args: list[Any] = []
        if ga:
            sql += " AND dst = ?"
            args.append(ga)
        if before_id:
            sql += " AND id < ?"
            args.append(before_id)
        # For one address, walk the (dst, ts) index instead of sorting all its rows.
        sql += " ORDER BY ts DESC, id DESC LIMIT ?" if ga else " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        rows = [dict(r) for r in self.db.execute(sql, args)]
        for r in rows:
            r["value"] = json.loads(r["value"]) if r["value"] else None
        return rows[::-1]

    def read_history(self, ga: str, since: float, max_points: int = 4000) -> list[tuple[float, float]]:
        """:meth:`history` on a separate read-only connection – safe to run in a worker thread.

        A long trend of a busy address reads hundreds of thousands of rows; in a
        thread the server keeps handling telegrams meanwhile (WAL allows a reader
        next to the writer).
        """
        db = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        try:
            return self.history(ga, since, max_points, db)
        finally:
            db.close()

    def history(self, ga: str, since: float, max_points: int = 4000,
                db: sqlite3.Connection | None = None) -> list[tuple[float, float]]:
        """``(timestamp, number)`` pairs for a trend chart, oldest first.

        A busy address can have hundreds of thousands of values in 30 days. Beyond
        *max_points* the range is split into time buckets, and each bucket gives
        its lowest and its highest value (with their own timestamps) – peaks stay
        visible, and the reply stays small however long the period is.
        """
        db = db or self.db
        where = "dst = ? AND ts >= ? AND type != 'read' AND num IS NOT NULL"
        n = db.execute(f"SELECT COUNT(*) FROM telegrams WHERE {where}", (ga, since)).fetchone()[0]
        if n <= max_points:
            rows = db.execute(f"SELECT ts, num FROM telegrams WHERE {where} ORDER BY ts", (ga, since))
            return [(r["ts"], r["num"]) for r in rows]
        bucket = max((time.time() - since) / (max_points // 2), 1e-3)
        points: set[tuple[float, float]] = set()
        for agg in ("MIN", "MAX"):
            # SQLite returns the ts of the row that has the MIN/MAX value ("bare column").
            rows = db.execute(
                f"SELECT ts, {agg}(num) AS num FROM telegrams WHERE {where} GROUP BY CAST((ts - ?) / ? AS INTEGER)",
                (ga, since, since, bucket),
            )
            points.update((r["ts"], r["num"]) for r in rows)
        return sorted(points)

    def traffic(self, minutes: int = 60) -> list[int]:
        """Number of telegrams per minute for the last *minutes* minutes, oldest first."""
        now = time.time()
        start = int(now // 60) - minutes + 1  # first minute bucket (in "minutes since epoch")
        counts = {
            r[0]: r[1]
            for r in self.db.execute(
                "SELECT CAST(ts / 60 AS INTEGER) AS m, COUNT(*) FROM telegrams WHERE ts >= ? GROUP BY m", (start * 60,)
            )
        }
        return [counts.get(start + i, 0) for i in range(minutes)]

    def last_values(self, addresses: list[str] | None = None) -> dict[str, dict]:
        """Last known value per group address – used to pre-fill the state at start-up.

        With *addresses* (the project's), one indexed lookup per address: the time
        depends on the number of addresses, not on the size of the log.
        """
        if addresses is None:
            rows = list(self.db.execute(
                "SELECT t.* FROM telegrams t JOIN (SELECT dst, MAX(id) AS id FROM telegrams "
                "WHERE type != 'read' GROUP BY dst) m ON t.id = m.id"
            ))
        else:
            q = "SELECT * FROM telegrams WHERE dst = ? AND type != 'read' ORDER BY id DESC LIMIT 1"
            rows = [r for a in addresses if (r := self.db.execute(q, (a,)).fetchone())]
        out = {}
        for r in rows:
            out[r["dst"]] = {
                "value": json.loads(r["value"]) if r["value"] else None,
                "raw": r["raw"],
                "ts": r["ts"],
                "src": r["src"],
            }
        return out

    def purge_batch(self, cutoff: float, limit: int = 5000) -> int:
        """Delete up to *limit* telegrams and events older than *cutoff* (unix time).

        Returns the larger of the two deleted counts; the caller repeats until
        it is below *limit*. Alarms are kept. Deleting in batches keeps each
        transaction short, so the server stays responsive during a big purge.
        """
        n = 0
        for table in ("telegrams", "events"):
            cur = self.db.execute(
                f"DELETE FROM {table} WHERE id IN (SELECT id FROM {table} WHERE ts < ? ORDER BY ts LIMIT ?)",
                (cutoff, limit),
            )
            n = max(n, cur.rowcount)
        self.db.commit()
        return n

    # -- events ----------------------------------------------------------------
    def add_event(self, level: str, kind: str, text: str, user: str | None = None) -> dict:
        """Insert an event log entry and return it as a dict (ready to broadcast)."""
        ts = time.time()
        cur = self.db.execute(
            "INSERT INTO events(ts, level, kind, user, text) VALUES (?,?,?,?,?)", (ts, level, kind, user, text)
        )
        self._dirty = True
        return {"id": cur.lastrowid, "ts": ts, "level": level, "kind": kind, "user": user, "text": text}

    def events(self, limit: int = 500) -> list[dict]:
        """The newest *limit* events, oldest first."""
        rows = self.db.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows][::-1]

    # -- alarms ------------------------------------------------------------------
    def open_alarms(self) -> list[dict]:
        """Alarms that are still active or not yet acknowledged (the alarm list)."""
        rows = self.db.execute("SELECT * FROM alarms WHERE acked_ts IS NULL OR cleared_ts IS NULL ORDER BY id")
        return [dict(r) for r in rows]

    def raise_alarm(self, ga: str, text: str, severity: str, value: Any) -> int:
        """Record a new alarm occurrence; returns its id. Committed immediately."""
        cur = self.db.execute(
            "INSERT INTO alarms(ga, text, severity, raised_ts, value) VALUES (?,?,?,?,?)",
            (ga, text, severity, time.time(), json.dumps(value, ensure_ascii=False)),
        )
        self.db.commit()
        return cur.lastrowid

    def clear_alarm(self, alarm_id: int) -> None:
        """Mark that the alarm condition is no longer active."""
        self.db.execute("UPDATE alarms SET cleared_ts = ? WHERE id = ?", (time.time(), alarm_id))
        self.db.commit()

    def ack_alarm(self, alarm_id: int, user: str) -> None:
        """Acknowledge an alarm (only the first acknowledgement is stored)."""
        self.db.execute(
            "UPDATE alarms SET acked_ts = ?, acked_by = ? WHERE id = ? AND acked_ts IS NULL", (time.time(), user, alarm_id)
        )
        self.db.commit()

    def alarm_history(self, limit: int = 200) -> list[dict]:
        """The newest *limit* alarms, newest first."""
        rows = self.db.execute("SELECT * FROM alarms ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]
