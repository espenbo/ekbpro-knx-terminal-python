#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""Release helper: version, CHANGELOG.md and git tag kept in step.

    python tools/release.py prepare 0.4.0   # set __version__, turn "Unreleased" in CHANGELOG.md into 0.4.0 – today
    python tools/release.py check           # ready to tag? version = CHANGELOG, clean tree, on main, tag is new
    python tools/release.py notes 0.4.0     # the CHANGELOG section of 0.4.0 (the release notes on GitHub)

The release itself: commit what ``prepare`` changed, ``check``, then ``git tag -a v0.4.0 -m "v0.4.0"`` and
``git push origin v0.4.0``. ``.github/workflows/release.yml`` runs every test on the tag, checks that the tag is the
version in ``knxscada/__init__.py`` and makes the GitHub release with these notes. Nothing here pushes or tags.
"""

from __future__ import annotations

import datetime
import re
import subprocess
import sys
from pathlib import Path

#: The repository (this file is in tools/).
ROOT = Path(__file__).resolve().parent.parent
#: Where ``__version__`` is.
INIT = ROOT / "knxscada" / "__init__.py"
CHANGELOG = ROOT / "CHANGELOG.md"
#: ``__version__ = "X.Y.Z"`` in knxscada/__init__.py.
VERSION_RE = re.compile(r'^__version__ = "([^"]+)"$', re.MULTILINE)
#: A version as written in tags and the changelog: three numbers.
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
#: A version heading in CHANGELOG.md: ``## 0.3.0 – 2026-11-09`` (or ``## Unreleased``).
HEADING_RE = re.compile(r"^## (\S+)", re.MULTILINE)


def version() -> str:
    """The version in knxscada/__init__.py."""
    m = VERSION_RE.search(INIT.read_text(encoding="utf-8"))
    if not m:
        raise SystemExit(f"no __version__ in {INIT}")
    return m.group(1)


def sections(text: str) -> dict[str, str]:
    """CHANGELOG.md → {heading word ("Unreleased", "0.3.0"): body without the heading}."""
    marks = list(HEADING_RE.finditer(text))
    out = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out[m.group(1)] = text[text.index("\n", m.start()) + 1:end].strip()
    return out


def notes(ver: str) -> str:
    """The changelog section of *ver*; SystemExit when there is none (no release without notes)."""
    body = sections(CHANGELOG.read_text(encoding="utf-8")).get(ver)
    if not body:
        raise SystemExit(f"CHANGELOG.md has no section for {ver}")
    return body


def prepare(ver: str, today: str | None = None) -> None:
    """Set ``__version__`` to *ver* and rename the "Unreleased" section to "*ver* – *today*" (a new, empty
    "Unreleased" stays on top). Refuses a malformed version and an empty "Unreleased"."""
    if not SEMVER_RE.match(ver):
        raise SystemExit(f"{ver!r}: write the version as X.Y.Z")
    text = CHANGELOG.read_text(encoding="utf-8")
    if not sections(text).get("Unreleased"):
        raise SystemExit("CHANGELOG.md: nothing under ## Unreleased – write what changed first")
    today = today or datetime.date.today().isoformat()
    text = text.replace("## Unreleased\n", f"## Unreleased\n\n## {ver} – {today}\n", 1)
    CHANGELOG.write_text(text, encoding="utf-8")
    INIT.write_text(VERSION_RE.sub(f'__version__ = "{ver}"', INIT.read_text(encoding="utf-8"), count=1),
                    encoding="utf-8")


def git(*args: str) -> str:
    """Output of a read-only git command in the repository."""
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def problems() -> list[str]:
    """Why the current state cannot be tagged as ``v<version>`` (empty: ready)."""
    ver = version()
    out = []
    if not SEMVER_RE.match(ver):
        out.append(f"__version__ {ver!r} is not X.Y.Z")
    if ver not in sections(CHANGELOG.read_text(encoding="utf-8")):
        out.append(f"CHANGELOG.md has no section for {ver} (run: tools/release.py prepare {ver})")
    if git("status", "--porcelain", "--untracked-files=no"):
        out.append("uncommitted changes")
    if git("rev-parse", "--abbrev-ref", "HEAD") != "main":
        out.append("not on main")
    if git("tag", "--list", f"v{ver}"):
        out.append(f"the tag v{ver} exists already – raise the version")
    return out


def main(argv: list[str]) -> int:
    """Command line: prepare VERSION | check | notes VERSION."""
    if len(argv) >= 2 and argv[0] == "prepare":
        prepare(argv[1])
        print(f"version {argv[1]}: knxscada/__init__.py and CHANGELOG.md changed – commit them, then run check")
        return 0
    if argv[:1] == ["check"]:
        found = problems()
        for p in found:
            print("✗", p)
        if not found:
            print(f"✓ ready: git tag -a v{version()} -m v{version()} && git push origin v{version()}")
        return 1 if found else 0
    if len(argv) >= 2 and argv[0] == "notes":
        print(notes(argv[1].removeprefix("v")))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
