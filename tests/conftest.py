# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""Shared test set-up: import the package from the source tree, English UI."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from knxscada.i18n import set_language  # noqa: E402


@pytest.fixture(autouse=True)
def english():
    """Every test starts (and ends) with the English source texts."""
    set_language("en")
    yield
    set_language("en")
