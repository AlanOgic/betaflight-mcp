"""
Tests du modèle de rates : conversions par rates_type (unités du configurateur),
limites firmware (controlrate_profile.c), vitesse max plein manche (fc/rc.c).
"""

import struct
import pytest
from unittest.mock import MagicMock

from betaflight import rates
from betaflight.rates import RatesType
from betaflight.msp import MSPProtocol
from betaflight.msp_codes import MSPCodes
from betaflight.commands import BetaflightCommands
from server.validators import validate_rates


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


def rc_tuning(rates_type: int = RatesType.ACTUAL, rc=(7, 7, 7), rate=(67, 67, 67),
              expo=(0, 0, 0), limits=(1998, 1998, 1998), hover: int | None = 50) -> bytes:
    """Payload MSP_RC_TUNING tel qu'encodé par msp.c (API 1.47). Défauts = Actual 4.5."""
    payload = bytes([rc[0], expo[0], rate[0], rate[1], rate[2], 0, 50, 0]) \
        + struct.pack("<H", 0) \
        + bytes([expo[2], rc[2], rc[1], expo[1], 0, 100]) \
        + struct.pack("<HHH", *limits) \
        + bytes([rates_type])
    return payload + (bytes([hover]) if hover is not None else b'')


def written_payload(conn, index: int = 1) -> bytes:
    frame = conn.write.call_args_list[index][0][0]
    return frame[5:5 + frame[3]]


# ── Conversions ───────────────────────────────────────────────────────

@pytest.mark.parametrize("rates_type, field, raw, display", [
    (RatesType.BETAFLIGHT, "rc_rate", 100, 1.0),
    (RatesType.BETAFLIGHT, "rate",     70, 0.7),
    (RatesType.BETAFLIGHT, "expo",     15, 0.15),
    (RatesType.RACEFLIGHT, "rc_rate",  37, 370),
    (RatesType.RACEFLIGHT, "rate",     80, 80),
    (RatesType.RACEFLIGHT, "expo",     50, 50),
    (RatesType.KISS,       "rc_rate", 100, 1.0),
    (RatesType.KISS,       "rate",     70, 0.7),
    (RatesType.ACTUAL,     "rc_rate",   7, 70),
    (RatesType.ACTUAL,     "rate",     67, 670),
    (RatesType.ACTUAL,     "expo",     54, 0.54),
    (RatesType.QUICK,      "rc_rate", 100, 1.0),
    (RatesType.QUICK,      "rate",     67, 670),
])
def test_display_conversion_matches_configurator(rates_type, field, raw, display):
    assert rates.to_display(rates_type, field, raw) == pytest.approx(display)
    assert rates.to_raw(rates_type, field, display) == raw


def test_to_raw_rounds_to_firmware_resolution():
    assert rates.to_raw(RatesType.ACTUAL, "rate", 675) == 68   # pas de 10 °/s


@pytest.mark.parametrize("rates_type, field, limit", [
    (RatesType.BETAFLIGHT, "rc_rate", 255),
    (RatesType.BETAFLIGHT, "rate",    100),
    (RatesType.RACEFLIGHT, "rc_rate", 200),
    (RatesType.RACEFLIGHT, "rate",    255),
    (RatesType.KISS,       "rate",     99),
    (RatesType.ACTUAL,     "rc_rate", 200),
    (RatesType.ACTUAL,     "rate",    200),
    (RatesType.QUICK,      "rate",    200),
    (RatesType.ACTUAL,     "expo",    100),
])
def test_raw_limits_mirror_firmware(rates_type, field, limit):
    assert rates.raw_limit(rates_type, field) == limit


def test_labels_follow_rates_type():
    assert rates.labels(RatesType.ACTUAL)["rc_rate"].startswith("Center Sensitivity")
    assert rates.labels(RatesType.ACTUAL)["rate"].startswith("Max Rate")
    assert rates.labels(RatesType.BETAFLIGHT)["rc_rate"].startswith("RC Rate")
    assert rates.labels(RatesType.RACEFLIGHT)["rate"].startswith("Acro+")
    assert rates.labels(RatesType.KISS)["expo"].startswith("RC Curve")


