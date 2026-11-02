# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""Several KNX IP interfaces: configuration, routing of outgoing telegrams, de-duplication."""

import asyncio
import json
import sqlite3

import pytest
from xknx.dpt import DPTBinary
from xknx.telegram import GroupAddress, IndividualAddress, Telegram, TelegramDirection
from xknx.telegram.apci import GroupValueWrite

from knxscada import project
from knxscada.config import Config
from knxscada.knx_interfaces import Deduplicator, Router, interface_configs
from knxscada.plc import var_name
from knxscada.project import build_model, ga_key, merge_models, split_key
from knxscada.store import Store
from tests.test_project import raw_project

TWO = {
    "keyring_password": "secret",
    "interfaces": [
        {"name": "house", "gateway_ip": "192.168.1.50"},
        {"name": "garage", "gateway_ip": "192.168.1.51", "addresses": ["4/*", "5/1/*"]},
    ],
}


# --------------------------------------------------------------------------- configuration
def test_single_interface_form_still_works():
    """The old form (one gateway directly under knx:) still gives one interface named 'knx'."""
    (c,) = interface_configs({"connection": "tunneling", "gateway_ip": "192.168.1.50"})
    assert c["name"] == "knx"
    assert c["connection"] == "tunneling"
    assert c["gateway_ip"] == "192.168.1.50"
    assert c["addresses"] == []


def test_empty_knx_section_is_automatic():
    """An empty knx: section means automatic gateway discovery."""
    (c,) = interface_configs({})
    assert c["connection"] == "automatic"


def test_interfaces_inherit_knx_level_defaults():
    """Shared settings (keyring password, connection type) are inherited from the knx: level; per-interface keys such as
    the port are not.
    """
    house, garage = interface_configs({**TWO, "connection": "tunneling", "gateway_port": 9999})
    assert house["keyring_password"] == "secret" and garage["keyring_password"] == "secret"
    assert house["connection"] == garage["connection"] == "tunneling"
    assert "gateway_port" not in house          # per-interface key: not inherited
    assert garage["addresses"] == ["4/*", "5/1/*"]


def test_name_defaults_to_gateway_ip():
    """An interface without a name is named after its gateway IP."""
    (c,) = interface_configs({"interfaces": [{"gateway_ip": "10.0.0.5"}]})
    assert c["name"] == "10.0.0.5"


def test_address_pattern_as_a_single_string():
    """addresses: may be one pattern instead of a list."""
    (c,) = interface_configs({"interfaces": [{"name": "a", "addresses": "4/*"}]})
    assert c["addresses"] == ["4/*"]


@pytest.mark.parametrize("knx", [
    {"interfaces": [{"name": "a"}, {"name": "A"}]},                                # duplicate (case-insensitive)
    {"interfaces": [{"name": "with space"}]},                                       # invalid name
    {"interfaces": [{"name": "a", "connection": "tunnelling"}]},                    # typo
    {"interfaces": [{"name": "a", "default": True}, {"name": "b", "default": True}]},
    {"interfaces": "192.168.1.50"},                                                 # not a list
])
def test_invalid_configuration(knx):
    """Duplicate or invalid names, a misspelt connection type, two defaults and a non-list are refused at start-up."""
    with pytest.raises(ValueError):
        interface_configs(knx)


# --------------------------------------------------------------------------- routing
def test_route_by_pattern_then_learned_then_default():
    """Outgoing telegrams go by address pattern first, then the interface the address was last seen on, then the
    default.
    """
    r = Router(interface_configs(TWO))
    assert r.route("4/2/7") == "garage"       # pattern
    assert r.route("5/1/0") == "garage"
    assert r.route("5/2/0") == "house"        # no pattern, not seen: default (first)
    r.learn("5/2/0", "garage")
    assert r.route("5/2/0") == "garage"       # learned
    r.learn("4/2/7", "house")
    assert r.route("4/2/7") == "garage"       # a pattern wins over what was learned


def test_route_default_interface():
    """default: true picks the interface for addresses that are not matched or learned."""
    cfgs = interface_configs({"interfaces": [{"name": "a"}, {"name": "b", "default": True}]})
    assert Router(cfgs).route("1/1/1") == "b"


def test_route_single_interface_ignores_everything():
    """With one interface every address goes through it, whatever was learned."""
    r = Router(interface_configs({"gateway_ip": "x"}))
    r.learn("1/1/1", "other")
    assert r.route("1/1/1") == "knx"


