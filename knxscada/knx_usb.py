# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""KNX USB interfaces (``knx: connection: usb``) under xknx.

xknx only speaks KNXnet/IP. A KNX USB interface (Weinzierl, MDT, Gira, ABB …) is a USB HID device that carries the
same cEMI frames inside HID reports (KNX Standard 09/03 "KNX USB Interface"). :class:`UsbInterface` takes the place of
``xknx.knxip_interface``: xknx hands it cEMI frames to send (``send_cemi``) and it feeds the frames it receives to
``xknx.cemi_handler``. Everything above that – the telegram queue, the wait for ``L_Data.con``, the connection state
callback the server listens to – is xknx's own and works exactly as for an IP interface. The server
(:meth:`knxscada.server.ScadaServer._knx_connect`) swaps the interface in before ``xknx.start()``.

On the wire (one HID report = 64 bytes on Linux ``/dev/hidrawN``, report ID first):

* report header: report ID ``0x01``, packet info (sequence number in the high nibble, packet type in the low one:
  ``0x3`` = start and end of a single-report frame), and the number of data bytes in this report (max 61);
* in the first report of a frame, the 8-byte *transfer protocol header*: version 0, header length 8, body length,
  protocol (``0x01`` KNX tunnel, ``0x0F`` bus access server feature service), EMI type or service, manufacturer 0;
* then the body: a cEMI frame, or a device feature service.

Start-up of a connection (:meth:`UsbInterface.start`), all local to the USB device – nothing is sent on the bus:

1. Device Feature Get "supported EMI types": only interfaces that can do cEMI are supported (older EMI1/EMI2-only
   interfaces are refused with a clear message);
2. Device Feature Set "active EMI type" = cEMI;
3. cEMI M_PropWrite of the cEMI server object's ``PID_COMM_MODE`` = data link layer. Interfaces start in "no layer"
   (``0xFF``, seen on a Weinzierl KNX USB Interface 312) and then pass nothing from the bus up – the switch is what
   makes telegrams arrive. It is a local device management service, answered by the interface (``M_PropWrite.con``);
4. Device Feature Get "bus connection status": the interface tells whether its TP line has power. Later changes come
   as Device Feature Info and drive the connected/disconnected state.

When the USB device disappears (unplugged, read error) the state turns disconnected and the interface is looked
for again every few seconds – by the same ``usb_device`` setting, so a stick that comes back as another
``/dev/hidrawN`` is found again.

Only Linux (``/dev/hidraw``) is supported: the server runs on Linux. The device must be readable and writable by the
service user – see ``deploy/99-knx-usb.rules``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .i18n import _

log = logging.getLogger(__name__)

#: Where Linux lists the hidraw devices (``hidrawN/device/uevent`` names the USB device behind each).
SYSFS_HIDRAW = Path("/sys/class/hidraw")

#: USB vendor ids of KNX USB interface makers. A device is also taken as KNX when its name contains "KNX".
#: 0E77 Weinzierl (also inside many OEM interfaces), 135E Insta (Gira, Jung, Berker, Hager OEM), 147B ABB,
#: 0681 Siemens, 145C Hager, 28C2 Zennio, 16D0 MDT (shared vendor id: the name check decides there too).
KNX_VENDORS = {0x0E77, 0x135E, 0x147B, 0x0681, 0x145C, 0x28C2}

#: Bytes in one HID report on hidraw, report ID included (KNX USB: always 64).
REPORT_SIZE = 64
#: Report ID of every KNX USB HID report.
REPORT_ID = 0x01
#: Data bytes after report ID, packet info and length.
MAX_DATA = REPORT_SIZE - 3
#: Length of the KNX USB transfer protocol header (protocol version 0).
HEADER_LEN = 8

#: Packet types (low nibble of the packet info byte).
START, END, PARTIAL = 0x01, 0x02, 0x04

