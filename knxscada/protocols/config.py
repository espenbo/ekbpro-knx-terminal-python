# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""``protocols.yaml``: connections and gateways.

The file sits next to ``config.yaml`` (another place: ``server.protocols``).
It is written by people *and* by the TUI, so it is kept short and plain::

    connections:
      plc1:                         # a name of your own
        protocol: modbus
        host: 192.168.1.20
        tags:
          Temp_ute: {address: "ir:0", type: int16, scale: 0.1, unit: °C}
          Pump_start: {address: "co:0"}
      scada:
        protocol: opcua
        role: server
        endpoint: opc.tcp://0.0.0.0:4840

    gateways:
      - name: Plant to SCADA
        from: plc1
        to: scada
        map:
          - Temp_ute -> Outdoor.Temperature     # new name in the target
          - {from: Pump_start, to: Pumps.P1.Start, direction: both}

A mapping line is ``"source"`` (same name in the target), ``"source -> target"``
or a dict with extra settings. ``*`` in the source takes every matching tag;
the target is then a template with ``{name}`` (e.g. ``Lights/{name}``).

The connection ``knx`` exists by itself when KNX is set up in ``config.yaml``.
Settings that are left out take the defaults from the registry
(:data:`knxscada.protocols.PROTOCOLS`), so they are not written to the file.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from ..fileutil import backup_private, write_private
from ..i18n import _
from . import PROTOCOLS, Field, devices

if TYPE_CHECKING:
    from ..config import Config

#: Connection names: shown everywhere and used in tag references, so plain.
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")

#: Gateway directions: ``forward`` = from → to, ``both`` = also to → from.
DIRECTIONS = ("forward", "both")

#: Keys a mapping line may have.
LINK_KEYS = ("from", "to", "direction", "scale", "offset", "invert", "type", "deadband", "min_interval_ms",
             "initial", "unit", "description")

#: Keys a gateway may have (the defaults for its lines are the same as in LINK_KEYS).
GATEWAY_KEYS = ("name", "from", "to", "direction", "map", "initial", "deadband", "min_interval_ms", "enabled",
                "settle_ms", "burst_limit", "restart_quiet_s")

HEADER = """\
# Connections and gateways – EKBPRO KNX Terminal Python
#
# Edit this file by hand or in the user interface (F11 Gateways); the program keeps it tidy
# when it saves it (comments other than this header are not kept).
# The server picks up changes made in the user interface at once; after editing by hand,
# restart it or press r in F11.  Guide: docs/en/11-protocols-gateways.md
#
# Mapping lines:  "source"  ·  "source -> new name"  ·  {from: …, to: …, direction: both, scale: 0.1}
"""


def protocols_path(cfg: Config) -> Path:
    """Where ``protocols.yaml`` is (``server.protocols``, default next to ``config.yaml``)."""
    return cfg.resolve(str(cfg.server.get("protocols") or "protocols.yaml"))


def empty() -> dict[str, Any]:
    """The contents of an empty ``protocols.yaml``: no connections, no gateways."""
    return {"connections": {}, "gateways": []}


def load_data(path: Path) -> dict[str, Any]:
    """The contents of ``protocols.yaml`` (empty when the file does not exist)."""
    if not path.exists():
        return empty()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(_("{path}: expected connections: and gateways:").format(path=path.name))  # noqa: TRY004
    return {"connections": data.get("connections") or {}, "gateways": data.get("gateways") or []}


def parse_link(item: Any) -> dict[str, Any]:
    """A mapping line → ``{"from", "to", …}`` (``to`` empty = the same name)."""
    if isinstance(item, str):
        src, arrow, dst = item.partition("->")
        return {"from": src.strip(), "to": dst.strip() if arrow else ""}
    if isinstance(item, dict):
        out = {k: v for k, v in item.items() if v is not None}
        out["from"] = str(out.get("from") or "").strip()
        out["to"] = str(out.get("to") or "").strip()
        return out
    raise ValueError(repr(item))


def link_text(link: dict[str, Any]) -> str | dict[str, Any]:
    """The short form of a mapping line when it has no extra settings (inverse of :func:`parse_link`)."""
    extra = {k: v for k, v in link.items() if k not in ("from", "to") and v not in (None, "")}
    if extra:
        return {"from": link["from"], **({"to": link["to"]} if link.get("to") else {}), **extra}
    return f"{link['from']} -> {link['to']}" if link.get("to") and link["to"] != link["from"] else link["from"]


def settings_of(conn: dict[str, Any]) -> dict[str, Any]:
    """A connection's settings with the defaults filled in: the registry's, then the device template's.

    With ``model`` the template's tags come first and the connection's own ``tags`` are added
    (:func:`.devices.apply`). An unknown model is left for :func:`validate` and the driver to report.
    """
    p = PROTOCOLS.get(str(conn.get("protocol")))
    out: dict[str, Any] = {}
    if p is not None:
        out.update({f.key: f.default for f in p.settings if f.default is not None})
    try:
        conn = devices.apply(conn)
    except ValueError:
        pass
    out.update({k: v for k, v in conn.items() if v is not None})
    return out


