"""
Tests MSP_PID_ADVANCED (lecture + écriture) et du mode simplified PIDs.
Layout vérifié : msp.c 4.5.2 → 2026.6.2 (octets identiques ; 39-43 = d_min jusqu'à
l'API 1.46, d_max à partir de 1.47). Plages : cli/settings.c 2025.12.5 = 2026.6.2.
"""

import struct
import pytest
from unittest.mock import MagicMock

from betaflight import pid_advanced as pa
from betaflight.msp import MSPProtocol
from betaflight.msp_codes import MSPCodes
from betaflight.commands import BetaflightCommands
from server.validators import validate_pid_advanced


# ── Helpers ───────────────────────────────────────────────────────────

API_2026 = (1, 48)
API_4_5  = (1, 46)


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


def chunks(frame: bytes) -> list[bytes]:
    return [frame[:3], frame[3:5], frame[5:]]


def pid_advanced_payload(length: int = 61, **fields) -> bytes:
    """Payload MSP_PID_ADVANCED 2026.6 (61 octets) avec des valeurs par défaut réalistes."""
    values = {
        "feedforward_transition": 0, "acc_limit": 0, "acc_limit_yaw": 0, "angle_limit": 60,
        "anti_gravity_gain": 80, "iterm_rotation": 0, "iterm_relax": 1, "iterm_relax_type": 1,
        "throttle_boost": 5, "acro_trainer_angle_limit": 20,
        "f_roll": 120, "f_pitch": 125, "f_yaw": 120,
        "d_max_roll": 40, "d_max_pitch": 46, "d_max_yaw": 0, "d_max_gain": 37, "d_max_advance": 20,
        "use_integrated_yaw": 0, "integrated_yaw_relax": 200, "iterm_relax_cutoff": 15,
        "motor_output_limit": 100, "auto_profile_cell_count": 0, "dyn_idle_min_rpm": 0,
        "feedforward_averaging": 0, "feedforward_smooth_factor": 25, "feedforward_boost": 15,
        "feedforward_max_rate_limit": 90, "feedforward_jitter_factor": 7,
        "vbat_sag_compensation": 0, "thrust_linear": 0, "tpa_mode": 1, "tpa_rate": 65,
        "tpa_breakpoint": 1350,
    }
    values.update(fields)
    raw = bytearray(64)
    for name, value in values.items():
        field = pa.FIELDS[name]
        fmt   = {1: "b" if field.signed else "B", 2: "<h" if field.signed else "<H"}[field.size]
        struct.pack_into(fmt, raw, field.offset, value)
    return bytes(raw[:length])


def simplified_payload(mode: int = 2) -> bytes:
    """MSP_SIMPLIFIED_TUNING : 9 octets PIDs + 8 réservés + filtres D-term (5) + gyro (5)."""
    return bytes([mode, 100, 100, 100, 100, 100, 100, 100, 100]) + bytes(8) \
        + bytes([1, 100, 0, 0, 0]) + bytes([1, 100, 0, 0, 0])


def written(conn, index: int) -> tuple[int, bytes]:
    frame = conn.write.call_args_list[index][0][0]
    return frame[4], frame[5:5 + frame[3]]


# ── Table ─────────────────────────────────────────────────────────────

def test_field_table_matches_msp_layout():
    expected = {
        "feedforward_transition": (8, 1), "acc_limit": (13, 2), "acc_limit_yaw": (15, 2),
        "angle_limit": (17, 1), "anti_gravity_gain": (21, 2), "iterm_rotation": (25, 1),
        "iterm_relax": (27, 1), "iterm_relax_type": (28, 1), "throttle_boost": (30, 1),
        "acro_trainer_angle_limit": (31, 1), "f_roll": (32, 2), "f_pitch": (34, 2), "f_yaw": (36, 2),
        "d_max_roll": (39, 1), "d_max_pitch": (40, 1), "d_max_yaw": (41, 1),
        "d_max_gain": (42, 1), "d_max_advance": (43, 1), "use_integrated_yaw": (44, 1),
        "integrated_yaw_relax": (45, 1), "iterm_relax_cutoff": (46, 1), "motor_output_limit": (47, 1),
        "auto_profile_cell_count": (48, 1), "dyn_idle_min_rpm": (49, 1),
        "feedforward_averaging": (50, 1), "feedforward_smooth_factor": (51, 1),
        "feedforward_boost": (52, 1), "feedforward_max_rate_limit": (53, 1),
        "feedforward_jitter_factor": (54, 1), "vbat_sag_compensation": (55, 1),
        "thrust_linear": (56, 1), "tpa_mode": (57, 1), "tpa_rate": (58, 1), "tpa_breakpoint": (59, 2),
    }
    assert {name: (f.offset, f.size) for name, f in pa.FIELDS.items()} == expected


