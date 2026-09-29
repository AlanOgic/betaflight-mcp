"""
Tests MSP_BATTERY_CONFIG (lecture + écriture).
Layout msp.c identique en 2025.12.5 et 2026.6.2 ; tensions cellule en u16 (0.01 V, octets 7-12)
avec copie u8 historique en 0.1 V (octets 0-2, arrondi (v+5)/10). Le firmware refuse
l'écriture si min > warning ou warning > max. Sources de mesure : prises en compte au reboot.
"""

import struct
import pytest
from unittest.mock import MagicMock

from betaflight import battery_config as bc
from betaflight.msp import MSPProtocol
from betaflight.msp_codes import MSPCodes
from betaflight.commands import BetaflightCommands
from server.validators import validate_battery_config

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


def chunks(frame: bytes) -> list[bytes]:
    return [frame[:3], frame[3:5], frame[5:]]


def battery_payload(vmin=330, vmax=430, vwarn=350, capacity=0, voltage_meter=1, current_meter=1) -> bytes:
    legacy = [(v + 5) // 10 for v in (vmin, vmax, vwarn)]
    return (bytes(legacy) + struct.pack("<H", capacity) + bytes([voltage_meter, current_meter])
            + struct.pack("<HHH", vmin, vmax, vwarn))


def written(conn, index: int) -> tuple[int, bytes]:
    frame = conn.write.call_args_list[index][0][0]
    return frame[4], frame[5:5 + frame[3]]


# ── Table / lecture ───────────────────────────────────────────────────

def test_field_table_matches_msp_layout():
    expected = {"vbat_min_cell_voltage": (7, 2), "vbat_max_cell_voltage": (9, 2),
                "vbat_warning_cell_voltage": (11, 2), "bat_capacity": (3, 2),
                "battery_meter": (5, 1), "current_meter": (6, 1)}
    assert {n: (f.offset, f.size) for n, f in bc.FIELDS.items()} == expected


def test_parse_battery_config():
    result = bc.parse(battery_payload(vmin=320, vmax=440, vwarn=330, current_meter=1))
    assert result == {"vbat_min_cell_voltage": 320, "vbat_max_cell_voltage": 440,
                      "vbat_warning_cell_voltage": 330, "bat_capacity": 0,
                      "battery_meter": "ADC", "current_meter": "ADC"}


def test_get_battery_config():
    bf, conn = make_commands()
    conn.read.side_effect = chunks(v1_frame(MSPCodes.MSP_BATTERY_CONFIG, battery_payload()))
    assert bf.get_battery_config()["current_meter"] == "ADC"


# ── Validation ────────────────────────────────────────────────────────

CURRENT = bc.parse(battery_payload())


def test_validate_accepts_dump_values():
    updates = {"current_meter": "NONE", "vbat_max_cell_voltage": 440,
               "vbat_min_cell_voltage": 320, "vbat_warning_cell_voltage": 330}
    assert validate_battery_config(CURRENT, updates, API_2026)["errors"] == []


@pytest.mark.parametrize("updates, fragment", [
    ({"current_meter": "SENSOR"},          "VIRTUAL"),
    ({"battery_meter": "VIRTUAL"},         "ESC"),
    ({"vbat_max_cell_voltage": 501},       "500"),
    ({"bat_capacity": 20001},              "20000"),
    ({"vbat_warning_cell_voltage": 300},   "min"),        # 300 < min actuel 330
    ({"vbat_min_cell_voltage": 360},       "min"),        # 360 > warning actuel 350
    ({"vbat_max_cell_voltage": 340},       "max"),        # 340 < warning actuel 350
])
def test_validate_rejects(updates, fragment):
    errors = validate_battery_config(CURRENT, updates, API_2026)["errors"]
    assert errors and fragment in errors[0]


def test_validate_meter_change_warns_reboot():
    warnings = validate_battery_config(CURRENT, {"current_meter": "NONE"}, API_2026)["warnings"]
    assert warnings and "reboot" in warnings[0]


def test_validate_refuses_before_api_1_47():
    assert "1.47" in validate_battery_config(CURRENT, {"current_meter": "NONE"}, (1, 46))["errors"][0]


# ── Écriture ──────────────────────────────────────────────────────────

def test_set_current_meter_none_patches_one_byte():
    bf, conn = make_commands()
    original = battery_payload()
    conn.read.side_effect = (chunks(v1_frame(MSPCodes.MSP_BATTERY_CONFIG, original))
                             + chunks(v1_frame(MSPCodes.MSP_SET_BATTERY_CONFIG)))
    assert bf.set_battery_config({"current_meter": "NONE"}) is True
    cmd, payload = written(conn, 1)
    assert cmd == MSPCodes.MSP_SET_BATTERY_CONFIG
    assert payload == original[:6] + bytes([0]) + original[7:]


def test_set_cell_voltage_keeps_legacy_byte_in_0_1_volt():
    bf, conn = make_commands()
    conn.read.side_effect = (chunks(v1_frame(MSPCodes.MSP_BATTERY_CONFIG, battery_payload()))
                             + chunks(v1_frame(MSPCodes.MSP_SET_BATTERY_CONFIG)))
    assert bf.set_battery_config({"vbat_max_cell_voltage": 445}) is True
    _, payload = written(conn, 1)
    assert struct.unpack_from("<H", payload, 9)[0] == 445
    assert payload[1] == 45                                   # (445 + 5) // 10


def test_set_battery_config_false_when_fc_rejects():
    bf, conn = make_commands()
    conn.read.side_effect = (chunks(v1_frame(MSPCodes.MSP_BATTERY_CONFIG, battery_payload()))
                             + chunks(v1_frame(MSPCodes.MSP_SET_BATTERY_CONFIG, direction=b'!')))
    assert bf.set_battery_config({"current_meter": "NONE"}) is False


def test_set_battery_config_refuses_before_api_1_47():
    bf, conn = make_commands(api=(1, 46))
    assert bf.set_battery_config({"current_meter": "NONE"}) is False
    assert conn.write.call_count == 0


# ── Tool ──────────────────────────────────────────────────────────────

@pytest.fixture
def tools_with_bf():
    from server import tools as _tools
    bf, conn   = make_commands()
    original   = _tools._bf
    _tools._bf = bf
    yield _tools, conn
    _tools._bf = original


def test_tool_set_battery_config_reports_reboot_required(tools_with_bf):
    tools, conn = tools_with_bf
    after = battery_payload(current_meter=0)
    conn.read.side_effect = (chunks(v1_frame(MSPCodes.MSP_BATTERY_CONFIG, battery_payload()))   # validation
                             + chunks(v1_frame(MSPCodes.MSP_BATTERY_CONFIG, battery_payload())) # read-modify-write
                             + chunks(v1_frame(MSPCodes.MSP_SET_BATTERY_CONFIG))
                             + chunks(v1_frame(MSPCodes.MSP_BATTERY_CONFIG, after)))            # relecture
    result = tools.tool_set_battery_config({"current_meter": "NONE"})
    assert result["success"] is True
    assert result["battery_config"]["current_meter"] == "NONE"
    assert result["reboot_required"] is True


def test_tool_set_battery_config_order_error_sends_nothing(tools_with_bf):
    tools, conn = tools_with_bf
    conn.read.side_effect = chunks(v1_frame(MSPCodes.MSP_BATTERY_CONFIG, battery_payload()))
    result = tools.tool_set_battery_config({"vbat_warning_cell_voltage": 300})
    assert result["success"] is False and result["errors"]
    assert conn.write.call_count == 1                         # lecture de validation seulement
