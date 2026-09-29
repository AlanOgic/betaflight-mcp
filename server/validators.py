# Limites firmware : pid.h (PID_GAIN_MAX=250) ; rates : voir betaflight/rates.py

import math

from betaflight import rates
from betaflight.commands import PID_AXES, RATES_WRITABLE_FIELDS

_PID_VALID_AXES = frozenset(PID_AXES)

_PID_HARD_MAX = 250  # PID_GAIN_MAX

# Seuils soft-warn : inhabituel pour tout type de build (2" à 7")
_PID_WARN = {"p": 150, "i": 150, "d": 80}

# Écart toléré entre valeur demandée et valeur écrite avant d'avertir d'un arrondi
_RATE_ROUNDING_TOLERANCE = 1e-6


def validate_pid(axis: str, p: int, i: int, d: int) -> dict:
    """Retourne {"errors": [...], "warnings": [...]}."""
    errors: list[str] = []
    warnings: list[str] = []

    if axis not in _PID_VALID_AXES:
        errors.append(
            f"Axe '{axis}' invalide. Axes acceptés : {list(PID_AXES)}"
        )

    for name, val in (("p", p), ("i", i), ("d", d)):
        if not isinstance(val, int) or not (0 <= val <= _PID_HARD_MAX):
            errors.append(
                f"{name.upper()}={val} hors plage firmware [0–{_PID_HARD_MAX}] (PID_GAIN_MAX)"
            )
        elif val > _PID_WARN[name]:
            warnings.append(
                f"{name.upper()}={val} inhabituel (>{_PID_WARN[name]} pour tout type de build)"
            )

    return {"errors": errors, "warnings": warnings}


def _rate_value_scale(rates_type: int, name: str) -> tuple[float, float]:
    """(pas d'affichage d'un octet, valeur max affichable) pour un champ de set_rates."""
    if name.startswith("throttle_"):
        return rates.THROTTLE_SCALE, rates.THROTTLE_RAW_LIMIT * rates.THROTTLE_SCALE
    field = name.split("_", 1)[1]
    return rates.to_display(rates_type, field, 1), rates.display_limit(rates_type, field)


def _check_rate_value(type_name: str, rates_type: int, name: str,
                      value: float) -> tuple[str | None, str | None]:
    """Retourne (erreur, avertissement) pour une valeur ; None si rien à signaler."""
    if name not in RATES_WRITABLE_FIELDS:
        return f"Champ '{name}' inconnu. Champs acceptés : {list(RATES_WRITABLE_FIELDS)}", None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return f"{name}={value!r} : valeur numérique attendue", None
    step, hi = _rate_value_scale(rates_type, name)
    if not (0 <= value <= hi):
        return f"{name}={value} hors plage firmware [0–{hi}] pour les rates {type_name}", None
    raw     = round(value / step)
    applied = round(raw * step, 2)
    if value > 0 and raw == 0:
        return (f"{name}={value} sous la résolution des rates {type_name} (pas de {step}) : "
                f"serait écrit 0. Unités d'un autre rates_type ? Voir get_rates (labels)."), None
    if abs(applied - value) > _RATE_ROUNDING_TOLERANCE:
        return None, f"{name}={value} arrondi à {applied} (pas firmware de {step})"
    return None, None


def _check_axis_curve(rates_type: int, axis: str, current_axis: dict,
                      updates: dict) -> tuple[str | None, str | None]:
    """Vérifie la courbe résultante d'un axe modifié (valeurs fusionnées avec l'actuel)."""
    raw = {field: rates.to_raw(rates_type, field,
                               updates.get(f"{axis}_{field}", current_axis[field]))
           for field in rates.RATE_FIELDS}
    max_dps = rates.max_rate_dps(rates_type, raw["rc_rate"], raw["rate"], raw["expo"],
                                 current_axis["rate_limit_dps"])
    if max_dps is None:
        return (f"{axis} : rc_rate 0 en rates QUICK → division par zéro dans le firmware "
                f"(consigne NaN). rc_rate minimum : {rates.to_display(rates_type, 'rc_rate', 1)}"), None
    if max_dps == 0:
        return f"{axis} : 0 °/s à plein manche, l'axe ne serait plus pilotable", None
    if max_dps > rates.MAX_RATE_WARNING_DPS:
        return None, (f"{axis} : {max_dps} °/s à plein manche (> {rates.MAX_RATE_WARNING_DPS}, "
                      f"seuil d'alerte du configurateur)")
    return None, None


def validate_rates(current: dict, updates: dict) -> dict:
    """
    Valide des modifications de rates avant écriture.
    current : résultat de BetaflightCommands.get_rates() (rates_type actif + valeurs).
    updates : {"roll_rate": 800, "pitch_expo": 0.3, ...} en unités du configurateur.
    Erreurs : champ inconnu, rates_type non géré, valeur hors limites firmware du type.
    Erreurs aussi pour un axe modifié qui finirait à 0 °/s ou en consigne NaN (QUICK, rc_rate 0).
    Avertissements (axes modifiés seulement) : vitesse max plein manche > MAX_RATE_WARNING_DPS
    (seuil configurateur), ou valeur arrondie au pas firmware.
    Retourne {"errors": [...], "warnings": [...]}.
    """
    errors: list[str] = []
    warnings: list[str] = []

    rates_type = current["rates_type_id"]
    if not rates.is_supported(rates_type):
        return {"errors": [f"rates_type {current['rates_type']} non géré : écriture refusée"],
                "warnings": []}

    for name, value in updates.items():
        error, warning = _check_rate_value(current["rates_type"], rates_type, name, value)
        if error:
            errors.append(error)
        if warning:
            warnings.append(warning)

    if errors:
        return {"errors": errors, "warnings": warnings}

    touched_axes = [axis for axis in rates.AXES
                    if any(name.startswith(f"{axis}_") for name in updates)]
    for axis in touched_axes:
        error, warning = _check_axis_curve(rates_type, axis, current[axis], updates)
        if error:
            errors.append(error)
        if warning:
            warnings.append(warning)

    return {"errors": errors, "warnings": warnings}
