"""Tests de la couche transaction MSP : ack/erreur, appariement cmd, resync, verrou, timeout."""

import struct
import threading
import time
import pytest
from unittest.mock import MagicMock, patch

from betaflight.msp import MSPProtocol, _crc8_dvb_s2
from betaflight.msp_codes import MSPCodes
from betaflight.commands import BetaflightCommands
from betaflight.serial_conn import SerialConnection


# ── Helpers ───────────────────────────────────────────────────────────

def make_protocol():
    conn = MagicMock()
    conn.is_connected = True
    conn.timeout      = 2.0
    return MSPProtocol(conn), conn


def make_commands():
    proto, conn = make_protocol()
    return BetaflightCommands(proto), proto, conn


def v1_frame(cmd: int, payload: bytes = b'', direction: bytes = b'>') -> bytes:
    size     = len(payload)
    checksum = size ^ cmd
    for b in payload:
        checksum ^= b
    return b'$M' + direction + bytes([size, cmd]) + payload + bytes([checksum])


def chunks(frame: bytes) -> list[bytes]:
    """Découpe une trame v1 comme read_response la lit : préambule, meta, payload+crc."""
    return [frame[:3], frame[3:5], frame[5:]]


# ── Direction : ack vs erreur ─────────────────────────────────────────

def test_read_response_marks_ack_ok():
    proto, conn = make_protocol()
    conn.read.side_effect = chunks(v1_frame(MSPCodes.MSP_RC, b'\x01\x02'))
    result = proto.read_response()
    assert result["ok"] is True


def test_read_response_marks_error_reply_not_ok():
    proto, conn = make_protocol()
    conn.read.side_effect = chunks(v1_frame(MSPCodes.MSP_EEPROM_WRITE, direction=b'!'))
    result = proto.read_response()
    assert result["cmd"] == MSPCodes.MSP_EEPROM_WRITE
    assert result["ok"] is False


# ── Resynchronisation sur octets parasites ────────────────────────────

def test_read_response_resyncs_after_garbage():
    proto, conn = make_protocol()
    frame = v1_frame(MSPCodes.MSP_RC, b'\xDC\x05')
    conn.read.side_effect = [b'xx$', b'M', b'>', frame[3:5], frame[5:]]
    result = proto.read_response()
    assert result is not None
    assert result["cmd"]     == MSPCodes.MSP_RC
    assert result["payload"] == b'\xDC\x05'


@pytest.mark.parametrize("first_read, rest", [
    (b'$$M', [b'>']),
    (b'x$M', [b'>']),
    (b'$M$', [b'M', b'>']),
])
def test_read_response_resync_keeps_partial_preamble(first_read, rest):
    proto, conn = make_protocol()
    frame = v1_frame(MSPCodes.MSP_RC, b'\xDC\x05')
    conn.read.side_effect = [first_read] + rest + [frame[3:5], frame[5:]]
    result = proto.read_response()
    assert result is not None
    assert result["payload"] == b'\xDC\x05'


def test_read_response_gives_up_after_resync_limit():
    proto, conn = make_protocol()
    conn.read.side_effect = [b'abc'] + [b'x'] * (MSPProtocol.MAX_RESYNC_BYTES + 10)
    assert proto.read_response() is None
    assert conn.read.call_count <= 1 + MSPProtocol.MAX_RESYNC_BYTES


def test_read_response_none_on_timeout():
    proto, conn = make_protocol()
    conn.read.return_value = b''
    assert proto.read_response() is None


# ── Appariement requête / réponse ─────────────────────────────────────

def test_request_skips_stale_frame_of_other_cmd():
    proto, conn = make_protocol()
    stale = v1_frame(MSPCodes.MSP_ATTITUDE, b'\x00' * 6)
    good  = v1_frame(MSPCodes.MSP_RC, b'\xDC\x05')
    conn.read.side_effect = chunks(stale) + chunks(good)
    result = proto.request(MSPCodes.MSP_RC)
    assert result["cmd"]     == MSPCodes.MSP_RC
    assert result["payload"] == b'\xDC\x05'


