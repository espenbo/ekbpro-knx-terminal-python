"""KNX USB interface (knxscada.knx_usb): HID framing, finding devices and the xknx interface against a fake device."""

from __future__ import annotations

import asyncio
from typing import ClassVar

import pytest
from xknx import XKNX
from xknx.cemi import CEMIFrame, CEMILData, CEMIMessageCode
from xknx.core import XknxConnectionState
from xknx.dpt import DPTBinary
from xknx.exceptions import CommunicationError
from xknx.telegram import GroupAddress, IndividualAddress, Telegram
from xknx.telegram.apci import GroupValueWrite

from knxscada import knx_usb
from knxscada.knx_interfaces import describe, interface_configs
from knxscada.knx_usb import (
    CEMI,
    FEAT_ACTIVE_EMI,
    FEAT_BUS_STATUS,
    FEAT_SUPPORTED_EMI,
    FEATURE_GET,
    FEATURE_INFO,
    FEATURE_RESPONSE,
    FEATURE_SET,
    M_PROP_WRITE_CON,
    M_PROP_WRITE_REQ,
    PROTO_FEATURE,
    PROTO_TUNNEL,
    REPORT_SIZE,
    Reassembler,
    UsbDevice,
    UsbError,
    UsbInterface,
    find_devices,
    frame_reports,
    select_device,
)

#: A Weinzierl KNX USB Interface 312 (the model used for the live test; the serial number is made up).
WEINZIERL = UsbDevice("/dev/hidraw2", "Weinzierl Engineering GmbH KNX-USB Interface", 0x0E77, 0x0104, "00C512345678")


class FakeUsb:
    """A KNX USB interface in memory: answers the feature services and M_PropWrite (like a Weinzierl 312, which
    starts in comm mode "no layer"), confirms every L_Data.req with L_Data.con and records every frame it gets.
    Class attributes set the behaviour per test."""

    #: Supported EMI mask the device reports (0x04 = cEMI).
    emi_mask = 0x0004
    #: Bus connection status (1 = the TP line has power).
    bus = 1
    #: Whether the device accepts M_PropWrite (False: it answers with number of elements 0 = refused).
    accept_write = True
    #: The open devices, newest last.
    opened: ClassVar[list[FakeUsb]] = []

    def __init__(self, path, on_report, on_lost) -> None:
        """Keep the callbacks the interface gives, as HidrawTransport does."""
        self.path, self.on_report, self.on_lost = path, on_report, on_lost
        self.frames: list[tuple[int, int, bytes]] = []
        self.reasm = Reassembler()
        self.closed = False
        #: cEMI server PID_COMM_MODE: 0xFF ("no layer") until the interface sets it.
        self.comm_mode = 0xFF

    def open(self) -> None:
        """Nothing to open; remember the device for the test."""
        FakeUsb.opened.append(self)

    def close(self) -> None:
        """Mark closed."""
        self.closed = True

    def reply(self, protocol: int, emi: int, body: bytes) -> None:
        """Send a frame to the interface, as the device would (in the next loop round)."""
        for report in frame_reports(protocol, emi, body):
            asyncio.get_running_loop().call_soon(self.on_report, report)

    def write(self, report: bytes) -> None:
        """A report from the interface: check its size, collect the frame and answer like a real device."""
        assert len(report) == REPORT_SIZE
        frame = self.reasm.feed(report)
        if frame is None:
            return
        self.frames.append(frame)
        protocol, emi, body = frame
        if protocol == PROTO_FEATURE and emi == FEATURE_GET:
            value = {FEAT_SUPPORTED_EMI: self.emi_mask.to_bytes(2, "big"), FEAT_BUS_STATUS: bytes((self.bus,))}
            self.reply(PROTO_FEATURE, FEATURE_RESPONSE, bytes((body[0],)) + value[body[0]])
        elif protocol == PROTO_TUNNEL and body[0] == M_PROP_WRITE_REQ:
            # M_PropWrite.con echoes the request header; number of elements 0 plus an error code means refused.
            if self.accept_write:
                self.comm_mode = body[7]
                self.reply(PROTO_TUNNEL, CEMI, bytes((M_PROP_WRITE_CON,)) + body[1:7])
            else:
                self.reply(PROTO_TUNNEL, CEMI, bytes((M_PROP_WRITE_CON,)) + body[1:5] + bytes((0x00, 0x01, 0x07)))
        elif protocol == PROTO_TUNNEL:
            # L_Data.req → L_Data.con: same frame, other message code.
            self.reply(PROTO_TUNNEL, CEMI, bytes((CEMIMessageCode.L_DATA_CON.value,)) + body[1:])

    def tunnel_frames(self) -> list[bytes]:
        """The cEMI frames for the bus (L_Data), without the local device management (M_ services)."""
        return [body for protocol, _emi, body in self.frames
                if protocol == PROTO_TUNNEL and body[0] < 0xF0]


