"""Tests unitaires pour server/validators.py."""

import pytest
from server.validators import validate_pid, validate_rates


# ── validate_pid ──────────────────────────────────────────────────────────────

def test_pid_valid_nominal():
    v = validate_pid("roll", 45, 80, 30)
    assert v["errors"]   == []
    assert v["warnings"] == []


def test_pid_hard_reject_overflow():
    v = validate_pid("roll", 251, 80, 30)
    assert any("P=251" in e for e in v["errors"])


def test_pid_hard_reject_negative():
    v = validate_pid("roll", 45, -1, 30)
    assert any("I=-1" in e for e in v["errors"])


def test_pid_hard_reject_invalid_axis():
    v = validate_pid("invente", 45, 80, 30)
    assert any("invente" in e for e in v["errors"])


def test_pid_soft_warn_high_d():
    v = validate_pid("roll", 45, 80, 100)
    assert v["errors"] == []
    assert any("D=100" in w for w in v["warnings"])


def test_pid_soft_warn_high_p():
    v = validate_pid("pitch", 200, 80, 30)
    assert v["errors"] == []
    assert any("P=200" in w for w in v["warnings"])


def test_pid_boundary_max_allowed():
    v = validate_pid("yaw", 250, 250, 250)
    assert v["errors"] == []
    # 250 > seuils warn → doit avoir des warnings mais pas d'erreurs
    assert len(v["warnings"]) > 0


def test_pid_valid_axis_level():
    v = validate_pid("level", 45, 80, 30)
    assert v["errors"] == []


# ── validate_rates ────────────────────────────────────────────────────────────

def test_rates_valid_nominal():
    v = validate_rates({"rc_rate": 1.0, "roll_rate": 0.7, "rc_expo": 0.3})
    assert v["errors"]   == []
    assert v["warnings"] == []


def test_rates_hard_reject_overflow():
    v = validate_rates({"rc_rate": 3.0})
    assert any("rc_rate=3.0" in e for e in v["errors"])


def test_rates_hard_reject_expo_above_1():
    v = validate_rates({"rc_expo": 1.5})
    assert any("rc_expo=1.5" in e for e in v["errors"])


def test_rates_hard_reject_negative():
    v = validate_rates({"roll_rate": -0.1})
    assert any("roll_rate=-0.1" in e for e in v["errors"])


def test_rates_soft_warn_high_rate():
    v = validate_rates({"roll_rate": 2.0})
    assert v["errors"] == []
    assert any("roll_rate=2.0" in w for w in v["warnings"])


def test_rates_unknown_key_ignored():
    v = validate_rates({"champ_inconnu": 99})
    assert v["errors"]   == []
    assert v["warnings"] == []


def test_rates_multiple_fields_mixed():
    v = validate_rates({"rc_rate": 1.0, "roll_rate": 2.5, "rc_expo": 2.0})
    # roll_rate=2.5 → warn ; rc_expo=2.0 → error (>1.0)
    assert any("rc_expo" in e for e in v["errors"])
    assert any("roll_rate" in w for w in v["warnings"])


def test_rates_boundary_max_expo():
    v = validate_rates({"rc_expo": 1.0})
    assert v["errors"]   == []
    assert v["warnings"] == []
