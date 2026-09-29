# ── Betaflight MCP Server ── Modèle des rates ───────────────────────
# Sémantique des octets MSP_RC_TUNING selon rates_type.
# Sources firmware : fc/controlrate_profile.{h,c} (types, limites), fc/rc.c (courbes).
# Unités d'affichage : betaflight-configurator RatesSubTab.vue (échelles, libellés).

from enum import IntEnum


class RatesType(IntEnum):
    """Ordre de ratesType_e (controlrate_profile.h)."""
    BETAFLIGHT = 0
    RACEFLIGHT = 1
    KISS       = 2
    ACTUAL     = 3
    QUICK      = 4


AXES        = ("roll", "pitch", "yaw")
RATE_FIELDS = ("rc_rate", "rate", "expo")

# Seuil d'avertissement du configurateur (RatesSubTab.vue, MAX_RATE_WARNING)
MAX_RATE_WARNING_DPS = 1800

# Plafond de consigne firmware (rc.c, SETPOINT_RATE_LIMIT_MAX)
SETPOINT_RATE_LIMIT_DPS = 1998
# Gain supplémentaire au-delà de rc_rate 2.0 en rates Betaflight (rc.c)
_RC_RATE_INCREMENTAL = 14.54
# Borne basse du dénominateur « super factor » (rc.c, constrainf(..., 0.01f, 1.00f))
_SUPER_FACTOR_MIN = 0.01

# Facteur brut → affichage (rc_rate, rate, expo), identique au configurateur
_SCALES: dict[int, dict[str, float]] = {
    RatesType.BETAFLIGHT: {"rc_rate": 0.01, "rate": 0.01, "expo": 0.01},
    RatesType.RACEFLIGHT: {"rc_rate": 10,   "rate": 1,    "expo": 1},
    RatesType.KISS:       {"rc_rate": 0.01, "rate": 0.01, "expo": 0.01},
    RatesType.ACTUAL:     {"rc_rate": 10,   "rate": 10,   "expo": 0.01},
    RatesType.QUICK:      {"rc_rate": 0.01, "rate": 10,   "expo": 0.01},
}

# Limites brutes (controlrate_profile.c, ratesSettingLimits) : appliquées au boot
# par validateAndFixConfig, pas par MSP_SET_RC_TUNING → on les applique nous-mêmes.
_RAW_LIMITS: dict[int, dict[str, int]] = {
    RatesType.BETAFLIGHT: {"rc_rate": 255, "rate": 100, "expo": 100},
    RatesType.RACEFLIGHT: {"rc_rate": 200, "rate": 255, "expo": 100},
    RatesType.KISS:       {"rc_rate": 255, "rate": 99,  "expo": 100},
    RatesType.ACTUAL:     {"rc_rate": 200, "rate": 200, "expo": 100},
    RatesType.QUICK:      {"rc_rate": 255, "rate": 200, "expo": 100},
}

# Libellés du configurateur (locales/en/messages.json)
_LABELS: dict[int, dict[str, str]] = {
    RatesType.BETAFLIGHT: {"rc_rate": "RC Rate",                  "rate": "Rate (super rate)", "expo": "RC Expo (0-1)"},
    RatesType.RACEFLIGHT: {"rc_rate": "Rate (°/s)",               "rate": "Acro+ (%)",         "expo": "Expo (0-100)"},
    RatesType.KISS:       {"rc_rate": "RC Rate",                  "rate": "Rate",              "expo": "RC Curve (0-1)"},
    RatesType.ACTUAL:     {"rc_rate": "Center Sensitivity (°/s)", "rate": "Max Rate (°/s)",    "expo": "Expo (0-1)"},
    RatesType.QUICK:      {"rc_rate": "RC Rate",                  "rate": "Max Rate (°/s)",    "expo": "Expo (0-1)"},
}

# Throttle mid/expo/hover : octet brut en centièmes (0-100)
THROTTLE_SCALE     = 0.01
THROTTLE_RAW_LIMIT = 100


def is_supported(rates_type: int) -> bool:
    return rates_type in _SCALES


def type_name(rates_type: int) -> str:
    return RatesType(rates_type).name if is_supported(rates_type) else f"UNKNOWN_{rates_type}"


def labels(rates_type: int) -> dict[str, str]:
    return dict(_LABELS[rates_type])


def raw_limit(rates_type: int, field: str) -> int:
    return _RAW_LIMITS[rates_type][field]


def display_limit(rates_type: int, field: str) -> float:
    return to_display(rates_type, field, raw_limit(rates_type, field))


def to_display(rates_type: int, field: str, raw: int) -> float:
    return round(raw * _SCALES[rates_type][field], 2)


def to_raw(rates_type: int, field: str, value: float) -> int:
    return round(value / _SCALES[rates_type][field])


# ── Vitesse max plein manche (rcCommandf = 1), formules de fc/rc.c ────

def _super_factor(fraction: float) -> float:
    return 1.0 / min(max(1.0 - fraction, _SUPER_FACTOR_MIN), 1.0)


def _betaflight(rc: int, rate: int, _expo: int) -> float:
    rc_rate = rc / 100.0
    if rc_rate > 2.0:
        rc_rate += _RC_RATE_INCREMENTAL * (rc_rate - 2.0)
    angle = 200.0 * rc_rate
    return angle * _super_factor(rate / 100.0) if rate else angle


def _raceflight(rc: int, rate: int, _expo: int) -> float:
    return 10.0 * rc * (1 + rate * 0.01)


def _kiss(rc: int, rate: int, _expo: int) -> float:
    return 2000.0 * _super_factor(rate / 100.0) * (rc / 1000.0)


def _actual(rc: int, rate: int, _expo: int) -> float:
    center = rc * 10.0
    return center + max(0.0, rate * 10.0 - center)


def _quick(rc: int, rate: int, _expo: int) -> float:
    rc_rate = rc * 2
    if rc_rate == 0:
        return 0.0
    max_dps = max(rate * 10, rc_rate)
    ratio   = max_dps / rc_rate
    return rc_rate * _super_factor((ratio - 1) / ratio)


_CURVES = {
    RatesType.BETAFLIGHT: _betaflight,
    RatesType.RACEFLIGHT: _raceflight,
    RatesType.KISS:       _kiss,
    RatesType.ACTUAL:     _actual,
    RatesType.QUICK:      _quick,
}


def max_rate_dps(rates_type: int, rc_rate_raw: int, rate_raw: int, expo_raw: int,
                 rate_limit_dps: int) -> int:
    """Consigne (°/s) à plein manche, bornée comme dans le firmware."""
    angle = _CURVES[rates_type](rc_rate_raw, rate_raw, expo_raw)
    return round(min(angle, SETPOINT_RATE_LIMIT_DPS, rate_limit_dps))
