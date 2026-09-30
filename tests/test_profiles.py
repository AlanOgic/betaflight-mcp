"""
Tests de sélection de profils (MSP_SELECT_SETTING) et des profils batterie
(MSP2_BATTERY_PROFILE / MSP2_SET_BATTERY_PROFILE, API 1.48, Betaflight 2026.6).
Le firmware ignore silencieusement un changement de profil PID si le FC est armé et
ramène à 0 un index hors plage : la sélection est vérifiée par relecture.
"""

import struct
import pytest
from unittest.mock import MagicMock

from betaflight import battery_profiles as bp
from betaflight.msp import MSPProtocol
from betaflight.msp_codes import MSPCodes
from betaflight.commands import BetaflightCommands
from server.validators import validate_battery_profile

API_2026 = (1, 48)


def make_commands(api=API_2026):
    conn = MagicMock()
    conn.is_connected = True
    conn.timeout      = 2.0
    bf = BetaflightCommands(MSPProtocol(conn))
    bf.api_version = api
    return bf, conn


def v1_frame(cmd: int, payload: bytes = b'', direction: bytes = b'>') -> bytes:
    size     = len(payload)
    checksum = size ^ cmd
    for b in payload:
        checksum ^= b
    return b'$M' + direction + bytes([size, cmd]) + payload + bytes([checksum])


def v2_frame(cmd: int, payload: bytes = b'', direction: bytes = b'>') -> bytes:
    from betaflight.msp import _crc8_dvb_s2
    header = bytes([0]) + struct.pack("<HH", cmd, len(payload))
    return b'$X' + direction + header + payload + bytes([_crc8_dvb_s2(header + payload)])


def chunks(frame: bytes) -> list[bytes]:
    return [frame[:3], frame[3:5], frame[5:]]


def chunks_v2(frame: bytes) -> list[bytes]:
    return [frame[:3], frame[3:8], frame[8:]]


def status_ex(pid_profile: int, rate_profile: int = 0) -> list[bytes]:
    payload = (struct.pack("<HHHIBHBB", 125, 0, 0x23, 0, pid_profile, 10, 4, rate_profile)
               + bytes([0]) + struct.pack("<BIB", 0, 0, 0))
    return chunks(v1_frame(MSPCodes.MSP_STATUS_EX, payload))


def battery_profile_payload(index: int, vmin=330, vmax=430, vwarn=350, vfull=410,
                            capacity=0, force=0, alert=10) -> bytes:
    return struct.pack("<BHHHHHBB", index, vmin, vmax, vwarn, vfull, capacity, force, alert)


def active_battery(index: int) -> list[bytes]:
    return chunks_v2(v2_frame(MSPCodes.MSP2_BATTERY_PROFILE, battery_profile_payload(index)))


def written(conn, index: int) -> tuple[int, bytes]:
    frame = conn.write.call_args_list[index][0][0]
    if frame[:2] == b'$X':
        size = struct.unpack_from("<H", frame, 6)[0]
        return struct.unpack_from("<H", frame, 4)[0], frame[8:8 + size]
    return frame[4], frame[5:5 + frame[3]]


# ── Table ─────────────────────────────────────────────────────────────

def test_battery_profile_table_matches_msp2_layout():
    expected = {"vbat_min_cell_voltage": (1, 2), "vbat_max_cell_voltage": (3, 2),
                "vbat_warning_cell_voltage": (5, 2), "vbat_full_cell_voltage": (7, 2),
                "bat_capacity": (9, 2), "force_battery_cell_count": (11, 1),
                "cbat_alert_percent": (12, 1)}
    assert {n: (f.offset, f.size) for n, f in bp.FIELDS.items()} == expected
    assert bp.PROFILE_COUNT == 3


# ── Profils actifs ────────────────────────────────────────────────────

def test_get_profiles():
    bf, conn = make_commands()
    conn.read.side_effect = status_ex(pid_profile=2, rate_profile=1) + active_battery(1)
    assert bf.get_profiles() == {"pid_profile": 2, "pid_profile_count": 4,
                                 "rate_profile": 1, "battery_profile": 1}


def test_get_profiles_without_battery_profiles_before_1_48():
    bf, conn = make_commands(api=(1, 47))
    conn.read.side_effect = status_ex(pid_profile=0)
    assert "battery_profile" not in bf.get_profiles()


@pytest.mark.parametrize("kind, index, byte", [("pid", 2, 2), ("rate", 1, 0x81), ("battery", 2, 0x42)])
def test_select_profile_encodes_masks(kind, index, byte):
    bf, conn = make_commands()
    after = {"pid": status_ex(2) + active_battery(0),
             "rate": status_ex(0, 1) + active_battery(0),
             "battery": status_ex(0) + active_battery(2)}[kind]
    conn.read.side_effect = chunks(v1_frame(MSPCodes.MSP_SELECT_SETTING)) + after
    assert bf.select_profile(kind, index) is True
    cmd, payload = written(conn, 0)
    assert cmd == MSPCodes.MSP_SELECT_SETTING and payload == bytes([byte])


