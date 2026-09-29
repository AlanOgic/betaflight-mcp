"""
Tests MSP_FILTER_CONFIG (lecture + écriture).
Layout vérifié : msp.c 2025.12.5 / 2026.6.2 (1.48 ajoute rpm fade/q/weights en fin de trame).
Plages : cli/settings.c, identiques en 2025.12.5 et 2026.6.2.
"""

import struct
import pytest
from unittest.mock import MagicMock

from betaflight import filter_config as fc
from betaflight.msp import MSPProtocol
from betaflight.msp_codes import MSPCodes
from betaflight.commands import BetaflightCommands
from server.validators import validate_filter_config

API_2026 = (1, 48)


# ── Helpers ───────────────────────────────────────────────────────────

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


def filter_payload(length: int = 56, gyro_lpf1: int = 250, **u16_overrides) -> bytes:
    """MSP_FILTER_CONFIG 2026.6 (56 octets), valeurs par défaut Betaflight réalistes."""
    raw = bytearray(56)
    raw[0] = gyro_lpf1 & 0xFF                                # u8 historique
    struct.pack_into("<HHHHHHHH", raw, 1, 75, 0, 0, 0, 0, 0, 0, 0)
    raw[17] = 0                                              # dterm_lpf1_type PT1
    raw[18] = 0                                              # gyro_hardware_lpf NORMAL
    struct.pack_into("<HH", raw, 20, gyro_lpf1, 500)         # gyro lpf1 / lpf2 statiques
    raw[24], raw[25] = 0, 0                                  # types gyro lpf1/lpf2
    struct.pack_into("<H", raw, 26, 150)                     # dterm_lpf2_static_hz
    raw[28] = 0
    struct.pack_into("<HHHH", raw, 29, 250, 500, 75, 150)    # dyn lpf gyro / dterm
    struct.pack_into("<HH", raw, 39, 500, 150)               # dyn_notch_q, dyn_notch_min_hz
    raw[43], raw[44] = 3, 100                                # rpm harmonics / min hz
    struct.pack_into("<H", raw, 45, 600)                     # dyn_notch_max_hz
    raw[47], raw[48] = 5, 3                                  # dterm dyn expo, dyn notch count
    struct.pack_into("<HH", raw, 49, 50, 500)                # rpm fade, rpm q
    raw[53:56] = bytes([100, 100, 100])                      # rpm weights
    for offset, value in u16_overrides.items():
        struct.pack_into("<H", raw, int(offset[1:]), value)
    return bytes(raw[:length])


def written(conn, index: int) -> tuple[int, bytes]:
    frame = conn.write.call_args_list[index][0][0]
    return frame[4], frame[5:5 + frame[3]]


# ── Table ─────────────────────────────────────────────────────────────

def test_field_table_matches_msp_layout():
    expected = {
        "gyro_lpf1_static_hz": (20, 2), "dterm_lpf1_static_hz": (1, 2), "yaw_lowpass_hz": (3, 2),
        "gyro_notch1_hz": (5, 2), "gyro_notch1_cutoff": (7, 2), "dterm_notch_hz": (9, 2),
        "dterm_notch_cutoff": (11, 2), "gyro_notch2_hz": (13, 2), "gyro_notch2_cutoff": (15, 2),
        "dterm_lpf1_type": (17, 1), "gyro_hardware_lpf": (18, 1), "gyro_lpf2_static_hz": (22, 2),
        "gyro_lpf1_type": (24, 1), "gyro_lpf2_type": (25, 1), "dterm_lpf2_static_hz": (26, 2),
        "dterm_lpf2_type": (28, 1), "gyro_lpf1_dyn_min_hz": (29, 2), "gyro_lpf1_dyn_max_hz": (31, 2),
        "dterm_lpf1_dyn_min_hz": (33, 2), "dterm_lpf1_dyn_max_hz": (35, 2), "dyn_notch_q": (39, 2),
        "dyn_notch_min_hz": (41, 2), "rpm_filter_harmonics": (43, 1), "rpm_filter_min_hz": (44, 1),
        "dyn_notch_max_hz": (45, 2), "dterm_lpf1_dyn_expo": (47, 1), "dyn_notch_count": (48, 1),
        "rpm_filter_fade_range_hz": (49, 2), "rpm_filter_q": (51, 2), "rpm_filter_weights": (53, 1),
    }
    assert {name: (f.offset, f.size) for name, f in fc.FIELDS.items()} == expected
    assert fc.FIELDS["rpm_filter_weights"].count == 3


