# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""tools/release.py: version and changelog kept in step, release notes, and the repository's own state."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from knxscada import PRODUCT, __version__

ROOT = Path(__file__).resolve().parent.parent


def load():
    """tools/release.py as a module (tools/ is not a package)."""
    spec = importlib.util.spec_from_file_location("release", ROOT / "tools" / "release.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_repository_is_consistent():
    """The program's version has a section in CHANGELOG.md, so a tag of it gets release notes; ./scada --version
    prints it."""
    rel = load()
    assert rel.version() == __version__
    assert rel.SEMVER_RE.match(__version__)
    assert "Added" in rel.notes(__version__)
    out = subprocess.run([sys.executable, "-m", "knxscada", "--version"], cwd=ROOT, capture_output=True, text=True,
                         check=True).stdout
    assert out.strip() == f"{PRODUCT} {__version__}"


def test_prepare_and_notes(tmp_path, monkeypatch):
    """prepare turns "Unreleased" into the new version (with an empty Unreleased on top) and sets __version__;
    a malformed version and an empty Unreleased are refused; notes gives exactly one version's section."""
    rel = load()
    init, log = tmp_path / "__init__.py", tmp_path / "CHANGELOG.md"
    init.write_text('PRODUCT = "x"\n__version__ = "0.3.0"\n', encoding="utf-8")
    log.write_text("# Changelog\n\n## Unreleased\n\n### Fixed\n\n- a bug\n\n## 0.3.0 – 2026-11-09\n\n- first\n",
                   encoding="utf-8")
    monkeypatch.setattr(rel, "INIT", init)
    monkeypatch.setattr(rel, "CHANGELOG", log)
    with pytest.raises(SystemExit, match="X.Y.Z"):
        rel.prepare("0.4")
    rel.prepare("0.4.0", today="2026-12-01")
    assert rel.version() == "0.4.0"
    assert rel.notes("0.4.0") == "### Fixed\n\n- a bug"
    assert rel.notes("v0.3.0".removeprefix("v")) == "- first"
    assert "## Unreleased\n\n## 0.4.0 – 2026-12-01" in log.read_text(encoding="utf-8")
    with pytest.raises(SystemExit, match="nothing under"):
        rel.prepare("0.5.0")
    with pytest.raises(SystemExit, match="no section"):
        rel.notes("9.9.9")