def test_select_profile_false_when_firmware_ignored_it():
    """Ack reçu mais profil inchangé (ex. index hors plage ramené à 0)."""
    bf, conn = make_commands()
    conn.read.side_effect = chunks(v1_frame(MSPCodes.MSP_SELECT_SETTING)) + status_ex(0) + active_battery(0)
    assert bf.select_profile("pid", 3) is False


def test_select_battery_profile_refused_before_1_48():
    bf, conn = make_commands(api=(1, 47))
    assert bf.select_profile("battery", 1) is False
    assert conn.write.call_count == 0


# ── Profils batterie ──────────────────────────────────────────────────

def test_get_battery_profiles_reads_all_three():
    bf, conn = make_commands()
    conn.read.side_effect = sum((chunks_v2(v2_frame(MSPCodes.MSP2_BATTERY_PROFILE,
                                                    battery_profile_payload(i, vmax=430 + i)))
                                 for i in range(3)), [])
    profiles = bf.get_battery_profiles()
    assert [p["index"] for p in profiles] == [0, 1, 2]
    assert [p["vbat_max_cell_voltage"] for p in profiles] == [430, 431, 432]
    requested = [written(conn, i) for i in range(3)]
    assert requested == [(MSPCodes.MSP2_BATTERY_PROFILE, bytes([i])) for i in range(3)]


def test_set_battery_profile_patches_the_indexed_profile():
    bf, conn = make_commands()
    original = battery_profile_payload(2)
    conn.read.side_effect = (chunks_v2(v2_frame(MSPCodes.MSP2_BATTERY_PROFILE, original))
                             + chunks_v2(v2_frame(MSPCodes.MSP2_SET_BATTERY_PROFILE)))
    updates = {"vbat_min_cell_voltage": 320, "vbat_warning_cell_voltage": 330, "vbat_max_cell_voltage": 440}
    assert bf.set_battery_profile(2, updates) is True
    assert written(conn, 0) == (MSPCodes.MSP2_BATTERY_PROFILE, bytes([2]))
    cmd, payload = written(conn, 1)
    assert cmd == MSPCodes.MSP2_SET_BATTERY_PROFILE
    assert payload == battery_profile_payload(2, vmin=320, vwarn=330, vmax=440)


def test_set_battery_profile_refuses_bad_index_and_old_api():
    bf, conn = make_commands()
    assert bf.set_battery_profile(3, {"vbat_max_cell_voltage": 440}) is False
    bf_old, conn_old = make_commands(api=(1, 47))
    assert bf_old.set_battery_profile(0, {"vbat_max_cell_voltage": 440}) is False
    assert conn.write.call_count == 0 and conn_old.write.call_count == 0


# ── Validation ────────────────────────────────────────────────────────

CURRENT = bp.parse(battery_profile_payload(0))


def test_validate_battery_profile_accepts_hv_voltages():
    updates = {"vbat_min_cell_voltage": 320, "vbat_warning_cell_voltage": 330, "vbat_max_cell_voltage": 440}
    assert validate_battery_profile(CURRENT, updates, API_2026)["errors"] == []


@pytest.mark.parametrize("updates, fragment", [
    ({"vbat_full_cell_voltage": 440},  "full"),       # full 440 > max 430
    ({"vbat_max_cell_voltage": 400},   "full"),       # max 400 < full 410
    ({"force_battery_cell_count": 25}, "24"),
    ({"cbat_alert_percent": 101},      "100"),
])
def test_validate_battery_profile_rejects(updates, fragment):
    errors = validate_battery_profile(CURRENT, updates, API_2026)["errors"]
    assert errors and fragment in errors[0]


def test_validate_battery_profile_refuses_before_1_48():
    assert "1.48" in validate_battery_profile(CURRENT, {"bat_capacity": 450}, (1, 47))["errors"][0]


# ── Tools ─────────────────────────────────────────────────────────────

@pytest.fixture
def tools_with_bf():
    from server import tools as _tools
    bf, conn   = make_commands()
    original   = _tools._bf
    _tools._bf = bf
    yield _tools, conn
    _tools._bf = original


def test_tool_select_profile_reports_active_profiles(tools_with_bf):
    tools, conn = tools_with_bf
    conn.read.side_effect = (chunks(v1_frame(MSPCodes.MSP_SELECT_SETTING))
                             + status_ex(2) + active_battery(0)      # vérification
                             + status_ex(2) + active_battery(0))     # réponse du tool
    result = tools.tool_select_profile("pid", 2)
    assert result["success"] is True
    assert result["profiles"]["pid_profile"] == 2


def test_tool_select_profile_reports_ignored_switch(tools_with_bf):
    tools, conn = tools_with_bf
    conn.read.side_effect = (chunks(v1_frame(MSPCodes.MSP_SELECT_SETTING))
                             + status_ex(0) + active_battery(0))
    result = tools.tool_select_profile("pid", 3)
    assert result["success"] is False


def test_tool_set_battery_profile_validation_error_sends_nothing_but_reads(tools_with_bf):
    tools, conn = tools_with_bf
    conn.read.side_effect = chunks_v2(v2_frame(MSPCodes.MSP2_BATTERY_PROFILE, battery_profile_payload(1)))
    result = tools.tool_set_battery_profile(1, {"vbat_full_cell_voltage": 450})
    assert result["success"] is False and result["errors"]
    assert conn.write.call_count == 1
