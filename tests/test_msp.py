"""Tests MSP protocol with a mock serial connection."""

import pytest
from unittest.mock import MagicMock
from betaflight.msp import MSPProtocol, MSPCommand


def make_protocol():
    conn = MagicMock()
    conn.is_connected = True
    return MSPProtocol(conn), conn


def _frame(cmd: int, payload: bytes = b'') -> bytes:
    """Build a valid MSP response frame ($M>)."""
    size     = len(payload)
    checksum = size ^ cmd
    for b in payload:
        checksum ^= b
    return b'$M>' + bytes([size, cmd]) + payload + bytes([checksum])


def test_build_frame_checksum():
    proto, _ = make_protocol()
    frame = proto._build_frame(MSPCommand.MSP_STATUS, b'')
    assert frame[:3] == b'$M<'
    # checksum = 0 ^ 101 = 101
    assert frame[-1] == 101


def test_read_response_valid():
    proto, conn = make_protocol()
    payload  = b'\x01\x02\x03'
    response = _frame(MSPCommand.MSP_RAW_IMU, payload)
    conn.read.side_effect = [response[:3], response[3:5], response[5:]]
    result = proto.read_response()
    assert result is not None
    assert result["cmd"] == MSPCommand.MSP_RAW_IMU
    assert result["payload"] == payload


def test_read_response_bad_checksum():
    proto, conn = make_protocol()
    frame = bytearray(_frame(MSPCommand.MSP_STATUS, b'\x01'))
    frame[-1] ^= 0xFF  # corrupt checksum
    frame = bytes(frame)
    conn.read.side_effect = [frame[:3], frame[3:5], frame[5:]]
    assert proto.read_response() is None