@pytest.fixture(autouse=True)
def _fresh_fake():
    """Every test starts with the default fake device and no open devices."""
    FakeUsb.emi_mask, FakeUsb.bus, FakeUsb.accept_write, FakeUsb.opened = 0x0004, 1, True, []


def _xknx(settings=None, devices=(WEINZIERL,)):
    """An XKNX whose interface is the USB interface on the fake device, and the list of state changes it reports."""
    states: list[XknxConnectionState] = []
    xknx = XKNX(connection_state_changed_cb=states.append)
    xknx.knxip_interface = UsbInterface(xknx, settings or {}, transport=FakeUsb, devices=lambda: list(devices))
    return xknx, states


def _ind(ga: str, value: int, source: str = "1.1.5") -> bytes:
    """A raw cEMI L_Data.ind for a GroupValueWrite, as the bus delivers it."""
    t = Telegram(GroupAddress(ga), payload=GroupValueWrite(DPTBinary(value)), source_address=IndividualAddress(source))
    return CEMIFrame(code=CEMIMessageCode.L_DATA_IND, data=CEMILData.init_from_telegram(t)).to_knx()


# -- framing ---------------------------------------------------------------------------------------------------------
def test_single_report_frame():
    """A short frame is one 64-byte report: report ID 1, packet info 0x13 (sequence 1, start+end), the data length,
    then the 8-byte transfer header and the body; read back it gives the same protocol, EMI and body."""
    body = bytes.fromhex("1100bce011050a01010080")
    (report,) = frame_reports(PROTO_TUNNEL, CEMI, body)
    assert report[:3] == bytes((0x01, 0x13, 8 + len(body)))
    assert report[3:11] == bytes((0x00, 0x08, 0x00, len(body), PROTO_TUNNEL, CEMI, 0, 0))
    assert len(report) == REPORT_SIZE and report[11 + len(body):] == bytes(REPORT_SIZE - 11 - len(body))
    assert Reassembler().feed(report) == (PROTO_TUNNEL, CEMI, body)


def test_long_frame_is_split_and_joined():
    """A frame longer than one report is split with sequence numbers and start/partial/end types, and joined again."""
    body = bytes(range(140))
    reports = frame_reports(PROTO_TUNNEL, CEMI, body)
    assert [r[1] for r in reports] == [0x15, 0x24, 0x36]
    r = Reassembler()
    assert [r.feed(x) for x in reports] == [None, None, (PROTO_TUNNEL, CEMI, body)]


def test_bad_reports_are_dropped():
    """Wrong report ID, a continuation without a start and an unknown header are ignored; the next frame works."""
    r = Reassembler()
    good = frame_reports(PROTO_FEATURE, FEATURE_INFO, bytes((FEAT_BUS_STATUS, 1)))[0]
    assert r.feed(b"\x02" + good[1:]) is None
    assert r.feed(bytes((1, 0x24, 3, 1, 2, 3))) is None
    assert r.feed(bytes((1, 0x13, 8)) + bytes((1, 8, 0, 0, 1, 3, 0, 0))) is None
    assert r.feed(good) == (PROTO_FEATURE, FEATURE_INFO, bytes((FEAT_BUS_STATUS, 1)))


