"""Tests unitaires pour server/validators.py."""

import pytest
from server.validators import validate_pid


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
