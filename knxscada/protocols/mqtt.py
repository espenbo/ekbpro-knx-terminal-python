# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""MQTT: subscribe to topics and publish values to a broker.

Tags
----
* Tags under ``tags:`` follow a topic (``topic:``, default *topic_prefix* +
  the tag name). Writing such a tag publishes to its ``set_topic`` when it
  has one, otherwise to the topic itself.
* ``subscribe:`` lists topic filters (``sensors/#``); every topic that
  arrives becomes a tag by itself, named after the topic without the prefix.
* A gateway target (``to: Room101/Temp``) is published to *topic_prefix* +
  the name, retained, so a client that connects later gets the last value.
  With ``direction: both`` the gateway also listens to ``<topic>/set`` and
  passes what arrives there back to the source.

Payloads are plain text (``21.5``, ``true``) or, with ``payload: json``,
``{"value": 21.5, "ts": 1730000000.0}``. Incoming JSON is understood in either
format; ``json_key`` picks the value out of a larger JSON object.
"""

from __future__ import annotations

import json
import time
from typing import Any

from ..i18n import _
from .base import TYPES, Tag, as_float
from .base import Driver as BaseDriver


def parse_payload(payload: bytes | str, json_key: str = "") -> Any:
    """Payload → value: JSON (an object's ``json_key`` or ``value``), a number, true/false or text."""
    text = payload.decode("utf-8", "replace") if isinstance(payload, (bytes, bytearray)) else str(payload)
    text = text.strip()
    if text[:1] in "{[\"" or text in ("true", "false", "null"):
        try:
            data = json.loads(text)
        except ValueError:
            data = text
        if isinstance(data, dict):
            key = json_key or "value"
            if key in data:
                return data[key]
            return text
        return data
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


def make_payload(value: Any, fmt: str) -> str:
    if fmt == "json":
        return json.dumps({"value": value, "ts": round(time.time(), 3)}, ensure_ascii=False)
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def topic_matches(pattern: str, topic: str) -> bool:
    """MQTT wildcard match (``+`` one level, ``#`` the rest)."""
    p, t = pattern.split("/"), topic.split("/")
    for i, part in enumerate(p):
        if part == "#":
            return True
        if i >= len(t) or (part != "+" and part != t[i]):
            return False
    return len(p) == len(t)


class Driver(BaseDriver):
    protocol = "mqtt"

    def __init__(self, name: str, settings: dict[str, Any], hub) -> None:
        self.prefix = str(settings.get("topic_prefix") or "")
        self.fmt = str(settings.get("payload") or "plain")
        self.qos = int(settings.get("qos") or 0)
        self.retain = bool(settings.get("retain", True))
        subs = settings.get("subscribe") or []
        self.filters = [self.prefix + f for f in ([subs] if isinstance(subs, str) else subs) if f]
        self.by_topic: dict[str, list[Tag]] = {}
        self.by_set: dict[str, Tag] = {}
        self.client = None
        super().__init__(name, settings, hub)

    def make_tag(self, name: str, spec: dict[str, Any]) -> Tag:
        typ = str(spec.get("type") or "")
        topic = str(spec.get("topic") or self.prefix + name)
        tag = Tag(name=name, address=topic, type=typ if typ in TYPES else "", writable=bool(spec.get("writable", True)),
                  unit=str(spec.get("unit") or ""), description=str(spec.get("description") or ""),
                  scale=as_float(spec.get("scale")), offset=as_float(spec.get("offset")), extra=dict(spec))
        self.by_topic.setdefault(topic, []).append(tag)
        return tag

    def create_tag(self, name: str, spec: dict[str, Any]) -> Tag:
        """A gateway target: published under the prefix; with ``direction: both`` also listening to ``/set``."""
        name = name.replace("+", "_").replace("#", "_")
        typ = str(spec.get("type") or "")
        tag = Tag(name=name, address=self.prefix + name, type=typ if typ in TYPES else "",
                  writable=bool(spec.get("writable")), unit=str(spec.get("unit") or ""),
                  description=str(spec.get("description") or ""), extra={"target": True})
        self.tags[name] = tag
        if tag.writable:
            self._listen_set(tag)
        return tag

    def _listen_set(self, tag: Tag) -> None:
        topic = tag.address + "/set"
        self.by_set[topic] = tag
        if self.client is not None and self.connected:
            import asyncio

            asyncio.get_running_loop().create_task(self.client.subscribe(topic, qos=self.qos))

    async def session(self) -> None:
        import aiomqtt

        s = self.settings
        kw: dict[str, Any] = {}
        if s.get("tls"):
            kw["tls_params"] = aiomqtt.TLSParameters()
        async with aiomqtt.Client(
            str(s.get("host") or "127.0.0.1"), port=int(s.get("port") or 1883),
            username=str(s["username"]) if s.get("username") else None,
            password=str(s["password"]) if s.get("password") else None,
            identifier=str(s["client_id"]) if s.get("client_id") else None,
            keepalive=60, **kw,
        ) as client:
            self.client = client
            topics = set(self.by_topic) | set(self.by_set) | set(self.filters)
            for topic in sorted(topics):
                await client.subscribe(topic, qos=self.qos)
            self.set_state(True)
            async for msg in client.messages:
                self._message(str(msg.topic), msg.payload)
                if self._stop:
                    break

    async def close(self) -> None:
        self.client = None

    def _message(self, topic: str, payload: Any) -> None:
        tag = self.by_set.get(topic)
        if tag is not None:
            # A command for a gateway target: pass it to the source, and show it as the new state.
            value = parse_payload(payload, str(tag.extra.get("json_key") or ""))
            self.publish(tag, value)
            if self.client is not None:
                import asyncio

                asyncio.get_running_loop().create_task(self._publish(tag.address, value))
            return
        tags = self.by_topic.get(topic)
        if tags is None:
            # Commands (<topic>/set, also our own) are not states: they never become tags.
            if topic.endswith("/set") or not any(topic_matches(f, topic) for f in self.filters):
                return
            name = topic[len(self.prefix):] if self.prefix and topic.startswith(self.prefix) else topic
            if name in self.tags:
                return
            tag = Tag(name=name, address=topic, writable=True, extra={"auto": True})
            self.tags[name] = tag
            tags = self.by_topic[topic] = [tag]
        for tag in tags:
            self.publish(tag, parse_payload(payload, str(tag.extra.get("json_key") or "")))

    async def _publish(self, topic: str, value: Any) -> None:
        if self.client is None:
            raise ConnectionError(_("{connection} is not connected").format(connection=self.name))
        await self.client.publish(topic, make_payload(value, self.fmt), qos=self.qos, retain=self.retain)

    async def write(self, tag: Tag, value: Any, user: str = "") -> None:
        raw = tag.to_raw(value)
        if tag.extra.get("target"):
            await self._publish(tag.address, raw)
            return
        topic = str(tag.extra.get("set_topic") or (tag.address + "/set" if tag.extra.get("auto") else tag.address))
        await self._publish(topic, raw)