#: Transfer protocol ids.
PROTO_TUNNEL = 0x01      # KNX tunnel: the EMI id says which EMI (we only use cEMI)
PROTO_FEATURE = 0x0F     # bus access server feature service: talks to the USB interface itself

#: EMI ids in the KNX tunnel protocol (also the values of the "active EMI type" feature).
EMI1, EMI2, CEMI = 0x01, 0x02, 0x03

#: Bus access server feature services.
FEATURE_GET, FEATURE_RESPONSE, FEATURE_SET, FEATURE_INFO = 0x01, 0x02, 0x03, 0x04

#: Feature ids.
FEAT_SUPPORTED_EMI = 0x01   # 2 bytes, bit 0 EMI1, bit 1 EMI2, bit 2 cEMI
FEAT_BUS_STATUS = 0x03      # 1 byte, bit 0: the TP line has power
FEAT_ACTIVE_EMI = 0x05      # 1 byte, an EMI id

#: cEMI local device management (KNX 03/06/03 §4.1.7): message codes of property read/write and their confirmations.
M_PROP_READ_REQ, M_PROP_READ_CON, M_PROP_WRITE_REQ, M_PROP_WRITE_CON = 0xFC, 0xFB, 0xF6, 0xF5
#: Interface object type of the cEMI server object, and its communication mode property.
OBJ_CEMI_SERVER, PID_COMM_MODE = 0x0008, 0x34
#: ``PID_COMM_MODE`` value for data link layer: L_Data frames from and to the bus (0xFF = no layer).
COMM_MODE_LINK_LAYER = 0x00

#: Seconds to wait for the interface to answer a Device Feature Get.
FEATURE_TIMEOUT_S = 2.0
#: Seconds between attempts to find and open the USB device again after it was lost.
REOPEN_S = 5.0


class UsbError(Exception):
    """The USB interface cannot be used (not found, no access, no cEMI). The message is meant for the event log."""


# -- framing ---------------------------------------------------------------------------------------------------------
def frame_reports(protocol: int, emi: int, body: bytes) -> list[bytes]:
    """One frame (transfer protocol header + *body*) as the HID reports that carry it, each padded to 64 bytes.

    Called for every cEMI frame we send and for the feature services at start-up. A standard cEMI frame fits in one
    report; longer ones (extended frames) are split, with sequence numbers 1, 2, … in the packet info byte.
    """
    data = bytes((0x00, HEADER_LEN)) + len(body).to_bytes(2, "big") + bytes((protocol, emi, 0, 0)) + body
    chunks = [data[i:i + MAX_DATA] for i in range(0, len(data), MAX_DATA)] or [b""]
    reports = []
    for seq, chunk in enumerate(chunks, 1):
        if len(chunks) == 1:
            kind = START | END
        elif seq == 1:
            kind = START | PARTIAL
        elif seq == len(chunks):
            kind = END | PARTIAL
        else:
            kind = PARTIAL
        report = bytes((REPORT_ID, (seq << 4) | kind, len(chunk))) + chunk
        reports.append(report.ljust(REPORT_SIZE, b"\x00"))
    return reports


class Reassembler:
    """Collects HID reports into frames: ``feed(report)`` returns ``(protocol, emi, body)`` once a frame is complete.

    One per open device. Reports that do not fit (wrong report ID, a continuation without a start, a header that is
    not version 0) are dropped with a debug message – the interface then continues with the next frame.
    """

    def __init__(self) -> None:
        """Start with no frame in progress."""
        #: Data bytes of the frame being collected (transfer header included), None between frames.
        self._data: bytearray | None = None

    def feed(self, report: bytes) -> tuple[int, int, bytes] | None:
        """Add one HID report (report ID first). Returns the complete frame, or None while more reports are due."""
        if len(report) < 3 or report[0] != REPORT_ID:
            log.debug("KNX USB: ignored report %s", report[:8].hex())
            return None
        kind, length = report[1] & 0x0F, min(report[2], MAX_DATA)
        chunk = report[3:3 + length]
        if kind & START:
            self._data = bytearray(chunk)
        elif self._data is None:
            log.debug("KNX USB: continuation without a start packet")
            return None
        else:
            self._data += chunk
        if not kind & END:
            return None
        data, self._data = bytes(self._data), None
        if len(data) < HEADER_LEN or data[0] != 0x00 or data[1] != HEADER_LEN:
            log.debug("KNX USB: unknown transfer header %s", data[:8].hex())
            return None
        body_len = int.from_bytes(data[2:4], "big")
        return data[4], data[5], data[HEADER_LEN:HEADER_LEN + body_len]


