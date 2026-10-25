# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""The OPC UA bridge against a local OPC UA server that looks like CODESYS.

CODESYS publishes GVL variables as ``ns=<n>;s=|var|<device>.Application.<GVL>.<var>``;
the test server does the same, so the bridge's GVL discovery is exercised too.
"""

import asyncio
import socket

import pytest
from asyncua import Server, ua

from knxscada import dpt as dptlib
from knxscada.config import Config
from knxscada.opcua_bridge import PlcBridge, parse_security
from knxscada.plc import PlcEntry, PlcMap

PREFIX = "|var|WAGO 750-8302 PFC300 2ETH RS.Application.GVL_KNX"
VARS = {  # var: (ga, dir, IEC type, DPT, initial PLC value, OPC UA type)
    "xLight": ("1/1/1", "both", "BOOL", "1.001", False, ua.VariantType.Boolean),
    "xLightStatus": ("1/1/2", "to_plc", "BOOL", "1.001", False, ua.VariantType.Boolean),
    "rTemp": ("3/0/1", "to_plc", "REAL", "9.001", 0.0, ua.VariantType.Float),
    "rSetpoint": ("3/0/2", "both", "REAL", "9.001", 20.0, ua.VariantType.Float),
    "usiMode": ("3/0/3", "both", "USINT", "20.102", 1, ua.VariantType.Byte),
    "rFromPlc": ("3/0/5", "to_knx", "REAL", "9.001", 0.0, ua.VariantType.Float),
}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def start_server(port: int, users: dict | None = None, secure: bool = False, tmp=None) -> tuple[Server, dict]:
    server = Server(user_manager=_Users(users) if users else None)
    await server.init()
    server.set_endpoint(f"opc.tcp://127.0.0.1:{port}")
    if secure:
        from asyncua.crypto.cert_gen import setup_self_signed_certificate
        from cryptography.x509.oid import ExtendedKeyUsageOID

        key, cert = tmp / "server_key.pem", tmp / "server_cert.der"
        await setup_self_signed_certificate(key, cert, "urn:test:server", "localhost",
                                            [ExtendedKeyUsageOID.SERVER_AUTH], {"commonName": "test"})
        await server.load_certificate(str(cert))
        await server.load_private_key(str(key))
        server.set_security_policy([ua.SecurityPolicyType.Basic256Sha256_SignAndEncrypt])
    if users:
        server.set_identity_tokens([ua.UserNameIdentityToken])
    idx = await server.register_namespace("urn:test:CODESYSSPV3")
    device = await server.nodes.objects.add_folder(idx, "DeviceSet")
    gvl = await device.add_object(ua.NodeId(PREFIX, idx), ua.QualifiedName("GVL_KNX", idx))
    nodes = {}
    for var, (_ga, _d, _t, _dpt, value, vt) in VARS.items():
        nodes[var] = await gvl.add_variable(ua.NodeId(f"{PREFIX}.{var}", idx), ua.QualifiedName(var, idx), ua.Variant(value, vt))
        await nodes[var].set_writable()
    await server.start()
    return server, nodes


class _Users:
    """Minimal user manager: user name → password."""

    def __init__(self, users: dict) -> None:
        self.users = users

    def get_user(self, iserver, username=None, password=None, certificate=None):
        from asyncua.crypto.permission_rules import User, UserRole

        if username in self.users and self.users[username] == password:
            return User(role=UserRole.User)
        return None


class FakeServer:
    """The parts of ScadaServer the bridge uses."""

    def __init__(self) -> None:
        self.state: dict = {}
        self.status: dict = {}
        self.events: list = []
        self.sent: list = []   # (ga, decoded value)
        self.gas = {ga: {"dpt": dpt} for ga, _d, _t, dpt, _v, _vt in VARS.values()}

    def event(self, level, kind, text, user=None):
        self.events.append((level, kind, text, user))

    def _broadcast(self, msg):
        pass

    async def _send(self, ga, kind, payload):
        self.sent.append((ga, dptlib.decode(self.gas[ga]["dpt"], payload)))


def make_bridge(port: int, tmp_path, **plc) -> tuple[PlcBridge, FakeServer]:
    entries = [PlcEntry(ga=ga, on=True, dir=d, type=t, var=var, name=var, dpt=dpt)
               for var, (ga, d, t, dpt, _v, _vt) in VARS.items()]
    cfg = Config(path=tmp_path / "config.yaml",
                 plc={"enabled": True, "endpoint": f"opc.tcp://127.0.0.1:{port}", "publish_interval_ms": 20,
                      "min_interval_ms": 150, **plc})
    fake = FakeServer()
    return PlcBridge(fake, cfg, PlcMap("marked", "GVL_KNX", entries)), fake


async def until(cond, timeout=5.0):
    """Wait until cond() is true (the subscription delivers asynchronously)."""
    end = asyncio.get_running_loop().time() + timeout
    while not cond():
        if asyncio.get_running_loop().time() > end:
            raise AssertionError("timed out")
        await asyncio.sleep(0.02)


async def run_bridge(bridge, fake):
    task = asyncio.create_task(bridge.run())
    await until(lambda: bridge.connected, 15)
    await asyncio.sleep(0.3)  # first subscription values
    return task


async def stop_bridge(bridge, task):
    await bridge.stop()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_parse_security():
    assert parse_security(None) == (None, "None")
    assert parse_security("None") == (None, "None")
    assert parse_security("Aes128Sha256RsaOaep,Sign") == ("SecurityPolicyAes128Sha256RsaOaep", "Sign")
    assert parse_security("basic256sha256") == ("SecurityPolicyBasic256Sha256", "SignAndEncrypt")
    for bad in ("Basic128Rsa15,Sign", "Basic256Sha256,Encrypt"):
        with pytest.raises(ValueError):
            parse_security(bad)


def test_bridge_both_directions(tmp_path):
    async def run():
        port = free_port()
        server, nodes = await start_server(port)
        try:
            bridge, fake = make_bridge(port, tmp_path)
            fake.state = {"1/1/2": {"value": True}, "3/0/1": {"value": 21.5}, "3/0/2": {"value": 21.0},
                          "3/0/3": {"value": "COMFORT"}}
            task = await run_bridge(bridge, fake)

            # Start-up: the PLC gets the last known KNX values, the bus gets nothing.
            assert await nodes["xLightStatus"].read_value() is True
            assert await nodes["rTemp"].read_value() == pytest.approx(21.5)
            assert await nodes["rSetpoint"].read_value() == pytest.approx(21.0)
            assert fake.sent == [], "no telegrams at start-up"
            assert fake.status["plc"] == "connected"

            # KNX → PLC
            bridge.on_knx("3/0/1", 22.25)
            await until(lambda: True)
            await asyncio.sleep(0.2)
            assert await nodes["rTemp"].read_value() == pytest.approx(22.25)

            # KNX → PLC on a "both" variable must not come back as a KNX write (echo).
            bridge.on_knx("1/1/1", True)
            await asyncio.sleep(0.4)
            assert await nodes["xLight"].read_value() is True
            assert fake.sent == []

            # PLC → KNX: the PLC program switches the light off and changes the setpoint.
            await nodes["xLight"].write_value(False)
            await nodes["rSetpoint"].write_value(ua.Variant(22.5, ua.VariantType.Float))
            await until(lambda: len(fake.sent) == 2)
            assert ("1/1/1", False) in fake.sent
            assert ("3/0/2", pytest.approx(22.5, abs=0.01)) in fake.sent
            assert any(e[1] == "operation" and e[3] == "plc" for e in fake.events)   # audit log

            # Our own outgoing telegram comes back through ingest → no write-back loop.
            bridge.on_knx("3/0/2", 22.5)
            await asyncio.sleep(0.4)
            assert len(fake.sent) == 2

            # HVAC mode as a number in the PLC (3 = economy).
            await nodes["usiMode"].write_value(ua.Variant(3, ua.VariantType.Byte))
            await until(lambda: ("3/0/3", "ECONOMY") in fake.sent)

            # to_plc variables are never sent to KNX, even if the PLC changes them.
            await nodes["rTemp"].write_value(ua.Variant(30.0, ua.VariantType.Float))
            await asyncio.sleep(0.4)
            assert not any(ga == "3/0/1" for ga, _v in fake.sent)
            await stop_bridge(bridge, task)
        finally:
            await server.stop()

    asyncio.run(run())


def test_rate_limit_sends_latest(tmp_path):
    async def run():
        port = free_port()
        server, nodes = await start_server(port)
        try:
            bridge, fake = make_bridge(port, tmp_path, min_interval_ms=600)
            task = await run_bridge(bridge, fake)
            for v in (1.0, 2.0, 3.0, 4.0):
                await nodes["rFromPlc"].write_value(ua.Variant(v, ua.VariantType.Float))
                await asyncio.sleep(0.08)
            await until(lambda: fake.sent and fake.sent[-1][1] == pytest.approx(4.0, abs=0.01), 3)
            await asyncio.sleep(0.8)
            values = [v for ga, v in fake.sent if ga == "3/0/5"]
            assert len(values) <= 2 and values[-1] == pytest.approx(4.0, abs=0.01)
            await stop_bridge(bridge, task)
        finally:
            await server.stop()

    asyncio.run(run())


def test_username_password(tmp_path):
    async def run():
        port = free_port()
        server, nodes = await start_server(port, users={"knx": "secret"})
        try:
            bridge, fake = make_bridge(port, tmp_path, username="knx", password="secret")
            fake.state = {"3/0/1": {"value": 19.5}}
            task = await run_bridge(bridge, fake)
            assert await nodes["rTemp"].read_value() == pytest.approx(19.5)
            await stop_bridge(bridge, task)

            wrong, fake2 = make_bridge(port, tmp_path, username="knx", password="wrong")
            task = asyncio.create_task(wrong.run())
            await until(lambda: any(e[1] == "plc" and e[0] == "error" for e in fake2.events), 15)
            assert not wrong.connected
            await stop_bridge(wrong, task)
        finally:
            await server.stop()

    asyncio.run(run())


def test_encrypted_connection_creates_client_certificate(tmp_path):
    async def run():
        port = free_port()
        server, nodes = await start_server(port, secure=True, tmp=tmp_path)
        try:
            bridge, fake = make_bridge(port, tmp_path, security="Basic256Sha256,SignAndEncrypt")
            fake.state = {"3/0/1": {"value": 18.0}}
            task = await run_bridge(bridge, fake)
            assert (tmp_path / "var/opcua/client_cert.der").exists()
            assert await nodes["rTemp"].read_value() == pytest.approx(18.0)
            await stop_bridge(bridge, task)
        finally:
            await server.stop()

    asyncio.run(run())


def test_missing_gvl_is_reported(tmp_path):
    async def run():
        port = free_port()
        server, _nodes = await start_server(port)
        try:
            bridge, fake = make_bridge(port, tmp_path)
            bridge.gvl = "GVL_OTHER"
            task = asyncio.create_task(bridge.run())
            await until(lambda: any("GVL_OTHER" in e[2] for e in fake.events), 15)
            await stop_bridge(bridge, task)
        finally:
            await server.stop()

    asyncio.run(run())


def test_new_variables_after_online_change_are_picked_up(tmp_path):
    """Variables missing at connect (old GVL in the PLC) are used once they appear – without telegrams."""
    async def run():
        port = free_port()
        server, nodes = await start_server(port)
        try:
            idx = nodes["rTemp"].nodeid.NamespaceIndex
            await server.delete_nodes([nodes["rTemp"]])               # the PLC still runs the old GVL
            bridge, fake = make_bridge(port, tmp_path, missing_recheck_s=0.5)
            fake.state = {"3/0/1": {"value": 20.5}}
            task = await run_bridge(bridge, fake)
            assert "rTemp" not in bridge.nodes
            assert any("missing" in e[2] for e in fake.events)
            # "Online change": the variable appears.
            gvl = server.get_node(ua.NodeId(PREFIX, idx))
            new = await gvl.add_variable(ua.NodeId(f"{PREFIX}.rTemp", idx), ua.QualifiedName("rTemp", idx),
                                         ua.Variant(0.0, ua.VariantType.Float))
            await new.set_writable()
            await until(lambda: "rTemp" in bridge.nodes and bridge.connected, 20)
            await until(lambda: True)
            await asyncio.sleep(0.3)
            assert await new.read_value() == pytest.approx(20.5)   # synced from the last KNX value
            assert fake.sent == []                                  # still no telegrams
            assert not any(e[2] == "PLC (OPC UA) disconnected" for e in fake.events)  # planned, not an error
            await stop_bridge(bridge, task)
        finally:
            await server.stop()

    asyncio.run(run())


# --------------------------------------------------------------------------- PLC restart (download) while connected
class _Fake:
    """Minimal ScadaServer for the restart tests."""

    def __init__(self, dpts):
        self.state, self.status, self.events, self.sent = {}, {}, [], []
        self.dpts = dpts

    def event(self, level, kind, text, user=None):
        self.events.append((level, kind, text, user))

    def _broadcast(self, msg):
        pass

    async def _send(self, ga, kind, payload):
        self.sent.append((ga, dptlib.decode(self.dpts[ga], payload)))


def restart_bridge(tmp_path, n_lights=12, **plc):
    """A bridge with n lights (BOOL, both) and one setpoint, 'connected' without a real PLC."""
    entries = [PlcEntry(ga=f"1/1/{i}", on=True, dir="both", type="BOOL", var=f"x{i}", name=f"L{i}", dpt="1.001")
               for i in range(n_lights)]
    entries.append(PlcEntry(ga="3/1/0", on=True, dir="both", type="REAL", var="rSp", name="SP", dpt="9.001"))
    fake = _Fake({e.ga: e.dpt for e in entries})
    cfg = Config(path=tmp_path / "config.yaml", plc={"enabled": True, "settle_ms": 50, "restart_quiet_s": 0.3,
                                                     "min_interval_ms": 0, **plc})
    bridge = PlcBridge(fake, cfg, PlcMap("all", "GVL_KNX", entries))
    bridge.by_nodeid = {e.var: e for e in entries}   # node id = variable name here
    bridge.nodes = {e.var: object() for e in entries}
    bridge.connected = True
    for e in entries:                                  # first reports after connecting: only remembered
        bridge._on_plc(e.var, True if e.type == "BOOL" else 21.0)
    return bridge, fake


def test_download_without_values_sends_nothing(tmp_path):
    """What happened on 25.10.2026: values without content, then the new application's zeros."""
    async def run():
        bridge, fake = restart_bridge(tmp_path, n_lights=3)       # few variables: the burst guard is not needed
        synced = []

        async def fake_sync():
            synced.append(True)
        bridge._sync_to_plc = fake_sync
        for var in ("x0", "x1", "x2", "rSp"):
            bridge._on_plc(var, None, good=False)               # CODESYS is loading the application
        await asyncio.sleep(0.1)
        for var in ("x0", "x1", "x2"):
            bridge._on_plc(var, False)                          # the new application starts with FALSE …
        bridge._on_plc("rSp", 0.0)                              # … and 0.0 °C
        await asyncio.sleep(0.5)
        assert fake.sent == [], "a PLC download must never reach the bus"
        assert any("PLC restart detected" in e[2] for e in fake.events)
        assert synced, "after the quiet time the PLC gets the KNX values again"
        # Afterwards the PLC program works normally again.
        bridge._on_plc("x1", True)
        await asyncio.sleep(0.2)
        assert fake.sent == [("1/1/1", True)]

    asyncio.run(run())


