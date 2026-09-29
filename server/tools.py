from typing import Optional

from betaflight.serial_conn import SerialConnection
from betaflight.msp import MSPProtocol
from betaflight.commands import BetaflightCommands, RC_CHANNEL_NAMES, WriteBlockedError
from config.settings import SERIAL_PORT, BAUD_RATE, TIMEOUT
from server.validators import validate_pid, validate_rates

_conn: SerialConnection   = None
_msp:  MSPProtocol        = None
_bf:   BetaflightCommands = None


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


def tool_connect(port: str = SERIAL_PORT, baudrate: int = BAUD_RATE) -> dict:
    global _conn, _msp, _bf
    _conn = SerialConnection(port=port, baudrate=baudrate, timeout=TIMEOUT)
    if _conn.connect():
        _msp = MSPProtocol(_conn)
        _bf  = BetaflightCommands(_msp)
        # Récupère la version API pour le parsing conditionnel
        _bf.get_api_version()
        return {"success": True, "message": f"Connecté sur {port} @ {baudrate}",
                "api_version": ".".join(str(v) for v in _bf.api_version)}
    return {"success": False, "message": f"Impossible de se connecter sur {port}"}


def tool_disconnect() -> dict:
    global _conn, _msp, _bf
    if _conn:
        _conn.disconnect()
        _conn = _msp = _bf = None
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


def tool_snapshot_rc_delta(baseline: list, threshold: int = 200) -> dict:
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


def tool_measure_rc_noise(duration_s: float = 3.0, channels: list = None) -> dict:
    result = _get_bf().measure_rc_noise(duration_s=duration_s, channels=channels)
    return result or {"error": "Aucun sample collecté — vérifier la connexion série"}


def tool_detect_rc_mapping(duration_s: float = 30.0) -> dict:
    result = _get_bf().detect_rc_mapping(duration_s=duration_s)
    return result or {"error": "Aucun sample collecté — vérifier la connexion série"}


def tool_detect_rc_channel_move(baseline: list, duration_s: float = 5.0, threshold: int = 300) -> dict:
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


def tool_set_pid_values(axis: str, p: int, i: int, d: int) -> dict:
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
    roll_rc_rate:  float = None,
    pitch_rc_rate: float = None,
    yaw_rc_rate:   float = None,
    roll_rate:     float = None,
    pitch_rate:    float = None,
    yaw_rate:      float = None,
    roll_expo:     float = None,
    pitch_expo:    float = None,
    yaw_expo:      float = None,
    throttle_mid:  float = None,
    throttle_expo: float = None,
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
    return {"success": True, "message": "FC redémarré"}


# ── Registre MCP ─────────────────────────────────────────────────────