def test_request_skips_corrupted_frame_then_returns_valid_one():
    proto, conn = make_protocol()
    bad  = bytearray(v1_frame(MSPCodes.MSP_RC, b'\x01\x02'))
    bad[-1] ^= 0xFF
    good = v1_frame(MSPCodes.MSP_RC, b'\xDC\x05')
    conn.read.side_effect = chunks(bytes(bad)) + chunks(good)
    result = proto.request(MSPCodes.MSP_RC)
    assert result["payload"] == b'\xDC\x05'


def test_request_matches_v2_reply():
    proto, conn = make_protocol()
    cmd     = 0x3000
    payload = b'\xAB'
    header  = bytes([0]) + struct.pack("<HH", cmd, len(payload))
    frame   = b'$X>' + header + payload + bytes([_crc8_dvb_s2(header + payload)])
    conn.read.side_effect = [frame[:3], frame[3:8], frame[8:]]
    result = proto.request(cmd)
    assert result["cmd"]     == cmd
    assert result["version"] == 2
    assert result["ok"] is True


def test_request_returns_error_reply_after_stale_frame():
    proto, conn = make_protocol()
    stale = v1_frame(MSPCodes.MSP_ATTITUDE, b'\x00' * 6)
    error = v1_frame(MSPCodes.MSP_EEPROM_WRITE, direction=b'!')
    conn.read.side_effect = chunks(stale) + chunks(error)
    result = proto.request(MSPCodes.MSP_EEPROM_WRITE)
    assert result["cmd"] == MSPCodes.MSP_EEPROM_WRITE
    assert result["ok"] is False


def test_request_returns_none_after_too_many_stale_frames():
    proto, conn = make_protocol()
    stale = v1_frame(MSPCodes.MSP_ATTITUDE, b'\x00' * 6)
    conn.read.side_effect = chunks(stale) * (MSPProtocol.MAX_STALE_FRAMES + 5)
    assert proto.request(MSPCodes.MSP_RC) is None
    assert conn.read.call_count == 3 * MSPProtocol.MAX_STALE_FRAMES


def test_request_returns_none_on_timeout():
    proto, conn = make_protocol()
    conn.read.return_value = b''
    assert proto.request(MSPCodes.MSP_RC) is None


# ── Timeout ponctuel ──────────────────────────────────────────────────

def test_request_applies_and_restores_timeout_override():
    proto, conn = make_protocol()
    seen  = []
    frame = v1_frame(MSPCodes.MSP_EEPROM_WRITE)
    parts = iter(chunks(frame))

    def read(_size):
        seen.append(conn.timeout)
        return next(parts)

    conn.read.side_effect = read
    result = proto.request(MSPCodes.MSP_EEPROM_WRITE, timeout=7.5)
    assert result["ok"] is True
    assert seen == [7.5, 7.5, 7.5]
    assert conn.timeout == 2.0


def test_request_returns_none_and_restores_timeout_on_write_error():
    proto, conn = make_protocol()
    conn.write.side_effect = OSError("port fermé")
    assert proto.request(MSPCodes.MSP_EEPROM_WRITE, timeout=7.5) is None
    assert conn.timeout == 2.0


def test_request_returns_none_when_port_closed():
    proto, conn = make_protocol()
    conn.write.side_effect = ConnectionError("Port série non connecté")
    assert proto.request(MSPCodes.MSP_RC) is None


def test_serial_connection_timeout_applies_to_open_port():
    with patch("betaflight.serial_conn.serial.Serial") as serial_cls:
        port = serial_cls.return_value
        port.is_open = True
        sc = SerialConnection("/dev/null", timeout=2.0)
        assert sc.connect()
        sc.timeout = 5.0
        assert sc.timeout == 5.0
        assert port.timeout == 5.0


# ── Verrou : une seule transaction à la fois ──────────────────────────