def test_unknown_rates_type_is_unsupported():
    assert not rates.is_supported(9)
    assert rates.type_name(9) == "UNKNOWN_9"


# ── Vitesse max plein manche ──────────────────────────────────────────

@pytest.mark.parametrize("rates_type, rc, rate, expo, expected", [
    (RatesType.ACTUAL,     7,   67,  0,  670),
    (RatesType.ACTUAL,     20,  10,  0,  200),    # max < centre → centre
    (RatesType.BETAFLIGHT, 100, 70,  0,  667),
    (RatesType.BETAFLIGHT, 250, 0,   0,  1954),   # RC_RATE_INCREMENTAL au-delà de 2.0
    (RatesType.RACEFLIGHT, 37,  80,  50, 666),
    (RatesType.KISS,       100, 70,  0,  667),
    (RatesType.QUICK,      100, 67,  0,  670),
    (RatesType.QUICK,      0,   67,  0,  0),      # rc_rate nul : pas de division par zéro
])
def test_max_rate_dps_matches_firmware_formulas(rates_type, rc, rate, expo, expected):
    assert rates.max_rate_dps(rates_type, rc, rate, expo, 1998) == expected


def test_max_rate_dps_clamped_by_rate_limit():
    assert rates.max_rate_dps(RatesType.ACTUAL, 20, 200, 0, 1998) == 1998
    assert rates.max_rate_dps(RatesType.ACTUAL, 7, 67, 0, 500) == 500


# ── get_rates ─────────────────────────────────────────────────────────

def test_get_rates_actual_defaults():
    bf, conn = make_commands()
    conn.read.side_effect = chunks(v1_frame(MSPCodes.MSP_RC_TUNING, rc_tuning()))
    result = bf.get_rates()
    assert result["rates_type"]    == "ACTUAL"
    assert result["rates_type_id"] == RatesType.ACTUAL
    assert result["roll"] == {"rc_rate": 70, "rate": 670, "expo": 0.0,
                              "rate_limit_dps": 1998, "max_rate_dps": 670}
    assert result["pitch"]["rc_rate"] == 70
    assert result["yaw"]["rate"]      == 670
    assert result["throttle_mid"]     == 0.5
    assert result["throttle_hover"]   == 0.5
    assert "Center Sensitivity" in result["labels"]["rc_rate"]


def test_get_rates_reads_pitch_and_yaw_from_their_own_bytes():
    bf, conn = make_commands()
    payload = rc_tuning(RatesType.BETAFLIGHT, rc=(100, 110, 120), rate=(70, 71, 72), expo=(10, 11, 12))
    conn.read.side_effect = chunks(v1_frame(MSPCodes.MSP_RC_TUNING, payload))
    result = bf.get_rates()
    assert [result[a]["rc_rate"] for a in ("roll", "pitch", "yaw")] == [1.0, 1.1, 1.2]
    assert [result[a]["rate"]    for a in ("roll", "pitch", "yaw")] == [0.7, 0.71, 0.72]
    assert [result[a]["expo"]    for a in ("roll", "pitch", "yaw")] == [0.1, 0.11, 0.12]


def test_get_rates_without_hover_field():
    bf, conn = make_commands()
    conn.read.side_effect = chunks(v1_frame(MSPCodes.MSP_RC_TUNING, rc_tuning(hover=None)))
    assert "throttle_hover" not in bf.get_rates()


# ── set_rates ─────────────────────────────────────────────────────────

def feed_rc_tuning_then_ack(conn, payload: bytes):
    conn.read.side_effect = (
        chunks(v1_frame(MSPCodes.MSP_RC_TUNING, payload))
        + chunks(v1_frame(MSPCodes.MSP_SET_RC_TUNING))
    )


def test_set_rates_patches_only_requested_bytes():
    bf, conn = make_commands()
    original = rc_tuning()
    feed_rc_tuning_then_ack(conn, original)
    assert bf.set_rates({"roll_rate": 800}, expected_rates_type=RatesType.ACTUAL) is True
    written = written_payload(conn)
    assert len(written) == len(original)
    assert written[2] == 80                        # roll max rate 800 °/s
    assert written[:2] + written[3:] == original[:2] + original[3:]


