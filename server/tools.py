import threading
from typing import Annotated, Literal, Optional, Union

from mcp.types import ToolAnnotations
from pydantic import Field

from betaflight.serial_conn import SerialConnection
from betaflight.msp import MSPProtocol
from betaflight.commands import (
    BetaflightCommands, BETAFLIGHT_IDENTIFIER, MIN_API_VERSION, PID_AXES, PID_GAIN_MAX,
    PROFILE_KINDS, RC_CHANNEL_NAMES, WriteBlockedError,
)
from betaflight import battery_config, battery_profiles, filter_config, pid_advanced, rates
from config.settings import SERIAL_PORT, BAUD_RATE, TIMEOUT, MAX_SAMPLING_DURATION_S
from server.validators import (
    validate_battery_config, validate_battery_profile, validate_filter_config, validate_pid,
    validate_pid_advanced, validate_rates,
)

# ── Types de paramètres (FastMCP publie le schéma à partir des signatures) ──

_RC_CHANNEL_COUNT = len(RC_CHANNEL_NAMES)

RcBaseline = Annotated[list[int], Field(
    min_length=1, max_length=_RC_CHANNEL_COUNT,
    description="Valeurs RC au repos (µs), telles que retournées par get_rc (champ channels)",
)]
RcChannelIndex = Annotated[int, Field(ge=0, le=_RC_CHANNEL_COUNT - 1)]
PidAxis        = Literal[PID_AXES]
PidAdvancedName = Literal[pid_advanced.WRITABLE_NAMES]
FilterName      = Literal[filter_config.WRITABLE_NAMES]
BatteryName     = Literal[battery_config.WRITABLE_NAMES]
BatteryProfileName = Literal[battery_profiles.WRITABLE_NAMES]
ProfileKind     = Literal[PROFILE_KINDS]


def _duration(description: str):
    return Field(gt=0, le=MAX_SAMPLING_DURATION_S,
                 description=f"{description} (max {MAX_SAMPLING_DURATION_S:g} s)")


def _pid_gain(gain: str):
    return Field(ge=0, le=PID_GAIN_MAX, description=f"Gain {gain} (0-{PID_GAIN_MAX}, PID_GAIN_MAX)")


def _rate_field(axis: str, field: str):
    return Field(default=None, ge=0,
                 description=f"{axis} : {_RATE_FIELD_DESCRIPTIONS[field]}, unités du rates_type actif (voir get_rates)")


def _throttle_field(name: str):
    return Field(default=None, ge=0, le=rates.THROTTLE_RAW_LIMIT * rates.THROTTLE_SCALE,
                 description=f"{name} (0.0-1.0)")


_RATE_FIELD_DESCRIPTIONS = {
    "rc_rate": "RC Rate (BETAFLIGHT/KISS/QUICK) ou Center Sensitivity °/s (ACTUAL) ou Rate °/s (RACEFLIGHT)",
    "rate":    "super rate (BETAFLIGHT/KISS), Max Rate °/s (ACTUAL/QUICK) ou Acro+ % (RACEFLIGHT)",
    "expo":    "expo / RC Curve",
}

# ── Annotations MCP ──

_READ_ONLY  = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
_CONNECTION = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True,
                              openWorldHint=False)
_FC_WRITE   = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True,
                              openWorldHint=False)
_FC_REBOOT  = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False,
                              openWorldHint=False)

_conn: SerialConnection   = None
_msp:  MSPProtocol        = None
_bf:   BetaflightCommands = None
# Protège le remplacement de _conn/_msp/_bf (connect, disconnect, reboot)
_state_lock = threading.RLock()


_WRITE_REJECTED = (
    "Le FC n'a pas acquitté la commande (refus, FC armé, réponse perdue ou "
    "connexion coupée). État incertain : relire la valeur avant de réessayer."
)


def _guarded_write(write) -> Optional[dict]:
    """
    Exécute une écriture BetaflightCommands. Retourne None si elle a été acquittée,
    sinon le dict d'erreur du tool (garde d'armement ou absence d'ack).
    """
    try:
        if write():
            return None
    except WriteBlockedError as e:
        return {"success": False, "error": str(e)}
    return {"success": False, "error": _WRITE_REJECTED}


def _get_bf() -> BetaflightCommands:
    global _bf
    if _bf is None:
        raise RuntimeError("Non connecté au FC. Utilisez d'abord le tool 'connect'.")
    return _bf


# ── Connexion ─────────────────────────────────────────────────────────

