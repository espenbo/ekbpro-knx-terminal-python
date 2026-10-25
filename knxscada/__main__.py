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
from .config import load_config
from .i18n import LANGUAGES, _, set_language


def main(argv: list[str] | None = None) -> int:
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
    p = sub.add_parser("plc-browse", help="list the variables of the PLC's OPC UA server (uses the plc: settings)")
    p.add_argument("filter", nargs="?", default="", help="only show node ids containing this text, e.g. GVL_KNX")
    p.add_argument("--endpoints", action="store_true", help="only show the security modes and login types the PLC offers")

    args = ap.parse_args(argv)
    cmd = args.cmd or "tui"
    if cmd == "init":
        return init_config(args)
    cfg = load_config(args.config)
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

    if cmd == "plc-browse":
        from .opcua_bridge import browse, endpoints

        logging.basicConfig(level=logging.ERROR)  # asyncua warns a lot; errors are reported below
        endpoint = cfg.plc.get("endpoint") or "opc.tcp://127.0.0.1:4840"
        if not cfg.plc.get("endpoint"):
            print(_("No plc.endpoint in {path} – trying {endpoint}").format(path=cfg.path, endpoint=endpoint))
        try:
            if args.endpoints:
                for line in asyncio.run(endpoints(cfg)):
                    print(line)
                return 0
            found = asyncio.run(browse(cfg, args.filter))
        except Exception as exc:  # noqa: BLE001 - connection, login or certificate problem
            print(_("Could not connect to the PLC at {endpoint}: {error}").format(
                endpoint=endpoint, error=f"{type(exc).__name__}: {exc}"))
            return 1
        for nodeid, typ in found:
            print(f"{typ:<10} {nodeid}")
        print(_("{n} variables").format(n=len(found)))
        return 0

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


def plc_map(cfg, args) -> int:
    """``plc-map``: write plc_map.yaml (keeping existing choices) and the CODESYS GVL."""
    _quiet_project_logging()
    from .plc import build_map, gvl_text, load_map, write_map
    from .project import load_project

    model = load_project(cfg)
    path = cfg.resolve(cfg.plc.get("map", "plc_map.yaml"))
    old = load_map(path) if path.exists() else None
    pmap, unsupported, removed = build_map(model, old, gvl=str(cfg.plc.get("gvl", "GVL_KNX")),
                                           tag_prefix=cfg.plc.get("tag_prefix"), rename=args.rename)
    if args.all:
        pmap.select = "all"
    elif args.marked:
        pmap.select = "marked"
    write_map(pmap, path)
    gvl_path = cfg.resolve(cfg.plc.get("gvl_file", f"{pmap.gvl}.st"))
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