def test_set_rates_roll_rc_rate_does_not_touch_pitch():
    bf, conn = make_commands()
    feed_rc_tuning_then_ack(conn, rc_tuning())
    assert bf.set_rates({"roll_rc_rate": 100}, expected_rates_type=RatesType.ACTUAL) is True
    written = written_payload(conn)
    assert written[0]  == 10   # roll center sensitivity 100 °/s
    assert written[12] == 7    # pitch conserve sa valeur (octet explicite)


def test_set_rates_pitch_and_yaw_fields_hit_their_offsets():
    bf, conn = make_commands()
    feed_rc_tuning_then_ack(conn, rc_tuning())
    updates = {"pitch_rc_rate": 80, "yaw_rc_rate": 90, "pitch_expo": 0.3, "yaw_expo": 0.2,
               "pitch_rate": 700, "yaw_rate": 500, "throttle_mid": 0.4, "throttle_expo": 0.1}
    assert bf.set_rates(updates, expected_rates_type=RatesType.ACTUAL) is True
    w = written_payload(conn)
    assert (w[12], w[11], w[13], w[10], w[3], w[4], w[6], w[7]) == (8, 9, 30, 20, 70, 50, 40, 10)


def test_set_rates_refuses_when_rates_type_changed():
    bf, conn = make_commands()
    conn.read.side_effect = chunks(v1_frame(MSPCodes.MSP_RC_TUNING, rc_tuning(RatesType.BETAFLIGHT)))
    assert bf.set_rates({"roll_rate": 800}, expected_rates_type=RatesType.ACTUAL) is False
    assert conn.write.call_count == 1   # lecture seule, aucune écriture


def test_set_rates_refuses_unknown_field():
    bf, conn = make_commands()
    feed_rc_tuning_then_ack(conn, rc_tuning())
    assert bf.set_rates({"rc_rate": 1.0}, expected_rates_type=RatesType.ACTUAL) is False


def test_set_rates_refuses_unsupported_rates_type():
    bf, conn = make_commands()
    conn.read.side_effect = chunks(v1_frame(MSPCodes.MSP_RC_TUNING, rc_tuning(rates_type=9)))
    assert bf.set_rates({"roll_rate": 800}, expected_rates_type=9) is False
    assert conn.write.call_count == 1


def test_set_rates_refuses_field_absent_from_short_payload():
    """Firmware ancien : l'octet n'existe pas, on ne l'invente pas."""
    bf, conn = make_commands()
    conn.read.side_effect = chunks(v1_frame(MSPCodes.MSP_RC_TUNING, rc_tuning()[:10]))
    assert bf.set_rates({"yaw_expo": 0.2}, expected_rates_type=RatesType.BETAFLIGHT) is False
    assert conn.write.call_count == 1


# ── Validation ────────────────────────────────────────────────────────

def _view(rates_type=RatesType.ACTUAL, **overrides):
    bf, conn = make_commands()
    conn.read.side_effect = chunks(v1_frame(MSPCodes.MSP_RC_TUNING, rc_tuning(rates_type, **overrides)))
    return bf.get_rates()


def test_validate_rates_accepts_normal_actual_change():
    v = validate_rates(_view(), {"roll_rate": 800, "roll_expo": 0.5})
    assert v["errors"] == [] and v["warnings"] == []


def test_validate_rates_accepts_boundaries():
    v = validate_rates(_view(), {"roll_expo": 1.0, "pitch_expo": 0.0, "throttle_mid": 1.0})
    assert v["errors"] == []


def test_validate_rates_rejects_value_above_type_limit():
    v = validate_rates(_view(), {"roll_rate": 2100})      # Actual : max 200 brut = 2000 °/s
    assert v["errors"]


def test_validate_rates_rejects_negative_and_unknown():
    v = validate_rates(_view(), {"roll_rate": -1, "rc_rate": 1.0})
    assert len(v["errors"]) == 2


def test_validate_rates_rejects_betaflight_super_rate_above_one():
    view = _view(RatesType.BETAFLIGHT, rc=(100, 100, 100), rate=(70, 70, 70))
    assert validate_rates(view, {"roll_rate": 1.2})["errors"]


