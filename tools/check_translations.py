#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""List untranslated and unused strings in the translation catalogues.

Every string literal passed to ``_()`` or ``N_()`` in ``knxscada/`` is a
translatable text. For each catalogue in ``knxscada/locale/`` this prints

* **missing** – texts in the source without a translation, and
* **unused**  – catalogue entries no longer found in the source.

Usage::

    venv/bin/python tools/check_translations.py [LANG ...]

Exit status 1 when any text is missing (handy in CI and in the test suite).
"""

from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "knxscada"
MARKERS = {"_", "N_"}


def source_strings() -> dict[str, list[str]]:
    """Translatable text → list of ``file:line`` where it occurs."""
    found: dict[str, list[str]] = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        if "locale" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in MARKERS
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                where = f"{path.relative_to(ROOT)}:{node.lineno}"
                found.setdefault(node.args[0].value, []).append(where)
    return found


def check(language: str, strings: dict[str, list[str]]) -> tuple[list[str], list[str]]:
    """Return (missing, unused) for one catalogue."""
    catalogue = importlib.import_module(f"knxscada.locale.{language}").CATALOGUE
    missing = [s for s in strings if s not in catalogue]
    unused = [s for s in catalogue if s not in strings]
    return missing, unused


def main(argv: list[str]) -> int:
    sys.path.insert(0, str(ROOT))
    from knxscada.i18n import LANGUAGES

    languages = argv or [code for code in LANGUAGES if code != "en"]
    strings = source_strings()
    status = 0
    for lang in languages:
        missing, unused = check(lang, strings)
        print(f"[{lang}] {len(strings)} texts, {len(missing)} missing, {len(unused)} unused")
        for s in missing:
            print(f"  missing: {s!r}  ({strings[s][0]})")
        for s in unused:
            print(f"  unused:  {s!r}")
        if missing:
            status = 1
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