def defaults_of(conn: dict[str, Any]) -> dict[str, Any]:
    """The settings a connection gets when it leaves them out (registry, then device template)."""
    p = PROTOCOLS.get(str(conn.get("protocol")))
    out = {f.key: f.default for f in p.settings} if p else {}
    try:
        tpl = devices.find(conn.get("model"))
    except ValueError:
        tpl = None
    if tpl is not None:
        out.update(tpl.get("settings") or {})
    return out


def _check_value(f: Field, value: Any) -> str | None:
    """Error text when *value* does not fit the field, else None."""
    if value is None or value == "":
        return None
    try:
        if f.kind == "int":
            int(str(value))
        elif f.kind == "float":
            float(str(value).replace(",", "."))
        elif f.kind == "bool" and not isinstance(value, bool):
            return _("{key}: expected true or false").format(key=f.key)
        elif f.kind == "choice" and f.choices and str(value) not in f.choices:
            return _("{key}: {value} – use one of: {valid}").format(
                key=f.key, value=repr(value), valid=", ".join(c for c in f.choices if c))
        elif f.kind == "list" and not isinstance(value, (list, str)):
            return _("{key}: expected a list").format(key=f.key)
    except ValueError:
        return _("{key}: {value} is not a number").format(key=f.key, value=repr(value))
    return None


def validate(data: dict[str, Any], knx: bool = False) -> list[str]:
    """Every problem in *data* (an empty list when it is fine).

    *knx*: KNX is set up in ``config.yaml``, so the connection ``knx`` exists.
    """
    errors: list[str] = []
    conns = data.get("connections") or {}
    if not isinstance(conns, dict):
        return [_("connections: must be a list of names with settings")]
    names = {"knx"} if knx else set()
    for name, conn in conns.items():
        where = _("connection {name}").format(name=name)
        if not NAME_RE.match(str(name)):
            errors.append(_("{where}: the name must start with a letter and only have letters, digits, - and _")
                          .format(where=where))
        if not isinstance(conn, dict):
            errors.append(f"{where}: {conn!r}")
            continue
        names.add(str(name))
        pname = str(conn.get("protocol") or "")
        p = PROTOCOLS.get(pname)
        if p is None:
            errors.append(_("{where}: unknown protocol {name} – use one of: {valid}").format(
                where=where, name=repr(pname), valid=", ".join(k for k, v in PROTOCOLS.items() if not v.planned)))
            continue
        if p.planned:
            errors.append(_("{where}: the {protocol} module is not available yet").format(where=where, protocol=p.title))
            continue
        if pname == "knx":
            if not knx:
                errors.append(_("{where}: KNX is not set up in config.yaml (knx: and project:)").format(where=where))
            if str(name) != "knx":
                errors.append(_("{where}: the KNX connection must be called knx").format(where=where))
            continue
        role = str(conn.get("role") or "client")
        if role not in p.roles:
            errors.append(_("{where}: role {role} – {protocol} can be: {valid}").format(
                where=where, role=repr(role), protocol=p.title, valid=", ".join(p.roles)))
        try:
            tpl = devices.find(conn.get("model"))
        except ValueError as exc:
            errors.append(f"{where}: {exc}")
            tpl = None
        if tpl is not None and tpl.get("protocol") != pname:
            errors.append(_("{where}: {model} is a {protocol} device").format(
                where=where, model=tpl["model"], protocol=tpl.get("protocol")))
        values = settings_of(conn)
        for f in p.settings:
            if f.required and f.visible(values) and not values.get(f.key):
                errors.append(_("{where}: {key} is missing").format(where=where, key=f.key))
            if f.key == "model":
                continue   # checked above (a template name, any case)
            if (err := _check_value(f, conn.get(f.key))) is not None:
                errors.append(f"{where}: {err}")
        tags = conn.get("tags") or {}
        if not isinstance(tags, dict):
            errors.append(_("{where}: tags: must be names with settings").format(where=where))
            continue
        for tname, spec in tags.items():
            if spec is not None and not isinstance(spec, dict):
                errors.append(f"{where}, tag {tname}: {spec!r}")
                continue
            for f in p.tag_fields:
                if (err := _check_value(f, (spec or {}).get(f.key))) is not None:
                    errors.append(f"{where}, tag {tname}: {err}")
            if pname == "modbus" and spec and spec.get("address"):
                from .modbus import point_of, write_point

                try:
                    point = point_of(spec, 1)
                    if spec.get("write_address") not in (None, ""):
                        write_point(str(spec["write_address"]), point)
                except ValueError as exc:
                    errors.append(f"{where}, tag {tname}: {exc}")
            if pname == "bacnet" and spec and spec.get("object"):
                from .bacnet import parse_object

                try:
                    parse_object(str(spec["object"]))
                except ValueError as exc:
                    errors.append(f"{where}, tag {tname}: {exc}")
    gws = data.get("gateways") or []
    if not isinstance(gws, list):
        return [*errors, _("gateways: must be a list")]
    seen: set[str] = set()
    for i, gw in enumerate(gws, 1):
        if not isinstance(gw, dict):
            errors.append(f"gateways[{i}]: {gw!r}")
            continue
        gname = str(gw.get("name") or f"#{i}")
        where = _("gateway {name}").format(name=gname)
        if gname in seen:
            errors.append(_("{where} is listed twice").format(where=where))
        seen.add(gname)
        for key in ("from", "to"):
            if not gw.get(key):
                errors.append(_("{where}: {key} is missing").format(where=where, key=key))
            elif str(gw[key]) not in names:
                errors.append(_("{where}: there is no connection called {name}").format(where=where, name=repr(gw[key])))
        if gw.get("from") and gw.get("from") == gw.get("to"):
            errors.append(_("{where}: from and to are the same connection").format(where=where))
        if str(gw.get("direction") or "forward") not in DIRECTIONS:
            errors.append(_("{where}: direction must be forward or both").format(where=where))
        lines = gw.get("map") or []
        if not isinstance(lines, list):
            errors.append(_("{where}: map: must be a list").format(where=where))
            continue
        for j, item in enumerate(lines, 1):
            try:
                link = parse_link(item)
            except ValueError:
                errors.append(f"{where}, map[{j}]: {item!r}")
                continue
            if not link["from"]:
                errors.append(_("{where}, line {n}: the source tag is missing").format(where=where, n=j))
            if str(link.get("direction") or "forward") not in DIRECTIONS:
                errors.append(_("{where}, line {n}: direction must be forward or both").format(where=where, n=j))
            for key in ("scale", "offset", "deadband", "min_interval_ms"):
                if link.get(key) not in (None, ""):
                    try:
                        float(link[key])
                    except (TypeError, ValueError):
                        errors.append(_("{where}, line {n}: {key} must be a number").format(where=where, n=j, key=key))
    return errors


