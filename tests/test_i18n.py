# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""Language selection, the Norwegian catalogue and per-user preferences."""

import importlib.util
import sys
from pathlib import Path

import pytest

from knxscada import i18n, prefs

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize(("given", "expected"), [("nb", "nb"), ("no", "nb"), ("nb_NO.UTF-8", "nb"), ("NB", "nb"),
                                                  ("en", "en"), ("en_GB", "en"), ("xx", None), (None, None)])
def test_normalize_language(given, expected):
    assert i18n.normalize_language(given) == expected


def test_translate_and_fallback():
    assert i18n.set_language("nb") == "nb"
    assert i18n._("Overview") == "Oversikt"
    assert i18n._("Kjøkken") == "Kjøkken"   # ETS names are not in the catalogue: unchanged
    assert i18n.set_language("klingon") == "en"
    assert i18n._("Overview") == "Overview"


def test_n_marker_does_not_translate():
    i18n.set_language("nb")
    assert i18n.N_("Overview") == "Overview"


def test_catalogue_is_complete():
    spec = importlib.util.spec_from_file_location("check_translations", ROOT / "tools" / "check_translations.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    missing, unused = mod.check("nb", mod.source_strings())
    assert missing == [], f"untranslated texts: {missing}"
    assert unused == [], f"catalogue entries no longer used: {unused}"


def test_placeholders_survive_translation():
    """A translation must use the same {placeholders} as the English text."""
    from string import Formatter

    from knxscada.locale.nb import CATALOGUE

    def fields(text):
        return sorted(f for _lit, f, _spec, _conv in Formatter().parse(text) if f)

    for en, nb in CATALOGUE.items():
        assert fields(en) == fields(nb), en


def test_ui_language_precedence(tmp_path, monkeypatch):
    monkeypatch.setattr(prefs, "PREFS", tmp_path / "ui.json")
    assert prefs.ui_language("en") == "en"            # config only
    prefs.save_prefs(language="nb")
    assert prefs.ui_language("en") == "nb"            # the user's choice wins over config
    assert prefs.ui_language("nb", "en") == "en"      # --language wins over everything
    prefs.save_prefs(theme="amber")                   # other preferences are kept
    assert prefs.load_prefs() == {"language": "nb", "theme": "amber"}


def test_broken_prefs_file(tmp_path, monkeypatch):
    monkeypatch.setattr(prefs, "PREFS", tmp_path / "ui.json")
    (tmp_path / "ui.json").write_text("[not a dict", encoding="utf-8")
    assert prefs.load_prefs() == {}


def test_tui_modules_import_in_norwegian():
    """Class-level texts (key bindings) are created at import: importing must work in every language."""
    i18n.set_language("nb")
    for name in [m for m in sys.modules if m.startswith("knxscada.tui")]:
        del sys.modules[name]
    import knxscada.tui.app  # noqa: F401
