"""``./scada service``: the systemd unit made from deploy/knxscada.service, and what is printed without root."""

from __future__ import annotations

import os

import pytest

from knxscada import service
from knxscada.config import Config


def test_render_unit_for_a_site(tmp_path):
    """Placeholders are filled in, -c points at the site's config, ReadWritePaths is only the site's var/, the
    hardening of the template is kept and the manual sed instructions are replaced by a note."""
    text = service.render_unit(service.TEMPLATE.read_text(encoding="utf-8"), install_dir=tmp_path / "knx scada",
                               user="knx", group="site1", config=tmp_path / "sites/site1/config.yaml",
                               var_dir=tmp_path / "sites/site1/var", description="Site 1")
    assert "INSTALL_DIR" not in text and "YOUR_USER" not in text and "sed -e" not in text
    assert f"ExecStart='{tmp_path}/knx scada/scada' -c {tmp_path}/sites/site1/config.yaml server" in text
    assert f"ReadWritePaths={tmp_path}/sites/site1/var\n" in text
    assert "User=knx\nGroup=site1\n" in text and "Description=EKBPRO KNX Terminal Python server – Site 1" in text
    assert "ProtectSystem=strict" in text and "NoNewPrivileges=yes" in text


def test_default_name():
    """knxscada for the default config.yaml, knxscada-<folder> for a site."""
    assert service.default_name(Config(path=service.TEMPLATE.parent.parent / "config.yaml")) == "knxscada"
    assert service.default_name(Config(path=service.TEMPLATE.parent.parent / "sites/hus/config.yaml")) == \
        "knxscada-hus"


@pytest.mark.skipif(os.geteuid() == 0, reason="as root the commands would be run")
def test_install_without_root_only_prints(tmp_path, monkeypatch, capsys):
    """Without root nothing is installed: the unit is written to the staging folder and the sudo commands (install,
    daemon-reload, enable, restart) are printed. A bad name is refused."""
    monkeypatch.setattr(service, "STAGING", tmp_path / "systemd")
    site = tmp_path / "sites" / "hus"
    site.mkdir(parents=True)
    cfg = Config(path=site / "config.yaml")
    assert service.run(cfg, "install") == 0
    out = capsys.readouterr().out
    staged = tmp_path / "systemd" / "knxscada-hus.service"
    assert staged.exists() and f"-c {site}/config.yaml server" in staged.read_text()
    assert "sudo systemctl enable knxscada-hus" in out and "sudo systemctl restart knxscada-hus" in out
    assert service.run(cfg, "remove", "bad name") == 1
