"""``./scada setup``: the wizard's questions and the config.yaml it writes, with the searches replaced by fakes."""

from __future__ import annotations

import yaml

from knxscada import setup_wizard
from knxscada.knx_usb import UsbDevice
from knxscada.setup_wizard import Wizard

#: A KNX USB interface as the USB search would report it.
STICK = UsbDevice("/dev/hidraw2", "KNX-USB Interface", 0x0E77, 0x0104, "00C512345678")
#: A KNX IP interface as the KNXnet/IP search would report it.
IP_IF = {"ip": "192.168.1.50", "name": "IP Interface", "tunnelling": True, "routing": False, "secure": False}


def _wizard(target, answers, **kw):
    """A wizard that takes its answers from *answers* (in order) and records what it says."""
    said: list[str] = []
    it = iter(answers)
    w = Wizard(target, ask=lambda q: next(it), say=said.append, knx_ip=kw.pop("knx_ip", lambda: [IP_IF]),
               usb=kw.pop("usb", lambda: [STICK]), bacnet=list, network=lambda: "192.168.1.0/24",
               install=lambda names: 0, **kw)
    return w, said


def test_site_without_knx(tmp_path, monkeypatch):
    """No KNX: the modules are chosen, the Modbus search lists what answers on port 502 (nothing is set up), and a
    config.yaml with KNX off and the language is written with mode 0600; the service is not installed."""
    monkeypatch.setattr("knxscada.protocols.installed", lambda name: True)
    target = tmp_path / "config.yaml"
    w, said = _wizard(target, ["nb", "n", "modbus", "j", "n"],
                      ports=lambda net, port: ["192.168.1.40"] if port == 502 else [])
    assert w.run() == 0
    c = yaml.safe_load(target.read_text())
    assert c["knx"] == {"enabled": False} and c["ui"]["language"] == "nb"
    assert target.stat().st_mode & 0o777 == 0o600
    assert any("192.168.1.40:502" in s for s in said)


def test_knx_over_usb_with_password_retry(tmp_path, monkeypatch):
    """KNX: the project is checked before anything is written and its password asked again until right; the USB
    interface found is chosen; the config gets connection usb, its serial number and the password, and the project
    is imported."""
    project = tmp_path / "Hus.knxproj"
    project.write_text("x")

    def open_project(path, password):
        """Accept only the password "pw"."""
        if password != "pw":
            raise PermissionError("wrong")
        return "Hus"

    imported = []
    monkeypatch.setattr("knxscada.project.load_project",
                        lambda cfg, force=False: imported.append(cfg.path) or {"gas": {"1/1/1": {}}, "floors": []})
    target = tmp_path / "config.yaml"
    w, said = _wizard(target, ["en", "y", str(project), "bad", "pw", "2", "", "n"], project=open_project)
    assert w.run() == 0
    c = yaml.safe_load(target.read_text())
    assert c["knx"]["connection"] == "usb" and c["knx"]["usb_device"] == "00C512345678"
    assert c["project"]["file"] == "Hus.knxproj" and c["project"]["password"] == "pw"
    assert c["ui"]["title"].endswith("Hus") and imported == [target]
    assert any("Project: Hus" in s for s in said)


def test_found_ip_interface_and_keyring(tmp_path, monkeypatch):
    """The IP interface found is chosen; KNX Secure makes the connection tunneling_secure with the keyring."""
    project = tmp_path / "Hus.knxproj"
    project.write_text("x")
    keys = tmp_path / "Hus.knxkeys"
    keys.write_text("k")
    monkeypatch.setattr("knxscada.project.load_project", lambda cfg, force=False: {"gas": {}, "floors": []})
    target = tmp_path / "config.yaml"
    w, _said = _wizard(target, ["en", "y", str(project), "1", "y", str(keys), "secret", "", "n"],
                       project=lambda path, pw: "Hus")
    assert w.run() == 0
    knx = yaml.safe_load(target.read_text())["knx"]
    assert (knx["connection"], knx["gateway_ip"], knx["keyring_file"], knx["keyring_password"]) == \
        ("tunneling_secure", "192.168.1.50", "Hus.knxkeys", "secret")


def test_password_never_accepted_writes_nothing(tmp_path):
    """After the allowed number of wrong passwords the wizard stops without writing a file."""
    project = tmp_path / "Hus.knxproj"
    project.write_text("x")
    target = tmp_path / "config.yaml"

    def refuse(path, password):
        """Every password is wrong."""
        raise PermissionError("wrong")

    w, said = _wizard(target, ["en", "y", str(project), *["x"] * setup_wizard.PASSWORD_TRIES], project=refuse)
    assert w.run() == 1 and not target.exists()
    assert any("not accepted" in s for s in said)


def test_existing_config_is_kept_unless_confirmed(tmp_path):
    """An existing config.yaml is only replaced after a yes."""
    target = tmp_path / "config.yaml"
    target.write_text("knx: {}\n")
    w, _said = _wizard(target, ["en", "n"])
    assert w.run() == 1 and target.read_text() == "knx: {}\n"