def tool_list_serial_ports() -> dict:
    ports = SerialConnection.list_available_ports()
    return {"ports": ports, "count": len(ports)}


def _close_connection() -> None:
    """Ferme le port (après la transaction MSP en cours, s'il y en a une) et oublie l'état."""
    global _conn, _msp, _bf
    with _state_lock:
        if _conn is not None:
            if _msp is not None:
                with _msp.transaction():
                    _conn.disconnect()
            else:
                _conn.disconnect()
        _conn = _msp = _bf = None


def _identify(bf: BetaflightCommands, port: str) -> tuple[Optional[str], dict]:
    """
    Vérifie que l'appareil est un FC Betaflight supporté.
    Retourne (erreur, identité) ; erreur None si le FC est accepté.
    """
    if bf.get_api_version() is None:
        return (f"Aucune réponse MSP sur {port} : pas un FC Betaflight, FC pas encore démarré, "
                f"ou mauvais baudrate."), {}
    api      = ".".join(str(v) for v in bf.api_version)
    variant  = (bf.get_fc_variant() or {}).get("identifier", "?")
    version  = (bf.get_fc_version() or {}).get("version", "?")
    identity = {"api_version": api, "fc_variant": variant, "fc_version": version}
    if variant != BETAFLIGHT_IDENTIFIER:
        return (f"Firmware {variant} non supporté : ce serveur ne gère que Betaflight "
                f"({BETAFLIGHT_IDENTIFIER}), les layouts MSP diffèrent."), identity
    if bf.api_version < MIN_API_VERSION:
        minimum = ".".join(str(v) for v in MIN_API_VERSION)
        return (f"API MSP {api} (Betaflight {version}) trop ancienne : minimum {minimum}."), identity
    return None, identity


def _detect_port() -> tuple[Optional[str], Optional[str]]:
    """(port, None) si un seul FC Betaflight est branché, sinon (None, message d'erreur)."""
    candidates = SerialConnection.find_betaflight_ports()
    if len(candidates) == 1:
        return candidates[0], None
    if not candidates:
        return None, ("Aucun FC Betaflight détecté en USB. Vérifier le câble (données, pas "
                      "seulement charge), puis list_serial_ports ou passer port explicitement.")
    return None, (f"Plusieurs FC Betaflight détectés : {candidates}. "
                  "Préciser le port à utiliser.")


def tool_connect(
    port:     Annotated[Optional[str], Field(min_length=1, description=(
        "Port série, ex. /dev/ttyACM0, /dev/cu.usbmodem1101 ou COM3. "
        "Omis : BETAFLIGHT_PORT, sinon détection du FC Betaflight branché"))] = SERIAL_PORT,
    baudrate: Annotated[int, Field(gt=0, description="Baudrate du port USB VCP (Betaflight : 115200)")] = BAUD_RATE,
) -> dict:
    global _conn, _msp, _bf
    if port is None:
        port, error = _detect_port()
        if error:
            return {"success": False, "error": error}
    with _state_lock:
        _close_connection()
        conn = SerialConnection(port=port, baudrate=baudrate, timeout=TIMEOUT)
        if not conn.connect():
            return {"success": False,
                    "error": (f"Impossible d'ouvrir {port} : {conn.last_error}. "
                              "Port occupé (Betaflight Configurator ouvert, application ou version web "
                              "dans un navigateur, qui peut se reconnecter seule) ou inexistant "
                              "(voir list_serial_ports).")}
        msp = MSPProtocol(conn)
        bf  = BetaflightCommands(msp)
        error, identity = _identify(bf, port)
        if error:
            conn.disconnect()
            return {"success": False, "error": error, **identity}
        _conn, _msp, _bf = conn, msp, bf
        return {"success": True, "message": f"Connecté sur {port} @ {baudrate}", **identity}


def tool_disconnect() -> dict:
    _close_connection()
    return {"success": True, "message": "Déconnecté"}


# ── Identité FC ───────────────────────────────────────────────────────

def tool_get_board_info() -> dict:
    """Retourne : version firmware, variante FC, info carte, version API."""
    bf     = _get_bf()
    result = {}
    v = bf.get_fc_variant();   result.update(v or {})
    v = bf.get_fc_version();   result.update(v or {})
    v = bf.get_board_info();   result.update(v or {})
    v = bf.get_api_version();  result.update(v or {})
    if not result:
        return {"error": "Impossible de lire les infos board"}
    return result


# ── Télémétrie ────────────────────────────────────────────────────────