# -- finding devices -------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class UsbDevice:
    """A KNX USB interface found in sysfs."""

    path: str        #: /dev/hidrawN
    name: str        #: product name reported by the device (HID_NAME)
    vendor: int      #: USB vendor id
    product: int     #: USB product id
    serial: str      #: serial number (HID_UNIQ), empty when the device has none

    @property
    def usb_id(self) -> str:
        """``vvvv:pppp`` as lsusb shows it."""
        return f"{self.vendor:04x}:{self.product:04x}"


def find_devices(root: Path = SYSFS_HIDRAW) -> list[UsbDevice]:
    """Every KNX USB interface Linux knows (by vendor id or "KNX" in the name), ordered by device path.

    Reads only sysfs, so it works without access to the devices themselves. *root* is for tests.
    """
    found = []
    for entry in sorted(root.glob("hidraw*")):
        try:
            fields = dict(line.split("=", 1) for line in (entry / "device" / "uevent").read_text().splitlines()
                          if "=" in line)
            _bus, vendor, product = fields.get("HID_ID", "0:0:0").split(":")
            dev = UsbDevice(f"/dev/{entry.name}", fields.get("HID_NAME", ""), int(vendor, 16), int(product, 16),
                            fields.get("HID_UNIQ", ""))
        except (OSError, ValueError):
            continue
        if dev.vendor in KNX_VENDORS or "KNX" in dev.name.upper():
            found.append(dev)
    return found


def select_device(wanted: str | None, devices: list[UsbDevice]) -> UsbDevice:
    """The device for a ``usb_device`` setting: empty/``auto`` = the only (first) one, a ``/dev/hidrawN`` path,
    a serial number, or ``vvvv:pppp``. Raises :class:`UsbError` with what was found when nothing matches.

    A serial number is the stable choice with several sticks: the ``/dev/hidrawN`` numbers follow the order the
    devices were plugged in.
    """
    wanted = str(wanted or "").strip()
    if not wanted or wanted.lower() == "auto":
        matches = devices
    else:
        matches = [d for d in devices
                   if wanted in (d.path, d.serial) or wanted.lower() == d.usb_id]
    if not matches:
        have = ", ".join(f"{d.path} {d.name} ({d.usb_id}, {d.serial or '–'})" for d in devices) or _("none")
        raise UsbError(_("KNX USB interface {wanted} not found – found: {have}").format(
            wanted=wanted or "auto", have=have))
    return matches[0]


