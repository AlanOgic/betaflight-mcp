# Limites firmware : pid.h (PID_GAIN_MAX=250), rc_controls.h (RC_RATES_MAX=255, RC_EXPO_MAX=100)

from betaflight.commands import PID_AXES

_PID_VALID_AXES = frozenset(PID_AXES)

_PID_HARD_MAX = 250  # PID_GAIN_MAX

# Seuils soft-warn : inhabituel pour tout type de build (2" à 7")
_PID_WARN = {"p": 150, "i": 150, "d": 80}

# (min, max) pour chaque champ rates — firmware hard limits
_RATE_HARD: dict[str, tuple[float, float]] = {
    "rc_rate":       (0.0, 2.55),  # CONTROL_RATE_CONFIG_RC_RATES_MAX = 255
    "roll_rate":     (0.0, 2.55),
    "pitch_rate":    (0.0, 2.55),
    "yaw_rate":      (0.0, 2.55),
    "rc_expo":       (0.0, 1.0),   # CONTROL_RATE_CONFIG_RC_EXPO_MAX = 100
    "yaw_expo":      (0.0, 1.0),
    "pitch_expo":    (0.0, 1.0),
    "throttle_mid":  (0.0, 1.0),
    "throttle_expo": (0.0, 1.0),
}

# Seuils soft-warn rates
_RATE_WARN: dict[str, float] = {
    "rc_rate":   1.5,
    "roll_rate": 1.5,
    "pitch_rate": 1.5,
    "yaw_rate":  1.5,
}


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


def validate_rates(updates: dict) -> dict:
    """Retourne {"errors": [...], "warnings": [...]}."""
    errors: list[str] = []
    warnings: list[str] = []

    for key, val in updates.items():
        if key not in _RATE_HARD:
            continue
        lo, hi = _RATE_HARD[key]
        if not (lo <= val <= hi):
            errors.append(
                f"{key}={val} hors plage firmware [{lo}–{hi}]"
            )
        elif key in _RATE_WARN and val > _RATE_WARN[key]:
            warnings.append(
                f"{key}={val} inhabituel (>{_RATE_WARN[key]}, vérifier le profil de vol)"
            )

    return {"errors": errors, "warnings": warnings}