# ── Lecture ───────────────────────────────────────────────────────────

def test_parse_reads_fields_after_deprecated_bytes_at_right_offsets():
    """Régression : l'ancien parseur décalait tout d'un octet après l'octet 36."""
    result = fc.parse(filter_payload())
    assert result["dyn_notch_q"]          == 500
    assert result["dyn_notch_min_hz"]     == 150
    assert result["rpm_filter_harmonics"] == 3
    assert result["rpm_filter_min_hz"]    == 100
    assert result["dyn_notch_max_hz"]     == 600
    assert result["dyn_notch_count"]      == 3
    assert result["rpm_filter_q"]         == 500
    assert result["rpm_filter_weights"]   == "100,100,100"


def test_parse_uses_u16_gyro_lpf1_not_truncated_u8():
    result = fc.parse(filter_payload(gyro_lpf1=300))
    assert result["gyro_lpf1_static_hz"] == 300


def test_parse_enum_labels():
    raw = bytearray(filter_payload())
    raw[17], raw[18], raw[24] = 2, 1, 1
    result = fc.parse(bytes(raw))
    assert result["dterm_lpf1_type"]   == "PT2"
    assert result["gyro_hardware_lpf"] == "OPTION_1"
    assert result["gyro_lpf1_type"]    == "BIQUAD"


def test_parse_short_payload_before_api_1_48():
    result = fc.parse(filter_payload(length=49))
    assert "dyn_notch_count" in result
    assert "rpm_filter_q" not in result and "rpm_filter_weights" not in result


def test_get_filter_config_uses_table():
    bf, conn = make_commands()
    conn.read.side_effect = chunks(v1_frame(MSPCodes.MSP_FILTER_CONFIG, filter_payload()))
    result = bf.get_filter_config()
    assert result["dyn_notch_q"] == 500
    assert result["yaw_lowpass_hz"] == 0


# ── Validation ────────────────────────────────────────────────────────

def test_validate_accepts_valid_settings():
    updates = {"yaw_lowpass_hz": 0, "gyro_lpf1_static_hz": 300, "dterm_lpf1_type": "PT1",
               "rpm_filter_weights": "100,80,50", "dyn_notch_count": 1}
    assert validate_filter_config(updates, API_2026) == {"errors": [], "warnings": []}


@pytest.mark.parametrize("updates, fragment", [
    ({"gyro_lowpass_hz": 100},             "inconnu"),
    ({"yaw_lowpass_hz": 501},              "500"),
    ({"rpm_filter_q": 100},                "250"),
    ({"dyn_notch_count": 8},               "7"),
    ({"dterm_lpf1_type": "PT4"},           "PT3"),
    ({"rpm_filter_weights": "100,80"},     "3"),
    ({"rpm_filter_weights": "100,80,101"}, "100"),
    ({"rpm_filter_weights": 100},          "a,b,c"),
])
def test_validate_rejects(updates, fragment):
    errors = validate_filter_config(updates, API_2026)["errors"]
    assert errors and fragment in errors[0]


def test_validate_refuses_writes_before_api_1_47():
    errors = validate_filter_config({"yaw_lowpass_hz": 0}, (1, 46))["errors"]
    assert errors and "1.47" in errors[0]


# ── Écriture ──────────────────────────────────────────────────────────