def test_many_changes_at_once_are_a_restart(tmp_path):
    async def run():
        bridge, fake = restart_bridge(tmp_path, n_lights=12)
        bridge._sync_to_plc = lambda: asyncio.sleep(0)
        for i in range(12):
            bridge._on_plc(f"x{i}", False)                      # all lights "off" in one go
        bridge._on_plc("rSp", 0.0)
        await asyncio.sleep(0.2)
        assert fake.sent == []
        assert any("13" in e[2] for e in fake.events if e[0] == "error")

    asyncio.run(run())


def test_normal_change_is_sent_after_the_settle_time(tmp_path):
    async def run():
        bridge, fake = restart_bridge(tmp_path)
        bridge._on_plc("x3", False)
        bridge._on_plc("rSp", 22.5)
        await asyncio.sleep(0.02)
        assert fake.sent == []                                   # held for settle_ms
        await asyncio.sleep(0.15)
        assert ("1/1/3", False) in fake.sent and ("3/1/0", pytest.approx(22.5, abs=0.01)) in fake.sent

    asyncio.run(run())


def test_burst_guard_can_be_turned_off(tmp_path):
    async def run():
        bridge, fake = restart_bridge(tmp_path, burst_limit=0)
        for i in range(12):
            bridge._on_plc(f"x{i}", False)
        await asyncio.sleep(0.2)
        assert len(fake.sent) == 12

    asyncio.run(run())


def test_missing_value_is_never_false():
    from knxscada.plc import to_knx_value

    with pytest.raises(ValueError):
        to_knx_value(PlcEntry(ga="1/1/1", on=True, dir="both", type="BOOL", var="x", dpt="1.001"), None)
