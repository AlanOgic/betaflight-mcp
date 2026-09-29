# ── Betaflight MCP Server ── Réglages PID avancés ───────────────────
# Table des champs de MSP_PID_ADVANCED / MSP_SET_PID_ADVANCED, nommés comme la CLI.
# Layout (msp.c) identique de 4.5.2 à 2026.6.2 ; les octets 39-43 sont d_min jusqu'à
# l'API 1.46 et d_max à partir de l'API 1.47 (2025.12). Plages et tables d'énumération :
# cli/settings.c, identiques en 2025.12.5 et 2026.6.2 → écriture autorisée à partir de 1.47.

from typing import Optional

from . import msp_fields
from .msp_fields import Field, Value, enum_field as _enum

# API à partir de laquelle les octets 39-43 sont D-max (avant : D-min)
D_MAX_API       = (1, 47)
# API à partir de laquelle la table (plages, sémantique) est vérifiée pour l'écriture
WRITE_MIN_API   = (1, 47)

OFF_ON               = ("OFF", "ON")
ITERM_RELAX          = ("OFF", "RP", "RPY", "RP_INC", "RPY_INC")
ITERM_RELAX_TYPE     = ("GYRO", "SETPOINT")
FEEDFORWARD_AVERAGING = ("OFF", "2_POINT", "3_POINT", "4_POINT")
TPA_MODE             = ("PD", "D")  # "PDS" n'existe que sur les builds USE_WING
SIMPLIFIED_PIDS_MODE = ("OFF", "RP", "RPY")

# Champ hors MSP_PID_ADVANCED (MSP_SIMPLIFIED_TUNING, octet 0), exposé avec les autres
SIMPLIFIED_PIDS_MODE_FIELD = "simplified_pids_mode"


FIELDS: dict[str, Field] = {
    "feedforward_transition":     Field(8,  1, 0, 100),
    "acc_limit":                  Field(13, 2, 0, 500),
    "acc_limit_yaw":              Field(15, 2, 0, 500),
    "angle_limit":                Field(17, 1, 10, 80),
    "anti_gravity_gain":          Field(21, 2, 0, 250),
    "iterm_rotation":             _enum(25, OFF_ON),
    "iterm_relax":                _enum(27, ITERM_RELAX),
    "iterm_relax_type":           _enum(28, ITERM_RELAX_TYPE),
    "throttle_boost":             Field(30, 1, 0, 100),
    "acro_trainer_angle_limit":   Field(31, 1, 10, 80),
    "f_roll":                     Field(32, 2, 0, 1000),
    "f_pitch":                    Field(34, 2, 0, 1000),
    "f_yaw":                      Field(36, 2, 0, 1000),
    "d_max_roll":                 Field(39, 1, 0, 250, legacy_name="d_min_roll"),
    "d_max_pitch":                Field(40, 1, 0, 250, legacy_name="d_min_pitch"),
    "d_max_yaw":                  Field(41, 1, 0, 250, legacy_name="d_min_yaw"),
    "d_max_gain":                 Field(42, 1, 0, 100, legacy_name="d_min_gain"),
    "d_max_advance":              Field(43, 1, 0, 200, legacy_name="d_min_advance"),
    "use_integrated_yaw":         _enum(44, OFF_ON),
    "integrated_yaw_relax":       Field(45, 1, 0, 255),
    "iterm_relax_cutoff":         Field(46, 1, 1, 50),
    "motor_output_limit":         Field(47, 1, 1, 100),
    "auto_profile_cell_count":    Field(48, 1, -1, 8, signed=True),
    "dyn_idle_min_rpm":           Field(49, 1, 0, 200),
    "feedforward_averaging":      _enum(50, FEEDFORWARD_AVERAGING),
    "feedforward_smooth_factor":  Field(51, 1, 0, 95),
    "feedforward_boost":          Field(52, 1, 0, 50),
    "feedforward_max_rate_limit": Field(53, 1, 0, 200),
    "feedforward_jitter_factor":  Field(54, 1, 0, 20),
    "vbat_sag_compensation":      Field(55, 1, 0, 150),
    "thrust_linear":              Field(56, 1, 0, 150),
    "tpa_mode":                   _enum(57, TPA_MODE),
    "tpa_rate":                   Field(58, 1, 0, 100),
    "tpa_breakpoint":             Field(59, 2, 1000, 2000),
}

_SIMPLIFIED_FIELD = _enum(0, SIMPLIFIED_PIDS_MODE)
_WRITABLE_FIELDS  = {**FIELDS, SIMPLIFIED_PIDS_MODE_FIELD: _SIMPLIFIED_FIELD}
WRITABLE_NAMES    = tuple(_WRITABLE_FIELDS)


def field_for(name: str) -> Field:
    return _WRITABLE_FIELDS[name]


def parse(raw: bytes, api_version: tuple) -> dict:
    """Champs présents dans le payload, nommés comme la CLI de la version du FC."""
    return {
        (field.legacy_name if field.legacy_name and api_version < D_MAX_API else name):
            msp_fields.read(field, raw)
        for name, field in FIELDS.items() if msp_fields.is_present(field, raw)
    }


def parse_simplified_mode(raw: bytes) -> Optional[Value]:
    return msp_fields.read(_SIMPLIFIED_FIELD, raw) if raw else None


def describe(field: Field) -> str:
    return msp_fields.describe(field)


def to_raw(name: str, value: Value) -> int:
    """Valeur brute pour un champ ; ValueError (message utilisateur) si invalide."""
    return msp_fields.to_raw(_WRITABLE_FIELDS, name, value)


def patch(raw: bytes, raw_updates: dict[str, int]) -> bytes:
    """Copie de raw avec les champs modifiés ; ValueError si un champ est absent du payload."""
    return msp_fields.patch(FIELDS, raw, raw_updates)
