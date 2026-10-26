# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""Export telegrams: ETS group monitor XML, CSV (spreadsheets) and JSON lines (scripts).

ETS group monitor
-----------------

ETS saves and opens group monitor recordings as XML (*Diagnostics → Group
Monitor → Open*)::

    <CommunicationLog xmlns="http://knx.org/xml/telegrams/01">
      <RecordStart Timestamp="…" Mode="LinkLayer" … />
      <Telegram Timestamp="2026-10-26T18:44:06.123456Z" Service="L_Data.ind"
                FrameFormat="CommonEmi" RawData="2900BCE01118092F010081" />
      <RecordStop Timestamp="…" />
    </CommunicationLog>

``RawData`` is the complete cEMI frame. It is rebuilt with xknx from what the
server stored per telegram: source, destination, type and the raw payload
(``"0x01"`` for values that fit in the APCI, ``"0C 33"`` for data bytes).

CSV and JSON
------------
One row per telegram with local time, direction, source, destination, name,
type, DPT, raw data, value and interface. The CSV uses ``;`` and a UTF-8 BOM,
so Excel with Nordic/European settings opens it correctly.
"""

from __future__ import annotations

import csv
import io
import json
import socket
import time
from collections.abc import Iterable
from xml.sax.saxutils import quoteattr

from xknx.cemi import CEMIFrame, CEMILData, CEMIMessageCode
from xknx.dpt import DPTArray, DPTBinary
from xknx.exceptions import XKNXException
from xknx.telegram import GroupAddress, IndividualAddress, Telegram
from xknx.telegram.apci import GroupValueRead, GroupValueResponse, GroupValueWrite

from .project import split_key

NAMESPACE = "http://knx.org/xml/telegrams/01"


def _payload(raw: str | None) -> DPTArray | DPTBinary | None:
    if not raw:
        return None
    if raw.startswith("0x"):
        return DPTBinary(int(raw, 16))
    return DPTArray(bytes.fromhex(raw))


def cemi_hex(t: dict) -> str | None:
    """The cEMI L_Data.ind frame of a stored telegram as hex, or None if it cannot be rebuilt."""
    try:
        data = _payload(t.get("raw"))
        if t["type"] == "read":
            apci = GroupValueRead()
        elif data is None:
            return None
        elif t["type"] == "response":
            apci = GroupValueResponse(data)
        else:
            apci = GroupValueWrite(data)
        telegram = Telegram(
            destination_address=GroupAddress(split_key(t["dst"])[1]),   # without a project prefix
            source_address=IndividualAddress(t["src"]),
            payload=apci,
        )
        frame = CEMIFrame(code=CEMIMessageCode.L_DATA_IND, data=CEMILData.init_from_telegram(telegram))
        return frame.to_knx().hex().upper()
    except (KeyError, ValueError, TypeError, XKNXException):  # e.g. an address xknx cannot parse
        return None


def _stamp(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts)) + f".{int(ts % 1 * 1_000_000):06d}Z"


def telegrams_xml(telegrams: Iterable[dict], connection: str = "") -> tuple[str, int]:
    """The ETS group monitor XML for *telegrams* (oldest first) and the number of telegrams written."""
    rows = [(t["ts"], raw) for t in telegrams if (raw := cemi_hex(t))]
    start = rows[0][0] if rows else time.time()
    stop = rows[-1][0] if rows else start
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        f'<CommunicationLog xmlns="{NAMESPACE}">',
        f'  <RecordStart Timestamp="{_stamp(start)}" Mode="LinkLayer" Host={quoteattr(socket.gethostname())} '
        f'ConnectionName={quoteattr(connection or "EKBPRO KNX Terminal Python")} ConnectionOptions="" '
        'ConnectorType="KnxIpTunneling" MediumType="TP" />',
    ]
    lines += [f'  <Telegram Timestamp="{_stamp(ts)}" Service="L_Data.ind" FrameFormat="CommonEmi" RawData="{raw}" />'
              for ts, raw in rows]
    lines += [f'  <RecordStop Timestamp="{_stamp(stop)}" />', "</CommunicationLog>"]
    return "\n".join(lines) + "\n", len(rows)


#: Columns of the CSV and JSON export.
COLUMNS = ("time", "dir", "src", "dst", "name", "type", "dpt", "raw", "value", "via")


def _row(t: dict, gas: dict[str, dict]) -> dict:
    ga = gas.get(t["dst"], {})
    return {
        "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t["ts"])) + f".{int(t['ts'] % 1 * 1000):03d}",
        "dir": t["dir"], "src": t["src"], "dst": t["dst"], "name": ga.get("name", ""), "type": t["type"],
        "dpt": ga.get("dpt") or "", "raw": t.get("raw") or "", "value": t.get("text") or "", "via": t.get("via") or "",
    }


def telegrams_csv(telegrams: Iterable[dict], gas: dict[str, dict]) -> tuple[str, int]:
    """CSV (``;``, with a header) and the number of rows. The text starts with a BOM for Excel."""
    out = io.StringIO()
    w = csv.DictWriter(out, COLUMNS, delimiter=";", lineterminator="\r\n")
    w.writeheader()
    n = 0
    for t in telegrams:
        w.writerow(_row(t, gas))
        n += 1
    return "\ufeff" + out.getvalue(), n


def telegrams_jsonl(telegrams: Iterable[dict], gas: dict[str, dict]) -> tuple[str, int]:
    """One JSON object per line (with the unix time ``ts`` and the decoded ``value``) and the count."""
    lines = []
    for t in telegrams:
        row = _row(t, gas)
        row["ts"], row["value"], row["text"] = t["ts"], t.get("value"), t.get("text") or ""
        lines.append(json.dumps(row, ensure_ascii=False, default=str))
    return "\n".join(lines) + ("\n" if lines else ""), len(lines)


#: Export format → file extension.
FORMATS = {"xml": ".xml", "csv": ".csv", "json": ".jsonl"}


def render(fmt: str, telegrams: list[dict], gas: dict[str, dict], connection: str = "") -> tuple[str, int]:
    """The file contents for *fmt* (``xml`` | ``csv`` | ``json``) and the number of telegrams in it."""
    if fmt == "csv":
        return telegrams_csv(telegrams, gas)
    if fmt == "json":
        return telegrams_jsonl(telegrams, gas)
    return telegrams_xml(telegrams, connection)
