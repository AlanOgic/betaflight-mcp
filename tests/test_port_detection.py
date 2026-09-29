"""Détection automatique du port du FC (chaîne produit USB "Betaflight…" ou VID:PID connus)."""

import struct
import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from betaflight.msp import MSPProtocol
from betaflight.msp_codes import MSPCodes
from betaflight.commands import BetaflightCommands
from betaflight.serial_conn import SerialConnection, is_betaflight_port
from server import tools


def port(device, description="n/a", product=None, vid=None, pid=None):
    return SimpleNamespace(device=device, description=description, hwid="n/a",
                           product=product, vid=vid, pid=pid)


MATEK = port("/dev/cu.usbmodem0x80000001", "Betaflight - MATEKF411RX",
             product="Betaflight - MATEKF411RX", vid=0x0483, pid=0x5740)
BLUETOOTH = port("/dev/cu.Bluetooth-Incoming-Port")


# ── Reconnaissance ────────────────────────────────────────────────────

@pytest.mark.parametrize("info, expected", [
    (MATEK, True),
    (port("COM5", "Périphérique série USB (COM5)", vid=0x0483, pid=0x5740), True),   # Windows, sans produit
    (port("/dev/ttyACM0", "Betaflight APM32F425"), True),
    (port("/dev/ttyACM1", vid=0x2E3C, pid=0x5740), True),                             # AT32
    (port("/dev/ttyACM2", vid=0x314B, pid=0x5740), True),                             # APM32 (Geehy)
    (port("/dev/ttyUSB0", "CP2102 USB to UART", vid=0x10C4, pid=0xEA60), False),
    (BLUETOOTH, False),
])
def test_is_betaflight_port(info, expected):
    assert is_betaflight_port(info) is expected


def test_list_available_ports_flags_betaflight():
    with patch("betaflight.serial_conn.serial.tools.list_ports.comports", return_value=[BLUETOOTH, MATEK]):
        ports = SerialConnection.list_available_ports()
    flags = {p["port"]: p["is_betaflight"] for p in ports}
    assert flags == {BLUETOOTH.device: False, MATEK.device: True}


def test_find_betaflight_ports():
    with patch("betaflight.serial_conn.serial.tools.list_ports.comports", return_value=[BLUETOOTH, MATEK]):
        assert SerialConnection.find_betaflight_ports() == [MATEK.device]


# ── connect sans port ─────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clean_state():
    saved = (tools._conn, tools._msp, tools._bf)
    tools._conn = tools._msp = tools._bf = None
    yield
    tools._conn, tools._msp, tools._bf = saved


def test_connect_without_port_uses_the_single_detected_fc():
    with patch.object(SerialConnection, "find_betaflight_ports", return_value=[MATEK.device]), \
         patch.object(tools, "SerialConnection") as serial_cls:
        serial_cls.find_betaflight_ports = SerialConnection.find_betaflight_ports
        serial_cls.return_value.connect.return_value = False
        serial_cls.return_value.last_error = "test"
        tools.tool_connect(port=None)
    assert serial_cls.call_args.kwargs["port"] == MATEK.device


def test_connect_without_port_and_no_fc_explains():
    with patch.object(SerialConnection, "find_betaflight_ports", return_value=[]):
        result = tools.tool_connect(port=None)
    assert result["success"] is False
    assert "list_serial_ports" in result["error"]


def test_connect_without_port_and_several_fcs_asks_to_choose():
    candidates = ["/dev/cu.usbmodem1", "/dev/cu.usbmodem2"]
    with patch.object(SerialConnection, "find_betaflight_ports", return_value=candidates), \
         patch.object(tools, "SerialConnection") as serial_cls:
        serial_cls.find_betaflight_ports = SerialConnection.find_betaflight_ports
        result = tools.tool_connect(port=None)
    assert result["success"] is False
    assert all(c in result["error"] for c in candidates)
    serial_cls.assert_not_called()


def test_default_port_is_auto_detection(monkeypatch):
    import importlib, config.settings as settings
    monkeypatch.delenv("BETAFLIGHT_PORT", raising=False)
    assert importlib.reload(settings).SERIAL_PORT is None
    monkeypatch.setenv("BETAFLIGHT_PORT", "/dev/ttyACM0")
    assert importlib.reload(settings).SERIAL_PORT == "/dev/ttyACM0"
    monkeypatch.delenv("BETAFLIGHT_PORT")
    importlib.reload(settings)


# ── Batterie : courant signé ──────────────────────────────────────────

def test_battery_state_amperage_is_signed():
    """msp.c : sbufWriteU16(dst, (int16_t)constrain(getAmperage(), -0x8000, 0x7FFF))."""
    conn = MagicMock()
    conn.is_connected = True
    conn.timeout      = 2.0
    bf      = BetaflightCommands(MSPProtocol(conn))
    payload = struct.pack("<BHBHhBH", 1, 450, 42, 12, -150, 0, 420)
    frame   = b'$M>' + bytes([len(payload), MSPCodes.MSP_BATTERY_STATE]) + payload
    checksum = len(payload) ^ MSPCodes.MSP_BATTERY_STATE
    for b in payload:
        checksum ^= b
    frame += bytes([checksum])
    conn.read.side_effect = [frame[:3], frame[3:5], frame[5:]]
    assert bf.get_battery_state()["amperage_a"] == -1.5
