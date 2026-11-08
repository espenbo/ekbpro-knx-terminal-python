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

import asyncio
import contextlib
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


#: Quality words understood in an incoming JSON payload (``"quality"``) → our quality. "uncertain"/"stale" values are
#: old ones: still shown, not passed on as changes.
QUALITY_WORDS = {"good": "good", "bad": "bad", "old": "old", "uncertain": "old", "stale": "old"}


def parse_quality(payload: bytes | str) -> str:
    """The quality of an incoming payload: the ``"quality"`` of a JSON object (good, bad, old, uncertain, stale),
    else ``"good"`` – a plain value or JSON without the key is a normal measurement, as before."""
    text = payload.decode("utf-8", "replace") if isinstance(payload, (bytes, bytearray)) else str(payload)
    text = text.strip()
    if not text.startswith("{"):
        return "good"
    try:
        data = json.loads(text)
    except ValueError:
        return "good"
    word = data.get("quality") if isinstance(data, dict) else None
    return QUALITY_WORDS.get(str(word).strip().lower(), "good") if word is not None else "good"


def make_payload(value: Any, fmt: str, quality: str | None = None) -> str:
    """Value → payload text: ``{"value", "ts"}`` (and ``"quality"`` when given) for ``payload: json``, else plain text
    (``true``/``false``, numbers as Python prints them, None as an empty payload, dicts and lists as JSON)."""
    if fmt == "json":
        data = {"value": value, "ts": round(time.time(), 3)}
        if quality is not None:
            data["quality"] = quality
        return json.dumps(data, ensure_ascii=False)
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


#: After subscribing: how long the retained messages (the statuses) may take before values count as changes (s).
RETAINED_S = 1.0


