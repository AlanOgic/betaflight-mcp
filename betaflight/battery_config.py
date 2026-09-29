# ── Betaflight MCP Server ── Configuration batterie ─────────────────
# Table des champs de MSP_BATTERY_CONFIG / MSP_SET_BATTERY_CONFIG, nommés comme la CLI.
# Layout (msp.c) et plages (cli/settings.c) identiques en 2025.12.5 et 2026.6.2 ; en 2026.6
# tensions et capacité appartiennent au profil batterie actif. Tensions cellule en u16
# (0.01 V, octets 7-12) avec copie u8 historique en 0.1 V (octets 0-2, arrondi (v+5)/10).
# Le firmware refuse l'écriture si min > warning ou warning > max. Les sources de mesure
# (current_meter, battery_meter) ne sont prises en compte qu'au redémarrage (CMS : REBOOT_REQUIRED).

from . import msp_fields
from .msp_fields import Field, Value, enum_field as _enum

WRITE_MIN_API = (1, 47)

CELL_VOLTAGE_MIN = 100   # VBAT_CELL_VOTAGE_RANGE_MIN (0.01 V)
CELL_VOLTAGE_MAX = 500   # VBAT_CELL_VOTAGE_RANGE_MAX
CAPACITY_MAX     = 20000
_LEGACY_DIVISOR  = 10    # u8 historique en 0.1 V

CURRENT_METER = ("NONE", "ADC", "VIRTUAL", "ESC", "MSP")   # sensors/current.c
VOLTAGE_METER = ("NONE", "ADC", "ESC")                     # sensors/voltage.c

MIN_CELL  = "vbat_min_cell_voltage"
MAX_CELL  = "vbat_max_cell_voltage"
WARN_CELL = "vbat_warning_cell_voltage"


def _cell(offset: int, legacy_offset: int) -> Field:
    return Field(offset, 2, CELL_VOLTAGE_MIN, CELL_VOLTAGE_MAX,
                 legacy_u8_offset=legacy_offset, legacy_u8_divisor=_LEGACY_DIVISOR)


FIELDS: dict[str, Field] = {
    MIN_CELL:        _cell(7, 0),
    MAX_CELL:        _cell(9, 1),
    WARN_CELL:       _cell(11, 2),
    "bat_capacity":  Field(3, 2, 0, CAPACITY_MAX),
    "battery_meter": _enum(5, VOLTAGE_METER),
    "current_meter": _enum(6, CURRENT_METER),
}

WRITABLE_NAMES         = tuple(FIELDS)
REBOOT_REQUIRED_FIELDS = frozenset({"current_meter", "battery_meter"})


def parse(raw: bytes) -> dict:
    """Champs présents dans le payload, nommés comme la CLI (tensions en 0.01 V)."""
    return {name: msp_fields.read(field, raw)
            for name, field in FIELDS.items() if msp_fields.is_present(field, raw)}


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
