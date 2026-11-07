# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""``./scada service install|remove|status``: the server as a systemd service.

The unit is made from ``deploy/knxscada.service`` (the hardened template in the repo) for the configuration in use:
the install folder, the user, ``-c <config.yaml>`` when it is not the default one, and ``ReadWritePaths`` = the folder
of the database (``server.database``, normally ``var/``) – the only place the server writes. A site under
``sites/<site>/`` gets the group of its folder (the per-site Unix group, see CLAUDE.md), so the socket (660) can be
used by that group.

Installing needs root. Run as root the commands are executed; otherwise the rendered unit is written to
``var/systemd/<name>.service`` and the ``sudo`` commands are printed, so nothing changes without the user seeing it.
``status`` only reads (``systemctl show``) and works for everyone.
"""

from __future__ import annotations

import grp
import os
import pwd
import re
import shlex
import subprocess
from pathlib import Path

from .config import DEFAULT_CONFIG, Config
from .i18n import _

#: The template in the repo.
TEMPLATE = Path(__file__).resolve().parent.parent / "deploy" / "knxscada.service"
#: Where systemd looks for units installed by the administrator.
UNIT_DIR = Path("/etc/systemd/system")
#: Where the rendered unit is written before it is installed (inside the install folder, so no root is needed).
STAGING = TEMPLATE.parent.parent / "var" / "systemd"
#: Allowed service names: what systemd accepts, kept simple.
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def default_name(cfg: Config) -> str:
    """``knxscada`` for the default config.yaml, ``knxscada-<site>`` for ``sites/<site>/config.yaml`` (the folder name
    otherwise)."""
    if cfg.path.resolve() == DEFAULT_CONFIG.resolve():
        return "knxscada"
    return f"knxscada-{cfg.path.resolve().parent.name}"


def render_unit(template: str, *, install_dir: Path, user: str, group: str, config: Path | None, var_dir: Path,
                description: str = "") -> str:
    """The unit file text: placeholders filled in, ``-c`` added when *config* is given, and the header with the
    manual ``sed`` instructions replaced by a note on how the file was made.

    Pure function (tested without systemd). *var_dir* becomes ``ReadWritePaths``; paths with spaces are quoted.
    """
    body = template[template.index("[Unit]"):]
    exec_start = shlex.quote(str(install_dir / "scada"))
    if config is not None:
        exec_start += f" -c {shlex.quote(str(config))}"
    body = (body.replace("ExecStart=INSTALL_DIR/scada server", f"ExecStart={exec_start} server")
            .replace("ReadWritePaths=INSTALL_DIR/var", f"ReadWritePaths={shlex.quote(str(var_dir))}")
            .replace("INSTALL_DIR", str(install_dir))
            .replace("User=YOUR_USER", f"User={user}")
            .replace("Group=YOUR_USER", f"Group={group}"))
    if description:
        body = body.replace("Description=EKBPRO KNX Terminal Python server",
                            f"Description=EKBPRO KNX Terminal Python server – {description}", 1)
    return "# Made by ./scada service install from deploy/knxscada.service – change the template, not this file.\n" + body


def unit_for(cfg: Config) -> str:
    """The unit for *cfg*, for the user running the command."""
    install_dir = TEMPLATE.parent.parent
    user = pwd.getpwuid(os.getuid()).pw_name if os.getuid() else os.environ.get("SUDO_USER", "root")
    default = cfg.path.resolve() == DEFAULT_CONFIG.resolve()
    # A site folder has its own group (setgid); the default installation uses the user's own group.
    gid = cfg.path.resolve().parent.stat().st_gid
    group = grp.getgrgid(gid).gr_name
    title = str(cfg.ui.get("title") or "")
    return render_unit(TEMPLATE.read_text(encoding="utf-8"), install_dir=install_dir, user=user, group=group,
                       config=None if default else cfg.path.resolve(), var_dir=cfg.db_path.parent,
                       description=title.split("–")[-1].strip() if "–" in title else "")


def _commands(action: str, name: str, staged: Path) -> list[list[str]]:
    """The systemctl/install commands for *action* (``install`` or ``remove``)."""
    unit = UNIT_DIR / f"{name}.service"
    if action == "install":
        # enable + restart (not enable --now): a running service must be restarted to use a changed unit. A restart
        # loads the code on disk – run the tests first (CLAUDE.md).
        return [["install", "-m", "644", str(staged), str(unit)], ["systemctl", "daemon-reload"],
                ["systemctl", "enable", name], ["systemctl", "restart", name]]
    return [["systemctl", "disable", "--now", name], ["rm", "-f", str(unit)], ["systemctl", "daemon-reload"]]


def run(cfg: Config, action: str, name: str | None = None) -> int:
    """``service ACTION``. Returns the exit code for the command line."""
    name = name or default_name(cfg)
    if not NAME_RE.match(name):
        print(_("Invalid service name {name}").format(name=repr(name)))
        return 1
    if action == "status":
        return status(name)
    staged = STAGING / f"{name}.service"
    if action == "install":
        staged.parent.mkdir(parents=True, exist_ok=True)
        staged.write_text(unit_for(cfg), encoding="utf-8")
        print(_("Wrote {path}").format(path=staged))
        installed = UNIT_DIR / f"{name}.service"
        if installed.exists() and installed.read_text(encoding="utf-8") != staged.read_text(encoding="utf-8"):
            print(_("Note: {path} exists and differs – it is replaced.").format(path=installed))
    cmds = _commands(action, name, staged)
    if os.geteuid() != 0:
        print(_("Run as root:"))
        for c in cmds:
            print("  sudo " + shlex.join(c))
        if action == "install":
            print(_("Log: journalctl -u {name} -f").format(name=name))
        return 0
    for c in cmds:
        print("+ " + shlex.join(c))
        if subprocess.run(c, check=False).returncode != 0 and c[0] != "rm":
            return 1
    return 0


def status(name: str) -> int:
    """Print whether the service is installed, enabled and running (``systemctl show``, read-only)."""
    try:
        out = subprocess.run(["systemctl", "show", name, "--no-pager",
                              "-p", "LoadState,ActiveState,SubState,UnitFileState,ExecMainStartTimestamp,MainPID"],
                             capture_output=True, text=True, check=False).stdout
    except FileNotFoundError:
        print(_("systemctl not found – this system does not use systemd."))
        return 1
    props = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    if props.get("LoadState") == "not-found":
        print(_("{name}: not installed").format(name=name))
        return 3
    print(_("{name}: {active} ({sub}), {enabled}, since {since}, PID {pid}").format(
        name=name, active=props.get("ActiveState", "?"), sub=props.get("SubState", "?"),
        enabled=props.get("UnitFileState", "?"), since=props.get("ExecMainStartTimestamp") or "–",
        pid=props.get("MainPID", "–")))
    return 0 if props.get("ActiveState") == "active" else 3