# -- finding devices -------------------------------------------------------------------------------------------------
def _sysfs(tmp_path, entries):
    """A fake /sys/class/hidraw with one uevent file per (hidraw name, HID_ID, HID_NAME, HID_UNIQ)."""
    for name, hid_id, hid_name, uniq in entries:
        d = tmp_path / name / "device"
        d.mkdir(parents=True)
        (d / "uevent").write_text(f"DRIVER=hid-generic\nHID_ID={hid_id}\nHID_NAME={hid_name}\nHID_UNIQ={uniq}\n")
    return tmp_path


def test_find_devices_by_vendor_or_name(tmp_path):
    """KNX interfaces are found by vendor id or by "KNX" in the name; a keyboard is not."""
    root = _sysfs(tmp_path, [
        ("hidraw0", "0003:00001A2C:00002C27", "USB USB Keyboard", ""),
        ("hidraw2", "0003:00000E77:00000104", "Weinzierl Engineering GmbH KNX-USB Interface", "00C512345678"),
        ("hidraw3", "0003:000016D0:00000491", "MDT KNX USB Interface", ""),
    ])
    found = find_devices(root)
    assert [(d.path, d.usb_id, d.serial) for d in found] == [("/dev/hidraw2", "0e77:0104", "00C512345678"),
                                                             ("/dev/hidraw3", "16d0:0491", "")]


def test_select_device():
    """Empty/auto takes the first; a path, serial number or vvvv:pppp selects; no match names what was found."""
    other = UsbDevice("/dev/hidraw5", "KNX USB", 0x135E, 0x0024, "X1")
    devs = [WEINZIERL, other]
    assert select_device("", devs) is WEINZIERL and select_device("auto", devs) is WEINZIERL
    assert select_device("/dev/hidraw5", devs) is other
    assert select_device("X1", devs) is other and select_device("135E:0024", devs) is other
    with pytest.raises(UsbError, match="00C512345678"):
        select_device("nope", devs)
    with pytest.raises(UsbError):
        select_device("", [])


# -- configuration ---------------------------------------------------------------------------------------------------
def test_usb_interface_config():
    """``connection: usb`` is a valid interface next to IP ones; usb_device is per interface and shown in the status."""
    ifaces = interface_configs({"interfaces": [{"name": "ip", "gateway_ip": "192.168.1.50", "default": True},
                                               {"name": "usb", "connection": "usb", "usb_device": "00C512345678"}]})
    assert ifaces[1]["connection"] == "usb" and describe(ifaces[1]) == "USB 00C512345678"
    assert describe(interface_configs({"connection": "usb"})[0]) == "USB"


# -- the xknx interface ----------------------------------------------------------------------------------------------
def test_start_switches_to_cemi_and_sends_nothing_on_the_bus():
    """Start-up asks for the EMI types, sets cEMI, switches the cEMI server to data link layer (it starts in "no layer"
    and would pass nothing up) and asks for the bus status – all services to the interface itself. No telegram goes to
    the bus (safety rule: nothing is sent at start-up)."""
    async def run():
        xknx, states = _xknx()
        await xknx.start()
        await asyncio.sleep(0.05)
        dev = FakeUsb.opened[-1]
        await xknx.stop()
        return dev, states

    dev, states = asyncio.run(run())
    assert dev.frames == [(PROTO_FEATURE, FEATURE_GET, bytes((FEAT_SUPPORTED_EMI,))),
                          (PROTO_FEATURE, FEATURE_SET, bytes((FEAT_ACTIVE_EMI, CEMI))),
                          (PROTO_TUNNEL, CEMI, bytes((M_PROP_WRITE_REQ, 0x00, 0x08, 0x01, 0x34, 0x10, 0x01, 0x00))),
                          (PROTO_FEATURE, FEATURE_GET, bytes((FEAT_BUS_STATUS,)))]
    assert dev.tunnel_frames() == [] and dev.comm_mode == 0x00
    assert states[0] == XknxConnectionState.CONNECTED and states[-1] == XknxConnectionState.DISCONNECTED
    assert dev.closed


