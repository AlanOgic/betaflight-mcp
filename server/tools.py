from betaflight.serial_conn import SerialConnection
from betaflight.msp import MSPProtocol
from betaflight.commands import BetaflightCommands
from config.settings import SERIAL_PORT, BAUD_RATE, TIMEOUT

_conn: SerialConnection   = None
_msp:  MSPProtocol        = None
_bf:   BetaflightCommands = None


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


def tool_get_motors() -> dict:
    result = _get_bf().get_motors()
    return result or {"error": "Impossible de lire les moteurs"}


# ── PID ───────────────────────────────────────────────────────────────

def tool_get_pid_values() -> dict:
    result = _get_bf().get_pid_values()
    return result or {"error": "Impossible de lire les PID"}


def tool_set_pid_values(axis: str, p: int, i: int, d: int) -> dict:
    success = _get_bf().set_pid_values({axis: {"p": p, "i": i, "d": d}})
    return {"success": success, "axis": axis, "p": p, "i": i, "d": d}


# ── Rates ─────────────────────────────────────────────────────────────

def tool_get_rates() -> dict:
    result = _get_bf().get_rates()
    return result or {"error": "Impossible de lire les rates"}


def tool_set_rates(
    rc_rate:     float = None,
    rc_expo:     float = None,
    roll_rate:   float = None,
    pitch_rate:  float = None,
    yaw_rate:    float = None,
    throttle_mid:  float = None,
    throttle_expo: float = None,
    yaw_expo:      float = None,
    pitch_expo:    float = None,
) -> dict:
    updates = {k: v for k, v in {
        "rc_rate": rc_rate, "rc_expo": rc_expo,
        "roll_rate": roll_rate, "pitch_rate": pitch_rate, "yaw_rate": yaw_rate,
        "throttle_mid": throttle_mid, "throttle_expo": throttle_expo,
        "yaw_expo": yaw_expo, "pitch_expo": pitch_expo,
    }.items() if v is not None}
    success = _get_bf().set_rates(updates)
    return {"success": success, "updated": updates}


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
    success = _get_bf().save_config()
    return {"success": success, "message": "Config sauvegardée en EEPROM"}


def tool_reboot_fc() -> dict:
    success = _get_bf().reboot_fc()
    return {"success": success, "message": "FC redémarré"}


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
    "get_motors": {
        "fn":          tool_get_motors,
        "description": "Sorties moteurs actuelles (µs). 0 = moteur inactif",
        "parameters":  {},
    },
    "get_pid_values": {
        "fn":          tool_get_pid_values,
        "description": "Valeurs PID (P/I/D) pour chaque axe : roll, pitch, yaw...",
        "parameters":  {},
    },
    "set_pid_values": {
        "fn":          tool_set_pid_values,
        "description": "Modifie les valeurs PID pour un axe. Appeler save_config ensuite.",
        "parameters": {
            "axis": {"type": "string",  "description": "Axe : roll, pitch, yaw, level..."},
            "p":    {"type": "integer", "description": "Valeur P (0-255)"},
            "i":    {"type": "integer", "description": "Valeur I (0-255)"},
            "d":    {"type": "integer", "description": "Valeur D (0-255)"},
        },
        "required": ["axis", "p", "i", "d"],
    },
    "get_rates": {
        "fn":          tool_get_rates,
        "description": "Rates RC : rc_rate, expo, roll/pitch/yaw rate, throttle, limites",
        "parameters":  {},
    },
    "set_rates": {
        "fn":          tool_set_rates,
        "description": "Modifie les rates RC. Seuls les paramètres fournis sont mis à jour. Appeler save_config ensuite.",
        "parameters": {
            "rc_rate":       {"type": "number", "description": "RC rate global roll/pitch (0.0-1.0)"},
            "rc_expo":       {"type": "number", "description": "Expo roll/pitch (0.0-1.0)"},
            "roll_rate":     {"type": "number", "description": "Superrate roll (0.0-1.0)"},
            "pitch_rate":    {"type": "number", "description": "Superrate pitch (0.0-1.0)"},
            "yaw_rate":      {"type": "number", "description": "Superrate yaw (0.0-1.0)"},
            "throttle_mid":  {"type": "number", "description": "Throttle mid (0.0-1.0)"},
            "throttle_expo": {"type": "number", "description": "Throttle expo (0.0-1.0)"},
            "yaw_expo":      {"type": "number", "description": "Expo yaw (0.0-1.0)"},
            "pitch_expo":    {"type": "number", "description": "Expo pitch (0.0-1.0)"},
        },
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