def test_validate_rates_warns_above_configurator_threshold():
    v = validate_rates(_view(), {"roll_rate": 1900})
    assert v["errors"] == []
    assert any("roll" in w and "1900" in w for w in v["warnings"])


def test_validate_rates_warning_uses_merged_values():
    """Changer rc_rate en BF rates doit être évalué avec le super rate courant."""
    view = _view(RatesType.BETAFLIGHT, rc=(100, 100, 100), rate=(80, 80, 80))
    assert not validate_rates(view, {"pitch_rc_rate": 1.5})["warnings"]  # 200*1.5/0.2 = 1500 °/s
    assert validate_rates(view, {"pitch_rc_rate": 2.0})["warnings"]      # 2000 → 1998 (rate_limit)


@pytest.mark.parametrize("value", [1.0, 2.0, 4.9])
def test_validate_rates_rejects_betaflight_units_on_actual_quad(value):
    """rc_rate 1.0 en unités BF sur un quad Actual = 0 °/s de sensibilité centrale."""
    v = validate_rates(_view(), {"roll_rc_rate": value})
    assert v["errors"]
    assert "résolution" in v["errors"][0]


def test_validate_rates_zero_is_allowed_when_explicit():
    assert validate_rates(_view(), {"roll_expo": 0.0})["errors"] == []


def test_validate_rates_warns_on_quantization():
    v = validate_rates(_view(), {"roll_rate": 675})
    assert v["errors"] == []
    assert any("675" in w and "680" in w for w in v["warnings"])


def test_validate_rates_rejects_throttle_out_of_range():
    assert validate_rates(_view(), {"throttle_mid": 1.2})["errors"]


def test_validate_rates_rejects_unsupported_type():
    assert validate_rates(_view(rates_type=9), {"roll_rate": 800})["errors"]


# ── Tool ──────────────────────────────────────────────────────────────

@pytest.fixture
def tools_with_bf():
    from server import tools as _tools
    bf, conn   = make_commands()
    original   = _tools._bf
    _tools._bf = bf
    yield _tools, conn
    _tools._bf = original


def test_tool_set_rates_reads_back_applied_values(tools_with_bf):
    tools, conn = tools_with_bf
    after = rc_tuning(rate=(80, 67, 67))
    conn.read.side_effect = (
        chunks(v1_frame(MSPCodes.MSP_RC_TUNING, rc_tuning()))       # get_rates (validation)
        + chunks(v1_frame(MSPCodes.MSP_RC_TUNING, rc_tuning()))     # set_rates (read-modify-write)
        + chunks(v1_frame(MSPCodes.MSP_SET_RC_TUNING))              # ack
        + chunks(v1_frame(MSPCodes.MSP_RC_TUNING, after))           # relecture
    )
    result = tools.tool_set_rates(roll_rate=800)
    assert result["success"] is True
    assert result["rates_type"] == "ACTUAL"
    assert result["rates"]["roll"]["rate"] == 800
    assert result["rates"]["roll"]["max_rate_dps"] == 800


def test_tool_set_rates_validation_error_sends_nothing(tools_with_bf):
    tools, conn = tools_with_bf
    conn.read.side_effect = chunks(v1_frame(MSPCodes.MSP_RC_TUNING, rc_tuning()))
    result = tools.tool_set_rates(roll_rate=5000)
    assert result["success"] is False
    assert result["errors"]
    assert conn.write.call_count == 1


def test_tool_set_rates_requires_a_parameter(tools_with_bf):
    tools, conn = tools_with_bf
    result = tools.tool_set_rates()
    assert result["success"] is False
    assert conn.write.call_count == 0


def test_tool_set_rates_exposes_every_axis_parameter():
    import inspect
    from server.tools import MCP_TOOLS, tool_set_rates
    expected = {f"{axis}_{field}" for axis in rates.AXES for field in rates.RATE_FIELDS}
    expected |= {"throttle_mid", "throttle_expo"}
    assert set(inspect.signature(tool_set_rates).parameters) == expected
    assert set(MCP_TOOLS["set_rates"]["parameters"]) == expected