class Driver(BaseDriver):
    """One MQTT broker connection. There is no client/server split: tags follow topics, gateway targets are
    topics we publish (retained) under the prefix."""

    protocol = "mqtt"
    # Retained messages after a (re)connect are statuses, not changes: nothing is passed on for them.
    reads_first = True   # ready RETAINED_S after subscribing

    def __init__(self, name: str, settings: dict[str, Any], hub) -> None:
        """Set up the connection *name* (attributes used by :meth:`make_tag` first, then the base class builds
        the tags). ``subscribe`` filters are taken relative to ``topic_prefix``."""
        self.prefix = str(settings.get("topic_prefix") or "")
        self.fmt = str(settings.get("payload") or "plain")
        self.qos = int(settings.get("qos") or 0)
        self.retain = bool(settings.get("retain", True))
        subs = settings.get("subscribe") or []
        self.filters = [self.prefix + f for f in ([subs] if isinstance(subs, str) else subs) if f]
        self.by_topic: dict[str, list[Tag]] = {}   # state topic → tags following it (several may share one)
        self.by_set: dict[str, Tag] = {}           # "<target>/set" → gateway target written back to its source
        #: Publish the quality of gateway targets (``quality``, default on): see :meth:`set_quality`.
        self.quality = bool(settings.get("quality", True))
        #: Target name → quality last published on ``<topic>/quality`` this session (plain payloads). Empty after a
        #: reconnect, so the first value publishes "good" again and a retained "bad" from before is replaced.
        self._shown: dict[str, str] = {}
        self.client = None
        super().__init__(name, settings, hub)

    def make_tag(self, name: str, spec: dict[str, Any]) -> Tag:
        """A tag from its ``tags:`` entry, following ``topic`` (default: prefix + name)."""
        typ = str(spec.get("type") or "")
        topic = str(spec.get("topic") or self.prefix + name)
        tag = Tag(name=name, address=topic, type=typ if typ in TYPES else "", writable=bool(spec.get("writable", True)),
                  unit=str(spec.get("unit") or ""), description=str(spec.get("description") or ""),
                  scale=as_float(spec.get("scale")), offset=as_float(spec.get("offset")), extra=dict(spec))
        self.by_topic.setdefault(topic, []).append(tag)
        return tag

    def owns(self, tag: Tag) -> bool:
        """Gateway targets (our own retained state topics) are ours; other topics belong to devices. So initial values
        and qualities go only to targets."""
        return bool(tag.extra.get("target"))

    def create_tag(self, name: str, spec: dict[str, Any]) -> Tag:
        """A gateway target: published under the prefix; with ``direction: both`` also listening to ``/set``."""
        # "+" and "#" are wildcards in MQTT and may not appear in a topic we publish to.
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
        """Listen to ``<target>/set`` for *tag* (subscribed at once when connected, else on the next connect)."""
        topic = tag.address + "/set"
        self.by_set[topic] = tag
        if self.client is not None and self.connected:
            import asyncio

            asyncio.get_running_loop().create_task(self.client.subscribe(topic, qos=self.qos))

    async def session(self) -> None:
        """Connect to the broker, subscribe to every tag, ``/set`` and filter topic, then handle messages.

        The connection is *ready* :data:`RETAINED_S` after subscribing: the retained messages arriving before
        that are statuses and are not passed on as changes. A lost broker ends the ``async for`` with an error,
        and :meth:`Driver.run` reconnects.
        """
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
            ready = asyncio.get_running_loop().call_later(RETAINED_S, self.set_ready)
            try:
                async for msg in client.messages:
                    self._message(str(msg.topic), msg.payload)
                    if self._stop:
                        break
            finally:
                ready.cancel()


    async def close(self) -> None:
        """Forget the client (the ``async with`` in :meth:`session` has already disconnected it)."""
        self.client = None
        self._shown.clear()

    def _message(self, topic: str, payload: Any) -> None:
        """One message: a ``/set`` command for a target, a value of a known tag, or a new topic under a filter.

        A command is published back on the target's state topic so subscribers see the new state at once.
        A new topic that matches a ``subscribe`` filter becomes a tag (``auto``) named without the prefix.
        """
        tag = self.by_set.get(topic)
        if tag is not None:
            # A command for a gateway target: pass it to the source, and show it as the new state.
            value = parse_payload(payload, str(tag.extra.get("json_key") or ""))
            self.publish(tag, value)
            if self.client is not None:
                import asyncio

                asyncio.get_running_loop().create_task(self._publish_target(tag, value, "good"))
            return
        tags = self.by_topic.get(topic)
        if tags is None:
            # Commands (<topic>/set, also our own) are not states, and <topic>/quality only describes one: they never
            # become tags.
            if topic.endswith(("/set", "/quality")) or not any(topic_matches(f, topic) for f in self.filters):
                return
            name = topic[len(self.prefix):] if self.prefix and topic.startswith(self.prefix) else topic
            if name in self.tags:
                return
            tag = Tag(name=name, address=topic, writable=True, extra={"auto": True})
            self.tags[name] = tag
            tags = self.by_topic[topic] = [tag]
        # A JSON payload may say its quality (as our own targets do): a bad one is not passed on by gateways.
        quality = parse_quality(payload)
        for tag in tags:
            self.publish(tag, parse_payload(payload, str(tag.extra.get("json_key") or "")), quality)

    async def _publish(self, topic: str, value: Any, quality: str | None = None) -> None:
        """Publish *value* on *topic* with the connection's payload format, QoS and ``retain`` (*quality*: put into a
        JSON payload)."""
        if self.client is None:
            raise ConnectionError(_("{connection} is not connected").format(connection=self.name))
        await self.client.publish(topic, make_payload(value, self.fmt, quality), qos=self.qos, retain=self.retain)

    async def _publish_target(self, tag: Tag, raw: Any, quality: str) -> None:
        """Publish a gateway target's value with its *quality*: inside the payload (JSON), or – plain text has no room
        for it – on ``<topic>/quality``, retained like the value, and only when it differs from what this session
        published last (a value stream does not double the messages)."""
        if not self.quality:
            await self._publish(tag.address, raw)
            return
        if self.fmt == "json":
            await self._publish(tag.address, raw, quality)
            return
        await self._publish(tag.address, raw)
        if self._shown.get(tag.name) != quality:
            if self.client is None:
                raise ConnectionError(_("{connection} is not connected").format(connection=self.name))
            await self.client.publish(tag.address + "/quality", quality, qos=self.qos, retain=self.retain)
            self._shown[tag.name] = quality

    async def set_quality(self, tag: Tag, quality: str) -> None:
        """A gateway target's source turned bad (``"bad"``) or was lost (``"old"``): publish its last value again
        with that quality (:meth:`_publish_target`), so a subscriber does not take the retained value as current.
        The next value written publishes "good". Called by the gateway engine; nothing without a value or client."""
        rec = self.hub.values.get(f"{self.name}/{tag.name}")
        if not self.quality or rec is None or self.client is None or not self.connected:
            return
        with contextlib.suppress(TypeError, ValueError):
            await self._publish_target(tag, tag.to_raw(rec["v"]), "old" if quality == "old" else "bad")

    async def write(self, tag: Tag, value: Any, user: str = "") -> None:
        """Publish *value*: gateway targets on their state topic (with quality "good"); device tags on
        ``set_topic``, on ``<topic>/set`` for auto tags (from filters), else on the topic itself."""
        raw = tag.to_raw(value)
        if tag.extra.get("target"):
            await self._publish_target(tag, raw, "good")
            return
        topic = str(tag.extra.get("set_topic") or (tag.address + "/set" if tag.extra.get("auto") else tag.address))
        await self._publish(topic, raw)
