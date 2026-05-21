# ── Betaflight MCP Server ── High-level Commands ────────────────────
# Commandes haut niveau utilisant MSPProtocol

import struct
import logging
from typing import Optional
from .msp import MSPProtocol, MSPCommand

logger = logging.getLogger(__name__)


class BetaflightCommands:
    """
    Commandes haut niveau exposées aux tools MCP.
    Chaque méthode correspond à un ou plusieurs échanges MSP.
    """

    def __init__(self, msp: MSPProtocol):
        self.msp = msp

    # ── Status ────────────────────────────────────────────────────────

    def get_fc_status(self) -> Optional[dict]:
        """
        MSP_STATUS (101)
        Retourne : cycle_time, i2c_errors, sensors, flags, current_profile
        """
        resp = self.msp.request(MSPCommand.MSP_STATUS)
        if not resp or len(resp["payload"]) < 11:
            return None
        p = resp["payload"]
        cycle_time, i2c_errors, sensors, flags, profile = struct.unpack_from("<HHHIB", p)
        return {
            "cycle_time":  cycle_time,
            "i2c_errors":  i2c_errors,
            "sensors":     sensors,
            "flags":       flags,
            "profile":     profile,
        }

    # ── IMU ───────────────────────────────────────────────────────────

    def get_imu_data(self) -> Optional[dict]:
        """
        MSP_RAW_IMU (102)
        Retourne : gyro (x,y,z), accel (x,y,z), mag (x,y,z)
        """
        resp = self.msp.request(MSPCommand.MSP_RAW_IMU)
        if not resp or len(resp["payload"]) < 18:
            return None
        vals = struct.unpack_from("<9h", resp["payload"])
        return {
            "gyro":  {"x": vals[0], "y": vals[1], "z": vals[2]},
            "accel": {"x": vals[3], "y": vals[4], "z": vals[5]},
            "mag":   {"x": vals[6], "y": vals[7], "z": vals[8]},
        }

    # ── Batterie ──────────────────────────────────────────────────────

    def get_battery(self) -> Optional[dict]:
        """
        MSP_ANALOG (110)
        Retourne : voltage (V), mah_drawn, rssi, amperage (A)
        """
        resp = self.msp.request(MSPCommand.MSP_ANALOG)
        if not resp or len(resp["payload"]) < 7:
            return None
        p = resp["payload"]
        voltage  = p[0] / 10.0
        mah      = struct.unpack_from("<H", p, 1)[0]
        rssi     = struct.unpack_from("<H", p, 3)[0]
        amperage = struct.unpack_from("<H", p, 5)[0] / 100.0
        return {
            "voltage_v":  voltage,
            "mah_drawn":  mah,
            "rssi":       rssi,
            "amperage_a": amperage,
        }

    # ── PID ───────────────────────────────────────────────────────────

    def get_pid_values(self) -> Optional[dict]:
        """
        MSP_PID (112)
        Retourne les valeurs P/I/D pour chaque axe (roll, pitch, yaw, ...)
        """
        resp = self.msp.request(MSPCommand.MSP_PID)
        if not resp:
            return None
        payload = resp["payload"]
        axes    = ["roll", "pitch", "yaw", "alt", "pos", "posr", "navr", "level", "mag", "vel"]
        result  = {}
        for i, axis in enumerate(axes):
            offset = i * 3
            if offset + 2 < len(payload):
                result[axis] = {
                    "p": payload[offset],
                    "i": payload[offset + 1],
                    "d": payload[offset + 2],
                }
        return result

    def set_pid_values(self, pid_dict: dict) -> bool:
        """
        MSP_SET_PID (202)
        pid_dict : { 'roll': {'p':42,'i':40,'d':30}, 'pitch': {...}, ... }
        """
        axes   = ["roll", "pitch", "yaw", "alt", "pos", "posr", "navr", "level", "mag", "vel"]
        # Lire d'abord les valeurs actuelles
        current = self.get_pid_values()
        if not current:
            return False
        # Fusionner avec les nouvelles valeurs
        current.update(pid_dict)
        payload = b''
        for axis in axes:
            vals     = current.get(axis, {"p": 0, "i": 0, "d": 0})
            payload += bytes([vals["p"], vals["i"], vals["d"]])
        self.msp.send_command(MSPCommand.MSP_SET_PID, payload)
        return True

    # ── Rates ─────────────────────────────────────────────────────────

    def get_rates(self) -> Optional[dict]:
        """MSP_RC_TUNING (111)"""
        resp = self.msp.request(MSPCommand.MSP_RC_TUNING)
        if not resp or len(resp["payload"]) < 7:
            return None
        p = resp["payload"]
        return {
            "rc_rate":        p[0] / 100.0,
            "rc_expo":        p[1] / 100.0,
            "roll_pitch_rate": p[2] / 100.0,
            "yaw_rate":       p[3] / 100.0,
            "dyn_thr_pid":    p[4] / 100.0,
            "throttle_mid":   p[5] / 100.0,
            "throttle_expo":  p[6] / 100.0,
        }

    def set_rates(self, rates: dict) -> bool:
        """
        MSP_SET_RC_TUNING (204)
        rates : dict avec les mêmes clés que get_rates (valeurs float)
        """
        current = self.get_rates()
        if not current:
            return False
        current.update(rates)
        payload = bytes([
            int(current["rc_rate"]         * 100),
            int(current["rc_expo"]         * 100),
            int(current["roll_pitch_rate"] * 100),
            int(current["yaw_rate"]        * 100),
            int(current["dyn_thr_pid"]     * 100),
            int(current["throttle_mid"]    * 100),
            int(current["throttle_expo"]   * 100),
        ])
        self.msp.send_command(MSPCommand.MSP_SET_RC_TUNING, payload)
        return True

    def get_modes(self) -> Optional[list]:
        """
        MSP_MODE_RANGES (34)
        Retourne la liste des plages de modes RC actifs.
        Chaque entrée : { box_id, aux_channel, min, max }
        Format : 6 bytes par range [box_id:1][aux:1][min:2][max:2]
        """
        resp = self.msp.request(MSPCommand.MSP_MODE_RANGES)
        if not resp:
            return None
        payload = resp["payload"]
        modes   = []
        for offset in range(0, len(payload) - 5, 6):
            box_id, aux = payload[offset], payload[offset + 1]
            min_val     = struct.unpack_from("<H", payload, offset + 2)[0]
            max_val     = struct.unpack_from("<H", payload, offset + 4)[0]
            modes.append({
                "box_id":      box_id,
                "aux_channel": aux,
                "min":         min_val,
                "max":         max_val,
            })
        return modes

    # ── Sauvegarde / Reboot ───────────────────────────────────────────

    def save_config(self) -> bool:
        """MSP_EEPROM_WRITE (250) — Sauvegarde la config en EEPROM."""
        self.msp.send_command(MSPCommand.MSP_EEPROM_WRITE)
        logger.info("Config sauvegardée en EEPROM")
        return True

    def reboot_fc(self) -> bool:
        """MSP_REBOOT (68) — Redémarre le Flight Controller."""
        self.msp.send_command(MSPCommand.MSP_REBOOT)
        logger.info("FC redémarré")
        return True