def test_received_telegrams_reach_xknx():
    """An L_Data.ind from the device becomes an incoming xknx telegram with its source and destination."""
    async def run():
        xknx, _states = _xknx()
        got = []
        xknx.telegram_queue.register_telegram_received_cb(got.append)
        await xknx.start()
        FakeUsb.opened[-1].reply(PROTO_TUNNEL, CEMI, _ind("1/1/0", 1))
        await asyncio.sleep(0.1)
        await xknx.stop()
        return got

    (t,) = asyncio.run(run())
    assert t.destination_address == GroupAddress("1/1/0") and t.source_address == IndividualAddress("1.1.5")
    assert t.payload == GroupValueWrite(DPTBinary(1))


def test_sending_waits_for_the_confirmation():
    """A telegram is sent as an L_Data.req and xknx gets the device's L_Data.con, so nothing is reported as failed.
    The source is 0.0.0 (the interface puts in its own address) unless individual_address is set."""
    async def run(settings):
        xknx, _states = _xknx(settings)
        await xknx.start()
        await xknx.cemi_handler.send_telegram(
            Telegram(GroupAddress("1/1/0"), payload=GroupValueWrite(DPTBinary(0))))
        dev = FakeUsb.opened[-1]
        errors = xknx.connection_manager.cemi_count_outgoing_error
        await xknx.stop()
        return CEMIFrame.from_knx(dev.tunnel_frames()[0]), errors

    cemi, errors = asyncio.run(run({}))
    assert cemi.code == CEMIMessageCode.L_DATA_REQ and errors == 0
    assert cemi.data.dst_addr == GroupAddress("1/1/0") and cemi.data.src_addr == IndividualAddress("0.0.0")
    cemi, _errors = asyncio.run(run({"individual_address": "1.1.250"}))
    assert cemi.data.src_addr == IndividualAddress("1.1.250")


def test_interface_without_cemi_is_refused():
    """An EMI1/EMI2-only interface is closed again and start fails with a message saying why."""
    FakeUsb.emi_mask = 0x0003

    async def run():
        xknx, _states = _xknx()
        with pytest.raises(CommunicationError, match="cEMI"):
            await xknx.knxip_interface.start()
        return FakeUsb.opened[-1]

    assert asyncio.run(run()).closed


def test_refused_link_layer_mode_fails_start():
    """An interface that refuses the data link layer mode is closed again and start fails with the property in the
    message – it would never deliver a telegram."""
    FakeUsb.accept_write = False

    async def run():
        xknx, _states = _xknx()
        with pytest.raises(CommunicationError, match="0x34"):
            await xknx.knxip_interface.start()
        return FakeUsb.opened[-1]

    assert asyncio.run(run()).closed


def test_missing_device_fails_start():
    """No KNX USB device: start fails (the server retries with back-off)."""
    async def run():
        xknx, _states = _xknx(devices=())
        with pytest.raises(CommunicationError, match="not found"):
            await xknx.knxip_interface.start()

    asyncio.run(run())


def test_bus_status_drives_the_connection_state():
    """The interface reports a dead TP line at start and later power on the line (Device Feature Info)."""
    FakeUsb.bus = 0

    async def run():
        xknx, states = _xknx()
        await xknx.start()
        await asyncio.sleep(0.05)
        FakeUsb.opened[-1].reply(PROTO_FEATURE, FEATURE_INFO, bytes((FEAT_BUS_STATUS, 1)))
        await asyncio.sleep(0.05)
        connected = xknx.connection_manager.connected.is_set()
        await xknx.stop()
        return connected, states

    connected, states = asyncio.run(run())
    assert connected and XknxConnectionState.CONNECTED in states


def test_unplugged_device_is_opened_again(monkeypatch):
    """When the device is lost the state turns disconnected, and the interface opens it again by itself."""
    monkeypatch.setattr(knx_usb, "REOPEN_S", 0.05)

    async def run():
        xknx, states = _xknx()
        await xknx.start()
        first = FakeUsb.opened[-1]
        first.on_lost(OSError("No such device"))
        await asyncio.sleep(0.3)
        n_opened = len(FakeUsb.opened)
        connected = xknx.connection_manager.connected.is_set()
        await xknx.stop()
        return states, n_opened, connected

    states, n_opened, connected = asyncio.run(run())
    assert XknxConnectionState.DISCONNECTED in states and n_opened == 2 and connected