def tool_get_fc_status() -> dict:
    result = _get_bf().get_fc_status()
    return result or {"error": "Impossible de lire le status FC"}


def tool_get_imu_data() -> dict:
    result = _get_bf().get_imu_data()
    return result or {"error": "Impossible de lire les données IMU"}


def tool_get_attitude() -> dict:
    result = _get_bf().get_attitude()
    return result or {"error": "Impossible de lire l'attitude"}


def tool_get_altitude() -> dict:
    result = _get_bf().get_altitude()
    return result or {"error": "Impossible de lire l'altitude"}


def tool_get_battery() -> dict:
    result = _get_bf().get_battery()
    return result or {"error": "Impossible de lire la batterie"}


def tool_get_battery_state() -> dict:
    result = _get_bf().get_battery_state()
    return result or {"error": "Impossible de lire l'état batterie"}


def tool_get_voltage_meters() -> dict:
    result = _get_bf().get_voltage_meters()
    if result is None:
        return {"error": "Impossible de lire les voltmètres"}
    return {"meters": result, "count": len(result)}


def tool_get_current_meters() -> dict:
    result = _get_bf().get_current_meters()
    if result is None:
        return {"error": "Impossible de lire les ampèremètres"}
    return {"meters": result, "count": len(result)}


def tool_get_rc() -> dict:
    result = _get_bf().get_rc()
    return result or {"error": "Impossible de lire les canaux RC"}


def tool_snapshot_rc_delta(
    baseline:  RcBaseline,
    threshold: Annotated[int, Field(ge=1, description="Delta minimum en µs pour considérer un canal actif")] = 200,
) -> dict:
    result = _get_bf().get_rc()
    if not result:
        return {"error": "Impossible de lire les canaux RC"}
    current = result["channels"]
    changed = []
    unchanged = []
    for i, cur in enumerate(current):
        base = baseline[i] if i < len(baseline) else 1500
        delta = cur - base
        name = RC_CHANNEL_NAMES[i] if i < len(RC_CHANNEL_NAMES) else f"ch{i}"
        if abs(delta) >= threshold:
            changed.append({"channel": i, "name": name,
                            "baseline": base, "current": cur, "delta": delta})
        else:
            unchanged.append(i)
    return {"changed": changed, "unchanged": unchanged, "snapshot": current}


def tool_measure_rc_noise(
    duration_s: Annotated[float, _duration("Durée de mesure en secondes, sticks au repos")] = 3.0,
    channels:   Annotated[Optional[list[RcChannelIndex]], Field(
        description="Indices des canaux à mesurer (0 = roll … 17 = aux14) ; défaut : tous")] = None,
) -> dict:
    result = _get_bf().measure_rc_noise(duration_s=duration_s, channels=channels)
    return result or {"error": "Aucun sample collecté — vérifier la connexion série"}


def tool_detect_rc_mapping(
    duration_s: Annotated[float, _duration("Durée de la fenêtre d'observation en secondes")] = 30.0,
) -> dict:
    result = _get_bf().detect_rc_mapping(duration_s=duration_s)
    return result or {"error": "Aucun sample collecté — vérifier la connexion série"}


def tool_detect_rc_channel_move(
    baseline:   RcBaseline,
    duration_s: Annotated[float, _duration("Durée d'observation en secondes")] = 5.0,
    threshold:  Annotated[int, Field(ge=1, description="Delta minimum en µs pour valider une détection")] = 300,
) -> dict:
    result = _get_bf().detect_rc_channel_move(
        baseline=baseline, duration_s=duration_s, threshold=threshold
    )
    return result or {"error": "Aucun sample collecté — vérifier la connexion série"}


def tool_get_motors() -> dict:
    result = _get_bf().get_motors()
    return result or {"error": "Impossible de lire les moteurs"}


# ── PID ───────────────────────────────────────────────────────────────

def tool_get_pid_values() -> dict:
    result = _get_bf().get_pid_values()
    return result or {"error": "Impossible de lire les PID"}


def tool_set_pid_values(
    axis: Annotated[PidAxis, Field(description="Axe PID : roll, pitch, yaw, level (mode angle/horizon), mag")],
    p:    Annotated[int, _pid_gain("P")],
    i:    Annotated[int, _pid_gain("I")],
    d:    Annotated[int, _pid_gain("D")],
) -> dict:
    v = validate_pid(axis, p, i, d)
    if v["errors"]:
        return {"success": False, "errors": v["errors"]}
    failure = _guarded_write(lambda: _get_bf().set_pid_values({axis: {"p": p, "i": i, "d": d}}))
    if failure:
        return failure
    result = {"success": True, "axis": axis, "p": p, "i": i, "d": d}
    if v["warnings"]:
        result["warnings"] = v["warnings"]
    return result


