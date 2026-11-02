# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""DPT conversion: encode → decode round trips, loose user input and display text."""

import pytest
from xknx.dpt import DPTArray, DPTBinary

from knxscada import dpt
from knxscada.i18n import set_language


@pytest.mark.parametrize(
    ("raw", "expected"),
    [({"main": 9, "sub": 1}, "9.001"), ({"main": 1, "sub": None}, "1"), ({"main": None}, None), (None, None)],
)
def test_dpt_str(raw, expected):
    """The DPT from an ETS project ({main, sub}) becomes '9.001' or '1', and a missing main type gives None."""
    assert dpt.dpt_str(raw) == expected


def test_split():
    """'9.001' splits into main and sub type; a main type alone gives no sub type."""
    assert dpt.split("9.001") == (9, 1)
    assert dpt.split("1") == (1, None)
    assert dpt.split(None) == (None, None)


@pytest.mark.parametrize("text", ["on", "On", "1", "true", "på", "ja", True, 1])
def test_dpt1_true_inputs(text):
    """Every way a user or YAML may write 'on' (English, Norwegian, 1, True) encodes DPT 1 as 1."""
    assert dpt.encode("1.001", text) == DPTBinary(1)


@pytest.mark.parametrize("text", ["off", "0", "false", "nei", False, 0])
def test_dpt1_false_inputs(text):
    """Every way of writing 'off' encodes DPT 1 as 0."""
    assert dpt.encode("1.001", text) == DPTBinary(0)


def test_dpt1_decodes_to_plain_bool():
    """DPT 1 always decodes to a plain bool, whatever the subtype, so alarms and trends can compare it."""
    # Subtype enums (e.g. window open/closed) are not used: always a bool.
    assert dpt.decode("1.019", DPTBinary(1)) is True
    assert dpt.decode("1", DPTBinary(0)) is False


@pytest.mark.parametrize(
    ("dpt_", "value", "expected"),
    [
        ("9.001", 21.5, 21.5),       # temperature
        ("9.001", "21,5", 21.5),     # Norwegian decimal comma
        ("9.001", "-3.2", -3.2),
        ("5.001", 40, 40),           # percent
        ("5.001", "100", 100),       # integer DPT given as text
        ("5.010", 7, 7),             # counter
        ("7.001", 1000, 1000),
        ("14.056", 1234.5, 1234.5),  # power W
    ],
)
def test_round_trip(dpt_, value, expected):
    """Common numeric DPTs survive encode → decode, also with a Norwegian decimal comma and numbers given as text."""
    decoded = dpt.decode(dpt_, dpt.encode(dpt_, value))
    assert decoded == pytest.approx(expected, abs=0.02)


def test_main_type_fallback():
    """A DPT with only the main type ('9') is encoded and decoded with the default subtype."""
    # ETS often only knows the main type; "9" must still be usable as 9.001.
    assert dpt.decode("9", dpt.encode("9", 20.0)) == pytest.approx(20.0, abs=0.01)


def test_hvac_mode_by_name_and_label():
    """HVAC mode (20.102) accepts the enum name in any case and the shown label ('Night/Eco')."""
    for text in ("COMFORT", "comfort", "Comfort"):
        assert dpt.decode("20.102", dpt.encode("20.102", text)) == "COMFORT"
    assert dpt.decode("20.102", dpt.encode("20.102", "Night/Eco")) == "ECONOMY"


def test_hvac_mode_by_translated_label():
    """HVAC mode also accepts the Norwegian labels the user sees in the TUI."""
    set_language("nb")
    assert dpt.decode("20.102", dpt.encode("20.102", "Komfort")) == "COMFORT"
    assert dpt.decode("20.102", dpt.encode("20.102", "frostsikring")) == "BUILDING_PROTECTION"


def test_enum_options():
    """Enum DPTs list their choices for the write dialog; numeric DPTs have none."""
    assert "COMFORT" in dpt.enum_options("20.102")
    assert dpt.enum_options("9.001") == []


def test_raw_value():
    """A raw value ({'raw': hex}) is sent as given, as array or binary, and raw_hex shows payloads the same way."""
    assert dpt.encode(None, {"raw": "0C 33"}) == DPTArray((0x0C, 0x33))
    assert dpt.encode(None, {"raw": "0x01", "binary": True}) == DPTBinary(1)
    assert dpt.raw_hex(DPTArray((0x0C, 0x33))) == "0C 33"
    assert dpt.raw_hex(DPTBinary(1)) == "0x01"


def test_unknown_dpt_needs_raw_value():
    """Without a DPT a plain value cannot be encoded: guessing could send the wrong bytes."""
    with pytest.raises(ValueError):
        dpt.encode(None, 5)


def test_invalid_value_raises():
    """An out-of-range value is refused instead of being clipped and sent."""
    with pytest.raises(Exception):  # noqa: B017 - xknx raises its own ConversionError
        dpt.encode("5.001", 250)  # percent out of range


def test_decode_garbage_returns_none():
    """A payload of the wrong length decodes to None instead of raising in the telegram handler."""
    assert dpt.decode("9.001", DPTArray((0x01,))) is None  # wrong length


def test_unknown_dpt_small_value():
    """Without a DPT a binary payload is shown as a number, an array payload as nothing."""
    assert dpt.decode(None, DPTBinary(3)) == 3
    assert dpt.decode(None, DPTArray((1, 2))) is None


@pytest.mark.parametrize(
    ("dpt_", "value", "text"),
    [
        (None, None, "–"),
        ("1.001", True, "On"),
        ("1.001", False, "Off"),
        ("1.009", True, "Closed"),
        ("9.001", 21.0, "21.0 °C"),
        ("9.001", 21.04, "21.0 °C"),
        ("5.001", 40, "40 %"),
        ("14.056", 1234.5, "1234 W"),
        ("20.102", "ECONOMY", "Night/Eco"),
    ],
)
def test_fmt(dpt_, value, text):
    """Display text per DPT: on/off and open/closed words, one decimal and unit for temperature, % and W, enum labels.
    """
    assert dpt.fmt(dpt_, value) == text


def test_fmt_translated():
    """Display texts follow the chosen language."""
    set_language("nb")
    assert dpt.fmt("1.001", True) == "På"
    assert dpt.fmt("20.102", "COMFORT") == "Komfort"


def test_is_numeric():
    """Only numeric DPTs count as numeric (for trends and value conditions); switches and strings do not."""
    assert dpt.is_numeric("9.001")
    assert dpt.is_numeric("14.056")
    assert not dpt.is_numeric("1.001")
    assert not dpt.is_numeric("16.000")
