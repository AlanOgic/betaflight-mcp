# ── Betaflight MCP Server ── Profils batterie ───────────────────────
# MSP2_BATTERY_PROFILE / MSP2_SET_BATTERY_PROFILE (0x300E / 0x300F), Betaflight 2026.6
# (API 1.48). Lecture et écriture adressent un profil par index ; les deux payloads ont
# le même layout (octet 0 = index). Le firmware refuse l'écriture si l'ordre
# min <= warning <= full <= max n'est pas respecté. Tensions en 0.01 V.
# La détection du nombre de cellules vaut floor(tension / vbat_max_cell_voltage) + 1
# (sensors/battery.c) : un max trop bas fait lire un 1S HV chargé comme un 2S.

from . import msp_fields
from .msp_fields import Field, Value

WRITE_MIN_API = (1, 48)
PROFILE_COUNT = 3        # BATTERY_PROFILE_COUNT

CELL_VOLTAGE_MIN = 100   # VBAT_CELL_VOTAGE_RANGE_MIN (0.01 V)
CELL_VOLTAGE_MAX = 500

MIN_CELL  = "vbat_min_cell_voltage"
WARN_CELL = "vbat_warning_cell_voltage"
FULL_CELL = "vbat_full_cell_voltage"
MAX_CELL  = "vbat_max_cell_voltage"
VOLTAGE_ORDER = (MIN_CELL, WARN_CELL, FULL_CELL, MAX_CELL)


def _cell(offset: int) -> Field:
    return Field(offset, 2, CELL_VOLTAGE_MIN, CELL_VOLTAGE_MAX)


FIELDS: dict[str, Field] = {
    MIN_CELL:                   _cell(1),
    MAX_CELL:                   _cell(3),
    WARN_CELL:                  _cell(5),
    FULL_CELL:                  _cell(7),
    "bat_capacity":             Field(9, 2, 0, 20000),
    "force_battery_cell_count": Field(11, 1, 0, 24),
    "cbat_alert_percent":       Field(12, 1, 0, 100),
}

WRITABLE_NAMES = tuple(FIELDS)


def parse(raw: bytes) -> dict:
    """Profil lu : {"index": n, <champs CLI>...}."""
    result = {"index": raw[0]} if raw else {}
    result.update({name: msp_fields.read(field, raw)
                   for name, field in FIELDS.items() if msp_fields.is_present(field, raw)})
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
