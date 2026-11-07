"""``./scada init``: the config.yaml text made from config.example.yaml (``render_config``)."""

from __future__ import annotations

from pathlib import Path

import yaml

from knxscada.__main__ import render_config

#: The template ``init`` fills in.
TEMPLATE = (Path(__file__).resolve().parent.parent / "config.example.yaml").read_text(encoding="utf-8")


def _render(**kw) -> dict:
    """The rendered configuration, parsed."""
    return yaml.safe_load(render_config(TEMPLATE, project="Hus.knxproj", title="Hus", language="nb", **kw))


def test_plain_tunneling_and_automatic():
    """Without options: automatic discovery; with a gateway: tunneling. No keyring lines are switched on."""
    knx = _render()["knx"]
    assert knx["connection"] == "automatic" and knx["gateway_ip"] is None and "keyring_file" not in knx
    assert _render(gateway="192.168.1.50")["knx"]["connection"] == "tunneling"


def test_keyring_switches_on_knx_secure():
    """--keyring fills in keyring_file/keyring_password (quoted, so # and : survive) and the tunnel user, and makes
    the connection tunneling_secure with a gateway or routing_secure without; the PLC password stays commented."""
    c = _render(gateway="192.168.1.50", keyring="Hus.knxkeys", keyring_password="k#1: x", user_id=3)
    assert c["knx"]["connection"] == "tunneling_secure"
    assert (c["knx"]["keyring_file"], c["knx"]["keyring_password"], c["knx"]["user_id"]) == ("Hus.knxkeys", "k#1: x", 3)
    assert "password" not in (c.get("plc") or {})
    assert _render(keyring="Hus.knxkeys")["knx"]["connection"] == "routing_secure"
    assert _render(keyring="Hus.knxkeys", connection="tunneling_tcp")["knx"]["connection"] == "tunneling_tcp"


def test_project_password_and_usb():
    """--password goes to project.password only; --usb-device gives connection usb with usb_device."""
    c = _render(password="p#w", usb_device="00C512345678")
    assert c["project"]["password"] == "p#w"
    assert c["knx"]["connection"] == "usb" and c["knx"]["usb_device"] == "00C512345678"
