"""Tests de la garde d'armement : aucune écriture tant que le FC est armé ou d'état inconnu."""

import struct
import pytest
from unittest.mock import MagicMock

from betaflight.msp import MSPProtocol
from betaflight.msp_codes import MSPCodes
from betaflight.commands import BetaflightCommands, WriteBlockedError

pytestmark = pytest.mark.arming_guard


# ── Helpers ───────────────────────────────────────────────────────────

def make_commands():
    conn = MagicMock()
    conn.is_connected = True
    conn.timeout      = 2.0
    return BetaflightCommands(MSPProtocol(conn)), conn


def v1_frame(cmd: int, payload: bytes = b'', direction: bytes = b'>') -> bytes:
    size     = len(payload)
    checksum = size ^ cmd
    for b in payload:
        checksum ^= b
    return b'$M' + direction + bytes([size, cmd]) + payload + bytes([checksum])


def chunks(frame: bytes) -> list[bytes]:
    return [frame[:3], frame[3:5], frame[5:]]


def status_ex(armed: bool, extra_modes: int = 0) -> list[bytes]:
    """Trame MSP_STATUS_EX (msp.c) ; bit 0 des mode flags = BOXARM = ARMING_FLAG(ARMED)."""
    mode_flags = (1 if armed else 0) | extra_modes
    payload = (
        struct.pack("<HHHIBHBB", 125, 0, 0x23, mode_flags, 0, 10, 4, 0)
        + bytes([0])                          # octets de flags supplémentaires
        + struct.pack("<BIB", 25, 0, 0)       # arming disable count/flags, config state
    )
    return chunks(v1_frame(MSPCodes.MSP_STATUS_EX, payload))


PID_15   = bytes([45, 80, 30, 47, 84, 34, 45, 80, 0, 50, 75, 75, 40, 0, 0])
RC_TUNING = bytes([7, 0, 67, 67, 67, 0, 50, 0, 0, 0, 0, 7, 7, 0, 0, 100]) \
    + struct.pack("<HHH", 1998, 1998, 1998) + bytes([3, 50])


# ── is_armed ──────────────────────────────────────────────────────────

def test_is_armed_true_when_arm_bit_set():
    bf, conn = make_commands()
    conn.read.side_effect = status_ex(armed=True)
    assert bf.is_armed() is True


def test_is_armed_ignores_other_mode_bits():
    bf, conn = make_commands()
    conn.read.side_effect = status_ex(armed=False, extra_modes=0b1110)
    assert bf.is_armed() is False


def test_is_armed_none_when_status_unreadable():
    bf, conn = make_commands()
    conn.read.return_value = b''
    assert bf.is_armed() is None


# ── Écritures bloquées ────────────────────────────────────────────────

@pytest.mark.parametrize("write", [
    lambda bf: bf.set_pid_values({"roll": {"p": 50, "i": 80, "d": 30}}),
    lambda bf: bf.set_rates({"roll_rate": 800}, expected_rates_type=3),
    lambda bf: bf.save_config(),
    lambda bf: bf.reboot_fc(),
    lambda bf: bf.set_motor([1000] * 4),
])
def test_writes_blocked_when_armed(write):
    bf, conn = make_commands()
    conn.read.side_effect = status_ex(armed=True)
    with pytest.raises(WriteBlockedError, match="armé"):
        write(bf)
    assert conn.write.call_count == 1   # uniquement la lecture de MSP_STATUS_EX


def test_write_blocked_when_arming_state_unknown():
    bf, conn = make_commands()
    conn.read.return_value = b''
    with pytest.raises(WriteBlockedError, match="inconnu"):
        bf.save_config()
    sent = [call[0][0][4] for call in conn.write.call_args_list]
    assert sent == [MSPCodes.MSP_STATUS_EX, MSPCodes.MSP_STATUS]   # lectures seules


def test_arming_check_is_inside_the_write_transaction():
    bf, conn = make_commands()
    seen = []
    original = bf._require_disarmed

    def spy():
        seen.append(bf.msp._lock._is_owned())
        return original()

    bf._require_disarmed = spy
    conn.read.side_effect = status_ex(armed=False) + chunks(v1_frame(MSPCodes.MSP_EEPROM_WRITE))
    assert bf.save_config() is True
    assert seen == [True]


# ── Écritures autorisées quand désarmé ────────────────────────────────

def test_set_pid_values_proceeds_when_disarmed():
    bf, conn = make_commands()
    conn.read.side_effect = (
        status_ex(armed=False)
        + chunks(v1_frame(MSPCodes.MSP_PID, PID_15))
        + chunks(v1_frame(MSPCodes.MSP_SET_PID))
    )
    assert bf.set_pid_values({"roll": {"p": 50, "i": 80, "d": 30}}) is True
    assert conn.write.call_args_list[0][0][0][4] == MSPCodes.MSP_STATUS_EX


def test_set_rates_proceeds_when_disarmed():
    bf, conn = make_commands()
    conn.read.side_effect = (
        status_ex(armed=False)
        + chunks(v1_frame(MSPCodes.MSP_RC_TUNING, RC_TUNING))
        + chunks(v1_frame(MSPCodes.MSP_SET_RC_TUNING))
    )
    assert bf.set_rates({"roll_rate": 800}, expected_rates_type=3) is True


def test_reboot_proceeds_when_disarmed():
    bf, conn = make_commands()
    conn.read.side_effect = status_ex(armed=False) + chunks(v1_frame(MSPCodes.MSP_SET_REBOOT, b'\x00'))
    assert bf.reboot_fc() is True


# ── Tools ─────────────────────────────────────────────────────────────

@pytest.fixture
def tools_with_bf():
    from server import tools as _tools
    bf, conn   = make_commands()
    original   = _tools._bf
    _tools._bf = bf
    yield _tools, conn
    _tools._bf = original


@pytest.mark.parametrize("call", [
    lambda t: t.tool_save_config(),
    lambda t: t.tool_reboot_fc(),
    lambda t: t.tool_set_pid_values("roll", 50, 80, 30),
])
def test_tools_report_armed_fc(tools_with_bf, call):
    tools, conn = tools_with_bf
    conn.read.side_effect = status_ex(armed=True)
    result = call(tools)
    assert result["success"] is False
    assert "armé" in result["error"]


def test_tool_set_rates_reports_armed_fc(tools_with_bf):
    tools, conn = tools_with_bf
    conn.read.side_effect = (
        chunks(v1_frame(MSPCodes.MSP_RC_TUNING, RC_TUNING))   # get_rates (validation)
        + status_ex(armed=True)
    )
    result = tools.tool_set_rates(roll_rate=800)
    assert result["success"] is False
    assert "armé" in result["error"]
