"""Tests unitaires pour BetaflightCommands.measure_rc_noise."""

import pytest
from unittest.mock import patch, MagicMock
from betaflight.commands import BetaflightCommands, RC_CHANNEL_NAMES
from betaflight.msp import MSPProtocol


def make_bf():
    conn = MagicMock()
    conn.is_connected = True
    return BetaflightCommands(MSPProtocol(conn))


def _run(bf, rc_samples, duration_s=3.0, channels=None):
    """Lance measure_rc_noise avec time mocké pour éviter les vrais sleeps."""
    # time.monotonic : deadline=0+3=3, puis N valeurs < 3, puis une > 3
    n = len(rc_samples)
    mono_values = [0.0] + [i * (duration_s / (n + 1)) for i in range(1, n + 1)] + [duration_s + 0.1]
    with patch.object(bf, "get_rc", side_effect=rc_samples), \
         patch("betaflight.commands.time.monotonic", side_effect=mono_values), \
         patch("betaflight.commands.time.sleep"):
        return bf.measure_rc_noise(duration_s=duration_s, channels=channels)


# ── Tests de base ─────────────────────────────────────────────────────────────

def test_sample_count():
    samples = [{"channels": [1500, 1500], "count": 2}] * 10
    result = _run(make_bf(), samples)
    assert result["sample_count"] == 10


def test_stable_channel_low_deadband():
    """Canal stable → bruit = 0 → deadband suggéré = 5 (marge minimale = p95+5)."""
    samples = [{"channels": [1500, 1500, 1500, 1000], "count": 4}] * 20
    result = _run(make_bf(), samples)
    roll = result["channels"]["roll"]
    assert roll["noise_p95_us"] == 0
    assert roll["suggested_deadband"] == 5

def test_stable_channel_suggested():
    """Canal stable : p95=0 → suggested = max(1, 0+5) = 5."""
    samples = [{"channels": [1500], "count": 1}] * 20
    result = _run(make_bf(), samples, channels=[0])
    assert result["channels"]["roll"]["suggested_deadband"] == 5


def test_noisy_channel():
    """Canal avec ±20 µs de bruit → suggested_deadband ≥ 20."""
    import random
    random.seed(42)
    samples = [{"channels": [1500 + random.randint(-20, 20)], "count": 1} for _ in range(50)]
    result = _run(make_bf(), samples, channels=[0])
    roll = result["channels"]["roll"]
    assert roll["noise_p95_us"] >= 15
    assert roll["suggested_deadband"] == roll["noise_p95_us"] + 5


def test_center_us_is_median():
    """center_us doit être la médiane des valeurs collectées."""
    vals = [1490, 1495, 1500, 1505, 1510]
    samples = [{"channels": [v], "count": 1} for v in vals]
    result = _run(make_bf(), samples, channels=[0])
    assert result["channels"]["roll"]["center_us"] == 1500


def test_min_max_correct():
    vals = [1480, 1500, 1500, 1500, 1520]
    samples = [{"channels": [v], "count": 1} for v in vals]
    result = _run(make_bf(), samples, channels=[0])
    roll = result["channels"]["roll"]
    assert roll["min_us"] == 1480
    assert roll["max_us"] == 1520


def test_only_requested_channels_returned():
    samples = [{"channels": [1500, 1500, 1500, 1000], "count": 4}] * 5
    result = _run(make_bf(), samples, channels=[0, 3])
    assert "roll"     in result["channels"]
    assert "throttle" in result["channels"]
    assert "pitch"    not in result["channels"]
    assert "yaw"      not in result["channels"]


def test_aux_channel_name():
    samples = [{"channels": [1500, 1500, 1500, 1000, 2000], "count": 5}] * 5
    result = _run(make_bf(), samples, channels=[4])
    assert "aux1" in result["channels"]


def test_returns_none_on_no_samples():
    bf = make_bf()
    mono_values = iter([0.0, 3.1])
    with patch.object(bf, "get_rc", return_value=None), \
         patch("betaflight.commands.time.monotonic", side_effect=mono_values), \
         patch("betaflight.commands.time.sleep"):
        result = bf.measure_rc_noise(duration_s=3.0)
    assert result is None
