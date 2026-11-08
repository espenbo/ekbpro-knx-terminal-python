# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""``./scada setup``: a question-and-answer set-up of a new installation in the terminal.

It asks for the language, whether KNX is used and then for the ETS project (a file, ``user@host:path`` fetched with
``scp``, or an ``http(s)://`` URL), its password (asked again when wrong), the connection (KNX IP interfaces found
with a search, KNX USB interfaces, an address typed in, routing or automatic) and an optional KNX Secure keyring. It
installs the protocol modules chosen, can search the network for BACnet devices and for Modbus TCP and OPC UA servers
(port 502/4840 open), writes ``config.yaml`` (with :func:`knxscada.__main__.render_config`, the same as ``init``),
imports the project and offers ``./scada service install``.

Nothing is sent on the KNX bus: the KNX search is the KNXnet/IP search request (a multicast the interfaces answer),
the project is read from the file. Equipment found is only listed, with the command that takes it further – the
connections themselves are set up in F11 or ``protocols.yaml``, where writing stays off (``writable: false``) until
it has been agreed.

The questions go through :class:`Wizard`'s ``ask``/``say`` and the searches through replaceable functions, so the
flow is tested without a terminal or network (``tests/test_setup_wizard.py``).
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import os
import shlex
import socket
import subprocess
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .fileutil import backup_private, write_private
from .i18n import _, set_language

#: TCP ports searched for in the equipment search: Modbus TCP and OPC UA.
EQUIPMENT_PORTS = {"modbus": 502, "opcua": 4840}
#: Seconds per address in the port search (a /24 network is searched in parallel, so the whole search is ~1 s).
PORT_TIMEOUT_S = 0.6
#: Wrong project passwords accepted before the wizard gives up.
PASSWORD_TRIES = 3


# -- searches (replaced in tests) ------------------------------------------------------------------------------------
def scan_knx_ip(timeout_s: float = 3.0) -> list[dict[str, Any]]:
    """KNX IP interfaces that answer a KNXnet/IP search request: ``[{"ip", "name", "tunnelling", "routing", "secure"}]``.

    A multicast search the interfaces answer – no telegram on the bus. Empty when xknx is not installed.
    """
    try:
        from xknx import XKNX
        from xknx.io import GatewayScanner
    except ImportError:
        return []

    async def run() -> list[Any]:
        """One scan with a throw-away XKNX object (it is never started)."""
        return await GatewayScanner(XKNX(), timeout_in_seconds=timeout_s).scan()

    try:
        found = asyncio.run(run())
    except OSError:
        return []
    return [{"ip": g.ip_addr, "name": g.name, "tunnelling": bool(g.supports_tunnelling or g.supports_tunnelling_tcp),
             "routing": bool(g.supports_routing), "secure": bool(g.supports_secure)} for g in found]


def scan_usb() -> list[Any]:
    """KNX USB interfaces (see :func:`knxscada.knx_usb.find_devices`)."""
    from .knx_usb import find_devices

    return find_devices()


