# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""The Docker build context: everything the Dockerfile copies is there, nothing private is."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_dockerfile_sources_exist_and_are_in_the_build_context():
    copies = re.findall(r"^COPY\s+(.+?)\s+\S+\s*$", (ROOT / "Dockerfile").read_text(), re.M)
    sources = [s.rstrip("/") for line in copies for s in line.split()]
    allowed = [line[1:].rstrip("/") for line in (ROOT / ".dockerignore").read_text().splitlines() if line.startswith("!")]
    for src in sources:
        assert (ROOT / src).exists(), src
        assert src in allowed, f"{src} is excluded by .dockerignore"


def test_private_files_are_not_in_the_build_context():
    ignore = (ROOT / ".dockerignore").read_text().splitlines()
    assert ignore[ignore.index("*") if "*" in ignore else 0] == "*"   # everything excluded by default
    allowed = {line[1:] for line in ignore if line.startswith("!")}
    assert not {"config.yaml", "plan.yaml", "var/", "venv/"} & allowed


def test_package_is_found_from_the_data_directory():
    """The container runs `python -m knxscada` in /data while the code is in /app: PYTHONPATH must say so."""
    text = (ROOT / "Dockerfile").read_text()
    workdirs = re.findall(r"^WORKDIR\s+(\S+)", text, re.MULTILINE)
    assert workdirs[-1] == "/data"
    assert re.search(r"PYTHONPATH=/app\b", text)
    assert "COPY knxscada/ knxscada/" in text and workdirs[workdirs.index("/app")] == "/app"