def test_pattern_does_not_match_a_longer_main_group():
    """The pattern '4/*' matches main group 4 only, not 14."""
    r = Router(interface_configs({"interfaces": [{"name": "a"}, {"name": "b", "addresses": ["4/*"]}]}))
    assert r.route("14/1/1") == "a"


# --------------------------------------------------------------------------- de-duplication
def test_copy_from_another_interface_is_dropped():
    """The same telegram seen shortly after through another interface (via a coupler) is a duplicate."""
    d = Deduplicator(1000)
    assert not d.duplicate("house", "1/1/1", "write", "01", now=10.0)
    assert d.duplicate("garage", "1/1/1", "write", "01", now=10.2)


def test_same_interface_twice_is_kept():
    """Repeats on the same interface are real telegrams (e.g. a button pressed twice) and are kept."""
    d = Deduplicator(1000)
    assert not d.duplicate("house", "1/1/1", "write", "01", now=10.0)
    assert not d.duplicate("house", "1/1/1", "write", "01", now=10.1)


def test_different_value_or_late_copy_is_kept():
    """Another value or a copy outside the time window is not a duplicate."""
    d = Deduplicator(1000)
    assert not d.duplicate("house", "1/1/1", "write", "01", now=10.0)
    assert not d.duplicate("garage", "1/1/1", "write", "00", now=10.1)   # other value
    assert not d.duplicate("house", "1/1/2", "write", "01", now=20.0)
    assert not d.duplicate("garage", "1/1/2", "write", "01", now=21.5)   # outside the window


def test_dedup_off():
    """A window of 0 turns de-duplication off."""
    d = Deduplicator(0)
    assert not d.duplicate("house", "1/1/1", "write", "01", now=10.0)
    assert not d.duplicate("garage", "1/1/1", "write", "01", now=10.0)


# --------------------------------------------------------------------------- the server with two interfaces
class FakeXknx:
    """Collects the telegrams the server puts in the queue."""

    def __init__(self):
        """The fake is its own telegram queue, as the server only calls xknx.telegrams.put()."""
        self.telegrams = self
        self.sent: list[Telegram] = []

    async def put(self, telegram):
        """Keep the telegram instead of sending it."""
        self.sent.append(telegram)


class State:
    """A stand-in for xknx's connection state (only its name is used)."""
    def __init__(self, name):
        """Name as in xknx's XknxConnectionState, e.g. 'CONNECTED'."""
        self.name = name


@pytest.fixture
def server(tmp_path):
    """A ScadaServer with the interfaces 'house' and 'garage' replaced by fakes; nothing is connected."""
    from knxscada.server import ScadaServer

    model = project.build_model(raw_project())
    (tmp_path / "cache.json").write_text(json.dumps(model), encoding="utf-8")
    cfg = Config(path=tmp_path / "config.yaml", knx=TWO,
                 project={"file": "none.knxproj", "cache": "cache.json"},
                 server={"database": "db.sqlite", "socket": "s.sock"})
    s = ScadaServer(cfg)
    s.xknx = {"house": FakeXknx(), "garage": FakeXknx()}
    yield s
    s.store.close()


def telegram(ga, value, direction=TelegramDirection.INCOMING, src="1.1.24"):
    """An incoming (or outgoing) group write with a binary value."""
    return Telegram(destination_address=GroupAddress(ga), source_address=IndividualAddress(src),
                    direction=direction, payload=GroupValueWrite(DPTBinary(value)))


def test_server_status_per_interface(server):
    """The status lists each interface and is 'partly connected' until all are connected."""
    assert [i["name"] for i in server.status["interfaces"]] == ["house", "garage"]
    assert server.status["gateway"] == "192.168.1.50, 192.168.1.51"
    server._on_conn_state("house", State("CONNECTED"))
    assert server.status["connected"] and server.status["connection"] == "partly connected"
    server._on_conn_state("garage", State("CONNECTED"))
    assert server.status["connection"] == "connected"
    server._on_conn_state("house", State("DISCONNECTED"))
    server._on_conn_state("garage", State("DISCONNECTED"))
    assert not server.status["connected"]


def test_server_logs_a_telegram_once_and_learns_the_interface(server):
    """A telegram seen through two interfaces is logged once, with the interface it came through first, and the router
    learns it.
    """
    server._on_telegram("garage", telegram("1/1/2", 1))
    server._on_telegram("house", telegram("1/1/2", 1, src="1.0.250"))   # the copy through the coupler
    assert server.tel_count == 1
    (t,) = server.store.telegrams()
    assert t["via"] == "garage" and t["src"] == "1.1.24"
    assert server.router.route("1/1/2") == "garage"


