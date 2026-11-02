# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""KNX is an optional module: without xknx/xknxproject (and asyncua) the program still runs for sites with only
Modbus, OPC UA, BACnet or MQTT, and a site with KNX gets a clear install hint instead of an ImportError."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Runs in a fresh interpreter: hides the KNX and OPC UA packages, then imports and starts the parts that must work.
SCRIPT = r'''
import importlib.abc, json, sys, tempfile, pathlib

class Hide(importlib.abc.MetaPathFinder):
    """Wraps a real finder and finds nothing for the optional packages – as if they were not installed."""
    def __init__(self, finder):
        self.finder = finder
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in ("xknx", "xknxproject", "asyncua"):
            return None
        find = getattr(self.finder, "find_spec", None)
        return find(name, path, target) if find else None

sys.meta_path[:] = [Hide(f) for f in sys.meta_path]
out = {}
from knxscada import dpt
out["unit"] = dpt.unit("9.001")                       # no transcoder: no unit, no crash
out["fmt"] = dpt.fmt("9.001", 21.5)
from knxscada.protocols import installed
out["knx_installed"] = installed("knx")
out["opcua_installed"] = installed("opcua")
import knxscada.server, knxscada.tui.app             # both import without the KNX packages
from knxscada.config import Config
from knxscada.server import ScadaServer
tmp = pathlib.Path(tempfile.mkdtemp())
no_knx = Config(path=tmp / "config.yaml", server={"socket": "s.sock", "database": "db.sqlite"}, knx={"enabled": False})
server = ScadaServer(no_knx)
out["server_without_knx"] = server.knx_enabled is False
with_knx = Config(path=tmp / "config.yaml", server={"socket": "s.sock", "database": "db2.sqlite"},
                  knx={"gateway_ip": "192.168.1.50"})
try:
    ScadaServer(with_knx)
    out["hint"] = ""
except ModuleNotFoundError as exc:
    out["hint"] = str(exc)
print(json.dumps(out))
'''


def test_runs_without_the_knx_packages():
    """With the KNX and OPC UA packages hidden: the DPT helpers degrade gracefully, the server and the TUI import,
    a server without KNX starts, and one with KNX stops with the install command in the message."""
    result = subprocess.run([sys.executable, "-c", SCRIPT], cwd=ROOT, capture_output=True, text=True, timeout=120,
                            check=False)
    assert result.returncode == 0, result.stderr[-2000:]
    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert out["unit"] == "" and out["fmt"] == "21.5"
    assert out["knx_installed"] is False and out["opcua_installed"] is False
    assert out["server_without_knx"] is True
    assert "./scada modules install knx" in out["hint"]


def test_cli_reports_a_missing_module(monkeypatch, capsys):
    """``./scada server`` for a KNX site without the KNX module prints the install command, not a traceback."""
    from knxscada import __main__ as cli
    from knxscada.protocols import missing_message

    def fail(argv):
        """Stands in for the command that finds the module missing."""
        raise ModuleNotFoundError(missing_message("knx"))

    monkeypatch.setattr(cli, "_run", fail)
    assert cli.main(["server"]) == 2
    assert "./scada modules install knx" in capsys.readouterr().err
