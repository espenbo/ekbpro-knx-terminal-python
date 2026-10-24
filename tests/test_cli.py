# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""Command line helpers."""

import pytest
import yaml

from knxscada.__main__ import _yaml_str


@pytest.mark.parametrize("value", ['pa"ss', "back\\slash", "colon: hash #", "Storgata «1»"])
def test_yaml_str_round_trip(value):
    assert yaml.safe_load(f"key: {_yaml_str(value)}")["key"] == value


def test_example_config_has_all_placeholders():
    from knxscada.config import DEFAULT_CONFIG

    text = DEFAULT_CONFIG.with_name("config.example.yaml").read_text(encoding="utf-8")
    for placeholder in ("__CONNECTION__", "__GATEWAY__", "__PROJECT__", "__TITLE__", "__LANGUAGE__"):
        assert placeholder in text
