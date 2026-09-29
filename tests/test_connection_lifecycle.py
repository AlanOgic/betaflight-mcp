"""Cycle de vie de la connexion : identification à la connexion, reconnexion, déconnexion, reboot."""

import threading
import pytest
import serial
from unittest.mock import MagicMock, patch

from betaflight.msp_codes import MSPCodes
from betaflight.commands import MIN_API_VERSION
from betaflight.serial_conn import SerialConnection
from server import tools


# ── Helpers ───────────────────────────────────────────────────────────

def v1_frame(cmd: int, payload: bytes = b'', direction: bytes = b'>') -> bytes:
    size     = len(payload)
    checksum = size ^ cmd
    for b in payload:
        checksum ^= b
    return b'$M' + direction + bytes([size, cmd]) + payload + bytes([checksum])


def chunks(frame: bytes) -> list[bytes]:
    return [frame[:3], frame[3:5], frame[5:]]


def identity_frames(variant: bytes = b'BTFL', api: tuple = (1, 47), version: tuple = (4, 5, 2)) -> list:
    return (
        chunks(v1_frame(MSPCodes.MSP_API_VERSION, bytes([0, *api])))
        + chunks(v1_frame(MSPCodes.MSP_FC_VARIANT, variant))
        + chunks(v1_frame(MSPCodes.MSP_FC_VERSION, bytes(version)))
    )


def fake_serial(frames=None, opens: bool = True, last_error: str | None = None) -> MagicMock:
    conn = MagicMock()
    conn.connect.return_value = opens
    conn.is_connected         = opens
    conn.timeout              = 2.0
    conn.last_error           = last_error
    if frames is not None:
        conn.read.side_effect = frames
    else:
        conn.read.return_value = b''
    return conn


@pytest.fixture(autouse=True)
def clean_state():
    saved = (tools._conn, tools._msp, tools._bf)
    tools._conn = tools._msp = tools._bf = None
    yield
    tools._conn, tools._msp, tools._bf = saved


def connect_with(conn: MagicMock, port: str = "/dev/ttyACM0") -> dict:
    with patch.object(tools, "SerialConnection", return_value=conn):
        return tools.tool_connect(port=port)


# ── connect ───────────────────────────────────────────────────────────

def test_connect_reports_identity():
    conn   = fake_serial(identity_frames())
    result = connect_with(conn)
    assert result["success"] is True
    assert result["api_version"] == "1.47"
    assert result["fc_variant"]  == "BTFL"
    assert result["fc_version"]  == "4.5.2"
    assert tools._bf is not None


def test_connect_fails_and_closes_port_without_msp_reply():
    conn   = fake_serial()
    result = connect_with(conn)
    assert result["success"] is False
    assert "MSP" in result["error"]
    conn.disconnect.assert_called_once()
    assert tools._bf is None and tools._conn is None


def test_connect_refuses_non_betaflight_firmware():
    conn   = fake_serial(identity_frames(variant=b'INAV'))
    result = connect_with(conn)
    assert result["success"] is False
    assert "INAV" in result["error"]
    conn.disconnect.assert_called_once()
    assert tools._bf is None


def test_connect_refuses_api_below_minimum():
    too_old = (MIN_API_VERSION[0], MIN_API_VERSION[1] - 1)
    conn    = fake_serial(identity_frames(api=too_old))
    result  = connect_with(conn)
    assert result["success"] is False
    assert f"{too_old[0]}.{too_old[1]}" in result["error"]
    conn.disconnect.assert_called_once()


def test_connect_reports_serial_open_error():
    conn   = fake_serial(opens=False, last_error="[Errno 16] Resource busy")
    result = connect_with(conn, port="/dev/cu.usbmodem1101")
    assert result["success"] is False
    assert "Resource busy" in result["error"]
    assert "/dev/cu.usbmodem1101" in result["error"]
    assert tools._conn is None


