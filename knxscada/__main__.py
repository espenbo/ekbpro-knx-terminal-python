# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""Command line entry point: ``python -m knxscada <command>`` (or ``./scada <command>``).

Commands
--------
``tui`` (default)  start the terminal user interface (a client of the server)
``server``         start the server: KNX connection, logging, alarms, socket API
``init``           create ``config.yaml`` for a new ETS project
``import``         re-read the ETS project(s) and rebuild the cache
``info``           print a summary of the parsed project
``plan-dxf``       export the floor plan to DXF for CAD programs
``plc-map``        create/update the PLC mapping (plc_map.yaml) and the CODESYS GVL
``plc-browse``     list the variables of the PLC's OPC UA server
``modules``        list the protocol modules; ``modules install modbus mqtt`` installs their packages
``gateway``        check ``protocols.yaml`` and show the connections and gateways (live when the server runs)
``simulate``       play a device template as a Modbus slave, for tests without equipment
``bacnet-scan``    find BACnet devices (Who-Is), or list the objects of one device

The argparse help texts are English on purpose: they are printed before the
configuration (and therefore the UI language) is known. Everything printed
after the configuration has been loaded is translated.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from . import PRODUCT
from .config import Config, load_config
from .i18n import LANGUAGES, _, set_language


def main(argv: list[str] | None = None) -> int:
    """The ``./scada`` command line: parse *argv* (default ``sys.argv``), run the sub-command, return the exit code.

    Without a sub-command the TUI starts. ``server`` runs the SCADA server in the foreground (systemd runs it
    so); the other commands are one-off tools (project import, PLC map, protocol modules, scans, simulators).
    """
    ap = argparse.ArgumentParser(prog="knxscada", description=f"{PRODUCT} – terminal SCADA for KNX")
    ap.add_argument("-c", "--config", help="path to config.yaml (or set KNXSCADA_CONFIG)")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("server", help="start the SCADA server (KNX connection, logging, alarms)")
    p.add_argument("--demo", action="store_true", help="simulate the bus instead of connecting to KNX")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    p = sub.add_parser("tui", help="start the terminal user interface (default)")
    p.add_argument("--language", choices=sorted(LANGUAGES),
                   help="user interface language for this session (overrides config and saved choice)")
    sub.add_parser("import", help="re-read the ETS project(s) and rebuild the cache")
    sub.add_parser("info", help="show a summary of the project")
    p = sub.add_parser("init", help="create config.yaml for a new KNX project")
    p.add_argument("project", help="path to the .knxproj or .knxprojarchive exported from ETS")
    p.add_argument("--gateway", default="", help="IP address of the KNX IP interface (empty = automatic discovery)")
    p.add_argument(
        "--connection",
        default=None,
        help="tunneling | tunneling_tcp | routing | automatic | tunneling_secure | routing_secure",
    )
    p.add_argument("--password", default=None, help="password of the .knxproj (if protected)")
    p.add_argument("--language", default="en", choices=sorted(LANGUAGES), help="user interface language")
    p.add_argument("--force", action="store_true", help="overwrite an existing config.yaml (a .bak copy is kept)")
    p = sub.add_parser("plan-dxf", help="export the floor plan (plan.yaml) to DXF for CAD programs")
    p.add_argument("output", nargs="?", default="plan.dxf")
    p = sub.add_parser("plc-map", help="create/update the PLC mapping (plc_map.yaml) and the CODESYS GVL")
    p.add_argument("--all", action="store_true", help="exchange every address with the PLC (select: all)")
    p.add_argument("--marked", action="store_true", help="exchange only addresses marked with use: true (select: marked)")
    p.add_argument("--rename", action="store_true",
                   help="give every variable a fresh name (e.g. after setting plc.tag_prefix) instead of keeping var")
    p.add_argument("--plc", help="only this PLC (name from plc.controllers); default: all")
    p = sub.add_parser("plc-browse", help="list the variables of the PLC's OPC UA server (uses the plc: settings)")
    p.add_argument("filter", nargs="?", default="", help="only show node ids containing this text, e.g. GVL_KNX")
    p.add_argument("--endpoints", action="store_true", help="only show the security modes and login types the PLC offers")
    p.add_argument("--plc", help="which PLC (name from plc.controllers); default: the first")

    p = sub.add_parser("modules", help="list the protocol modules (Modbus, MQTT, OPC UA …) or install them")
    p.add_argument("action", nargs="?", choices=("list", "install"), default="list")
    p.add_argument("names", nargs="*", help="modules to install, e.g. modbus mqtt")
    sub.add_parser("gateway", help="check protocols.yaml and show connections and gateways (live when the server runs)")
    p = sub.add_parser("simulate", help="play a device from a template as a Modbus slave, for tests without equipment")
    p.add_argument("model", nargs="?", default="", help='device template, e.g. "WAGO 879-3000" or EM111')
    p.add_argument("--host", default="127.0.0.1", help="address to listen on (0.0.0.0 = all)")
    p.add_argument("--port", type=int, default=5020, help="Modbus TCP port")
    p.add_argument("--unit", type=int, default=1, help="unit id (slave address)")
    p.add_argument("--device", help="serial port (RS485) instead of TCP, with the template's serial settings")
    p = sub.add_parser("bacnet-scan", help="find BACnet devices (Who-Is), or list the objects of one device")
    p.add_argument("address", nargs="?", default="", help="device address, e.g. 192.168.1.40 or 2:5; empty = Who-Is")
    p.add_argument("--device", type=int, help="device instance (found with Who-Is when there is no address)")
    p.add_argument("--local", default="", help="our address with mask, e.g. 192.168.1.10/24 (empty = this machine)")
    p.add_argument("--port", type=int, default=None,
                   help="our UDP port (default 47808 for Who-Is, which is answered by broadcast; "
                        "any free port when an address is given)")
    p.add_argument("--bbmd", default="", help="register as a foreign device at this BBMD, e.g. 10.0.0.1:47808")
    p.add_argument("--timeout", type=float, default=3, help="seconds to wait for answers")
    p.add_argument("--yaml", action="store_true", help="print a connection for protocols.yaml with the objects as tags")

    args = ap.parse_args(argv)
    cmd = args.cmd or "tui"
    if cmd == "init":
        return init_config(args)
    try:
        cfg = load_config(args.config)
    except FileNotFoundError:
        if cmd not in ("modules", "simulate", "bacnet-scan"):
            raise
        # A fresh installation (setup.sh, Dockerfile) installs modules before there is a config.yaml.
        import os
        from pathlib import Path

        cfg = Config(path=Path(args.config or os.environ.get("KNXSCADA_CONFIG") or "config.yaml").resolve())
    set_language(cfg.language)

    if cmd == "server":
        logging.basicConfig(
            level=logging.DEBUG if args.verbose else logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
        # xknx is chatty at INFO level; only show its warnings unless -v is given.
        logging.getLogger("xknx").setLevel(logging.INFO if args.verbose else logging.WARNING)
        # asyncua (PLC bridge) logs every browse and publish at INFO and harmless notes at WARNING.
        logging.getLogger("asyncua").setLevel(logging.INFO if args.verbose else logging.ERROR)
        from .server import ScadaServer

        asyncio.run(ScadaServer(cfg, demo=args.demo).run())
        return 0

    if cmd in ("import", "info"):
        _quiet_project_logging()
        from .project import load_project

        model = load_project(cfg, force=cmd == "import")
        print_summary(model)
        print(f"{_('Cache:'):<17}{cfg.cache_path}")
        return 0

    if cmd == "plan-dxf":
        from pathlib import Path

        from .plan import export_dxf, load_plan, plan_path

        floors = load_plan(cfg)
        if not floors:
            print(_("No floors found in {path}").format(path=plan_path(cfg)))
            return 1
        out = Path(args.output)
        export_dxf(floors, out)
        print(_("Wrote {path} ({floors} floors, {rooms} rooms)").format(
            path=out, floors=len(floors), rooms=sum(len(f.rooms) for f in floors)))
        return 0

    if cmd == "plc-map":
        return plc_map(cfg, args)

    if cmd == "simulate":
        return simulate(args)

    if cmd == "bacnet-scan":
        return bacnet_scan(args)

    if cmd == "plc-browse":
        from .opcua_bridge import browse, endpoints

        logging.basicConfig(level=logging.ERROR)  # asyncua warns a lot; errors are reported below
        plc = _select_plcs(cfg, args.plc)[0]
        endpoint = plc.get("endpoint") or "opc.tcp://127.0.0.1:4840"
        if not plc.get("endpoint"):
            print(_("No plc.endpoint in {path} – trying {endpoint}").format(path=cfg.path, endpoint=endpoint))
        try:
            if args.endpoints:
                for line in asyncio.run(endpoints(cfg, plc)):
                    print(line)
                return 0
            found = asyncio.run(browse(cfg, args.filter, p=plc))
        except Exception as exc:  # noqa: BLE001 - connection, login or certificate problem
            print(_("Could not connect to the PLC at {endpoint}: {error}").format(
                endpoint=endpoint, error=f"{type(exc).__name__}: {exc}"))
            return 1
        for nodeid, typ in found:
            print(f"{typ:<10} {nodeid}")
        print(_("{n} variables").format(n=len(found)))
        return 0

    if cmd == "modules":
        return modules(cfg, args)

    if cmd == "gateway":
        return gateway(cfg)

    # Default: the terminal user interface (imported lazily – Textual is heavy).
    # The language must be chosen before the import: some texts (key binding
    # descriptions) are evaluated when the Textual classes are defined.
    from .prefs import ui_language

    set_language(ui_language(cfg.language, getattr(args, "language", None)))
    from .tui.app import run

    run(cfg)
    return 0


def _quiet_project_logging() -> None:
    """Plain log output for commands that parse the project (xknxproject warns a lot)."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("xknxproject").setLevel(logging.ERROR)


def print_summary(model: dict) -> None:
    """Print what was found in the project: name, address count, floors and rooms."""
    n_used = sum(1 for g in model["gas"].values() if g["used"])
    source = {"ets-functions": _("ETS functions"), "group-addresses": _("group addresses"),
              "room-names": _("room names in the group addresses")}.get(
        model.get("source", "ets-functions"), model.get("source", ""))
    print(f"{_('Project:'):<17}{model['info'].get('name')}")
    print(f"{_('Group addresses:'):<17}{len(model['gas'])} ({_('{n} in use').format(n=n_used)})")
    print(f"{_('Cards:'):<17}{len(model['functions'])}  ({_('built from {source}').format(source=source)})")
    for floor in model["floors"]:
        print(f"  {floor['name']}: " + ", ".join(r["name"] for r in floor["rooms"]))


def _select_plcs(cfg, name: str | None) -> list[dict]:
    """The PLCs of ``plc:`` – all, or the one called *name* (SystemExit if there is none)."""
    from .plc import plc_configs

    plcs = plc_configs(cfg.plc)
    if name:
        plcs = [p for p in plcs if p["name"] == name]
        if not plcs:
            raise SystemExit(_("No PLC called {name} in plc.controllers").format(name=repr(name)))
    return plcs


def plc_map(cfg, args) -> int:
    """``plc-map``: write the mapping (keeping existing choices) and the CODESYS GVL for each PLC."""
    _quiet_project_logging()
    from .plc import build_map, gvl_text, load_map, write_map
    from .project import load_project

    model = load_project(cfg)
    for plc in _select_plcs(cfg, args.plc):
        path = cfg.resolve(plc["map"])
        old = load_map(path) if path.exists() else None
        pmap, unsupported, removed = build_map(model, old, gvl=str(plc["gvl"]),
                                               tag_prefix=plc.get("tag_prefix"), rename=args.rename)
        if args.all:
            pmap.select = "all"
        elif args.marked:
            pmap.select = "marked"
        write_map(pmap, path)
        gvl_path = cfg.resolve(plc.get("gvl_file", f"{pmap.gvl}.st"))
        gvl_path.write_text(gvl_text(pmap), encoding="utf-8")
        active = len(pmap.active())
        print(_("Wrote {path}: {n} group addresses, {active} exchanged with the PLC (select: {select})").format(
            path=path, n=len(pmap.entries), active=active, select=pmap.select))
        if unsupported:
            print(_("{n} addresses left out – their DPT cannot be exchanged (e.g. relative dimming, date/time)").format(
                n=len(unsupported)))
        if removed:
            print(_("No longer in the project, removed: {addresses}").format(addresses=", ".join(removed)))
        print(_("Wrote {path} ({n} variables) – import it into the CODESYS project as GVL {gvl}").format(
            path=gvl_path, n=active, gvl=pmap.gvl))
        if not active:
            print(_("Mark addresses with use: true in {path} (or use --all) and run plc-map again.").format(path=path.name))
    return 0


def simulate(args) -> int:
    """``simulate MODEL``: a Modbus slave that plays a device template (list the templates without a model)."""
    from .protocols import devices, installed

    if not args.model:
        print(_("Device templates:"))
        for name, tpl in devices.templates().items():
            print(f"  {name:<24} {tpl['title']}")
        print(_("Example: ./scada simulate \"WAGO 879-3000\" --port 5020"))
        return 0
    if not installed("modbus"):
        print(_("The Modbus module is not installed. Install it with: ./scada modules install modbus"))
        return 1
    from .protocols.simulate import run

    try:
        asyncio.run(run(args.model, args.host, args.port, args.unit, args.device))
    except ValueError as exc:
        print(exc)
        return 1
    except KeyboardInterrupt:
        pass
    return 0


def bacnet_scan(args) -> int:
    """``bacnet-scan``: the devices on the network, or the objects of one device (``--yaml``: as tags)."""
    from .protocols import installed

    if not installed("bacnet"):
        print(_("The BACnet module is not installed. Install it with: ./scada modules install bacnet"))
        return 1
    import yaml

    from .protocols import bacnet

    port = args.port if args.port is not None else (0 if args.address else 47808)
    local = args.local or (args.address and bacnet.route_source(args.address)) or "host"
    local = local if ":" in local.rsplit("/", 1)[-1] else f"{local}:{port}"
    # bacpypes3 tries to open the UDP port until it succeeds: give up when it never does.
    wait = args.timeout + 5

    async def run() -> int:
        """Do the scan with a temporary BACnet device of our own (instance 4194302) and print the result.

        Only reads (Who-Is, ReadProperty/ReadPropertyMultiple) – nothing is written to any device.
        """
        app = bacnet.make_app(local, bacnet.MAX_INSTANCE, "knxscada scan", args.timeout, args.bbmd)
        try:
            await asyncio.sleep(0)   # let the UDP endpoint open
            if args.bbmd:
                await asyncio.sleep(1)   # the registration at the BBMD
            if not args.address and args.device is None:
                devices = await asyncio.wait_for(bacnet.scan_devices(app, args.timeout), wait)
                for d in devices:
                    print(f"{d['instance']:>8}  {d['address']:<22} {d['name']:<32} {d['vendor']}")
                print(_("{n} BACnet devices").format(n=len(devices)))
                if devices:
                    print(_("Objects of one device: ./scada bacnet-scan ADDRESS (or --device N), "
                            "--yaml for protocols.yaml"))
                return 0
            target, device_id = await asyncio.wait_for(
                bacnet.find_device(app, args.address, args.device, args.timeout), wait)
            rows = await bacnet.scan_objects(app, target, device_id)
            if args.yaml:
                conn = {"protocol": "bacnet", "address": str(target), "device_id": device_id,
                        "tags": bacnet.tags_yaml(rows)}
                print(yaml.safe_dump({"connections": {f"device{device_id}": conn}}, allow_unicode=True,
                                     sort_keys=False, default_flow_style=None, width=120), end="")
                return 0
            for r in rows:
                value = "" if r["value"] is None else str(r["value"])
                print(f"{r['object']:<14} {r['name']:<32} {value:>12} {r['unit']:<6} {r['description']}")
            print(_("{n} objects in device {device} at {address}").format(n=len(rows), device=device_id,
                                                                        address=target))
            return 0
        finally:
            app.close()

    logging.basicConfig(level=logging.ERROR)  # bacpypes3 logs harmless notes
    try:
        return asyncio.run(run())
    except TimeoutError:
        print(_("No answer: is UDP port {local} free and the address right?").format(local=local))
        return 1
    except (ConnectionError, ValueError, OSError) as exc:
        print(exc)
        return 1
    except KeyboardInterrupt:
        return 1


def modules(cfg, args) -> int:
    """``modules``: which protocol modules exist, are installed and are used; ``modules install …``."""
    from .protocols import PROTOCOLS, install, installed
    from .protocols.config import load_data, protocols_path

    if args.action == "install":
        if not args.names:
            print(_("Name the modules to install, e.g.: ./scada modules install modbus mqtt"))
            return 1
        try:
            code = install(args.names)
        except ValueError as exc:
            print(exc)
            return 1
        if code == 0:
            print(_("Installed: {names}. Restart the server to use them.").format(names=", ".join(args.names)))
        return code
    used: dict[str, list[str]] = {}
    try:
        data = load_data(protocols_path(cfg))
    except Exception:  # noqa: BLE001 - a broken file is reported by ./scada gateway
        data = {"connections": {}}
    for name, conn in (data.get("connections") or {}).items():
        if isinstance(conn, dict):
            used.setdefault(str(conn.get("protocol")), []).append(str(name))
    if cfg.knx_enabled:
        used.setdefault("knx", []).insert(0, "knx")
    for p in PROTOCOLS.values():
        if p.planned:
            state = _("planned")
        elif p.module is None or installed(p.name):
            state = _("installed")
        else:
            state = _("not installed")
        names = ", ".join(used.get(p.name, []))
        print(f"{p.name:<10} {p.title:<18} {state:<15} {names}")
        if names and state == _("not installed"):
            print("           " + _("Install it with: ./scada modules install {name}").format(name=p.name))
    return 0


def gateway(cfg) -> int:
    """``gateway``: check ``protocols.yaml``; with a running server also show its live status."""
    from .protocols.config import load_data, parse_link, protocols_path, validate

    path = protocols_path(cfg)
    if not path.exists():
        print(_("{path} does not exist yet – create it in the TUI (F11) or from protocols.example.yaml").format(path=path))
        return 0
    try:
        data = load_data(path)
    except Exception as exc:  # noqa: BLE001 - YAML error
        print(_("{path} cannot be read: {error}").format(path=path, error=exc))
        return 1
    errors = validate(data, cfg.knx_enabled)
    live = _server_snapshot(cfg)
    status = {s["name"]: s for s in (live or {}).get("status", [])}
    print(_("Connections ({path}):").format(path=path))
    names = (["knx"] if cfg.knx_enabled else []) + [n for n in data["connections"] if n != "knx"]
    for name in names:
        conn = data["connections"].get(name) or {"protocol": "knx"}
        line = f"  {name:<16} {conn.get('protocol', ''):<8} {conn.get('role', 'client'):<7}"
        st = status.get(name)
        if st is not None:
            state = _("connected") if st["connected"] else (st["error"] or _("disconnected"))
            line += f" {st['where']:<28} {st['tags']:>5} tags  {state}"
        print(line)
    print(_("Gateways:"))
    links = {}
    for link in (live or {}).get("links", []):
        links.setdefault(link["gateway"], []).append(link)
    for gw in data["gateways"]:
        if not isinstance(gw, dict):
            continue
        name = str(gw.get("name") or "")
        off = "  " + _("(off)") if gw.get("enabled") is False else ""
        print(f"  {name}: {gw.get('from')} → {gw.get('to')}  ({len(gw.get('map') or [])} {_('lines')}){off}")
        for item in gw.get("map") or []:
            try:
                ln = parse_link(item)
            except ValueError:
                continue
            arrow = "↔" if (ln.get("direction") or gw.get("direction")) == "both" else "→"
            print(f"      {ln['from']} {arrow} {ln['to'] or ln['from']}")
        for link in links.get(name, []):
            if link["error"]:
                print("      ! " + f"{link['from']} → {link['to']}: {link['error']}")
    if live is None:
        print(_("No live status: the server is not running, or it is older than this version."))
    else:
        for problem in live.get("problems", []):
            print("! " + problem)
    for err in errors:
        print("! " + err)
    return 1 if errors else 0


def _server_snapshot(cfg) -> dict | None:
    """The hub's snapshot from the running server, or None when it does not answer."""
    from .client import ScadaClient

    async def ask():
        """Connect to the server's socket (3 s at most) and ask for the ``protocols`` snapshot."""
        c = ScadaClient(cfg.socket_path, lambda _m: None)
        await asyncio.wait_for(c.connect(), 3)
        try:
            return await c.request("protocols")
        finally:
            c.close_nowait()

    try:
        return asyncio.run(ask())
    except Exception:  # noqa: BLE001 - not running, no access, old server without protocols
        return None


def _yaml_str(value: str) -> str:
    """Quote *value* for YAML. JSON strings are valid YAML double-quoted scalars."""
    return json.dumps(value, ensure_ascii=False)


def init_config(args) -> int:
    """Write ``config.yaml`` from ``config.example.yaml`` and import the project.

    The template contains placeholders (``__GATEWAY__`` etc.) that are
    replaced here. An existing configuration is only overwritten with
    ``--force``, and then a ``.bak`` copy is kept.
    """
    import os
    from pathlib import Path

    from .config import DEFAULT_CONFIG

    set_language(args.language)
    target = Path(args.config or os.environ.get("KNXSCADA_CONFIG") or DEFAULT_CONFIG).resolve()
    template = DEFAULT_CONFIG.with_name("config.example.yaml")
    # abspath, not resolve(): keep the path as given, so a symlinked project stays relative to the config.
    project = Path(os.path.abspath(Path(args.project).expanduser()))
    if not project.exists():
        print(_("Cannot find {path}").format(path=project))
        return 1
    if target.exists() and not args.force:
        print(_("{path} already exists. Use --force to overwrite it (a .bak copy is kept).").format(path=target))
        return 1
    if target.exists():
        target.with_suffix(".yaml.bak").write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
    # Store the project path relative to the config when possible (portable installs).
    try:
        rel = project.relative_to(target.parent)
    except ValueError:
        rel = project
    connection = args.connection or ("tunneling" if args.gateway else "automatic")
    text = (
        template.read_text(encoding="utf-8")
        .replace("__CONNECTION__", connection)
        .replace("__GATEWAY__", args.gateway or "null")
        .replace("__PROJECT__", str(rel))
        .replace("__TITLE__", _yaml_str(f"{PRODUCT} – {project.stem}"))
        .replace("__LANGUAGE__", args.language)
    )
    if args.password:
        text = text.replace('  # password: ""', f"  password: {_yaml_str(args.password)}")
    target.write_text(text, encoding="utf-8")
    target.chmod(0o600)  # may contain the KNX Secure keyring password and the PLC password
    print(_("Wrote {path}").format(path=target))

    _quiet_project_logging()
    from .project import load_project

    cfg = load_config(target)
    model = load_project(cfg, force=True)
    # Use the project's real name (from ETS) as the title instead of the file name.
    if model["info"].get("name"):
        cfg_text = target.read_text(encoding="utf-8").replace(
            f"title: {_yaml_str(f'{PRODUCT} – {project.stem}')}", f"title: {_yaml_str(f'{PRODUCT} – ' + model['info']['name'])}"
        )
        target.write_text(cfg_text, encoding="utf-8")
    print_summary(model)
    print("\n" + _("Next: check the knx: section in config.yaml, start ./scada server and then ./scada"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