def test_set_filter_config_patches_only_requested_bytes():
    bf, conn = make_commands()
    original = filter_payload()
    conn.read.side_effect = (
        chunks(v1_frame(MSPCodes.MSP_FILTER_CONFIG, original))
        + chunks(v1_frame(MSPCodes.MSP_SET_FILTER_CONFIG))
    )
    updates = {"yaw_lowpass_hz": 100, "dterm_lpf1_type": "PT2", "rpm_filter_weights": "100,80,50"}
    assert bf.set_filter_config(updates) is True
    cmd, payload = written(conn, 1)
    assert cmd == MSPCodes.MSP_SET_FILTER_CONFIG
    assert len(payload) == len(original)
    assert struct.unpack_from("<H", payload, 3)[0] == 100
    assert payload[17] == 2
    assert payload[53:56] == bytes([100, 80, 50])
    changed = {i for i in range(len(original)) if payload[i] != original[i]}
    assert changed <= {3, 4, 17, 54, 55}


def test_set_gyro_lpf1_keeps_legacy_byte_consistent():
    bf, conn = make_commands()
    conn.read.side_effect = (
        chunks(v1_frame(MSPCodes.MSP_FILTER_CONFIG, filter_payload()))
        + chunks(v1_frame(MSPCodes.MSP_SET_FILTER_CONFIG))
    )
    assert bf.set_filter_config({"gyro_lpf1_static_hz": 300}) is True
    _, payload = written(conn, 1)
    assert struct.unpack_from("<H", payload, 20)[0] == 300
    assert payload[0] == 300 & 0xFF


def test_set_filter_config_refuses_field_absent_from_payload():
    bf, conn = make_commands(api=(1, 47))
    conn.read.side_effect = chunks(v1_frame(MSPCodes.MSP_FILTER_CONFIG, filter_payload(length=49)))
    assert bf.set_filter_config({"rpm_filter_q": 500}) is False
    assert conn.write.call_count == 1


def test_set_filter_config_refuses_before_api_1_47():
    bf, conn = make_commands(api=(1, 46))
    assert bf.set_filter_config({"yaw_lowpass_hz": 0}) is False
    assert conn.write.call_count == 0


def test_set_filter_config_false_when_fc_rejects():
    bf, conn = make_commands()
    conn.read.side_effect = (
        chunks(v1_frame(MSPCodes.MSP_FILTER_CONFIG, filter_payload()))
        + chunks(v1_frame(MSPCodes.MSP_SET_FILTER_CONFIG, direction=b'!'))
    )
    assert bf.set_filter_config({"yaw_lowpass_hz": 0}) is False


# ── Tool ──────────────────────────────────────────────────────────────

@pytest.fixture
def tools_with_bf():
    from server import tools as _tools
    bf, conn   = make_commands()
    original   = _tools._bf
    _tools._bf = bf
    yield _tools, conn
    _tools._bf = original


def test_tool_set_filter_config_validation_error_sends_nothing(tools_with_bf):
    tools, conn = tools_with_bf
    result = tools.tool_set_filter_config({"yaw_lowpass_hz": 9000})
    assert result["success"] is False and result["errors"]
    assert conn.write.call_count == 0


def test_tool_set_filter_config_returns_read_back(tools_with_bf):
    tools, conn = tools_with_bf
    after = bytearray(filter_payload())
    struct.pack_into("<H", after, 3, 100)
    conn.read.side_effect = (
        chunks(v1_frame(MSPCodes.MSP_FILTER_CONFIG, filter_payload()))
        + chunks(v1_frame(MSPCodes.MSP_SET_FILTER_CONFIG))
        + chunks(v1_frame(MSPCodes.MSP_FILTER_CONFIG, bytes(after)))
    )
    result = tools.tool_set_filter_config({"yaw_lowpass_hz": 100})
    assert result["success"] is True
    assert result["filter_config"]["yaw_lowpass_hz"] == 100


def test_tool_set_filter_config_schema_lists_allowed_names():
    import asyncio, main
    tool   = next(t for t in asyncio.run(main.app.list_tools()) if t.name == "set_filter_config")
    schema = tool.inputSchema["properties"]["settings"]
    assert set(schema["propertyNames"]["enum"]) == set(fc.FIELDS)
    assert tool.annotations.destructiveHint is True
