# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""OPC UA: client of a PLC or another server (``role: client``), or our own server (``role: server``).

Client
------
Tags name a node id (``node: "ns=4;s=|var|PLC.Application.GVL.rTemp"``) and
are followed with a subscription. The value type is taken from the server
when the tag has none. Writes carry no timestamps (CODESYS rejects them).
User name/password, security policies and certificates work as for the PLC
bridge (:func:`knxscada.opcua_bridge.make_client`); ``./scada plc-browse`` lists node ids.

Server
------
SCADA systems (Ignition, WinCC, …) connect to us. Every gateway target
becomes a variable; dots or slashes in its name make folders::

    to: Building.Room101.Temperature   →   Objects/Gateway/Building/Room101/Temperature
                                            node id ns=2;s=Building.Room101.Temperature

The node id is the tag name, so it does not change when the server restarts.
Targets mapped with ``direction: both`` are writable; what a client writes goes
back to the source. Anonymous access unless ``username``/``password`` are set;
with a ``security`` policy the server makes its own certificate
(``var/opcua/server_cert.der``) on first start.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..i18n import _
from .base import Driver as BaseDriver
from .base import Tag, tag_from_spec

#: OPC UA variant type name → tag type.
VARIANT_TYPES = {"Boolean": "bool", "SByte": "int", "Byte": "int", "Int16": "int", "UInt16": "int", "Int32": "int",
                 "UInt32": "int", "Int64": "int", "UInt64": "int", "Float": "float", "Double": "float",
                 "String": "str"}

#: Tag type → variant type name of the server's variables.
SERVER_TYPES = {"bool": "Boolean", "int": "Int64", "float": "Double", "str": "String", "": "String"}

#: Default values of new server variables (until the first value arrives).
DEFAULTS = {"Boolean": False, "Int64": 0, "Double": 0.0, "String": ""}

SERVER_APP_URI = "urn:ekbpro:knx-terminal-python:gateway"


class _Users:
    """One user name and password (asyncua user manager)."""

    def __init__(self, username: str, password: str) -> None:
        self.username, self.password = username, password

    def get_user(self, iserver, username=None, password=None, certificate=None):
        from asyncua.crypto.permission_rules import User, UserRole

        if username == self.username and password == self.password:
            return User(role=UserRole.User)
        return None


class _Handler:
    """Subscription handler of the client."""

    def __init__(self, driver: Driver) -> None:
        self.d = driver

    def datachange_notification(self, node, val, data) -> None:
        status = getattr(getattr(getattr(data, "monitored_item", None), "Value", None), "StatusCode", None)
        tag = self.d.by_node.get(node.nodeid)
        if tag is None:
            return
        if status is not None and not status.is_good():
            rec = self.d.hub.values.get(f"{self.d.name}/{tag.name}")
            self.d.hub.update(self.d.name, tag.name, rec["v"] if rec else None, "bad")
            return
        self.d.publish(tag, val)

    def status_change_notification(self, status) -> None:
        self.d.lost.set()


async def server_certificate(cert: Path, key: Path) -> None:
    """A self-signed server certificate, made once."""
    if cert.exists() and key.exists():
        return
    from asyncua.crypto.cert_gen import setup_self_signed_certificate
    from cryptography.x509.oid import ExtendedKeyUsageOID

    cert.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    host = socket.gethostname()
    await setup_self_signed_certificate(
        key, cert, SERVER_APP_URI, host, [ExtendedKeyUsageOID.SERVER_AUTH],
        {"countryName": "NO", "organizationName": "EKBPRO", "commonName": f"EKBPRO gateway@{host}"})
    key.chmod(0o600)


