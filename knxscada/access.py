# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""Who may do what over the server's Unix socket: three access levels per Linux account.

The socket (``server.socket``, mode 660) decides who can connect at all: the owner and the group. Within that,
``server.access`` in ``config.yaml`` gives each account a level::

    server:
      access:
        configure: [caesar]          # also change protocols.yaml (F11) and reload it
        operate: ["@tomteveien30"]   # also write values, send KNX read requests, acknowledge alarms
        read: ["*"]                  # look only

Entries are account names, ``@group`` (a Unix group, primary or supplementary) or ``*`` (every account that can
open the socket). An account gets the highest level that lists it; an account listed nowhere is refused when it
connects. Without ``server.access`` everyone who can open the socket has every level, as before this module –
so an update never locks anyone out.

The account comes from the kernel (``SO_PEERCRED``), never from the client. The server's own account and root always
have every level: they can read the configuration and the database anyway. :func:`level_of` is called once per
connection by :class:`knxscada.server.ScadaServer`, :func:`check` for every command.
"""

from __future__ import annotations

import grp
import os
import pwd
from typing import Any

from .i18n import N_, _

#: The levels, lowest first. Each includes everything of the levels before it.
LEVELS = ("read", "operate", "configure")
#: Level → its name for messages (translated when shown).
LEVEL_NAMES = {"read": N_("read"), "operate": N_("operate"), "configure": N_("configure")}

#: Commands that need more than ``read`` → the lowest level allowed to send them. Every other command (snapshots,
#: history, tag lists, a BACnet priority array) only reads. ``read`` sends a KNX read request onto the bus, so it is
#: an operator's command like ``write``.
COMMAND_LEVEL = {
    "write": "operate",
    "read": "operate",
    "ack": "operate",
    "point_write": "operate",
    "protocols_save": "configure",
    "protocols_reload": "configure",
}


def groups_of(name: str) -> set[str]:
    """The Unix groups of account *name* (primary and supplementary); empty when the account is unknown."""
    try:
        gid = pwd.getpwnam(name).pw_gid
        return {grp.getgrgid(g).gr_name for g in os.getgrouplist(name, gid)}
    except (KeyError, OSError):
        return set()


def matches(entry: Any, name: str, groups: set[str]) -> bool:
    """Whether one entry of an access list (``name``, ``@group`` or ``*``) covers the account *name*."""
    entry = str(entry).strip()
    if entry == "*":
        return True
    if entry.startswith("@"):
        return entry[1:] in groups
    return entry == name


def level_of(access: dict[str, Any] | None, uid: int | None, name: str | None) -> str | None:
    """The access level of the account (*uid*, *name*) that connected, or None when it may not connect.

    *access* is ``server.access`` (None or empty: everyone gets ``configure``). *uid* None means the kernel could
    not tell (not Linux): then only an empty *access* lets the client in, since nothing can be checked.
    """
    if not access:
        return "configure"
    if uid is not None and uid in (0, os.getuid()):
        return "configure"        # root and the server's own account can read config and database anyway
    if uid is None or not name:
        return None
    groups = groups_of(name)
    for level in reversed(LEVELS):
        entries = access.get(level) or []
        if isinstance(entries, str):
            entries = [entries]
        if any(matches(e, name, groups) for e in entries):
            return level
    return None


def allowed(level: str, cmd: Any) -> bool:
    """Whether *level* may send the command *cmd*."""
    need = COMMAND_LEVEL.get(str(cmd), "read")
    return LEVELS.index(level) >= LEVELS.index(need)


def check(level: str, cmd: Any, user: str) -> None:
    """Raise PermissionError when *level* may not send *cmd* (the client gets the message as the command's error)."""
    if not allowed(level, cmd):
        raise PermissionError(_("{user} may not do this (access: {level}, needed: {need})").format(
            user=user, level=_(LEVEL_NAMES[level]), need=_(LEVEL_NAMES[COMMAND_LEVEL.get(str(cmd), "read")])))


def effective(access: Any) -> dict[str, Any]:
    """``server.access`` as :func:`level_of` uses it. A value that is not a mapping is a mistake that must not open
    the socket to everyone, so it becomes "nobody but the server's account and root" (``{"configure": []}``)."""
    if access in (None, {}):
        return {}
    if not isinstance(access, dict):
        return {"configure": []}
    return access


def validate(access: Any) -> list[str]:
    """Problems with ``server.access``: not a mapping, an unknown level, or entries that are not a list of names."""
    if access in (None, {}):
        return []
    if not isinstance(access, dict):
        return [_("server.access must be a mapping of levels (read, operate, configure) to lists of accounts")]
    out = []
    for key, entries in access.items():
        if key not in LEVELS:
            out.append(_("server.access: unknown level {level} (read, operate, configure)").format(level=repr(key)))
        elif not isinstance(entries, (list, str)):
            out.append(_("server.access.{level}: write a list of accounts, @groups or *").format(level=key))
    return out
