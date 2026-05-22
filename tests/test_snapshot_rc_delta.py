"""Tests unitaires pour tool_snapshot_rc_delta."""

import struct
import pytest
from unittest.mock import MagicMock, patch

from betaflight.msp import MSPProtocol
from betaflight.commands import BetaflightCommands
from betaflight.msp_codes import MSPCodes


def make_commands():
    conn = MagicMock()
    conn.is_connected = True
    proto = MSPProtocol(conn)
    return BetaflightCommands(proto), proto, conn


def v1_frame(cmd: int, payload: bytes) -> bytes:
    size = len(payload)
    checksum = size ^ cmd
    for b in payload:
        checksum ^= b
    return b'$M>' + bytes([size, cmd]) + payload + bytes([checksum])


def make_rc_frame(channels: list[int]) -> bytes:
    payload = struct.pack(f"<{len(channels)}H", *channels)
    return v1_frame(MSPCodes.MSP_RC, payload)


def make_tool_with_rc(channels: list[int]):
    """Retourne tool_snapshot_rc_delta pré-configuré avec un BetaflightCommands mocké."""
    bf, proto, conn = make_commands()
    frame = make_rc_frame(channels)
    conn.read.side_effect = [frame[:3], frame[3:5], frame[5:]]

    from server import tools as _tools
    original_bf = _tools._bf
    _tools._bf = bf
    return _tools, original_bf


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_roll_moved_right():
    channels = [1987, 1500, 1500, 1000, 1000, 1000, 1000, 1000]
    baseline = [1502, 1500, 1500, 1000, 1000, 1000, 1000, 1000]
    tools, orig = make_tool_with_rc(channels)
    try:
        result = tools.tool_snapshot_rc_delta(baseline)
        assert len(result["changed"]) == 1
        c = result["changed"][0]
        assert c["channel"] == 0
        assert c["name"]    == "roll"
        assert c["delta"]   == pytest.approx(485)
        assert 0 not in result["unchanged"]
    finally:
        tools._bf = orig


def test_no_movement_all_unchanged():
    channels = [1500, 1500, 1500, 1000, 1000, 1000, 1000, 1000]
    baseline = [1500, 1500, 1500, 1000, 1000, 1000, 1000, 1000]
    tools, orig = make_tool_with_rc(channels)
    try:
        result = tools.tool_snapshot_rc_delta(baseline)
        assert result["changed"]   == []
        assert len(result["unchanged"]) == 8
    finally:
        tools._bf = orig


def test_aux_switch_flipped():
    channels = [1500, 1500, 1500, 1000, 2000, 1000, 1000, 1000]
    baseline = [1500, 1500, 1500, 1000, 1000, 1000, 1000, 1000]
    tools, orig = make_tool_with_rc(channels)
    try:
        result = tools.tool_snapshot_rc_delta(baseline)
        assert len(result["changed"]) == 1
        c = result["changed"][0]
        assert c["channel"] == 4
        assert c["name"]    == "aux1"
        assert c["delta"]   == 1000
    finally:
        tools._bf = orig


def test_custom_threshold():
    channels = [1600, 1500, 1500, 1000, 1000, 1000, 1000, 1000]
    baseline = [1500, 1500, 1500, 1000, 1000, 1000, 1000, 1000]
    tools, orig = make_tool_with_rc(channels)
    try:
        # delta=100, threshold=200 → unchanged
        result = tools.tool_snapshot_rc_delta(baseline, threshold=200)
        assert result["changed"] == []
        # même delta, threshold=50 → changed
        frame2 = make_rc_frame(channels)
        from betaflight.msp import MSPProtocol
        conn2 = MagicMock()
        conn2.is_connected = True
        proto2 = MSPProtocol(conn2)
        bf2 = BetaflightCommands(proto2)
        conn2.read.side_effect = [frame2[:3], frame2[3:5], frame2[5:]]
        tools._bf = bf2
        result2 = tools.tool_snapshot_rc_delta(baseline, threshold=50)
        assert len(result2["changed"]) == 1
    finally:
        tools._bf = orig


def test_snapshot_contains_all_current_values():
    channels = [1987, 1500, 1500, 1000, 1000, 1000, 1000, 1000]
    baseline = [1500] * 8
    tools, orig = make_tool_with_rc(channels)
    try:
        result = tools.tool_snapshot_rc_delta(baseline)
        assert result["snapshot"] == channels
    finally:
        tools._bf = orig


def test_baseline_shorter_than_channels():
    """baseline courte → les canaux manquants supposés à 1500."""
    channels = [1500, 1500, 1500, 1800, 1000, 1000, 1000, 1000]
    baseline = [1500, 1500, 1500]  # throttle manquant → supposé 1500
    tools, orig = make_tool_with_rc(channels)
    try:
        result = tools.tool_snapshot_rc_delta(baseline, threshold=200)
        assert any(c["channel"] == 3 for c in result["changed"])
    finally:
        tools._bf = orig
