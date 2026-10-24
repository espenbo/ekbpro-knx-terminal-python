# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""Alarm rules: selection of group addresses, conditions and alarm texts."""

import pytest

from knxscada.alarms import Rule, build_rules, is_critical, normalize_severity


def ga(address, name, dpt="1.001", used=True, placeholder=False):
    return {"address": address, "name": name, "dpt": dpt, "used": used, "placeholder": placeholder}


SMOKE = {"match_address": "0/6/*", "match_name": "(?i)alarm$", "exclude_name": "(?i)acknowledg",
         "condition": "true", "severity": "critical", "text": "Smoke: {name}"}


def test_matches_address_glob_and_name():
    rule = Rule.from_dict(SMOKE)
    assert rule.matches(ga("0/6/1", "Kitchen smoke alarm"))
    assert not rule.matches(ga("0/5/1", "Kitchen smoke alarm"))         # wrong address
    assert not rule.matches(ga("0/6/2", "Kitchen smoke battery"))       # name does not match
    assert not rule.matches(ga("0/6/3", "Acknowledge smoke alarm"))     # excluded


def test_unused_and_placeholder_addresses_are_never_monitored():
    rule = Rule.from_dict(SMOKE)
    assert not rule.matches(ga("0/6/1", "Smoke alarm", used=False))
    assert not rule.matches(ga("0/6/1", "Smoke alarm", placeholder=True))


def test_dpt_main_filter():
    rule = Rule.from_dict({"match_name": "temp", "dpt_main": 9, "condition": "> 30"})
    assert rule.matches(ga("3/0/1", "temp living", "9.001"))
    assert not rule.matches(ga("3/0/2", "temp living alarm", "1.001"))


def test_address_alias():
    assert Rule.from_dict({"address": "1/2/3"}).matches(ga("1/2/3", "x"))


@pytest.mark.parametrize(
    ("condition", "value", "expected"),
    [
        ("true", True, True),
        ("true", False, False),
        ("on", 1, True),
        ("false", False, True),
        ("false", True, False),
        ("> 30", 31.0, True),
        ("> 30", 30.0, False),
        (">= 30", 30, True),
        ("< 5", 4.9, True),
        ("<= 5", 5, True),
        ("== 2", 2, True),
        ("!= 2", 2, False),
        ("> 21,5", 21.6, True),     # decimal comma
        ("< -5", -10, True),        # negative numbers
        ("> 30", True, None),       # on/off cannot be compared with a number
        ("> 30", "hot", None),      # text cannot be judged
        ("true", "on", None),
        ("true", None, None),
        ("bogus", 1, None),         # invalid condition: never an alarm
    ],
)
def test_conditions(condition, value, expected):
    assert Rule.from_dict({"condition": condition}).active(value) is expected


def test_first_matching_rule_wins():
    rules = [
        {"match_name": "(?i)smoke", "severity": "critical", "text": "specific"},
        {"match_name": "(?i)alarm", "severity": "warning", "text": "general"},
    ]
    gas = {"0/6/1": ga("0/6/1", "Smoke alarm"), "0/4/1": ga("0/4/1", "Pump alarm"), "1/1/1": ga("1/1/1", "Light")}
    bound = build_rules(rules, gas)
    assert bound["0/6/1"].text == "specific"
    assert bound["0/4/1"].text == "general"
    assert "1/1/1" not in bound


@pytest.mark.parametrize(("given", "expected"), [("kritisk", "critical"), ("advarsel", "warning"),
                                                  ("Critical", "critical"), (None, "warning"), ("info", "info")])
def test_severity_names(given, expected):
    assert normalize_severity(given) == expected


def test_old_norwegian_severity_in_rule():
    assert Rule.from_dict({"severity": "kritisk"}).severity == "critical"
    assert is_critical({"severity": "kritisk"})
    assert not is_critical({"severity": "advarsel"})


def test_alarm_text():
    rule = Rule.from_dict({"text": "High temperature: {name} = {value} ({address})"})
    assert rule.format_text(name="Living", address="3/0/1", value="31.0 °C") == "High temperature: Living = 31.0 °C (3/0/1)"


@pytest.mark.parametrize("text", ["Alarm {room}", "Broken {", "Index {0}"])
def test_bad_alarm_text_does_not_crash(text):
    # A typo in config.yaml must not stop the alarm from being raised.
    out = Rule.from_dict({"text": text}).format_text(name="Pump", address="0/4/1", value="On")
    assert "Pump" in out
