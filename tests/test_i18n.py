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
    """Locale strings (nb, no, nb_NO.UTF-8, en_GB …) map to a supported language, unknown ones to None."""
    assert i18n.normalize_language(given) == expected


def test_translate_and_fallback():
    """Norwegian texts come from the catalogue, unknown texts (ETS names) stay unchanged and an unknown language falls
    back to English.
    """
    assert i18n.set_language("nb") == "nb"
    assert i18n._("Overview") == "Oversikt"
    assert i18n._("Kjøkken") == "Kjøkken"   # ETS names are not in the catalogue: unchanged
    assert i18n.set_language("klingon") == "en"
    assert i18n._("Overview") == "Overview"


def test_n_marker_does_not_translate():
    """N_() only marks a text for extraction; translation happens later when it is shown."""
    i18n.set_language("nb")
    assert i18n.N_("Overview") == "Overview"


def test_catalogue_is_complete():
    """Every _() text has a Norwegian entry and the catalogue has no entries that are no longer used."""
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
        """The placeholder names in a format string."""
        return sorted(f for _lit, f, _spec, _conv in Formatter().parse(text) if f)

    for en, nb in CATALOGUE.items():
        assert fields(en) == fields(nb), en


def test_ui_language_precedence(tmp_path, monkeypatch):
    """Language order: --language beats the user's saved choice, which beats config.yaml; saving one preference keeps
    the others.
    """
    monkeypatch.setattr(prefs, "PREFS", tmp_path / "ui.json")
    assert prefs.ui_language("en") == "en"            # config only
    prefs.save_prefs(language="nb")
    assert prefs.ui_language("en") == "nb"            # the user's choice wins over config
    assert prefs.ui_language("nb", "en") == "en"      # --language wins over everything
    prefs.save_prefs(theme="amber")                   # other preferences are kept
    assert prefs.load_prefs() == {"language": "nb", "theme": "amber"}


def test_broken_prefs_file(tmp_path, monkeypatch):
    """A corrupt ui.json is ignored instead of stopping the TUI."""
    monkeypatch.setattr(prefs, "PREFS", tmp_path / "ui.json")
    (tmp_path / "ui.json").write_text("[not a dict", encoding="utf-8")
    assert prefs.load_prefs() == {}


def test_tui_modules_import_in_norwegian():
    """Class-level texts (key bindings) are created at import: importing must work in every language."""
    i18n.set_language("nb")
    for name in [m for m in sys.modules if m.startswith("knxscada.tui")]:
        del sys.modules[name]
    import knxscada.tui.app  # noqa: F401


def test_colour_theme_is_chosen_per_installation(tmp_path, monkeypatch):
    """Colour themes are saved per config file; the installation's ui.theme beats the old global theme, which stays the
    fallback.
    """
    from knxscada import prefs

    monkeypatch.setattr(prefs, "PREFS", tmp_path / "ui.json")
    prefs.save_prefs(theme="amber", language="nb")                 # old format: one theme for everything
    assert prefs.site_theme("/a/config.yaml", "storm") == "storm"  # the installation's ui.theme wins over it
    assert prefs.site_theme("/a/config.yaml", None) == "amber"     # … and it is still the fallback
    prefs.save_site_theme("/a/config.yaml", "modern")
    prefs.save_site_theme("/b/config.yaml", "retro")
    assert prefs.site_theme("/a/config.yaml", "storm") == "modern"
    assert prefs.site_theme("/b/config.yaml", "storm") == "retro"
    assert prefs.load_prefs()["language"] == "nb"                   # the language is untouched
