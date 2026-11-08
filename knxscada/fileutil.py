# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""Writing files that may hold passwords: ``config.yaml`` (``./scada init``, ``./scada setup``), ``protocols.yaml``
(F11, ``protocols_save``) and their ``.bak`` copies.

Such a file is never readable by others, not even for a moment: it is created with its final mode (``os.open`` with
the mode, then ``fchmod`` – the umask can only take bits away) as a temporary file next to the target and moved into
place with ``os.replace``, so a reader sees either the old or the new file. The ``.bak`` copy gets the same mode as
the file it copies (before, it was written with the umask, typically 0644, and the old passwords were readable by
every local user).

A file that exists keeps its group bits (a site directory is shared by its Unix group, ``config.yaml`` 640); the
"other" bits are always removed. A new file is 0600.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

#: Mode of a new private file: the owner only.
NEW_MODE = 0o600


def private_mode(path: Path) -> int:
    """The mode to write *path* with: its current owner and group bits (never "other"), or :data:`NEW_MODE` when it
    does not exist yet."""
    try:
        return path.stat().st_mode & 0o660 or NEW_MODE
    except FileNotFoundError:
        return NEW_MODE


def write_private(path: Path, data: str | bytes, mode: int | None = None) -> None:
    """Write *data* (text as UTF-8) to *path* atomically, created with *mode* (default :func:`private_mode`).

    Raises OSError like ``Path.write_text``; the temporary file is removed then.
    """
    mode = private_mode(path) if mode is None else mode
    raw = data.encode("utf-8") if isinstance(data, str) else data
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        os.fchmod(fd, mode)      # also when an old temporary file was left with other bits
        with os.fdopen(fd, "wb") as f:
            fd = -1              # closed by the file object from here on
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if fd >= 0:
            os.close(fd)
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def backup_private(path: Path) -> Path | None:
    """Copy *path* to ``<name>.bak`` with the same restricted mode (called before the file is replaced). Returns the
    backup's path, or None when *path* does not exist."""
    if not path.exists():
        return None
    bak = path.with_name(path.name + ".bak")
    write_private(bak, path.read_bytes(), private_mode(path))
    return bak