# ── Rates ─────────────────────────────────────────────────────────────

def tool_get_rates() -> dict:
    result = _get_bf().get_rates()
    return result or {"error": "Impossible de lire les rates"}


def tool_set_rates(
    roll_rc_rate:  Annotated[Optional[float], _rate_field("roll", "rc_rate")] = None,
    roll_rate:     Annotated[Optional[float], _rate_field("roll", "rate")] = None,
    roll_expo:     Annotated[Optional[float], _rate_field("roll", "expo")] = None,
    pitch_rc_rate: Annotated[Optional[float], _rate_field("pitch", "rc_rate")] = None,
    pitch_rate:    Annotated[Optional[float], _rate_field("pitch", "rate")] = None,
    pitch_expo:    Annotated[Optional[float], _rate_field("pitch", "expo")] = None,
    yaw_rc_rate:   Annotated[Optional[float], _rate_field("yaw", "rc_rate")] = None,
    yaw_rate:      Annotated[Optional[float], _rate_field("yaw", "rate")] = None,
    yaw_expo:      Annotated[Optional[float], _rate_field("yaw", "expo")] = None,
    throttle_mid:  Annotated[Optional[float], _throttle_field("Throttle mid")] = None,
    throttle_expo: Annotated[Optional[float], _throttle_field("Throttle expo")] = None,
) -> dict:
    updates = {name: value for name, value in {
        "roll_rc_rate": roll_rc_rate, "pitch_rc_rate": pitch_rc_rate, "yaw_rc_rate": yaw_rc_rate,
        "roll_rate":    roll_rate,    "pitch_rate":    pitch_rate,    "yaw_rate":    yaw_rate,
        "roll_expo":    roll_expo,    "pitch_expo":    pitch_expo,    "yaw_expo":    yaw_expo,
        "throttle_mid": throttle_mid, "throttle_expo": throttle_expo,
    }.items() if value is not None}
    if not updates:
        return {"success": False, "errors": ["Aucun paramètre de rates fourni"]}

    bf      = _get_bf()
    current = bf.get_rates()
    if not current:
        return {"success": False, "error": "Impossible de lire les rates actuels"}
    v = validate_rates(current, updates)
    if v["errors"]:
        return {"success": False, "rates_type": current["rates_type"], "errors": v["errors"]}
    failure = _guarded_write(lambda: bf.set_rates(updates, expected_rates_type=current["rates_type_id"]))
    if failure:
        return failure

    result = {"success": True, "rates_type": current["rates_type"], "updated": updates,
              "rates": bf.get_rates() or {"error": "Relecture des rates impossible"}}
    if v["warnings"]:
        result["warnings"] = v["warnings"]
    return result


# ── Modes / Features ──────────────────────────────────────────────────

def tool_get_modes() -> dict:
    result = _get_bf().get_modes()
    if result is None:
        return {"error": "Impossible de lire les modes RC"}
    return {"modes": result, "count": len(result)}


def tool_get_feature_config() -> dict:
    result = _get_bf().get_feature_config()
    return result or {"error": "Impossible de lire les features"}


# ── Réglages avancés ──────────────────────────────────────────────────

def tool_get_advanced_config() -> dict:
    result = _get_bf().get_advanced_config()
    return result or {"error": "Impossible de lire la config avancée"}


def tool_get_filter_config() -> dict:
    result = _get_bf().get_filter_config()
    return result or {"error": "Impossible de lire la config filtres"}


def tool_get_pid_advanced() -> dict:
    result = _get_bf().get_pid_advanced()
    return result or {"error": "Impossible de lire les PID avancés"}


def _settings_description(table) -> str:
    fields = "; ".join(f"{name} ({table.describe(table.field_for(name))})"
                       for name in table.WRITABLE_NAMES)
    return ("Réglages à modifier, noms CLI Betaflight 2025.12+ → valeur (entier, ou libellé "
            f"pour les énumérations). Champs : {fields}")


