# ── Betaflight MCP Server ── Configuration des filtres ──────────────
# Table des champs de MSP_FILTER_CONFIG / MSP_SET_FILTER_CONFIG, nommés comme la CLI.
# Layout (msp.c) identique en 2025.12.5 et 2026.6.2, l'API 1.48 ajoutant en fin de
# trame rpm_filter_fade_range_hz, rpm_filter_q et rpm_filter_weights.
# Plages : cli/settings.c, identiques en 2025.12.5 et 2026.6.2 → écriture dès l'API 1.47.
# Les filtres gyro sont globaux ; D-term et yaw_lowpass_hz dépendent du profil PID actif.

from . import msp_fields
from .msp_fields import Field, Value, enum_field as _enum

WRITE_MIN_API = (1, 47)

LPF_MAX_HZ     = 1000  # sensors/gyro.h
DYN_LPF_MAX_HZ = 1000
RPM_HARMONICS  = 3     # RPM_FILTER_HARMONICS_MAX

LOWPASS_TYPE      = ("PT1", "BIQUAD", "PT2", "PT3")
GYRO_HARDWARE_LPF = ("NORMAL", "OPTION_1", "OPTION_2")  # "EXPERIMENTAL" : builds dédiés uniquement

FIELDS: dict[str, Field] = {
    # gyro_lpf1_static_hz : u16 aux octets 20-21, copie u8 historique à l'octet 0
    "gyro_lpf1_static_hz":      Field(20, 2, 0, LPF_MAX_HZ, legacy_u8_offset=0),
    "dterm_lpf1_static_hz":     Field(1,  2, 0, LPF_MAX_HZ),
    "yaw_lowpass_hz":           Field(3,  2, 0, 500),
    "gyro_notch1_hz":           Field(5,  2, 0, LPF_MAX_HZ),
    "gyro_notch1_cutoff":       Field(7,  2, 0, LPF_MAX_HZ),
    "dterm_notch_hz":           Field(9,  2, 0, LPF_MAX_HZ),
    "dterm_notch_cutoff":       Field(11, 2, 0, LPF_MAX_HZ),
    "gyro_notch2_hz":           Field(13, 2, 0, LPF_MAX_HZ),
    "gyro_notch2_cutoff":       Field(15, 2, 0, LPF_MAX_HZ),
    "dterm_lpf1_type":          _enum(17, LOWPASS_TYPE),
    "gyro_hardware_lpf":        _enum(18, GYRO_HARDWARE_LPF),
    "gyro_lpf2_static_hz":      Field(22, 2, 0, LPF_MAX_HZ),
    "gyro_lpf1_type":           _enum(24, LOWPASS_TYPE),
    "gyro_lpf2_type":           _enum(25, LOWPASS_TYPE),
    "dterm_lpf2_static_hz":     Field(26, 2, 0, LPF_MAX_HZ),
    "dterm_lpf2_type":          _enum(28, LOWPASS_TYPE),
    "gyro_lpf1_dyn_min_hz":     Field(29, 2, 0, DYN_LPF_MAX_HZ),
    "gyro_lpf1_dyn_max_hz":     Field(31, 2, 0, DYN_LPF_MAX_HZ),
    "dterm_lpf1_dyn_min_hz":    Field(33, 2, 0, DYN_LPF_MAX_HZ),
    "dterm_lpf1_dyn_max_hz":    Field(35, 2, 0, DYN_LPF_MAX_HZ),
    # octets 37-38 : dyn_notch_range / dyn_notch_width_percent obsolètes (toujours 0)
    "dyn_notch_q":              Field(39, 2, 1, 1000),
    "dyn_notch_min_hz":         Field(41, 2, 20, 250),
    "rpm_filter_harmonics":     Field(43, 1, 0, RPM_HARMONICS),
    "rpm_filter_min_hz":        Field(44, 1, 30, 200),
    "dyn_notch_max_hz":         Field(45, 2, 200, 1000),
    "dterm_lpf1_dyn_expo":      Field(47, 1, 0, 10),
    "dyn_notch_count":          Field(48, 1, 0, 7),       # DYN_NOTCH_COUNT_MAX
    # API >= 1.48
    "rpm_filter_fade_range_hz": Field(49, 2, 0, 1000),
    "rpm_filter_q":             Field(51, 2, 250, 3000),
    "rpm_filter_weights":       Field(53, 1, 0, 100, count=RPM_HARMONICS),
}

WRITABLE_NAMES = tuple(FIELDS)
_GYRO_LPF1     = "gyro_lpf1_static_hz"


def parse(raw: bytes) -> dict:
    """Champs présents dans le payload, nommés comme la CLI."""
    result = {name: msp_fields.read(field, raw)
              for name, field in FIELDS.items() if msp_fields.is_present(field, raw)}
    if _GYRO_LPF1 not in result and raw:
        result[_GYRO_LPF1] = raw[FIELDS[_GYRO_LPF1].legacy_u8_offset]  # firmware très ancien
    return result


def field_for(name: str) -> Field:
    return FIELDS[name]


def describe(field: Field) -> str:
    return msp_fields.describe(field)


def to_raw(name: str, value: Value):
    """Valeur brute pour un champ ; ValueError (message utilisateur) si invalide."""
    return msp_fields.to_raw(FIELDS, name, value)


def patch(raw: bytes, raw_updates: dict) -> bytes:
    """Copie de raw avec les champs modifiés ; ValueError si un champ est absent du payload."""
    return msp_fields.patch(FIELDS, raw, raw_updates)