# -- transport -------------------------------------------------------------------------------------------------------
class HidrawTransport:
    """``/dev/hidrawN`` opened non-blocking and read through the event loop (no thread).

    *on_report* is called in the event loop for every report; *on_lost* once when the device stops working (unplugged
    or a read/write error).
    """

    def __init__(self, path: str, on_report: Callable[[bytes], None], on_lost: Callable[[Exception], None]) -> None:
        """Remember the callbacks; the device is opened by :meth:`open`."""
        self.path = path
        self.on_report = on_report
        self.on_lost = on_lost
        #: File descriptor while open, else None.
        self.fd: int | None = None

    def open(self) -> None:
        """Open the device and start reading. PermissionError is turned into a hint about the udev rule."""
        try:
            self.fd = os.open(self.path, os.O_RDWR | os.O_NONBLOCK)
        except PermissionError as exc:
            raise UsbError(_("No access to {path} – install deploy/99-knx-usb.rules and add the service user to "
                             "the plugdev group").format(path=self.path)) from exc
        except OSError as exc:
            raise UsbError(f"{self.path}: {exc}") from exc
        asyncio.get_running_loop().add_reader(self.fd, self._readable)

    def _readable(self) -> None:
        """The event loop says a report is waiting: read it (one ``read`` = one report on hidraw)."""
        try:
            report = os.read(self.fd, REPORT_SIZE)
        except BlockingIOError:
            return
        except OSError as exc:
            self._lost(exc)
            return
        if not report:
            self._lost(OSError("end of file"))
            return
        self.on_report(report)

    def write(self, report: bytes) -> None:
        """Send one 64-byte report. A full output queue is retried briefly; other errors mean the device is gone."""
        if self.fd is None:
            raise UsbError(_("KNX USB interface not open"))
        for _attempt in range(50):
            try:
                os.write(self.fd, report)
                return
            except BlockingIOError:
                # The interface takes one report per USB interval (a few ms); a short busy wait is fine here.
                os.sched_yield()
            except OSError as exc:
                self._lost(exc)
                raise UsbError(f"{self.path}: {exc}") from exc
        raise UsbError(_("KNX USB interface {path} does not take data").format(path=self.path))

    def _lost(self, exc: Exception) -> None:
        """Close and report the loss once."""
        if self.fd is None:
            return
        self.close()
        self.on_lost(exc)

    def close(self) -> None:
        """Stop reading and close the device (safe to call twice)."""
        if self.fd is not None:
            with contextlib.suppress(Exception):
                asyncio.get_running_loop().remove_reader(self.fd)
            with contextlib.suppress(OSError):
                os.close(self.fd)
            self.fd = None