def test_request_serializes_concurrent_calls():
    proto, _ = make_protocol()
    state = {"active": 0, "max": 0, "cmd": None}

    def fake_send(cmd, payload=b''):
        state["active"] += 1
        state["max"]     = max(state["max"], state["active"])
        state["cmd"]     = cmd
        time.sleep(0.02)
        return True

    def fake_read():
        time.sleep(0.02)
        state["active"] -= 1
        return {"cmd": state["cmd"], "payload": b'', "version": 1, "ok": True}

    with patch.object(proto, "send_command", side_effect=fake_send), \
         patch.object(proto, "_read_frame", side_effect=fake_read):
        threads = [threading.Thread(target=proto.request, args=(MSPCodes.MSP_RC,))
                   for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    assert state["max"] == 1


def test_read_modify_write_holds_transaction_lock():
    """Le GET et le SET d'un read-modify-write ne doivent pas être entrelacés."""
    bf, proto, _ = make_commands()
    held = []

    def fake_request(cmd, payload=b'', timeout=None):
        held.append(proto._lock._is_owned())
        if cmd == MSPCodes.MSP_PID:
            return {"cmd": cmd, "payload": bytes(15), "version": 1, "ok": True}
        return {"cmd": cmd, "payload": b'', "version": 1, "ok": True}

    with patch.object(proto, "request", side_effect=fake_request):
        assert bf.set_pid_values({"roll": {"p": 45, "i": 42, "d": 32}}) is True
    assert held == [True, True]


def test_set_pid_values_false_on_serial_write_error():
    bf, _, conn = make_commands()
    conn.write.side_effect = OSError("USB débranché")
    assert bf.set_pid_values({"roll": {"p": 45, "i": 42, "d": 32}}) is False


# ── Commandes : réponses d'erreur ─────────────────────────────────────

def test_get_rc_none_on_error_reply():
    bf, _, conn = make_commands()
    conn.read.side_effect = chunks(v1_frame(MSPCodes.MSP_RC, direction=b'!'))
    assert bf.get_rc() is None


def test_get_fc_variant_none_on_error_reply():
    bf, _, conn = make_commands()
    conn.read.side_effect = chunks(v1_frame(MSPCodes.MSP_FC_VARIANT, b'BTFL', direction=b'!'))
    assert bf.get_fc_variant() is None


def test_get_fc_status_falls_back_when_status_ex_rejected():
    bf, _, conn = make_commands()
    status = struct.pack("<HHHIB", 125, 0, 0x23, 0, 0)
    conn.read.side_effect = (
        chunks(v1_frame(MSPCodes.MSP_STATUS_EX, direction=b'!'))
        + chunks(v1_frame(MSPCodes.MSP_STATUS, status))
    )
    result = bf.get_fc_status()
    assert result["source"]     == "MSP_STATUS"
    assert result["cycle_time"] == 125


# ── Écritures : ack obligatoire ───────────────────────────────────────

# MSP_RC_TUNING réaliste, rates Betaflight (rc_rate 1.0, super rate 0.7), 23 octets
BF_RC_TUNING = (bytes([100, 0, 70, 70, 70, 0, 50, 0, 0, 0, 0, 100, 100, 0, 0, 100])
                + struct.pack("<HHH", 1998, 1998, 1998) + bytes([0]))


def _pid_frame() -> bytes:
    return v1_frame(MSPCodes.MSP_PID, bytes([40, 38, 28, 42, 40, 30, 50, 45, 0, 50, 75, 75, 40, 0, 0]))


def test_set_pid_values_true_on_ack():
    bf, _, conn = make_commands()
    conn.read.side_effect = chunks(_pid_frame()) + chunks(v1_frame(MSPCodes.MSP_SET_PID))
    assert bf.set_pid_values({"roll": {"p": 45, "i": 42, "d": 32}}) is True


def test_set_pid_values_false_when_fc_rejects():
    bf, _, conn = make_commands()
    conn.read.side_effect = (
        chunks(_pid_frame()) + chunks(v1_frame(MSPCodes.MSP_SET_PID, direction=b'!'))
    )
    assert bf.set_pid_values({"roll": {"p": 45, "i": 42, "d": 32}}) is False


def test_set_pid_values_false_without_ack():
    bf, _, conn = make_commands()
    conn.read.side_effect = chunks(_pid_frame()) + [b'']
    assert bf.set_pid_values({"roll": {"p": 45, "i": 42, "d": 32}}) is False


def test_set_rates_false_when_fc_rejects():
    bf, _, conn = make_commands()
    rc_tuning = BF_RC_TUNING
    conn.read.side_effect = (
        chunks(v1_frame(MSPCodes.MSP_RC_TUNING, rc_tuning))
        + chunks(v1_frame(MSPCodes.MSP_SET_RC_TUNING, direction=b'!'))
    )
    assert bf.set_rates({"roll_expo": 0.1}, expected_rates_type=0) is False


def test_save_config_true_on_ack():
    bf, _, conn = make_commands()
    conn.read.side_effect = chunks(v1_frame(MSPCodes.MSP_EEPROM_WRITE))
    assert bf.save_config() is True


def test_save_config_false_when_fc_rejects():
    bf, _, conn = make_commands()
    conn.read.side_effect = chunks(v1_frame(MSPCodes.MSP_EEPROM_WRITE, direction=b'!'))
    assert bf.save_config() is False


def test_save_config_uses_eeprom_timeout():
    from config.settings import EEPROM_TIMEOUT
    bf, proto, _ = make_commands()
    with patch.object(proto, "request", return_value=None) as req:
        bf.save_config()
    assert req.call_args.kwargs["timeout"] == EEPROM_TIMEOUT


def test_reboot_fc_false_when_fc_rejects():
    bf, _, conn = make_commands()
    conn.read.side_effect = chunks(v1_frame(MSPCodes.MSP_SET_REBOOT, direction=b'!'))
    assert bf.reboot_fc() is False


# ── Tools : l'échec d'écriture remonte au LLM ─────────────────────────

@pytest.fixture
def tools_with_bf():
    from server import tools as _tools
    bf, _, conn = make_commands()
    original    = _tools._bf
    _tools._bf  = bf
    yield _tools, conn
    _tools._bf  = original


def test_tool_save_config_reports_rejection(tools_with_bf):
    tools, conn = tools_with_bf
    conn.read.side_effect = chunks(v1_frame(MSPCodes.MSP_EEPROM_WRITE, direction=b'!'))
    result = tools.tool_save_config()
    assert result["success"] is False
    assert "error" in result


def test_tool_save_config_reports_success(tools_with_bf):
    tools, conn = tools_with_bf
    conn.read.side_effect = chunks(v1_frame(MSPCodes.MSP_EEPROM_WRITE))
    assert tools.tool_save_config()["success"] is True


def test_tool_reboot_fc_reports_rejection(tools_with_bf):
    tools, conn = tools_with_bf
    conn.read.side_effect = chunks(v1_frame(MSPCodes.MSP_SET_REBOOT, direction=b'!'))
    result = tools.tool_reboot_fc()
    assert result["success"] is False
    assert "error" in result


def test_tool_set_rates_reports_rejection(tools_with_bf):
    tools, conn = tools_with_bf
    conn.read.side_effect = (
        chunks(v1_frame(MSPCodes.MSP_RC_TUNING, BF_RC_TUNING))   # get_rates (validation)
        + chunks(v1_frame(MSPCodes.MSP_RC_TUNING, BF_RC_TUNING)) # set_rates (read-modify-write)
        + chunks(v1_frame(MSPCodes.MSP_SET_RC_TUNING, direction=b'!'))
    )
    result = tools.tool_set_rates(roll_expo=0.1)
    assert result["success"] is False
    assert "error" in result


def test_write_rejected_message_does_not_claim_nothing_changed(tools_with_bf):
    tools, _ = tools_with_bf
    assert "Rien n'a été modifié" not in tools._WRITE_REJECTED


def test_tool_set_pid_values_reports_rejection(tools_with_bf):
    tools, conn = tools_with_bf
    conn.read.side_effect = (
        chunks(_pid_frame()) + chunks(v1_frame(MSPCodes.MSP_SET_PID, direction=b'!'))
    )
    result = tools.tool_set_pid_values("roll", 45, 42, 32)
    assert result["success"] is False
    assert "error" in result