def test_reconnect_closes_previous_connection():
    first  = fake_serial(identity_frames())
    second = fake_serial(identity_frames())
    assert connect_with(first)["success"] is True
    assert connect_with(second)["success"] is True
    first.disconnect.assert_called_once()
    assert tools._conn is second


def test_failed_reconnect_leaves_no_stale_connection():
    first = fake_serial(identity_frames())
    assert connect_with(first)["success"] is True
    assert connect_with(fake_serial())["success"] is False
    first.disconnect.assert_called_once()
    assert tools._bf is None


# ── disconnect ────────────────────────────────────────────────────────

def test_disconnect_closes_port_inside_msp_transaction():
    conn = fake_serial(identity_frames())
    assert connect_with(conn)["success"] is True
    msp  = tools._msp
    held = []
    conn.disconnect.side_effect = lambda: held.append(msp._lock._is_owned())
    assert tools.tool_disconnect()["success"] is True
    assert held == [True]
    assert tools._bf is None


def test_disconnect_waits_for_in_flight_request():
    conn = fake_serial(identity_frames())
    assert connect_with(conn)["success"] is True
    msp      = tools._msp
    released = threading.Event()
    order    = []

    def in_flight():
        with msp.transaction():
            order.append("request")
            released.wait(1)
            order.append("request done")

    worker = threading.Thread(target=in_flight)
    worker.start()
    while not order:
        pass
    closer = threading.Thread(target=lambda: (tools.tool_disconnect(), order.append("closed")))
    closer.start()
    released.set()
    worker.join()
    closer.join()
    assert order == ["request", "request done", "closed"]


def test_disconnect_when_not_connected_is_harmless():
    assert tools.tool_disconnect()["success"] is True


# ── reboot ────────────────────────────────────────────────────────────

def test_reboot_closes_connection():
    conn = fake_serial(identity_frames() + chunks(v1_frame(MSPCodes.MSP_SET_REBOOT, b'\x00')))
    assert connect_with(conn)["success"] is True
    result = tools.tool_reboot_fc()
    assert result["success"] is True
    assert "connect" in result["message"]
    conn.disconnect.assert_called_once()
    with pytest.raises(RuntimeError, match="connect"):
        tools.tool_get_rc()


def test_reboot_without_ack_counts_as_rebooting_and_closes_connection():
    """Matériel réel : le reset USB avale souvent l'ack de MSP_SET_REBOOT."""
    conn = fake_serial(identity_frames() + [OSError("read failed: [Errno 6] Device not configured")])
    assert connect_with(conn)["success"] is True
    result = tools.tool_reboot_fc()
    assert result["success"] is True
    conn.disconnect.assert_called_once()
    assert tools._bf is None


def test_busy_port_error_mentions_web_configurator():
    conn   = fake_serial(opens=False, last_error="[Errno 16] Resource busy")
    result = connect_with(conn)
    assert "navigateur" in result["error"]


def test_failed_reboot_keeps_connection():
    conn = fake_serial(identity_frames() + chunks(v1_frame(MSPCodes.MSP_SET_REBOOT, direction=b'!')))
    assert connect_with(conn)["success"] is True
    assert tools.tool_reboot_fc()["success"] is False
    conn.disconnect.assert_not_called()
    assert tools._bf is not None


# ── SerialConnection ──────────────────────────────────────────────────

def test_serial_connection_keeps_last_open_error():
    with patch("betaflight.serial_conn.serial.Serial",
               side_effect=serial.SerialException("[Errno 16] Resource busy")):
        sc = SerialConnection("/dev/ttyACM0")
        assert sc.connect() is False
        assert "Resource busy" in sc.last_error


def test_serial_connection_clears_last_error_on_success():
    with patch("betaflight.serial_conn.serial.Serial") as serial_cls:
        serial_cls.return_value.is_open = True
        sc = SerialConnection("/dev/ttyACM0")
        sc.last_error = "ancienne erreur"
        assert sc.connect() is True
        assert sc.last_error is None