# -- the xknx interface ----------------------------------------------------------------------------------------------
class UsbInterface:
    """Stands in for ``xknx.knxip_interface``: ``start``/``stop``/``send_cemi``/``gateway_info`` as xknx calls them.

    *settings*: the interface's entry from ``knx.interfaces`` (``usb_device``). *transport* makes the transport for a
    device path – the default opens hidraw; tests pass a fake.
    """

    def __init__(self, xknx, settings: dict[str, Any],
                 transport: Callable[..., Any] = HidrawTransport,
                 devices: Callable[[], list[UsbDevice]] = find_devices) -> None:
        """Set up; nothing is opened until :meth:`start`."""
        from xknx.io import ConnectionConfig

        self.xknx = xknx
        self.settings = settings
        #: xknx reads ``connection_config.threaded`` in ``XKNX.start``; we always run in the server's event loop.
        self.connection_config = ConnectionConfig()
        self._make_transport = transport
        self._devices = devices
        self.transport: Any = None
        self.device: UsbDevice | None = None
        self._reasm = Reassembler()
        #: Futures waiting for a Device Feature Response, by feature id.
        self._feature_waits: dict[int, asyncio.Future[bytes]] = {}
        #: Futures waiting for an M_PropRead.con/M_PropWrite.con, by (message code, object type, PID).
        self._prop_waits: dict[tuple[int, int, int], asyncio.Future[bytes]] = {}
        self._reopen_task: asyncio.Task | None = None
        self._stopped = False

    # xknx API -------------------------------------------------------------------------------------------------------
    async def start(self) -> None:
        """Find and open the device and switch it to cEMI. Raises (UsbError or xknx's CommunicationError) when
        that fails – the server then retries with back-off, as for an IP interface."""
        from xknx.exceptions import CommunicationError

        self._stopped = False
        try:
            await self._open()
        except UsbError as exc:
            raise CommunicationError(str(exc)) from exc

    async def stop_reconnect(self) -> None:
        """Stop looking for a lost device again (xknx 4 calls this in ``XKNX.stop`` before it empties the telegram
        queue, so a telegram waiting for a reconnect fails at once instead of blocking the shutdown). The device stays
        open until :meth:`stop`."""
        self._stopped = True
        if self._reopen_task is not None:
            self._reopen_task.cancel()
            self._reopen_task = None

    async def stop(self) -> None:
        """Close the device and stop looking for it."""
        self._stopped = True
        if self._reopen_task is not None:
            self._reopen_task.cancel()
            self._reopen_task = None
        self._close()
        self._set_state(False)

    def cemi_received(self, raw_cemi: bytes) -> None:
        """Hand a received cEMI frame to xknx (same name as on KNXIPInterface)."""
        self.xknx.cemi_handler.handle_raw_cemi(raw_cemi)

    async def send_cemi(self, cemi) -> None:
        """Send one cEMI frame (an ``L_Data.req`` from xknx). xknx then waits for the interface's ``L_Data.con``."""
        from xknx.exceptions import CommunicationError

        if self.transport is None:
            raise CommunicationError(_("KNX USB interface not connected"))
        try:
            self._send(PROTO_TUNNEL, CEMI, cemi.to_knx())
        except UsbError as exc:
            raise CommunicationError(str(exc)) from exc

    async def gateway_info(self):
        """There is no KNXnet/IP descriptor for a USB interface."""
        return

    # internals ------------------------------------------------------------------------------------------------------
    async def _open(self) -> None:
        """Find the device, open it and run the start-up feature services (all local to the interface)."""
        self.device = select_device(self.settings.get("usb_device"), self._devices())
        self._reasm = Reassembler()
        self.transport = self._make_transport(self.device.path, self._on_report, self._on_lost)
        self.transport.open()
        try:
            supported = await self._feature_get(FEAT_SUPPORTED_EMI)
            mask = int.from_bytes(supported[:2], "big") if supported else 0
            if not mask & 0x04:
                raise UsbError(_("{name} ({path}) does not support cEMI – only interfaces with cEMI can be used").format(
                    name=self.device.name, path=self.device.path))
            self._send(PROTO_FEATURE, FEATURE_SET, bytes((FEAT_ACTIVE_EMI, CEMI)))
            await self._prop_write(OBJ_CEMI_SERVER, PID_COMM_MODE, bytes((COMM_MODE_LINK_LAYER,)))
            status = await self._feature_get(FEAT_BUS_STATUS)
        except BaseException:
            self._close()
            raise
        if self.settings.get("individual_address"):
            from xknx.telegram import IndividualAddress

            # Source of our telegrams. Without it it stays 0.0.0 and the interface puts in its own address.
            self.xknx.current_address = IndividualAddress(str(self.settings["individual_address"]))
        log.info("KNX USB: %s %s (%s) opened, bus %s", self.device.path, self.device.name, self.device.usb_id,
                 "up" if status[:1] == b"\x01" else "down")
        self._set_state(bool(status[:1]) and status[0] & 0x01 == 1)

    async def _feature_get(self, feature: int) -> bytes:
        """Ask the interface for one feature and return the value bytes (UsbError on timeout)."""
        fut: asyncio.Future[bytes] = asyncio.get_running_loop().create_future()
        self._feature_waits[feature] = fut
        try:
            self._send(PROTO_FEATURE, FEATURE_GET, bytes((feature,)))
            return await asyncio.wait_for(fut, FEATURE_TIMEOUT_S)
        except TimeoutError as exc:
            raise UsbError(_("{path}: no answer from the KNX USB interface").format(
                path=self.device.path if self.device else "?")) from exc
        finally:
            self._feature_waits.pop(feature, None)

    async def _prop_write(self, obj: int, pid: int, value: bytes) -> None:
        """Write one element of a property of the interface's own object *obj* (instance 1) with cEMI M_PropWrite and
        wait for the confirmation. Raises UsbError when the interface refuses (number of elements 0) or does not
        answer. Local to the interface: nothing goes on the bus."""
        key = (M_PROP_WRITE_CON, obj, pid)
        fut: asyncio.Future[bytes] = asyncio.get_running_loop().create_future()
        self._prop_waits[key] = fut
        try:
            # Object type (2 bytes), instance 1, PID, number of elements 1 (high nibble) and start index 1.
            self._send(PROTO_TUNNEL, CEMI, bytes((M_PROP_WRITE_REQ, *obj.to_bytes(2, "big"), 1, pid, 0x10, 0x01))
                       + value)
            con = await asyncio.wait_for(fut, FEATURE_TIMEOUT_S)
        except TimeoutError as exc:
            raise UsbError(_("{path}: no answer from the KNX USB interface").format(
                path=self.device.path if self.device else "?")) from exc
        finally:
            self._prop_waits.pop(key, None)
        if con[5] >> 4 == 0:
            raise UsbError(_("{path}: the KNX USB interface refused setting {pid:#x} (error {code})").format(
                path=self.device.path if self.device else "?", pid=pid, code=con[7:8].hex() or "?"))

    def _send(self, protocol: int, emi: int, body: bytes) -> None:
        """Write one frame as one or more reports."""
        for report in frame_reports(protocol, emi, body):
            self.transport.write(report)

    def _on_report(self, report: bytes) -> None:
        """A report arrived (event loop): dispatch complete frames to xknx or to a waiting feature request."""
        frame = self._reasm.feed(report)
        if frame is None:
            return
        protocol, emi, body = frame
        if protocol == PROTO_TUNNEL and emi == CEMI and body[:1] in (bytes((M_PROP_READ_CON,)),
                                                                     bytes((M_PROP_WRITE_CON,))):
            # Answer to our own device management request: not a bus telegram, so it does not go to xknx.
            if len(body) >= 7:
                fut = self._prop_waits.get((body[0], int.from_bytes(body[1:3], "big"), body[4]))
                if fut is not None and not fut.done():
                    fut.set_result(body)
        elif protocol == PROTO_TUNNEL and emi == CEMI:
            self.cemi_received(body)
        elif protocol == PROTO_FEATURE and body:
            feature, value = body[0], body[1:]
            if emi == FEATURE_RESPONSE:
                fut = self._feature_waits.get(feature)
                if fut is not None and not fut.done():
                    fut.set_result(value)
            elif emi == FEATURE_INFO and feature == FEAT_BUS_STATUS:
                # The TP line lost or got power: shown as the interface's connection state.
                self._set_state(bool(value[:1]) and value[0] & 0x01 == 1)
        else:
            log.debug("KNX USB: ignored frame protocol %#x emi %#x", protocol, emi)

    def _on_lost(self, exc: Exception) -> None:
        """The device stopped working: disconnected, and look for it again in the background."""
        log.warning("KNX USB: %s lost (%s)", self.device.path if self.device else "?", exc)
        self.transport = None
        self._set_state(False)
        if not self._stopped and (self._reopen_task is None or self._reopen_task.done()):
            self._reopen_task = asyncio.get_running_loop().create_task(self._reopen())

    async def _reopen(self) -> None:
        """Try to open the device again every :data:`REOPEN_S` seconds until it works or we are stopped."""
        while not self._stopped:
            await asyncio.sleep(REOPEN_S)
            try:
                await self._open()
                return
            except (UsbError, OSError) as exc:
                log.debug("KNX USB: still not available (%s)", exc)

    def _close(self) -> None:
        """Close the transport (if open) and fail any waiting feature request."""
        if self.transport is not None:
            self.transport.close()
            self.transport = None
        for fut in [*self._feature_waits.values(), *self._prop_waits.values()]:
            if not fut.done():
                fut.cancel()

    def _set_state(self, connected: bool) -> None:
        """Tell xknx (and through it the server's callback) whether the bus is reachable."""
        from xknx.core import XknxConnectionState

        self.xknx.connection_manager.connection_state_changed(
            XknxConnectionState.CONNECTED if connected else XknxConnectionState.DISCONNECTED)