def test_server_sends_through_the_routed_interface(server):
    """Writes go through the interface the router picks, not through all of them."""
    for name in ("house", "garage"):
        server._on_conn_state(name, State("CONNECTED"))
    asyncio.run(server._send("4/0/1", "write", DPTBinary(1)))
    asyncio.run(server._send("1/1/1", "write", DPTBinary(1)))
    assert [str(t.destination_address) for t in server.xknx["garage"].sent] == ["4/0/1"]
    assert [str(t.destination_address) for t in server.xknx["house"].sent] == ["1/1/1"]


def test_server_refuses_to_send_through_a_disconnected_interface(server):
    """A write for a disconnected interface is refused with its name, not sent elsewhere."""
    server._on_conn_state("house", State("CONNECTED"))
    with pytest.raises(RuntimeError, match="garage"):
        asyncio.run(server._send("4/0/1", "write", DPTBinary(1)))


def test_own_telegram_seen_by_the_other_interface_is_logged_once(server):
    """Our own write echoed back through the other interface is logged once as outgoing and does not change the routing.
    """
    server._on_telegram("house", telegram("1/1/1", 1, direction=TelegramDirection.OUTGOING, src="0.0.0"))
    server._on_telegram("garage", telegram("1/1/1", 1, src="1.0.250"))
    (t,) = server.store.telegrams()
    assert t["dir"] == "out"
    assert server.router.route("1/1/1") == "house"   # our own telegram does not teach the router


def test_unknown_connection_type_stops_the_server(tmp_path):
    """A misspelt connection type stops the server with the wrong word in the message instead of connecting
    automatically.
    """
    from knxscada.server import ScadaServer

    model = project.build_model(raw_project())
    (tmp_path / "cache.json").write_text(json.dumps(model), encoding="utf-8")
    cfg = Config(path=tmp_path / "config.yaml", knx={"connection": "tunnelling"},
                 project={"file": "none.knxproj", "cache": "cache.json"}, server={"database": "db.sqlite"})
    with pytest.raises(ValueError, match="tunnelling"):
        ScadaServer(cfg)


# --------------------------------------------------------------------------- database
def test_old_database_gets_the_via_column(tmp_path):
    """A database from before several interfaces gets the via column; old rows have none."""
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE telegrams (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, dir TEXT NOT NULL,"
                " src TEXT NOT NULL, dst TEXT NOT NULL, type TEXT NOT NULL, raw TEXT, value TEXT, num REAL)")
    con.execute("INSERT INTO telegrams(ts, dir, src, dst, type) VALUES (1, 'in', '1.1.1', '1/1/1', 'write')")
    con.commit()
    con.close()
    store = Store(db)
    store.add_telegram({"ts": 2, "dir": "in", "src": "1.1.1", "dst": "1/1/1", "type": "write", "value": 1,
                        "via": "garage"})
    store.commit()
    old, new = store.telegrams()
    assert old["via"] is None and new["via"] == "garage"
    store.close()


# --------------------------------------------------------------------------- several ETS projects in one site

SITE = [{"name": "main", "label": "Main", "prefix": ""}, {"name": "annex", "label": "Annex", "prefix": "S"}]


def test_key_round_trip():
    """Address keys get the project prefix ('S:1/2/3'), and the main project has none."""
    assert ga_key("", "1/2/3") == "1/2/3"
    assert ga_key("S", "1/2/3") == "S:1/2/3"
    assert split_key("S:1/2/3") == ("S", "1/2/3")
    assert split_key("1/2/3") == ("", "1/2/3")


def test_config_projects(tmp_path):
    """project.file gives one project 'main'; project.files gives several with paths relative to config.yaml; duplicate
    names are refused.
    """
    single = Config(path=tmp_path / "config.yaml", project={"file": "a.knxproj"})
    assert [(p["name"], p["prefix"]) for p in single.projects] == [("main", "")]
    two = Config(path=tmp_path / "config.yaml",
                 project={"files": [{"name": "hb", "file": "a.knxproj"}, {"name": "sb", "file": "b.knxproj", "prefix": "S"}]})
    assert [(p["name"], p["prefix"]) for p in two.projects] == [("hb", ""), ("sb", "S")]
    assert two.projects[1]["file"] == tmp_path / "b.knxproj"
    clash = Config(path=tmp_path / "config.yaml",
                   project={"files": [{"name": "a", "file": "a"}, {"name": "a", "file": "b"}]})
    with pytest.raises(ValueError):
        _ = clash.projects