def tool_set_pid_advanced(
    settings: Annotated[dict[PidAdvancedName, Union[int, str]],
                        Field(min_length=1, description=_settings_description(pid_advanced))],
) -> dict:
    bf = _get_bf()
    v  = validate_pid_advanced(settings, bf.api_version)
    if v["errors"]:
        return {"success": False, "errors": v["errors"]}
    failure = _guarded_write(lambda: bf.set_pid_advanced(settings))
    if failure:
        return failure
    return {"success": True, "updated": settings,
            "pid_advanced": bf.get_pid_advanced() or {"error": "Relecture impossible"}}


def tool_set_filter_config(
    settings: Annotated[dict[FilterName, Union[int, str]],
                        Field(min_length=1, description=_settings_description(filter_config))],
) -> dict:
    bf = _get_bf()
    v  = validate_filter_config(settings, bf.api_version)
    if v["errors"]:
        return {"success": False, "errors": v["errors"]}
    failure = _guarded_write(lambda: bf.set_filter_config(settings))
    if failure:
        return failure
    return {"success": True, "updated": settings,
            "filter_config": bf.get_filter_config() or {"error": "Relecture impossible"}}


def tool_get_battery_config() -> dict:
    result = _get_bf().get_battery_config()
    return result or {"error": "Impossible de lire la config batterie"}


def tool_set_battery_config(
    settings: Annotated[dict[BatteryName, Union[int, str]],
                        Field(min_length=1, description=_settings_description(battery_config)
                              + ". Tensions en centièmes de volt (440 = 4.40 V).")],
) -> dict:
    bf      = _get_bf()
    current = bf.get_battery_config()
    if not current:
        return {"success": False, "error": "Impossible de lire la config batterie actuelle"}
    v = validate_battery_config(current, settings, bf.api_version)
    if v["errors"]:
        return {"success": False, "errors": v["errors"]}
    failure = _guarded_write(lambda: bf.set_battery_config(settings))
    if failure:
        return failure
    result = {"success": True, "updated": settings,
              "reboot_required": bool(battery_config.REBOOT_REQUIRED_FIELDS & set(settings)),
              "battery_config": bf.get_battery_config() or {"error": "Relecture impossible"}}
    if v["warnings"]:
        result["warnings"] = v["warnings"]
    return result


def tool_get_profiles() -> dict:
    result = _get_bf().get_profiles()
    return result or {"error": "Impossible de lire les profils actifs"}


def tool_select_profile(
    kind:  Annotated[ProfileKind, Field(description="Type de profil : pid, rate ou battery (Betaflight 2026.6+)")],
    index: Annotated[int, Field(ge=0, description="Index du profil (0 = premier ; nombre de profils selon le firmware)")],
) -> dict:
    bf = _get_bf()
    try:
        selected = bf.select_profile(kind, index)
    except WriteBlockedError as e:
        return {"success": False, "error": str(e)}
    profiles = bf.get_profiles() or {}
    if not selected:
        return {"success": False, "profiles": profiles,
                "error": (f"Profil {kind} {index} non activé : index hors plage, profil batterie "
                          "non supporté par ce firmware, ou changement refusé par le FC.")}
    return {"success": True, "profiles": profiles,
            "message": "Profil activé en RAM ; appeler save_config pour le garder au redémarrage."}


def tool_get_battery_profiles() -> dict:
    profiles = _get_bf().get_battery_profiles()
    if profiles is None:
        return {"error": "Profils batterie illisibles (Betaflight 2026.6+ / API 1.48 requis)"}
    return {"profiles": profiles, "count": len(profiles)}


def tool_set_battery_profile(
    index:    Annotated[int, Field(ge=0, le=battery_profiles.PROFILE_COUNT - 1,
                                   description="Index du profil batterie")],
    settings: Annotated[dict[BatteryProfileName, int],
                        Field(min_length=1, description=_settings_description(battery_profiles)
                              + ". Tensions en centièmes de volt ; ordre min <= warning <= full <= max.")],
) -> dict:
    bf      = _get_bf()
    current = bf.get_battery_profile(index)
    if not current:
        return {"success": False, "error": "Profil batterie illisible (Betaflight 2026.6+ / API 1.48 requis)"}
    v = validate_battery_profile(current, settings, bf.api_version)
    if v["errors"]:
        return {"success": False, "errors": v["errors"]}
    failure = _guarded_write(lambda: bf.set_battery_profile(index, settings))
    if failure:
        return failure
    return {"success": True, "updated": settings,
            "battery_profile": bf.get_battery_profile(index) or {"error": "Relecture impossible"}}


def tool_get_sensor_config() -> dict:
    result = _get_bf().get_sensor_config()
    return result or {"error": "Impossible de lire la config capteurs"}


