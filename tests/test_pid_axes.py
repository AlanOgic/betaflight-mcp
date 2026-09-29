"""Tests de la table d'axes PID : firmware 4.x = ROLL, PITCH, YAW, LEVEL, MAG (PID_ITEM_COUNT=5)."""

import pytest
from unittest.mock import MagicMock

from betaflight.msp import MSPProtocol
from betaflight.msp_codes import MSPCodes
from betaflight.commands import BetaflightCommands, PID_AXES
from server.validators import validate_pid


# ── Helpers ───────────────────────────────────────────────────────────

def make_commands():
    conn = MagicMock()
    conn.is_connected = True
    conn.timeout      = 2.0
    return BetaflightCommands(MSPProtocol(conn)), conn


def v1_frame(cmd: int, payload: bytes = b'') -> bytes:
    size     = len(payload)
    checksum = size ^ cmd
    for b in payload:
        checksum ^= b
    return b'$M>' + bytes([size, cmd]) + payload + bytes([checksum])


def chunks(frame: bytes) -> list[bytes]:
    return [frame[:3], frame[3:5], frame[5:]]


# roll, pitch, yaw, level, mag — valeurs par défaut Betaflight 4.5
FC_PID = bytes([45, 80, 30,  47, 84, 34,  45, 80, 0,  50, 75, 75,  40, 0, 0])


def feed_get_then_ack(conn, pid_payload: bytes):
    conn.read.side_effect = (
        chunks(v1_frame(MSPCodes.MSP_PID, pid_payload))
        + chunks(v1_frame(MSPCodes.MSP_SET_PID))
    )


def written_set_pid_payload(conn) -> bytes:
    frame = conn.write.call_args_list[1][0][0]
    assert frame[4] == MSPCodes.MSP_SET_PID
    size  = frame[3]
    return frame[5:5 + size]


# ── Table d'axes ──────────────────────────────────────────────────────

def test_pid_axes_match_firmware_order():
    assert PID_AXES == ("roll", "pitch", "yaw", "level", "mag")


# ── Lecture ───────────────────────────────────────────────────────────

def test_get_pid_values_labels_level_and_mag():
    bf, conn = make_commands()
    conn.read.side_effect = chunks(v1_frame(MSPCodes.MSP_PID, FC_PID))
    result = bf.get_pid_values()
    assert list(result) == list(PID_AXES)
    assert result["level"] == {"p": 50, "i": 75, "d": 75}
    assert result["mag"]   == {"p": 40, "i": 0,  "d": 0}


def test_get_pid_values_ignores_unknown_trailing_axes():
    bf, conn = make_commands()
    conn.read.side_effect = chunks(v1_frame(MSPCodes.MSP_PID, FC_PID + bytes([1, 2, 3])))
    assert list(bf.get_pid_values()) == list(PID_AXES)


# ── Écriture ──────────────────────────────────────────────────────────

def test_set_pid_level_writes_level_slot():
    bf, conn = make_commands()
    feed_get_then_ack(conn, FC_PID)
    assert bf.set_pid_values({"level": {"p": 60, "i": 70, "d": 80}}) is True
    payload = written_set_pid_payload(conn)
    assert len(payload)  == 15
    assert payload[9:12] == bytes([60, 70, 80])
    assert payload[:9]   == FC_PID[:9]    # roll/pitch/yaw inchangés
    assert payload[12:]  == FC_PID[12:]   # mag inchangé


def test_set_pid_preserves_unknown_trailing_axes():
    """Un firmware plus récent avec plus d'axes ne doit pas voir ses axes inconnus remis à zéro."""
    bf, conn = make_commands()
    extra = bytes([11, 22, 33])
    feed_get_then_ack(conn, FC_PID + extra)
    assert bf.set_pid_values({"roll": {"p": 50, "i": 90, "d": 35}}) is True
    payload = written_set_pid_payload(conn)
    assert payload[:3]  == bytes([50, 90, 35])
    assert payload[15:] == extra


def test_set_pid_refuses_short_payload():
    """Le firmware lit 0 au-delà du payload : écrire un payload court remettrait des gains à zéro."""
    bf, conn = make_commands()
    conn.read.side_effect = chunks(v1_frame(MSPCodes.MSP_PID, FC_PID[:9]))
    assert bf.set_pid_values({"roll": {"p": 50, "i": 90, "d": 35}}) is False
    assert conn.write.call_count == 1  # seulement le GET, aucun SET envoyé


def test_set_pid_rejects_unknown_axis():
    bf, conn = make_commands()
    feed_get_then_ack(conn, FC_PID)
    assert bf.set_pid_values({"alt": {"p": 50, "i": 90, "d": 35}}) is False
    assert conn.write.call_count <= 1


# ── Validation ────────────────────────────────────────────────────────

@pytest.mark.parametrize("axis", ["alt", "pos", "posr", "navr", "vel"])
def test_validate_pid_rejects_pre_4x_axes(axis):
    assert validate_pid(axis, 40, 40, 20)["errors"]


@pytest.mark.parametrize("axis", PID_AXES)
def test_validate_pid_accepts_firmware_axes(axis):
    assert validate_pid(axis, 40, 40, 20)["errors"] == []