def test_merge_keeps_both_projects_apart():
    """Two projects with the same addresses are merged without clashes: every reference of the annex uses its prefix and
    each project gets its own address tree.
    """
    m = merge_models(SITE, [build_model(raw_project()), build_model(raw_project())])
    assert "1/1/1" in m["gas"] and "S:1/1/1" in m["gas"]
    assert m["gas"]["S:1/1/1"]["address"] == "S:1/1/1"
    assert len(m["gas"]) == 2 * len(build_model(raw_project())["gas"])
    # Every reference of the annex points to annex keys.
    annex_rooms = [r for f in m["floors"] if f["name"].startswith("Annex: ") for r in f["rooms"]]
    assert annex_rooms and all(r["id"].startswith("S:") for r in annex_rooms)
    for r in annex_rooms:
        for fid in r["functions"]:
            f = m["functions"][fid]
            assert all((d["ga"] or "S:").startswith("S:") and (d["status"] or "S:").startswith("S:")
                       for d in f["datapoints"])
    # The address tree gets one top node per project.
    assert [(r["name"], r["project"]) for r in m["ranges"]] == [("Main", ""), ("Annex", "S")]
    assert all(a.startswith("S:") for r in m["ranges"][1]["children"] for a in r["gas"] + [r["address"]])


def test_plc_variable_name_with_prefix():
    """The project prefix is part of the GVL variable name, so the names stay unique."""
    assert var_name("S:1/2/3", "Light") == "GA_S_1_2_3_Light"


def test_router_keeps_projects_apart():
    """An interface bound to a project only gets that project's addresses; an unknown prefix has no route."""
    cfgs = interface_configs({"interfaces": [{"name": "hb"}, {"name": "sb", "project": "annex"}]},
                             {"main": "", "annex": "S"})
    r = Router(cfgs)
    assert r.route("1/1/1") == "hb"
    assert r.route("S:1/1/1") == "sb"
    assert r.route("X:1/1/1") is None


def test_interface_with_unknown_project():
    """An interface naming an unknown project is refused."""
    with pytest.raises(ValueError, match="nope"):
        interface_configs({"interfaces": [{"name": "a", "project": "nope"}]}, {"main": ""})


@pytest.fixture
def site_server(tmp_path):
    """A ScadaServer with two ETS projects (main and annex 'S'), each on its own connected fake interface."""
    from knxscada.server import ScadaServer

    m = merge_models(SITE, [build_model(raw_project()), build_model(raw_project())])
    (tmp_path / "cache.json").write_text(json.dumps(m), encoding="utf-8")
    cfg = Config(path=tmp_path / "config.yaml",
                 knx={"interfaces": [{"name": "hb", "gateway_ip": "10.0.0.1"},
                                     {"name": "sb", "gateway_ip": "10.0.0.2", "project": "annex"}]},
                 project={"files": [{"name": "main", "file": "a.knxproj", "label": "Main"},
                                    {"name": "annex", "file": "b.knxproj", "label": "Annex", "prefix": "S"}],
                          "cache": "cache.json"},
                 server={"database": "db.sqlite", "socket": "s.sock"})
    s = ScadaServer(cfg)
    s.xknx = {"hb": FakeXknx(), "sb": FakeXknx()}
    for name in ("hb", "sb"):
        s._on_conn_state(name, State("CONNECTED"))
    yield s
    s.store.close()


def test_same_address_in_two_projects_is_two_addresses(site_server):
    """The same address in two buildings is two addresses, not a duplicate telegram."""
    s = site_server
    s._on_telegram("hb", telegram("1/1/2", 1))
    s._on_telegram("sb", telegram("1/1/2", 0))     # same number, other building: not a copy
    assert s.state["1/1/2"]["value"] is True
    assert s.state["S:1/1/2"]["value"] is False
    assert [t["dst"] for t in s.store.telegrams()] == ["1/1/2", "S:1/1/2"]


def test_annex_address_is_sent_through_the_annex_interface(site_server):
    """An annex address is sent through the annex interface with the prefix removed on the bus."""
    s = site_server
    asyncio.run(s._send("S:1/1/1", "write", DPTBinary(1)))
    asyncio.run(s._send("1/1/1", "write", DPTBinary(1)))
    assert [str(t.destination_address) for t in s.xknx["sb"].sent] == ["1/1/1"]   # prefix stripped on the bus
    assert [str(t.destination_address) for t in s.xknx["hb"].sent] == ["1/1/1"]