MCP_TOOLS = {
    "list_serial_ports": {
        "fn":          tool_list_serial_ports,
        "description": "Liste tous les ports série disponibles sur le système",
        "parameters":  {},
    },
    "connect": {
        "fn":          tool_connect,
        "description": "Connecte le serveur MCP au Flight Controller Betaflight via port série",
        "parameters": {
            "port":     {"type": "string",  "description": "Port série ex: /dev/ttyUSB0 ou COM3"},
            "baudrate": {"type": "integer", "description": "Baudrate, défaut 115200"},
        },
    },
    "disconnect": {
        "fn":          tool_disconnect,
        "description": "Ferme la connexion série vers le FC",
        "parameters":  {},
    },
    "get_board_info": {
        "fn":          tool_get_board_info,
        "description": "Retourne l'identité du FC : variante firmware (BTFL), version, carte, MCU, version API",
        "parameters":  {},
    },
    "get_fc_status": {
        "fn":          tool_get_fc_status,
        "description": "État général du FC : cycle time, capteurs actifs, flags arming, charge CPU",
        "parameters":  {},
    },
    "get_imu_data": {
        "fn":          tool_get_imu_data,
        "description": "Données IMU : accéléromètre (g), gyroscope (°/s), magnétomètre",
        "parameters":  {},
    },
    "get_attitude": {
        "fn":          tool_get_attitude,
        "description": "Attitude du drone : roulis, tangage, cap en degrés",
        "parameters":  {},
    },
    "get_altitude": {
        "fn":          tool_get_altitude,
        "description": "Altitude (m) et variomètre (cm/s) depuis le baromètre",
        "parameters":  {},
    },
    "get_battery": {
        "fn":          tool_get_battery,
        "description": "Tension batterie (V), courant (A), mAh consommés, RSSI",
        "parameters":  {},
    },
    "get_battery_state": {
        "fn":          tool_get_battery_state,
        "description": "État détaillé de la batterie : cellules, capacité, état (OK/WARNING/CRITICAL)",
        "parameters":  {},
    },
    "get_voltage_meters": {
        "fn":          tool_get_voltage_meters,
        "description": "Liste tous les voltmètres disponibles sur le FC",
        "parameters":  {},
    },
    "get_current_meters": {
        "fn":          tool_get_current_meters,
        "description": "Liste tous les ampèremètres disponibles sur le FC",
        "parameters":  {},
    },
    "get_rc": {
        "fn":          tool_get_rc,
        "description": "Valeurs actuelles des canaux RC (µs, typiquement 1000-2000)",
        "parameters":  {},
    },
    "snapshot_rc_delta": {
        "fn":          tool_snapshot_rc_delta,
        "description": (
            "Compare un snapshot RC courant à une baseline. "
            "Retourne les canaux dont le delta dépasse le seuil (défaut 200 µs). "
            "Utiliser pour détecter quel canal bouge quand l'utilisateur déplace un stick ou active un interrupteur."
        ),
        "parameters": {
            "baseline":  {"type": "array",   "items": {"type": "integer"},
                          "description": "Valeurs au repos issues d'un get_rc précédent"},
            "threshold": {"type": "integer", "description": "Delta minimum en µs pour considérer un canal actif (défaut 200)"},
        },
        "required": ["baseline"],
    },
    "get_motors": {
        "fn":          tool_get_motors,
        "description": "Sorties moteurs actuelles (µs). 0 = moteur inactif",
        "parameters":  {},
    },
    "get_pid_values": {
        "fn":          tool_get_pid_values,
        "description": "Valeurs PID (P/I/D) pour chaque axe : roll, pitch, yaw, level, mag",
        "parameters":  {},
    },
    "set_pid_values": {
        "fn":          tool_set_pid_values,
        "description": (
            "Modifie les valeurs PID pour un axe (roll, pitch, yaw, level, mag). "
            "Les autres axes sont conservés. Appeler save_config ensuite."
        ),
        "parameters": {
            "axis": {"type": "string",  "description": "Axe : roll, pitch, yaw, level (mode angle/horizon), mag"},
            "p":    {"type": "integer", "description": "Valeur P (0-250, PID_GAIN_MAX)"},
            "i":    {"type": "integer", "description": "Valeur I (0-250, PID_GAIN_MAX)"},
            "d":    {"type": "integer", "description": "Valeur D (0-250, PID_GAIN_MAX)"},
        },
        "required": ["axis", "p", "i", "d"],
    },
    "get_rates": {
        "fn":          tool_get_rates,
        "description": (
            "Rates RC par axe (roll, pitch, yaw) dans les unités du configurateur pour le "
            "rates_type actif (BETAFLIGHT, RACEFLIGHT, KISS, ACTUAL, QUICK) : rc_rate, rate, "
            "expo, libellés (labels), rate_limit_dps et max_rate_dps (vitesse à plein manche). "
            "Throttle mid/expo/limit/hover."
        ),
        "parameters":  {},
    },
    "set_rates": {
        "fn":          tool_set_rates,
        "description": (
            "Modifie les rates par axe. Seuls les paramètres fournis changent. "
            "Les valeurs sont dans les unités du rates_type ACTUEL du FC (appeler get_rates "
            "d'abord : ex. ACTUAL → rc_rate = Center Sensitivity en °/s, rate = Max Rate en °/s, "
            "expo 0-1 ; BETAFLIGHT → rc_rate 0-2.55, rate = super rate 0-1, expo 0-1). "
            "Retourne les valeurs relues et max_rate_dps. Appeler save_config ensuite."
        ),
        "parameters": {
            "roll_rc_rate":  {"type": "number", "description": "roll : RC Rate / Center Sensitivity selon le rates_type"},
            "roll_rate":     {"type": "number", "description": "roll : Super rate / Max Rate / Acro+ selon le rates_type"},
            "roll_expo":     {"type": "number", "description": "roll : Expo / RC Curve selon le rates_type"},
            "pitch_rc_rate": {"type": "number", "description": "pitch : RC Rate / Center Sensitivity selon le rates_type"},
            "pitch_rate":    {"type": "number", "description": "pitch : Super rate / Max Rate / Acro+ selon le rates_type"},
            "pitch_expo":    {"type": "number", "description": "pitch : Expo / RC Curve selon le rates_type"},
            "yaw_rc_rate":   {"type": "number", "description": "yaw : RC Rate / Center Sensitivity selon le rates_type"},
            "yaw_rate":      {"type": "number", "description": "yaw : Super rate / Max Rate / Acro+ selon le rates_type"},
            "yaw_expo":      {"type": "number", "description": "yaw : Expo / RC Curve selon le rates_type"},
            "throttle_mid":  {"type": "number", "description": "Throttle mid (0.0-1.0)"},
            "throttle_expo": {"type": "number", "description": "Throttle expo (0.0-1.0)"},
        },
    },
    "measure_rc_noise": {
        "fn":          tool_measure_rc_noise,
        "description": (
            "Poll les canaux RC pendant N secondes (~50 Hz) et retourne "
            "le bruit mesuré (95e percentile de déviation) ainsi qu'une valeur "
            "de deadband suggérée par canal. À utiliser sticks au repos."
        ),
        "parameters": {
            "duration_s": {"type": "number",  "description": "Durée de mesure en secondes (défaut 3.0)"},
            "channels":   {"type": "array", "items": {"type": "integer"},
                           "description": "Indices des canaux à mesurer (défaut : tous)"},
        },
    },
    "detect_rc_mapping": {
        "fn":          tool_detect_rc_mapping,
        "description": (
            "Mode passif : échantillonne tous les canaux RC pendant N secondes (~50 Hz) "
            "et classifie chacun — throttle (repos ~1000 µs), stick (centré ~1500 µs, "
            "grand débattement), switch_2pos, switch_3pos, unused. "
            "Identifie la convention TAER (throttle=ch0) ou AETR (throttle=ch2). "
            "roll/pitch/yaw restent ambigus : combiner avec detect_rc_channel_move. "
            "Demander à l'utilisateur de bouger tous les sticks et switches pendant la mesure."
        ),
        "parameters": {
            "duration_s": {"type": "number", "description": "Durée de la fenêtre en secondes (défaut 30.0)"},
        },
    },
    "detect_rc_channel_move": {
        "fn":          tool_detect_rc_channel_move,
        "description": (
            "Mode guidé (une étape) : poll les canaux RC pendant duration_s secondes "
            "et retourne le canal dont le pic de delta depuis la baseline est le plus grand. "
            "Protocole : (1) get_rc pour obtenir la baseline au repos, "
            "(2) demander à l'utilisateur de bouger UN SEUL contrôle, "
            "(3) appeler cet outil — répéter pour chaque axe (roll, pitch, yaw, throttle). "
            "Plus fiable qu'un snapshot instantané car capture le pic sur toute la fenêtre."
        ),
        "parameters": {
            "baseline":   {"type": "array", "items": {"type": "integer"},
                           "description": "Valeurs RC au repos issues de get_rc"},
            "duration_s": {"type": "number",  "description": "Durée d'observation en secondes (défaut 5.0)"},
            "threshold":  {"type": "integer", "description": "Delta minimum en µs pour valider une détection (défaut 300)"},
        },
        "required": ["baseline"],
    },
    "get_modes": {
        "fn":          tool_get_modes,
        "description": "Plages de modes RC actifs (AUX switches) : box_id, canal, min/max µs",
        "parameters":  {},
    },
    "get_feature_config": {
        "fn":          tool_get_feature_config,
        "description": "Features Betaflight activées (AIRMODE, LED_STRIP, GPS, etc.)",
        "parameters":  {},
    },
    "get_advanced_config": {
        "fn":          tool_get_advanced_config,
        "description": "Config avancée : dénominateurs gyro/PID, protocole ESC (DSHOT), PWM rate",
        "parameters":  {},
    },
    "get_filter_config": {
        "fn":          tool_get_filter_config,
        "description": "Configuration des filtres : gyro lowpass/notch, Dterm lowpass, RPM filter",
        "parameters":  {},
    },
    "get_pid_advanced": {
        "fn":          tool_get_pid_advanced,
        "description": "Réglages PID avancés : feedforward, anti-gravity, TPA, iterm relax, D-Max",
        "parameters":  {},
    },
    "get_sensor_config": {
        "fn":          tool_get_sensor_config,
        "description": "Configuration des capteurs : accéléromètre, baromètre, magnétomètre",
        "parameters":  {},
    },
    "save_config": {
        "fn":          tool_save_config,
        "description": "Sauvegarde la configuration courante en EEPROM du FC (obligatoire après tout SET)",
        "parameters":  {},
    },
    "reboot_fc": {
        "fn":          tool_reboot_fc,
        "description": "Redémarre le Flight Controller",
        "parameters":  {},
    },
}
