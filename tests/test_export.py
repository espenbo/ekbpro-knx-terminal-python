# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""Bus monitor export in the ETS group monitor format."""

import xml.etree.ElementTree as ET

from xknx.cemi import CEMIFrame

from knxscada.export import NAMESPACE, cemi_hex, telegrams_xml


def tel(dst, kind, raw, src="1.1.24", ts=1792950000.25):
    return {"ts": ts, "dir": "in", "src": src, "dst": dst, "type": kind, "raw": raw}


def back(hexframe):
    return CEMIFrame.from_knx(bytes.fromhex(hexframe)).data.telegram()


def test_frames_round_trip():
    t = back(cemi_hex(tel("1/1/47", "write", "0x01")))
    assert str(t.source_address) == "1.1.24" and str(t.destination_address) == "1/1/47"
    assert t.payload.value.value == 1                                       # DPTBinary in the APCI
    t = back(cemi_hex(tel("3/0/1", "response", "0C 33")))
    assert type(t.payload).__name__ == "GroupValueResponse" and t.payload.value.value == (0x0C, 0x33)
    t = back(cemi_hex(tel("3/0/1", "read", None)))
    assert type(t.payload).__name__ == "GroupValueRead"
    assert cemi_hex(tel("1/1/1", "write", "0x01")) == "2900BCE011180901010081"


def test_project_prefix_is_removed():
    assert back(cemi_hex(tel("S:1/2/3", "write", "0x00"))).destination_address.raw == back(
        cemi_hex(tel("1/2/3", "write", "0x00"))).destination_address.raw


def test_broken_telegram_is_skipped():
    assert cemi_hex(tel("1/1/1", "write", None)) is None
    assert cemi_hex(tel("not/an/address", "write", "0x01")) is None


def test_xml_is_the_ets_communication_log():
    text, n = telegrams_xml([tel("1/1/1", "write", "0x01", ts=1.5), tel("1/1/1", "write", None),
                             tel("3/0/1", "write", "0C 33", ts=2.0)], "My house")
    assert n == 2
    root = ET.fromstring(text.encode())
    assert root.tag == f"{{{NAMESPACE}}}CommunicationLog"
    kinds = [child.tag.split("}")[1] for child in root]
    assert kinds == ["RecordStart", "Telegram", "Telegram", "RecordStop"]
    first = root[1].attrib
    assert first["Service"] == "L_Data.ind" and first["FrameFormat"] == "CommonEmi"
    assert first["Timestamp"] == "1970-01-01T00:00:01.500000Z"
    assert root[0].attrib["ConnectionName"] == "My house"


def test_csv_and_json():
    import json

    from knxscada.export import render

    gas = {"1/1/1": {"name": "Hall; light", "dpt": "1.001"}}
    rows = [dict(tel("1/1/1", "write", "0x01", ts=1792950000.5), text="On", value=True, via="house")]
    text, n = render("csv", rows, gas)
    assert n == 1 and text.startswith("﻿")                    # BOM: Excel reads UTF-8
    header, line = text.lstrip("﻿").splitlines()
    assert header == "time;dir;src;dst;name;type;dpt;raw;value;via"
    assert '"Hall; light"' in line and line.endswith(";On;house")   # separators inside a name are quoted
    text, n = render("json", rows, gas)
    obj = json.loads(text)
    assert n == 1 and obj["value"] is True and obj["text"] == "On" and obj["ts"] == 1792950000.5