class Driver(BaseDriver):
    protocol = "opcua"

    def __init__(self, name: str, settings: dict[str, Any], hub) -> None:
        super().__init__(name, settings, hub)
        self.echoes = self.role == "client"   # the subscription reports a written value
        self.client = None
        self.server = None
        self.idx = 2
        self.by_node: dict[Any, Tag] = {}
        self.nodes: dict[str, Any] = {}      # tag name → Node
        self.vtypes: dict[str, Any] = {}     # tag name → ua.VariantType
        self.folders: dict[str, Any] = {}
        self.lost = asyncio.Event()

    def make_tag(self, name: str, spec: dict[str, Any]) -> Tag:
        tag = tag_from_spec(name, spec, "node")
        if not tag.address:
            tag.address = name
        return tag

    def create_tag(self, name: str, spec: dict[str, Any]) -> Tag:
        if self.role != "server":
            return super().create_tag(name, spec)
        tag = tag_from_spec(name, spec)
        tag.address = name
        tag.writable = bool(spec.get("writable"))
        self.tags[name] = tag
        if self.server is not None:
            asyncio.get_running_loop().create_task(self._add_variable(tag))
        return tag

    async def session(self) -> None:
        if self.role == "server":
            await self._serve()
        else:
            await self._client_session()

    async def close(self) -> None:
        if self.client is not None:
            client, self.client = self.client, None
            with contextlib.suppress(Exception):
                await asyncio.wait_for(client.disconnect(), 3)
        if self.server is not None:
            server, self.server = self.server, None
            with contextlib.suppress(Exception):
                await asyncio.wait_for(server.stop(), 5)
        self.nodes.clear()
        self.by_node.clear()
        self.folders.clear()

    # -- client -----------------------------------------------------------------------------------
    async def _client_session(self) -> None:
        from ..opcua_bridge import make_client

        self.client = await make_client(self.hub.server.cfg, self.settings)
        await self.client.connect()
        missing = []
        for tag in self.tags.values():
            node = self.client.get_node(tag.address)
            try:
                vt = await node.read_data_type_as_variant_type()
            except Exception:  # noqa: BLE001 - no such node
                missing.append(tag.name)
                continue
            self.nodes[tag.name] = node
            self.vtypes[tag.name] = vt
            self.by_node[node.nodeid] = tag
            if not tag.type:
                tag.type = VARIANT_TYPES.get(vt.name, "")
        if missing:
            self.hub.event("error", _("{connection}: {n} nodes not found: {names}").format(
                connection=self.name, n=len(missing), names=", ".join(missing[:5]) + (" …" if len(missing) > 5 else "")))
        self.lost.clear()
        if self.nodes:
            sub = await self.client.create_subscription(int(self.settings.get("publish_interval_ms") or 500), _Handler(self))
            await sub.subscribe_data_change(list(self.nodes.values()))
        self.set_state(True)
        while not self._stop and not self.lost.is_set():
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.lost.wait(), 5)
            await self.client.check_connection()
        if self.lost.is_set() and not self._stop:
            raise ConnectionError(_("connection lost"))

    # -- server -----------------------------------------------------------------------------------
    async def _serve(self) -> None:
        from asyncua import Server, ua
        from asyncua.common.callback import CallbackType

        s = self.settings
        user = str(s.get("username") or "")
        server = Server(user_manager=_Users(user, str(s.get("password") or "")) if user else None)
        await server.init()
        server.set_endpoint(str(s.get("endpoint") or "opc.tcp://0.0.0.0:4840"))
        server.set_server_name(f"EKBPRO gateway {self.name}")
        await server.set_application_uri(SERVER_APP_URI)
        security = str(s.get("security") or "None")
        if security.lower() != "none":
            policy, _c, mode = security.partition(",")
            cfg = self.hub.server.cfg
            cert = cfg.resolve(str(s.get("certificate") or "var/opcua/server_cert.der"))
            key = cfg.resolve(str(s.get("private_key") or "var/opcua/server_key.pem"))
            await server_certificate(cert, key)
            await server.load_certificate(str(cert))
            await server.load_private_key(str(key))
            server.set_security_policy([getattr(ua.SecurityPolicyType, f"{policy}_{mode or 'SignAndEncrypt'}")])
        else:
            server.set_security_policy([ua.SecurityPolicyType.NoSecurity])
        server.set_identity_tokens([ua.UserNameIdentityToken] if user else [ua.AnonymousIdentityToken])
        self.idx = await server.register_namespace(str(s.get("namespace") or "urn:ekbpro:gateway"))
        self.folders[""] = await server.nodes.objects.add_folder(ua.NodeId("Gateway", self.idx),
                                                                 ua.QualifiedName("Gateway", self.idx))
        server.subscribe_server_callback(CallbackType.PostWrite, self._on_client_write)
        self.server = server
        for tag in list(self.tags.values()):
            await self._add_variable(tag)
        await server.start()
        self.set_state(True)
        await self.wait_stopped()

    async def _folder(self, path: list[str]):
        from asyncua import ua

        key = ".".join(path)
        if key in self.folders:
            return self.folders[key]
        parent = await self._folder(path[:-1])
        node = await parent.add_folder(ua.NodeId(key, self.idx), ua.QualifiedName(path[-1], self.idx))
        self.folders[key] = node
        return node

    async def _add_variable(self, tag: Tag) -> None:
        from asyncua import ua

        if tag.name in self.nodes or self.server is None:
            return
        parts = [p for p in tag.name.replace("/", ".").split(".") if p] or [tag.name]
        parent = await self._folder(parts[:-1])
        vt = getattr(ua.VariantType, SERVER_TYPES.get(tag.type, "String"))
        node = await parent.add_variable(ua.NodeId(tag.name, self.idx), ua.QualifiedName(parts[-1], self.idx),
                                         ua.Variant(DEFAULTS[vt.name], vt))
        if tag.description:
            with contextlib.suppress(Exception):
                await node.write_attribute(ua.AttributeIds.Description,
                                           ua.DataValue(ua.Variant(ua.LocalizedText(tag.description))))
        if tag.writable:
            await node.set_writable()
        self.nodes[tag.name] = node
        self.vtypes[tag.name] = vt
        self.by_node[node.nodeid] = tag
        rec = self.hub.values.get(f"{self.name}/{tag.name}")
        if rec is not None and rec.get("v") is not None:
            # A value that does not fit the variable must not stop the server: it keeps its default.
            with contextlib.suppress(TypeError, ValueError):
                await self.write(tag, rec["v"])

    async def _on_client_write(self, event, _dispatcher) -> None:
        """A client wrote variables: pass the values on (only good writes of the Value attribute)."""
        from asyncua import ua

        results = event.response_params or []
        for i, wv in enumerate(event.request_params.NodesToWrite):
            if wv.AttributeId != ua.AttributeIds.Value or (i < len(results) and not results[i].is_good()):
                continue
            tag = self.by_node.get(wv.NodeId)
            if tag is not None:
                self.publish(tag, wv.Value.Value.Value)

    # -- values -----------------------------------------------------------------------------------
    async def write(self, tag: Tag, value: Any, user: str = "") -> None:
        from asyncua import ua

        node = self.nodes.get(tag.name)
        if node is None:
            if self.role == "server":
                return   # added when the server starts; the value is set then
            raise RuntimeError(_("{connection}: node {node} not found").format(connection=self.name, node=tag.address))
        vt = self.vtypes[tag.name]
        raw = tag.to_raw(value)
        try:
            if vt.name == "String" and not isinstance(raw, str):
                raw = json.dumps(raw, ensure_ascii=False) if isinstance(raw, (dict, list)) else str(raw)
            elif vt.name == "Boolean":
                raw = bool(raw)
            elif vt.name in ("Float", "Double"):
                raw = float(raw)
            elif vt.name in VARIANT_TYPES:
                raw = round(float(raw))
        except (TypeError, ValueError):
            raise ValueError(_("{value} does not fit the {type} variable {node}").format(
                value=repr(raw), type=vt.name, node=tag.address)) from None
        if self.role == "server":
            dv = ua.DataValue(ua.Variant(raw, vt))
            dv.SourceTimestamp = dv.ServerTimestamp = datetime.now(UTC)
            await self.server.write_attribute_value(node.nodeid, dv)
            return
        await node.write_value(ua.DataValue(ua.Variant(raw, vt)))   # no timestamps: CODESYS rejects them

    def describe(self) -> str:
        return str(self.settings.get("endpoint") or "")