# ── Sauvegarde / Reboot ───────────────────────────────────────────────

def tool_save_config() -> dict:
    failure = _guarded_write(_get_bf().save_config)
    if failure:
        return failure
    return {"success": True, "message": "Config sauvegardée en EEPROM"}


def tool_reboot_fc() -> dict:
    failure = _guarded_write(_get_bf().reboot_fc)
    if failure:
        return failure
    # Le port USB disparaît pendant le redémarrage : l'ancienne connexion est inutilisable
    _close_connection()
    return {"success": True,
            "message": "FC redémarré. Connexion fermée : attendre quelques secondes puis rappeler connect."}


# ── Registre MCP ─────────────────────────────────────────────────────

MCP_TOOLS = {
    "list_serial_ports": {
        "fn":          tool_list_serial_ports,
        "annotations": _READ_ONLY,
        "description": "Liste les ports série ; is_betaflight signale les FC Betaflight détectés (USB)",
    },
    "connect": {
        "fn":          tool_connect,
        "annotations": _CONNECTION,
        "description": (
            "Connecte le serveur au FC via port série (ferme la connexion précédente). "
            "Vérifie l'identité : réponse MSP, firmware Betaflight (BTFL), API >= 1.40 ; "
            "sinon échec et port refermé. Retourne api_version, fc_variant, fc_version."
        ),
    },
    "disconnect": {
        "fn":          tool_disconnect,
        "annotations": _CONNECTION,
        "description": "Ferme la connexion série vers le FC (après la requête MSP en cours)",
    },
    "get_board_info": {
        "fn":          tool_get_board_info,
        "annotations": _READ_ONLY,
        "description": "Retourne l'identité du FC : variante firmware (BTFL), version, carte, MCU, version API",
    },
    "get_fc_status": {
        "fn":          tool_get_fc_status,
        "annotations": _READ_ONLY,
        "description": "État général du FC : cycle time, capteurs actifs, flags arming, charge CPU",
    },
    "get_imu_data": {
        "fn":          tool_get_imu_data,
        "annotations": _READ_ONLY,
        "description": "Données IMU : accéléromètre (g), gyroscope (°/s), magnétomètre",
    },
    "get_attitude": {
        "fn":          tool_get_attitude,
        "annotations": _READ_ONLY,
        "description": "Attitude du drone : roulis, tangage, cap en degrés",
    },
    "get_altitude": {
        "fn":          tool_get_altitude,
        "annotations": _READ_ONLY,
        "description": "Altitude (m) et variomètre (cm/s) depuis le baromètre",
    },
    "get_battery": {
        "fn":          tool_get_battery,
        "annotations": _READ_ONLY,
        "description": "Tension batterie (V), courant (A), mAh consommés, RSSI",
    },
    "get_battery_state": {
        "fn":          tool_get_battery_state,
        "annotations": _READ_ONLY,
        "description": "État détaillé de la batterie : cellules, capacité, état (OK/WARNING/CRITICAL)",
    },
    "get_voltage_meters": {
        "fn":          tool_get_voltage_meters,
        "annotations": _READ_ONLY,
        "description": "Liste tous les voltmètres disponibles sur le FC",
    },
    "get_current_meters": {
        "fn":          tool_get_current_meters,
        "annotations": _READ_ONLY,
        "description": "Liste tous les ampèremètres disponibles sur le FC",
    },
    "get_rc": {
        "fn":          tool_get_rc,
        "annotations": _READ_ONLY,
        "description": "Valeurs actuelles des canaux RC (µs, typiquement 1000-2000)",
    },
    "snapshot_rc_delta": {
        "fn":          tool_snapshot_rc_delta,
        "annotations": _READ_ONLY,
        "description": (
            "Compare un snapshot RC courant à une baseline. "
            "Retourne les canaux dont le delta dépasse le seuil (défaut 200 µs). "
            "Utiliser pour détecter quel canal bouge quand l'utilisateur déplace un stick ou active un interrupteur."
        ),
    },
    "get_motors": {
        "fn":          tool_get_motors,
        "annotations": _READ_ONLY,
        "description": "Sorties moteurs actuelles (µs). 0 = moteur inactif",
    },
    "get_pid_values": {
        "fn":          tool_get_pid_values,
        "annotations": _READ_ONLY,
        "description": "Valeurs PID (P/I/D) pour chaque axe : roll, pitch, yaw, level, mag",
    },
    "set_pid_values": {
        "fn":          tool_set_pid_values,
        "annotations": _FC_WRITE,
        "description": (
            "Modifie les valeurs PID pour un axe (roll, pitch, yaw, level, mag). "
            "Les autres axes sont conservés. Appeler save_config ensuite."
        ),
    },
    "get_rates": {
        "fn":          tool_get_rates,
        "annotations": _READ_ONLY,
        "description": (
            "Rates RC par axe (roll, pitch, yaw) dans les unités du configurateur pour le "
            "rates_type actif (BETAFLIGHT, RACEFLIGHT, KISS, ACTUAL, QUICK) : rc_rate, rate, "
            "expo, libellés (labels), rate_limit_dps et max_rate_dps (vitesse à plein manche). "
            "Throttle mid/expo/limit/hover."
        ),
    },
    "set_rates": {
        "fn":          tool_set_rates,
        "annotations": _FC_WRITE,
        "description": (
            "Modifie les rates par axe. Seuls les paramètres fournis changent. "
            "Les valeurs sont dans les unités du rates_type ACTUEL du FC (appeler get_rates "
            "d'abord : ex. ACTUAL → rc_rate = Center Sensitivity en °/s, rate = Max Rate en °/s, "
            "expo 0-1 ; BETAFLIGHT → rc_rate 0-2.55, rate = super rate 0-1, expo 0-1). "
            "Retourne les valeurs relues et max_rate_dps. Appeler save_config ensuite."
        ),
    },
    "measure_rc_noise": {
        "fn":          tool_measure_rc_noise,
        "annotations": _READ_ONLY,
        "description": (
            "Poll les canaux RC pendant N secondes (~50 Hz) et retourne "
            "le bruit mesuré (95e percentile de déviation) ainsi qu'une valeur "
            "de deadband suggérée par canal. À utiliser sticks au repos."
        ),
    },
    "detect_rc_mapping": {
        "fn":          tool_detect_rc_mapping,
        "annotations": _READ_ONLY,
        "description": (
            "Mode passif : échantillonne tous les canaux RC pendant N secondes (~50 Hz) "
            "et classifie chacun — throttle (repos ~1000 µs), stick (centré ~1500 µs, "
            "grand débattement), switch_2pos, switch_3pos, unused. "
            "Identifie la convention TAER (throttle=ch0) ou AETR (throttle=ch2). "
            "roll/pitch/yaw restent ambigus : combiner avec detect_rc_channel_move. "
            "Demander à l'utilisateur de bouger tous les sticks et switches pendant la mesure."
        ),
    },
    "detect_rc_channel_move": {
        "fn":          tool_detect_rc_channel_move,
        "annotations": _READ_ONLY,
        "description": (
            "Mode guidé (une étape) : poll les canaux RC pendant duration_s secondes "
            "et retourne le canal dont le pic de delta depuis la baseline est le plus grand. "
            "Protocole : (1) get_rc pour obtenir la baseline au repos, "
            "(2) demander à l'utilisateur de bouger UN SEUL contrôle, "
            "(3) appeler cet outil — répéter pour chaque axe (roll, pitch, yaw, throttle). "
            "Plus fiable qu'un snapshot instantané car capture le pic sur toute la fenêtre."
        ),
    },
    "get_modes": {
        "fn":          tool_get_modes,
        "annotations": _READ_ONLY,
        "description": "Plages de modes RC actifs (AUX switches) : box_id, canal, min/max µs",
    },
    "get_feature_config": {
        "fn":          tool_get_feature_config,
        "annotations": _READ_ONLY,
        "description": "Features Betaflight activées (AIRMODE, LED_STRIP, GPS, etc.)",
    },
    "get_advanced_config": {
        "fn":          tool_get_advanced_config,
        "annotations": _READ_ONLY,
        "description": "Config avancée : dénominateurs gyro/PID, protocole ESC (DSHOT), PWM rate",
    },
    "get_filter_config": {
        "fn":          tool_get_filter_config,
        "annotations": _READ_ONLY,
        "description": (
            "Configuration des filtres, noms CLI : lowpass gyro/D-term statiques et dynamiques "
            "(types PT1/BIQUAD/PT2/PT3), notches, dyn notch, filtre RPM, yaw_lowpass_hz"
        ),
    },
    "set_filter_config": {
        "fn":          tool_set_filter_config,
        "annotations": _FC_WRITE,
        "description": (
            "Modifie des filtres (noms CLI, voir get_filter_config). Seuls les champs fournis "
            "changent. Filtres gyro globaux ; D-term et yaw_lowpass_hz du profil PID actif. "
            "Le firmware peut corriger des valeurs incohérentes : la réponse contient les "
            "valeurs relues. Betaflight 2025.12+ (API >= 1.47). Refusé si FC armé. "
            "Appeler save_config ensuite."
        ),
    },
    "get_pid_advanced": {
        "fn":          tool_get_pid_advanced,
        "annotations": _READ_ONLY,
        "description": (
            "Réglages PID avancés du profil actif, noms CLI : feedforward (f_roll/f_pitch/f_yaw, "
            "feedforward_*), D-max (d_max_* ; d_min_* avant Betaflight 2025.12), iterm relax, "
            "anti-gravity, acc_limit, angle_limit, TPA, throttle_boost, simplified_pids_mode"
        ),
    },
    "set_pid_advanced": {
        "fn":          tool_set_pid_advanced,
        "annotations": _FC_WRITE,
        "description": (
            "Modifie des réglages PID avancés du profil actif (noms CLI, voir get_pid_advanced). "
            "Seuls les champs fournis changent. simplified_pids_mode=OFF empêche le firmware de "
            "recalculer les PIDs depuis les curseurs (ex. au prochain batch CLI). "
            "Betaflight 2025.12+ (API >= 1.47) uniquement. Refusé si FC armé. "
            "Retourne les valeurs relues. Appeler save_config ensuite."
        ),
    },
    "get_battery_config": {
        "fn":          tool_get_battery_config,
        "annotations": _READ_ONLY,
        "description": (
            "Config batterie, noms CLI : vbat_min/warning/max_cell_voltage (0.01 V), "
            "bat_capacity (mAh), battery_meter et current_meter (source de mesure)"
        ),
    },
    "set_battery_config": {
        "fn":          tool_set_battery_config,
        "annotations": _FC_WRITE,
        "description": (
            "Modifie la config batterie (noms CLI, voir get_battery_config). Tensions en 0.01 V, "
            "ordre min <= warning <= max obligatoire. current_meter=NONE si le FC n'a pas de "
            "capteur de courant. Changer une source de mesure exige save_config puis reboot_fc "
            "(reboot_required dans la réponse). Betaflight 2025.12+. Refusé si FC armé."
        ),
    },
    "get_profiles": {
        "fn":          tool_get_profiles,
        "annotations": _READ_ONLY,
        "description": "Profils actifs : pid_profile (et pid_profile_count), rate_profile, battery_profile (Betaflight 2026.6+)",
    },
    "select_profile": {
        "fn":          tool_select_profile,
        "annotations": _FC_WRITE,
        "description": (
            "Active un profil PID, de rates ou batterie (2026.6+). Vérifié par relecture : le "
            "firmware ignore un index hors plage. Refusé si FC armé. Le choix n'est gardé au "
            "redémarrage qu'après save_config. Note : le firmware change seul de profil PID au "
            "branchement de la batterie selon auto_profile_cell_count (voir get_pid_advanced)."
        ),
    },
    "get_battery_profiles": {
        "fn":          tool_get_battery_profiles,
        "annotations": _READ_ONLY,
        "description": (
            "Les 3 profils batterie (Betaflight 2026.6+) : tensions cellule min/warning/full/max "
            "(0.01 V), capacité, force_battery_cell_count, cbat_alert_percent. Le nombre de "
            "cellules détecté vaut floor(tension / vbat_max_cell_voltage) + 1."
        ),
    },
    "set_battery_profile": {
        "fn":          tool_set_battery_profile,
        "annotations": _FC_WRITE,
        "description": (
            "Modifie un profil batterie par index (Betaflight 2026.6+), actif ou non. Seuls les "
            "champs fournis changent ; ordre min <= warning <= full <= max obligatoire. "
            "Refusé si FC armé. Appeler save_config ensuite."
        ),
    },
    "get_sensor_config": {
        "fn":          tool_get_sensor_config,
        "annotations": _READ_ONLY,
        "description": "Configuration des capteurs : accéléromètre, baromètre, magnétomètre",
    },
    "save_config": {
        "fn":          tool_save_config,
        "annotations": _FC_WRITE,
        "description": "Sauvegarde la configuration courante en EEPROM du FC (obligatoire après tout SET)",
    },
    "reboot_fc": {
        "fn":          tool_reboot_fc,
        "annotations": _FC_REBOOT,
        "description": (
            "Redémarre le FC (refusé si armé). La connexion est fermée : "
            "rappeler connect après quelques secondes."
        ),
    },
}
