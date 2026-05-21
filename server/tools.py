# ── Betaflight MCP Server ── MCP Tools Definitions ──────────────────
# Définition des tools exposés au LLM / Skill via MCP

from betaflight.serial_conn import SerialConnection
from betaflight.msp import MSPProtocol
from betaflight.commands import BetaflightCommands
from config.settings import SERIAL_PORT, BAUD_RATE, TIMEOUT

# ── Instances globales ────────────────────────────────────────────────
_conn     : SerialConnection   = None
_msp      : MSPProtocol        = None
_bf       : BetaflightCommands = None


def _get_bf() -> BetaflightCommands:
    """Retourne l'instance BetaflightCommands (lazy init)."""
    global _conn, _msp, _bf
    if _bf is None:
        raise RuntimeError("Non connecté au FC. Utilisez d'abord l'outil 'connect'.")
    return _bf


# ── Tool : list_serial_ports ──────────────────────────────────────────

def tool_list_serial_ports() -> dict:
    """
    MCP Tool : list_serial_ports
    Liste tous les ports série disponibles sur le système.
    """
    ports = SerialConnection.list_available_ports()
    return {"ports": ports, "count": len(ports)}


# ── Tool : connect ────────────────────────────────────────────────────

def tool_connect(port: str = SERIAL_PORT, baudrate: int = BAUD_RATE) -> dict:
    """
    MCP Tool : connect
    Ouvre la connexion série vers le Flight Controller.
    """
    global _conn, _msp, _bf
    _conn = SerialConnection(port=port, baudrate=baudrate, timeout=TIMEOUT)
    if _conn.connect():
        _msp = MSPProtocol(_conn)
        _bf  = BetaflightCommands(_msp)
        return {"success": True, "message": f"Connecté sur {port} @ {baudrate}"}
    return {"success": False, "message": f"Impossible de se connecter sur {port}"}


# ── Tool : disconnect ─────────────────────────────────────────────────

def tool_disconnect() -> dict:
    """MCP Tool : disconnect"""
    global _conn, _msp, _bf
    if _conn:
        _conn.disconnect()
        _conn, _msp, _bf = None, None, None
    return {"success": True, "message": "Déconnecté"}


# ── Tool : get_fc_status ──────────────────────────────────────────────

def tool_get_fc_status() -> dict:
    """MCP Tool : get_fc_status — Retourne l'état général du FC."""
    result = _get_bf().get_fc_status()
    if result is None:
        return {"error": "Impossible de lire le status FC"}
    return result


# ── Tool : get_imu_data ───────────────────────────────────────────────

def tool_get_imu_data() -> dict:
    """MCP Tool : get_imu_data — Données gyroscope et accéléromètre."""
    result = _get_bf().get_imu_data()
    if result is None:
        return {"error": "Impossible de lire les données IMU"}
    return result


# ── Tool : get_battery ────────────────────────────────────────────────

def tool_get_battery() -> dict:
    """MCP Tool : get_battery — Tension batterie, courant, mAh."""
    result = _get_bf().get_battery()
    if result is None:
        return {"error": "Impossible de lire la batterie"}
    return result


# ── Tool : get_pid_values ─────────────────────────────────────────────

def tool_get_pid_values() -> dict:
    """MCP Tool : get_pid_values — Lit les PID pour chaque axe."""
    result = _get_bf().get_pid_values()
    if result is None:
        return {"error": "Impossible de lire les PID"}
    return result


# ── Tool : set_pid_values ─────────────────────────────────────────────

def tool_set_pid_values(axis: str, p: int, i: int, d: int) -> dict:
    """
    MCP Tool : set_pid_values
    Paramètres : axis (roll/pitch/yaw), p, i, d (entiers 0-255)
    """
    pid_dict = {axis: {"p": p, "i": i, "d": d}}
    success  = _get_bf().set_pid_values(pid_dict)
    return {"success": success, "axis": axis, "p": p, "i": i, "d": d}


# ── Tool : get_rates ──────────────────────────────────────────────────

def tool_get_rates() -> dict:
    """MCP Tool : get_rates — Lit les rates RC."""
    result = _get_bf().get_rates()
    if result is None:
        return {"error": "Impossible de lire les rates"}
    return result


# ── Tool : set_rates ─────────────────────────────────────────────────