def local_network() -> ipaddress.IPv4Network | None:
    """The /24 network of this machine's address towards the default route (no packet is sent)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("192.0.2.1", 9))          # TEST-NET: only picks the outgoing interface
            addr = s.getsockname()[0]
        except OSError:
            return None
    return ipaddress.ip_network(f"{addr}/24", strict=False)


def scan_ports(network: ipaddress.IPv4Network, port: int, timeout_s: float = PORT_TIMEOUT_S) -> list[str]:
    """Addresses in *network* with TCP *port* open (connect only, nothing is read or written)."""
    async def probe(host: str) -> str | None:
        """Try one address."""
        try:
            _r, w = await asyncio.wait_for(asyncio.open_connection(host, port), timeout_s)
        except (OSError, TimeoutError):
            return None
        w.close()
        with contextlib.suppress(OSError):
            await w.wait_closed()
        return host

    async def run() -> list[str]:
        """Probe every host address at once."""
        hits = await asyncio.gather(*(probe(str(h)) for h in network.hosts()))
        return [h for h in hits if h]

    return asyncio.run(run())


def scan_bacnet(timeout_s: float = 3.0) -> list[dict[str, Any]]:
    """BACnet devices that answer Who-Is on this network (empty when the BACnet module is not installed)."""
    from .protocols import installed

    if not installed("bacnet"):
        return []
    from .protocols import bacnet

    async def run() -> list[dict[str, Any]]:
        """Who-Is from a temporary BACnet device of our own on port 47808 (as ``bacnet-scan``), then close it."""
        app = bacnet.make_app("host:47808", bacnet.MAX_INSTANCE, "knxscada setup", timeout_s)
        try:
            await asyncio.sleep(0)                  # let the UDP endpoint open
            return await asyncio.wait_for(bacnet.scan_devices(app, timeout_s), timeout_s + 5)
        finally:
            app.close()

    try:
        return asyncio.run(run())
    except Exception:  # noqa: BLE001 - no network, port 47808 in use …: the search is only a help
        return []


def open_project(path: Path, password: str | None) -> str:
    """Read the ETS project to check it (and its password); returns the project name.

    Raises ``PermissionError`` for a missing or wrong password, ``ValueError`` for a file that is not a project.
    """
    from xknxproject import XKNXProj
    from xknxproject.exceptions import InvalidPasswordException, XknxProjectException

    try:
        raw = XKNXProj(str(path), password=password or None).parse()
    except InvalidPasswordException as exc:
        raise PermissionError(str(exc)) from exc
    except XknxProjectException as exc:
        raise ValueError(str(exc)) from exc
    return str(raw.get("info", {}).get("name") or path.stem)


def fetch(source: str, folder: Path) -> Path:
    """The project file for *source*: a local path, ``user@host:path`` (copied with scp) or an http(s) URL
    (downloaded). Copies are saved in *folder*. Raises ``FileNotFoundError`` with the reason."""
    source = source.strip()
    if source.startswith(("http://", "https://")):
        target = folder / (Path(source.split("?", 1)[0]).name or "project.knxproj")
        try:
            # The URL is the one the user typed in: http(s) only (checked above).
            with urllib.request.urlopen(source, timeout=60) as r, target.open("wb") as f:
                f.write(r.read())
        except OSError as exc:
            raise FileNotFoundError(f"{source}: {exc}") from exc
        return target
    if ":" in source and "@" in source.split(":", 1)[0] and not Path(source).exists():
        target = folder / Path(source.split(":", 1)[1]).name
        if subprocess.call(["scp", source, str(target)]) != 0 or not target.exists():
            raise FileNotFoundError(_("scp could not copy {source}").format(source=source))
        return target
    path = Path(os.path.abspath(Path(source).expanduser()))
    if not path.exists():
        raise FileNotFoundError(_("Cannot find {path}").format(path=path))
    return path


# -- the wizard ------------------------------------------------------------------------------------------------------
class Wizard:
    """The questions in order (:meth:`run`); *ask* and *say* talk to the user, the other arguments do the searches."""

    def __init__(self, target: Path, *, ask: Callable[[str], str] = input, say: Callable[[str], None] = print,
                 knx_ip: Callable[[], list] = scan_knx_ip, usb: Callable[[], list] = scan_usb,
                 bacnet: Callable[[], list] = scan_bacnet,
                 ports: Callable[[Any, int], list[str]] = scan_ports,
                 network: Callable[[], Any] = local_network,
                 project: Callable[[Path, str | None], str] = open_project,
                 install: Callable[[list[str]], int] | None = None) -> None:
        """*target* is the config.yaml to write."""
        self.target = target
        self.ask_raw, self.say = ask, say
        self.knx_ip, self.usb, self.bacnet, self.ports, self.network = knx_ip, usb, bacnet, ports, network
        self.project = project
        if install is None:
            from .protocols import install as install_modules

            install = install_modules
        self.install = install
        #: The answers, passed to render_config at the end.
        self.answers: dict[str, Any] = {}

    # small helpers -------------------------------------------------------------------------------------------------
    def ask(self, question: str, default: str = "") -> str:
        """One question; Enter gives *default* (shown in brackets)."""
        hint = f" [{default}]" if default else ""
        try:
            answer = self.ask_raw(f"{question}{hint}: ").strip()
        except EOFError:          # no terminal (piped): take the defaults
            answer = ""
        return answer or default

    def yes(self, question: str, default: bool = False) -> bool:
        """A yes/no question (y/j/yes/ja = yes)."""
        answer = self.ask(question, _("y") if default else _("n")).lower()
        return answer[:1] in ("y", "j")

    def choose(self, question: str, options: list[str], default: int = 1) -> int:
        """A numbered list; returns the 1-based number chosen (asked again for an invalid answer)."""
        for i, text in enumerate(options, 1):
            self.say(f"  {i}) {text}")
        while True:
            answer = self.ask(question, str(default))
            if answer.isdigit() and 1 <= int(answer) <= len(options):
                return int(answer)
            self.say(_("Answer with a number from 1 to {n}.").format(n=len(options)))

    # steps -------------------------------------------------------------------------------------------------------
    def run(self) -> int:
        """All steps; returns the exit code (0 = written)."""
        lang = self.ask("Language / Språk (en, nb)", "en")
        self.answers["language"] = lang if lang in ("en", "nb") else "en"
        set_language(self.answers["language"])
        self.say(_("This sets up {path}. Enter takes the suggestion in brackets.").format(path=self.target))
        if self.target.exists() and not self.yes(_("{path} exists. Replace it (a .bak copy is kept)?").format(
                path=self.target)):
            return 1
        knx = self.yes(_("Is KNX used?"), True)
        if knx and (self._project() != 0 or self._connection() != 0):
            return 1
        self._modules(knx)
        self._equipment()
        return self._write(knx)

    def _project(self) -> int:
        """The ETS project and its password (tried before anything is written)."""
        while True:
            source = self.ask(_("ETS project (.knxproj/.knxprojarchive): file, user@host:path or URL"))
            if not source:
                return 1
            try:
                path = fetch(source, self.target.parent)
            except FileNotFoundError as exc:
                self.say(str(exc))
                continue
            break
        password = None
        # First without a password, then up to PASSWORD_TRIES typed ones; after the last wrong one, stop.
        for attempt in range(PASSWORD_TRIES + 1):
            try:
                name = self.project(path, password)
            except PermissionError:
                if attempt == PASSWORD_TRIES:
                    break
                password = self.ask(_("Password of the project (wrong or missing)"))
                continue
            except ValueError as exc:
                self.say(_("{path} is not an ETS project: {error}").format(path=path, error=exc))
                return 1
            self.say(_("Project: {name}").format(name=name))
            self.answers.update(project_path=path, project_name=name, password=password)
            return 0
        self.say(_("The password was not accepted."))
        return 1

    def _connection(self) -> int:
        """How the server reaches KNX: a found IP interface, a USB interface, an address, routing or automatic."""
        self.say(_("Searching for KNX IP interfaces …"))
        found = self.knx_ip()
        usb = self.usb()
        options, values = [], []
        for g in found:
            kinds = ", ".join(k for k, on in (("tunneling", g["tunnelling"]), ("routing", g["routing"]),
                                              ("secure", g["secure"])) if on)
            options.append(f"{g['ip']}  {g['name']}  ({kinds})")
            values.append({"connection": "tunneling" if g["tunnelling"] else "routing", "gateway": g["ip"]})
        for d in usb:
            options.append(f"USB {d.name}  ({d.usb_id}, {d.serial or d.path})")
            values.append({"connection": "usb", "usb_device": d.serial or d.path})
        options += [_("Type the address of a KNX IP interface"), _("Routing (KNX IP router, multicast)"),
                    _("Automatic search at every start")]
        n = self.choose(_("KNX connection"), options)
        if n <= len(values):
            self.answers.update(values[n - 1])
        elif n == len(values) + 1:
            self.answers.update(connection="tunneling", gateway=self.ask(_("IP address of the KNX IP interface")))
        elif n == len(values) + 2:
            self.answers.update(connection="routing")
        else:
            self.answers.update(connection="automatic")
        if self.answers["connection"] != "usb" and self.yes(_("KNX Secure (keyring from ETS)?")):
            keyring = Path(os.path.abspath(Path(self.ask(_("Keyring file (.knxkeys)"))).expanduser()))
            if not keyring.exists():
                self.say(_("Cannot find {path}").format(path=keyring))
                return 1
            self.answers.update(keyring=keyring, keyring_password=self.ask(_("Password of the keyring")),
                                connection="tunneling_secure" if self.answers.get("gateway") else "routing_secure")
        return 0

    def _modules(self, knx: bool) -> None:
        """Which protocol modules to install (KNX is suggested when KNX is used)."""
        from .protocols import PROTOCOLS, installed

        names = [p.name for p in PROTOCOLS.values() if not p.planned and p.module is not None]
        self.say(_("Protocol modules: {names}").format(names=", ".join(names)))
        default = "knx" if knx else ""
        wanted = self.ask(_("Modules to use (space between)"), default).replace(",", " ").split()
        wanted = [w for w in wanted if w in names]
        if knx and "knx" not in wanted:
            wanted.insert(0, "knx")
        missing = [w for w in wanted if not installed(w)]
        if missing:
            self.say(_("Installing: {names}").format(names=", ".join(missing)))
            if self.install(missing) != 0:
                self.say(_("Installation failed – try again later with ./scada modules install {names}").format(
                    names=" ".join(missing)))
        self.answers["modules"] = wanted

    def _equipment(self) -> None:
        """Search for BACnet devices and Modbus TCP / OPC UA servers on this network; only lists what answers."""
        modules = self.answers.get("modules") or []
        if "bacnet" in modules and self.yes(_("Search for BACnet devices (Who-Is)?")):
            devices = self.bacnet()
            for d in devices:
                self.say(f"  BACnet {d.get('address')}  {_('device')} {d.get('instance')}  {d.get('name') or ''}")
            if devices:
                self.say(_("Objects of a device: ./scada bacnet-scan ADDRESS --yaml"))
            else:
                self.say(_("No BACnet device answered (Who-Is does not pass a VPN: use ./scada bacnet-scan ADDRESS)."))
        for proto in ("modbus", "opcua"):
            if proto not in modules:
                continue
            port = EQUIPMENT_PORTS[proto]
            net = self.network()
            if net is None or not self.yes(_("Search {network} for {protocol} (port {port})?").format(
                    network=net, protocol=proto, port=port)):
                continue
            hits = self.ports(net, port)
            for host in hits:
                self.say(f"  {proto}  {host}:{port}")
            if not hits:
                self.say(_("Nothing answered on port {port}.").format(port=port))
        if any(p in modules for p in ("bacnet", "modbus", "opcua", "mqtt")):
            self.say(_("Set up the connections in the TUI (F11) or in protocols.yaml – tags are read-only until "
                       "writing has been agreed (writable: true)."))

    def _write(self, knx: bool) -> int:
        """Write config.yaml (a .bak copy of an old one), import the project and offer the systemd service."""
        from . import PRODUCT
        from .__main__ import _yaml_str, render_config
        from .config import load_config

        a = self.answers
        self.target.parent.mkdir(parents=True, exist_ok=True)
        backup_private(self.target)       # the old config may hold passwords: the copy is as private as the file
        if knx:
            template = (Path(__file__).resolve().parent.parent / "config.example.yaml").read_text(encoding="utf-8")
            project = a["project_path"]
            with contextlib.suppress(ValueError):
                project = project.relative_to(self.target.parent)
            keyring = a.get("keyring")
            if keyring is not None:
                with contextlib.suppress(ValueError):
                    keyring = keyring.relative_to(self.target.parent)
            text = render_config(template, project=str(project), title=f"{PRODUCT} – {a['project_name']}",
                                 language=a["language"], connection=a.get("connection"), gateway=a.get("gateway"),
                                 password=a.get("password"), keyring=str(keyring) if keyring else None,
                                 keyring_password=a.get("keyring_password"), usb_device=a.get("usb_device"))
        else:
            text = (f"# Made by ./scada setup – a site without KNX: connections and gateways are in protocols.yaml.\n"
                    f"knx:\n  enabled: false\nui:\n  language: {a['language']}\n  title: {_yaml_str(PRODUCT)}\n")
        write_private(self.target, text)  # may hold the project, keyring and PLC passwords
        self.say(_("Wrote {path}").format(path=self.target))
        cfg = load_config(self.target)
        if knx:
            from .project import load_project

            model = load_project(cfg, force=True)
            self.say(_("Imported: {n} group addresses, {rooms} rooms.").format(
                n=len(model["gas"]), rooms=sum(len(f["rooms"]) for f in model["floors"])))
        if self.yes(_("Run the server as a systemd service?")):
            from .service import run as service_run

            service_run(cfg, "install")
        config = "" if self.target.resolve() == Path("config.yaml").resolve() else \
            f" -c {shlex.quote(str(self.target))}"
        self.say(_("Done. Start the server with ./scada{config} server and the visualisation with ./scada{config}")
                 .format(config=config))
        return 0
