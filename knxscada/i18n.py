# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""Minimal translation support.

All user-facing text in the source code is written in English and wrapped in
:func:`_`. When the configured language is not English, :func:`_` looks the
text up in a catalogue (``knxscada/locale/<lang>.py``) and returns the
translation, falling back to the English original if no translation exists.

Why not :mod:`gettext`? The program has a single, small catalogue that is
edited by hand, and a plain Python dict keeps the toolchain at zero
(no ``.po``/``.mo`` compilation step) while still making missing
translations easy to find with ``tools/check_translations.py``.

Important: call :func:`_` when text is *displayed*, not at import time.
Module-level constants therefore hold the English text, marked with
:func:`N_` so the checker finds them, and the code that renders them wraps
them in ``_()``. That way the language can be chosen after the modules have
been imported (it is read from ``config.yaml`` and the user's preferences).
"""

from __future__ import annotations

import importlib
import logging

#: Languages that have a catalogue. ``en`` is the source language.
LANGUAGES = {"en": "English", "nb": "Norsk bokmål"}

_language = "en"
_catalogue: dict[str, str] = {}


def normalize_language(language: str | None) -> str | None:
    """``"nb_NO.UTF-8"`` → ``"nb"``, ``"no"`` → ``"nb"``; None for unknown languages."""
    code = (language or "").strip().lower().split(".")[0].split("_")[0].split("-")[0]
    if code == "no":  # "no" (Norwegian) is commonly used for bokmål
        code = "nb"
    return code if code in LANGUAGES else None


def set_language(language: str | None) -> str:
    """Select the language used by :func:`_`. Returns the language actually used.

    Unknown languages (or a missing catalogue) fall back to English so a
    typo in ``config.yaml`` never prevents the program from starting.
    """
    global _language, _catalogue
    code = normalize_language(language) or "en"
    catalogue: dict[str, str] = {}
    if code != "en":
        try:
            catalogue = importlib.import_module(f"knxscada.locale.{code}").CATALOGUE
        except (ImportError, AttributeError):
            logging.getLogger(__name__).warning("No translation catalogue for %r – using English", code)
            code = "en"
    _language, _catalogue = code, catalogue
    return _language


def get_language() -> str:
    """Return the active language code (``en`` or ``nb``)."""
    return _language


def _(text: str) -> str:
    """Translate *text* into the active language (identity for English)."""
    if not _catalogue:
        return text
    return _catalogue.get(text, text)


def N_(text: str) -> str:  # noqa: N802 - conventional gettext name
    """Mark *text* for translation without translating it (for module-level constants)."""
    return text