def tool_set_rates(rc_rate: float = None, rc_expo: float = None,
                   roll_pitch_rate: float = None, yaw_rate: float = None,
                   dyn_thr_pid: float = None, throttle_mid: float = None,
                   throttle_expo: float = None) -> dict:
    """MCP Tool : set_rates — Modifie les rates RC (valeurs entre 0.0 et 1.0)."""
    updates = {k: v for k, v in {
        "rc_rate": rc_rate, "rc_expo": rc_expo,
        "roll_pitch_rate": roll_pitch_rate, "yaw_rate": yaw_rate,
        "dyn_thr_pid": dyn_thr_pid, "throttle_mid": throttle_mid,
        "throttle_expo": throttle_expo,
    }.items() if v is not None}
    success = _get_bf().set_rates(updates)
    return {"success": success, "updated": updates}


# ── Tool : get_modes ──────────────────────────────────────────────────

def tool_get_modes() -> dict:
    """MCP Tool : get_modes — Liste les plages de modes RC actifs."""
    result = _get_bf().get_modes()
    if result is None:
        return {"error": "Impossible de lire les modes RC"}
    return {"modes": result, "count": len(result)}


# ── Tool : save_config ────────────────────────────────────────────────

def tool_save_config() -> dict:
    """MCP Tool : save_config — Sauvegarde la configuration en EEPROM."""
    success = _get_bf().save_config()
    return {"success": success, "message": "Config sauvegardée en EEPROM"}


# ── Tool : reboot_fc ──────────────────────────────────────────────────

def tool_reboot_fc() -> dict:
    """MCP Tool : reboot_fc — Redémarre le Flight Controller."""
    success = _get_bf().reboot_fc()
    return {"success": success, "message": "FC redémarré"}


# ── Registre des tools MCP ────────────────────────────────────────────

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
            "port":     {"type": "string", "description": "Port série ex: /dev/ttyUSB0 ou COM3"},
            "baudrate": {"type": "integer", "description": "Baudrate, défaut 115200"},
        },
    },
    "disconnect": {
        "fn":          tool_disconnect,
        "description": "Ferme la connexion série vers le FC",
        "parameters":  {},
    },
    "get_fc_status": {
        "fn":          tool_get_fc_status,
        "description": "Retourne l'état général du Flight Controller (cycle time, sensors, flags)",
        "parameters":  {},
    },
    "get_imu_data": {
        "fn":          tool_get_imu_data,
        "description": "Retourne les données gyroscope et accéléromètre",
        "parameters":  {},
    },
    "get_battery": {
        "fn":          tool_get_battery,
        "description": "Retourne la tension batterie, le courant et les mAh consommés",
        "parameters":  {},
    },
    "get_pid_values": {
        "fn":          tool_get_pid_values,
        "description": "Lit les valeurs PID (P/I/D) pour chaque axe du FC",
        "parameters":  {},
    },
    "set_pid_values": {
        "fn":          tool_set_pid_values,
        "description": "Modifie les valeurs PID pour un axe (roll, pitch ou yaw)",
        "parameters": {
            "axis": {"type": "string",  "description": "Axe : roll, pitch ou yaw"},
            "p":    {"type": "integer", "description": "Valeur P (0-255)"},
            "i":    {"type": "integer", "description": "Valeur I (0-255)"},
            "d":    {"type": "integer", "description": "Valeur D (0-255)"},
        },
        "required": ["axis", "p", "i", "d"],
    },
    "get_rates": {
        "fn":          tool_get_rates,
        "description": "Lit les rates RC (rc_rate, expo, yaw_rate...)",
        "parameters":  {},
    },
    "set_rates": {
        "fn":          tool_set_rates,
        "description": "Modifie un ou plusieurs rates RC (valeurs float entre 0.0 et 1.0). Seuls les paramètres fournis sont mis à jour.",
        "parameters": {
            "rc_rate":         {"type": "number", "description": "RC rate global (0.0-1.0)"},
            "rc_expo":         {"type": "number", "description": "RC expo (0.0-1.0)"},
            "roll_pitch_rate": {"type": "number", "description": "Rate roll/pitch (0.0-1.0)"},
            "yaw_rate":        {"type": "number", "description": "Rate yaw (0.0-1.0)"},
            "dyn_thr_pid":     {"type": "number", "description": "Dynamic throttle PID (0.0-1.0)"},
            "throttle_mid":    {"type": "number", "description": "Throttle mid (0.0-1.0)"},
            "throttle_expo":   {"type": "number", "description": "Throttle expo (0.0-1.0)"},
        },
    },
    "get_modes": {
        "fn":          tool_get_modes,
        "description": "Retourne la liste des plages de modes RC actifs (AUX switches)",
        "parameters":  {},
    },
    "save_config": {
        "fn":          tool_save_config,
        "description": "Sauvegarde la configuration courante en EEPROM du FC",
        "parameters":  {},
    },
    "reboot_fc": {
        "fn":          tool_reboot_fc,
        "description": "Redémarre le Flight Controller",
        "parameters":  {},
    },
}
