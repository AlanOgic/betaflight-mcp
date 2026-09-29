# ── Betaflight MCP Server ── Réglages PID avancés ───────────────────
# Table des champs de MSP_PID_ADVANCED / MSP_SET_PID_ADVANCED, nommés comme la CLI.
# Layout (msp.c) identique de 4.5.2 à 2026.6.2 ; les octets 39-43 sont d_min jusqu'à
# l'API 1.46 et d_max à partir de l'API 1.47 (2025.12). Plages et tables d'énumération :
# cli/settings.c, identiques en 2025.12.5 et 2026.6.2 → écriture autorisée à partir de 1.47.

import struct
from dataclasses import dataclass
from typing import Optional, Union

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


@dataclass(frozen=True)
class Field:
    offset:      int
    size:        int                      # 1 ou 2 octets (little-endian)
    minimum:     int
    maximum:     int
    signed:      bool = False
    enum:        Optional[tuple] = None   # libellés CLI, index = valeur brute
    legacy_name: Optional[str] = None     # nom avant D_MAX_API


def _enum(offset: int, labels: tuple) -> Field:
    return Field(offset, 1, 0, len(labels) - 1, enum=labels)


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
WRITABLE_NAMES    = tuple(FIELDS) + (SIMPLIFIED_PIDS_MODE_FIELD,)


def _format(field: Field) -> str:
    return {1: "b" if field.signed else "B", 2: "<h" if field.signed else "<H"}[field.size]


def _label(field: Field, raw: int) -> Union[str, int]:
    return field.enum[raw] if field.enum and 0 <= raw < len(field.enum) else raw


def field_for(name: str) -> Field:
    return _SIMPLIFIED_FIELD if name == SIMPLIFIED_PIDS_MODE_FIELD else FIELDS[name]


def parse(raw: bytes, api_version: tuple) -> dict:
    """Champs présents dans le payload, nommés comme la CLI de la version du FC."""
    result = {}
    for name, field in FIELDS.items():
        if field.offset + field.size > len(raw):
            continue
        label = field.legacy_name if field.legacy_name and api_version < D_MAX_API else name
        value = struct.unpack_from(_format(field), raw, field.offset)[0]
        result[label] = _label(field, value)
    return result


def parse_simplified_mode(raw: bytes) -> Optional[Union[str, int]]:
    return _label(_SIMPLIFIED_FIELD, raw[0]) if raw else None


def describe(field: Field) -> str:
    if field.enum:
        return "valeurs acceptées : " + ", ".join(field.enum)
    return f"plage firmware [{field.minimum}–{field.maximum}]"


def to_raw(name: str, value: Union[int, str]) -> int:
    """Valeur brute pour un champ ; ValueError (message utilisateur) si invalide."""
    if name not in WRITABLE_NAMES:
        raise ValueError(f"Champ '{name}' inconnu. Champs acceptés : {list(WRITABLE_NAMES)}")
    field = field_for(name)
    if isinstance(value, str):
        if not field.enum or value.upper() not in field.enum:
            raise ValueError(f"{name}={value!r} invalide, {describe(field)}")
        return field.enum.index(value.upper())
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name}={value!r} : entier attendu ({describe(field)})")
    if not field.minimum <= value <= field.maximum:
        raise ValueError(f"{name}={value} hors {describe(field)}")
    return value


def patch(raw: bytes, raw_updates: dict[str, int]) -> bytes:
    """Copie de raw avec les champs modifiés ; ValueError si un champ est absent du payload."""
    patched = bytearray(raw)
    for name, value in raw_updates.items():
        field = FIELDS[name]
        if field.offset + field.size > len(raw):
            raise ValueError(f"Champ {name} absent du payload firmware ({len(raw)} octets)")
        struct.pack_into(_format(field), patched, field.offset, value)
    return bytes(patched)
