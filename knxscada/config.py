# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""Loading of ``config.yaml``.

The configuration is a plain YAML file with five sections (``knx``,
``project``, ``server``, ``alarms`` and ``ui``). This module only parses the
file and offers helpers for resolving paths; the meaning of the individual
keys is documented in ``docs/04-configuration.md`` and in
``config.example.yaml``.

Relative paths in the configuration are resolved against the directory that
contains the configuration file, *not* the current working directory. That
makes it possible to keep several installations (one per KNX site) side by
side, each with its own ``var/`` directory.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

#: Used when neither ``-c`` nor ``KNXSCADA_CONFIG`` is given.
DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config.yaml"


@dataclass
class Config:
    """Parsed configuration. Each section is kept as a plain dict."""

    path: Path
    knx: dict[str, Any] = field(default_factory=dict)
    project: dict[str, Any] = field(default_factory=dict)
    server: dict[str, Any] = field(default_factory=dict)
    alarms: list[dict[str, Any]] = field(default_factory=list)
    ui: dict[str, Any] = field(default_factory=dict)

    @property
    def base(self) -> Path:
        """Directory that relative paths are resolved against."""
        return self.path.parent

    def resolve(self, value: str) -> Path:
        """Turn a (possibly relative, possibly ``~``) path into an absolute one."""
        p = Path(os.path.expanduser(value))
        return p if p.is_absolute() else self.base / p

    @property
    def language(self) -> str:
        """UI language (``en`` or ``nb``); English when not configured."""
        return str(self.ui.get("language") or "en")

    @property
    def socket_path(self) -> Path:
        """Unix socket the server listens on and the TUI connects to."""
        return self.resolve(self.server.get("socket", "var/knxscada.sock"))

    @property
    def db_path(self) -> Path:
        """SQLite database with telegrams, events and alarms."""
        return self.resolve(self.server.get("database", "var/knxscada.db"))

    @property
    def project_file(self) -> Path:
        """The ETS project export (``.knxproj``)."""
        return self.resolve(self.project.get("file", "project.knxproj"))

    @property
    def cache_path(self) -> Path:
        """JSON cache of the parsed project (rebuilt when the ``.knxproj`` changes)."""
        return self.resolve(self.project.get("cache", "var/project_cache.json"))


def load_config(path: str | Path | None = None) -> Config:
    """Read the configuration file.

    Lookup order: the explicit *path* argument, the ``KNXSCADA_CONFIG``
    environment variable, and finally ``config.yaml`` next to the package.
    Missing sections are treated as empty.
    """
    path = Path(path or os.environ.get("KNXSCADA_CONFIG") or DEFAULT_CONFIG).resolve()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Config(
        path=path,
        knx=data.get("knx") or {},
        project=data.get("project") or {},
        server=data.get("server") or {},
        alarms=data.get("alarms") or [],
        ui=data.get("ui") or {},
    )