# ── Lecture ───────────────────────────────────────────────────────────

def test_parse_uses_cli_names_and_enum_labels():
    raw    = pid_advanced_payload(iterm_relax=2, iterm_relax_type=0, iterm_rotation=1,
                                  auto_profile_cell_count=-1, feedforward_averaging=2)
    result = pa.parse(raw, API_2026)
    assert result["iterm_relax"]             == "RPY"
    assert result["iterm_relax_type"]        == "GYRO"
    assert result["iterm_rotation"]          == "ON"
    assert result["feedforward_averaging"]   == "3_POINT"
    assert result["auto_profile_cell_count"] == -1
    assert result["f_roll"]                  == 120
    assert result["tpa_breakpoint"]          == 1350


def test_parse_drops_bytes_the_firmware_always_sends_as_zero():
    result = pa.parse(pid_advanced_payload(), API_2026)
    for junk in ("delta_method", "vbat_pid_compensation", "iterm_throttle_threshold",
                 "dterm_setpoint_weight", "anti_gravity_mode"):
        assert junk not in result


def test_parse_labels_d_min_before_api_1_47():
    result = pa.parse(pid_advanced_payload(), API_4_5)
    assert result["d_min_roll"] == 40
    assert "d_min_gain" in result and "d_max_roll" not in result


def test_parse_labels_d_max_from_api_1_47():
    result = pa.parse(pid_advanced_payload(), (1, 47))
    assert result["d_max_roll"] == 40
    assert "d_min_roll" not in result


def test_parse_short_payload_only_returns_present_fields():
    result = pa.parse(pid_advanced_payload(length=47), (1, 42))
    assert "iterm_relax_cutoff" in result
    assert "motor_output_limit" not in result


def test_get_pid_advanced_includes_simplified_mode():
    bf, conn = make_commands()
    conn.read.side_effect = (
        chunks(v1_frame(MSPCodes.MSP_PID_ADVANCED, pid_advanced_payload()))
        + chunks(v1_frame(MSPCodes.MSP_SIMPLIFIED_TUNING, simplified_payload(mode=2)))
    )
    result = bf.get_pid_advanced()
    assert result["simplified_pids_mode"] == "RPY"
    assert result["d_max_roll"] == 40


# ── Validation ────────────────────────────────────────────────────────

def test_validate_accepts_dump_values():
    updates = {"iterm_relax": "RPY", "iterm_relax_type": "GYRO", "iterm_rotation": "ON",
               "f_roll": 70, "f_pitch": 70, "f_yaw": 80, "d_max_roll": 0, "acc_limit_yaw": 100,
               "angle_limit": 30, "auto_profile_cell_count": 2, "simplified_pids_mode": "OFF"}
    assert validate_pid_advanced(updates, API_2026) == {"errors": [], "warnings": []}


@pytest.mark.parametrize("updates, fragment", [
    ({"level_limit": 30},           "inconnu"),
    ({"f_roll": 1001},              "1000"),
    ({"angle_limit": 5},            "10"),
    ({"iterm_relax": "BOTH"},       "RPY"),
    ({"feedforward_averaging": 4},  "4_POINT"),
    ({"throttle_boost": True},      "entier"),
    ({"auto_profile_cell_count": -2}, "-1"),
])
def test_validate_rejects(updates, fragment):
    errors = validate_pid_advanced(updates, API_2026)["errors"]
    assert errors and fragment in errors[0]


def test_validate_refuses_writes_before_api_1_47():
    errors = validate_pid_advanced({"f_roll": 70}, API_4_5)["errors"]
    assert errors and "1.47" in errors[0]


def test_validate_accepts_enum_as_index():
    assert validate_pid_advanced({"iterm_relax": 2}, API_2026)["errors"] == []


# ── Écriture ──────────────────────────────────────────────────────────

def test_set_pid_advanced_patches_only_requested_bytes():
    bf, conn = make_commands()
    original = pid_advanced_payload()
    conn.read.side_effect = (
        chunks(v1_frame(MSPCodes.MSP_PID_ADVANCED, original))
        + chunks(v1_frame(MSPCodes.MSP_SET_PID_ADVANCED))
    )
    updates = {"f_roll": 70, "iterm_relax": "RPY", "auto_profile_cell_count": -1, "acc_limit_yaw": 100}
    assert bf.set_pid_advanced(updates) is True
    cmd, payload = written(conn, 1)
    assert cmd == MSPCodes.MSP_SET_PID_ADVANCED
    assert len(payload) == len(original)
    assert struct.unpack_from("<H", payload, 32)[0] == 70
    assert payload[27] == 2
    assert payload[48] == 0xFF
    assert struct.unpack_from("<H", payload, 15)[0] == 100
    changed = {i for i in range(len(original)) if payload[i] != original[i]}
    assert changed <= {32, 33, 27, 48, 15, 16}