# -- writing ----------------------------------------------------------------------------------------
class _Flow(dict):
    """A dict written on one line (tag settings, mapping lines)."""


def _flow_representer(dumper: yaml.SafeDumper, data: _Flow):
    """Write a :class:`_Flow` dict in flow style (``{address: hr:100, type: float32}``) – one line per tag or
    mapping line keeps a hand-edited file readable."""
    return dumper.represent_mapping("tag:yaml.org,2002:map", data.items(), flow_style=True)


class _Dumper(yaml.SafeDumper):
    """A SafeDumper of our own, so the :class:`_Flow` representer does not change ``yaml.SafeDumper`` globally."""



_Dumper.add_representer(_Flow, _flow_representer)


def _clean(conn: dict[str, Any]) -> dict[str, Any]:
    """A connection as it is written: no empty values, no defaults, tags on one line each."""
    defaults = defaults_of(conn)
    out: dict[str, Any] = {"protocol": conn.get("protocol")}
    for k, v in conn.items():
        if k in ("protocol", "tags") or v is None or v == "" or v == []:
            continue
        if k in defaults and str(v) == str(defaults[k]) and k != "role":
            continue
        out[k] = v
    if out.get("role") == "client":
        del out["role"]
    tags = conn.get("tags") or {}
    if tags:
        out["tags"] = {str(n): _Flow({k: v for k, v in (spec or {}).items() if v not in (None, "")})
                       for n, spec in tags.items()}
    return out


def dump(data: dict[str, Any]) -> str:
    """``protocols.yaml`` as text: the header, then connections and gateways."""
    conns = {str(n): _clean(c) for n, c in (data.get("connections") or {}).items()}
    gws = []
    for gw in data.get("gateways") or []:
        g = {k: gw[k] for k in GATEWAY_KEYS if k != "map" and gw.get(k) not in (None, "")}
        if g.get("direction") == "forward":
            del g["direction"]
        lines = []
        for item in gw.get("map") or []:
            text = link_text(parse_link(item))
            lines.append(_Flow(text) if isinstance(text, dict) else text)
        g["map"] = lines
        gws.append(g)
    body = yaml.dump({"connections": conns, "gateways": gws}, Dumper=_Dumper, allow_unicode=True, sort_keys=False,
                     default_flow_style=False, width=200)
    return HEADER + "\n" + body


def save(path: Path, data: dict[str, Any], knx: bool = False) -> None:
    """Validate and write *data* (the old file is kept as ``.bak``). Raises ValueError with every problem."""
    errors = validate(data, knx)
    if errors:
        raise ValueError("\n".join(errors))
    text = dump(data)
    # Both may hold passwords (MQTT, OPC UA): the backup and the new file are created with restricted rights from the
    # start (fileutil), never with the umask.
    backup_private(path)
    write_private(path, text)