def test_set_pid_advanced_refuses_field_absent_from_payload():
    bf, conn = make_commands()
    conn.read.side_effect = chunks(v1_frame(MSPCodes.MSP_PID_ADVANCED, pid_advanced_payload(length=47)))
    assert bf.set_pid_advanced({"tpa_rate": 50}) is False
    assert conn.write.call_count == 1


def test_set_pid_advanced_refuses_before_api_1_47():
    bf, conn = make_commands(api=API_4_5)
    assert bf.set_pid_advanced({"f_roll": 70}) is False
    assert conn.write.call_count == 0


def test_set_simplified_mode_off_patches_only_first_byte():
    bf, conn = make_commands()
    original = simplified_payload(mode=2)
    conn.read.side_effect = (
        chunks(v1_frame(MSPCodes.MSP_SIMPLIFIED_TUNING, original))
        + chunks(v1_frame(MSPCodes.MSP_SET_SIMPLIFIED_TUNING))
    )
    assert bf.set_pid_advanced({"simplified_pids_mode": "OFF"}) is True
    cmd, payload = written(conn, 1)
    assert cmd == MSPCodes.MSP_SET_SIMPLIFIED_TUNING
    assert payload == bytes([0]) + original[1:]
    assert conn.write.call_count == 2          # pas d'écriture PID_ADVANCED inutile


def test_set_simplified_mode_is_written_before_pid_advanced():
    bf, conn = make_commands()
    conn.read.side_effect = (
        chunks(v1_frame(MSPCodes.MSP_SIMPLIFIED_TUNING, simplified_payload()))
        + chunks(v1_frame(MSPCodes.MSP_SET_SIMPLIFIED_TUNING))
        + chunks(v1_frame(MSPCodes.MSP_PID_ADVANCED, pid_advanced_payload()))
        + chunks(v1_frame(MSPCodes.MSP_SET_PID_ADVANCED))
    )
    assert bf.set_pid_advanced({"simplified_pids_mode": "OFF", "f_roll": 70}) is True
    sent = [written(conn, i)[0] for i in range(conn.write.call_count)]
    assert sent == [MSPCodes.MSP_SIMPLIFIED_TUNING, MSPCodes.MSP_SET_SIMPLIFIED_TUNING,
                    MSPCodes.MSP_PID_ADVANCED, MSPCodes.MSP_SET_PID_ADVANCED]


def test_set_pid_advanced_false_when_fc_rejects():
    bf, conn = make_commands()
    conn.read.side_effect = (
        chunks(v1_frame(MSPCodes.MSP_PID_ADVANCED, pid_advanced_payload()))
        + chunks(v1_frame(MSPCodes.MSP_SET_PID_ADVANCED, direction=b'!'))
    )
    assert bf.set_pid_advanced({"f_roll": 70}) is False


# ── Tool ──────────────────────────────────────────────────────────────

@pytest.fixture
def tools_with_bf():
    from server import tools as _tools
    bf, conn   = make_commands()
    original   = _tools._bf
    _tools._bf = bf
    yield _tools, conn
    _tools._bf = original


def test_tool_set_pid_advanced_validation_error_sends_nothing(tools_with_bf):
    tools, conn = tools_with_bf
    result = tools.tool_set_pid_advanced({"f_roll": 5000})
    assert result["success"] is False and result["errors"]
    assert conn.write.call_count == 0


def test_tool_set_pid_advanced_returns_read_back(tools_with_bf):
    tools, conn = tools_with_bf
    conn.read.side_effect = (
        chunks(v1_frame(MSPCodes.MSP_PID_ADVANCED, pid_advanced_payload()))
        + chunks(v1_frame(MSPCodes.MSP_SET_PID_ADVANCED))
        + chunks(v1_frame(MSPCodes.MSP_PID_ADVANCED, pid_advanced_payload(f_roll=70)))
        + chunks(v1_frame(MSPCodes.MSP_SIMPLIFIED_TUNING, simplified_payload()))
    )
    result = tools.tool_set_pid_advanced({"f_roll": 70})
    assert result["success"] is True
    assert result["updated"] == {"f_roll": 70}
    assert result["pid_advanced"]["f_roll"] == 70


def test_tool_set_pid_advanced_schema_lists_allowed_names():
    import asyncio, main
    tool   = next(t for t in asyncio.run(main.app.list_tools()) if t.name == "set_pid_advanced")
    schema = tool.inputSchema["properties"]["settings"]
    assert set(schema["propertyNames"]["enum"]) == set(pa.FIELDS) | {"simplified_pids_mode"}
    assert tool.annotations.destructiveHint is True
