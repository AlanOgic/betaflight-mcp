import struct
import time
import logging
import statistics as _stats
from typing import Optional
from .msp import MSPProtocol
from .msp_codes import MSPCodes

logger = logging.getLogger(__name__)

_PID_AXES = ["roll", "pitch", "yaw", "alt", "pos", "posr", "navr", "level", "mag", "vel"]

RC_CHANNEL_NAMES = [
    "roll", "pitch", "yaw", "throttle",
    "aux1", "aux2", "aux3", "aux4", "aux5", "aux6",
    "aux7", "aux8", "aux9", "aux10", "aux11", "aux12", "aux13", "aux14",
]

_FEATURES = {
    0:  "RX_PPM",
    2:  "INFLIGHT_ACC_CAL",
    3:  "RX_SERIAL",
    4:  "MOTOR_STOP",
    5:  "SERVO_TILT",
    6:  "SOFTSERIAL",
    7:  "GPS",
    9:  "SONAR",
    10: "TELEMETRY",
    12: "3D",
    13: "RX_PARALLEL_PWM",
    14: "RX_MSP",
    15: "RSSI_ADC",
    16: "LED_STRIP",
    17: "DISPLAY",
    19: "CHANNEL_FORWARDING",
    20: "TRANSPONDER",
    21: "AIRMODE",
    25: "RX_SPI",
    27: "ESC_SENSOR",
    28: "ANTI_GRAVITY",
    29: "DYNAMIC_FILTER",
}

_BATTERY_STATES = {0: "OK", 1: "WARNING", 2: "CRITICAL", 3: "NOT_PRESENT", 4: "INIT"}


class _DataReader:
    """
    Lecteur séquentiel de payload MSP.
    Miroir des méthodes readU8/readU16/read16/readU32/read32 du configurateur JS.
    Retourne 0 si la lecture dépasse la fin (champs optionnels selon version API).
    """

    def __init__(self, data: bytes):
        self._d = data
        self._p = 0

    @property
    def remaining(self) -> int:
        return len(self._d) - self._p

    def can_read(self, n: int) -> bool:
        return self._p + n <= len(self._d)

    def read_u8(self) -> int:
        if not self.can_read(1):
            return 0
        v = self._d[self._p]; self._p += 1; return v

    def read_s8(self) -> int:
        v = self.read_u8()
        return v if v < 128 else v - 256

    def read_u16(self) -> int:
        if not self.can_read(2):
            return 0
        v = struct.unpack_from("<H", self._d, self._p)[0]; self._p += 2; return v

    def read_s16(self) -> int:
        if not self.can_read(2):
            return 0
        v = struct.unpack_from("<h", self._d, self._p)[0]; self._p += 2; return v

    def read_u32(self) -> int:
        if not self.can_read(4):
            return 0
        v = struct.unpack_from("<I", self._d, self._p)[0]; self._p += 4; return v

    def read_s32(self) -> int:
        if not self.can_read(4):
            return 0
        v = struct.unpack_from("<i", self._d, self._p)[0]; self._p += 4; return v

    def read_bytes(self, n: int) -> bytes:
        actual = min(n, self.remaining)
        data   = self._d[self._p:self._p + actual]; self._p += actual; return data

    def read_text(self) -> str:
        length = self.read_u8()
        return self.read_bytes(length).decode("utf-8", errors="replace")

    def skip(self, n: int) -> None:
        self._p = min(self._p + n, len(self._d))


class BetaflightCommands:
    """
    Commandes haut niveau exposées aux tools MCP.
    Cible Betaflight 4.x (API >= 1.40). Parsing fidèle à betaflight-configurator/MSPHelper.js.
    """

    def __init__(self, msp: MSPProtocol):
        self.msp         = msp
        self.api_version = (0, 0)  # (major, minor) rempli par get_api_version()

    def _api_gte(self, major: int, minor: int) -> bool:
        return self.api_version >= (major, minor)

    def _api_lt(self, major: int, minor: int) -> bool:
        return self.api_version < (major, minor)

    def _req(self, cmd: int, payload: bytes = b'') -> Optional[_DataReader]:
        resp = self.msp.request(cmd, payload)
        if not resp:
            return None
        return _DataReader(resp["payload"])

    # ── Identité FC ───────────────────────────────────────────────────

    def get_api_version(self) -> Optional[dict]:
        """MSP_API_VERSION (1) — Protocole MSP et version API du firmware."""
        d = self._req(MSPCodes.MSP_API_VERSION)
        if not d or d.remaining < 3:
            return None
        msp_proto = d.read_u8()
        major     = d.read_u8()
        minor     = d.read_u8()
        self.api_version = (major, minor)
        return {"msp_protocol_version": msp_proto, "api_version": f"{major}.{minor}.0"}

    def get_fc_variant(self) -> Optional[dict]:
        """MSP_FC_VARIANT (2) — Identifiant firmware (ex: 'BTFL')."""
        resp = self.msp.request(MSPCodes.MSP_FC_VARIANT)
        if not resp or len(resp["payload"]) < 4:
            return None
        return {"identifier": resp["payload"][:4].decode("ascii", errors="replace")}

    def get_fc_version(self) -> Optional[dict]:
        """MSP_FC_VERSION (3) — Version du firmware (ex: '4.4.0')."""
        d = self._req(MSPCodes.MSP_FC_VERSION)
        if not d or d.remaining < 3:
            return None
        major = d.read_u8()
        minor = d.read_u8()
        patch = d.read_u8()
        return {"version": f"{major}.{minor}.{patch}"}

    def get_board_info(self) -> Optional[dict]:
        """MSP_BOARD_INFO (4) — Identifiant carte, nom cible, MCU."""
        d = self._req(MSPCodes.MSP_BOARD_INFO)
        if not d or d.remaining < 6:
            return None
        result = {
            "identifier":          d.read_bytes(4).decode("ascii", errors="replace"),
            "board_version":       d.read_u16(),
            "board_type":          d.read_u8(),
            "target_capabilities": d.read_u8(),
            "target_name":         d.read_text(),
            "board_name":          d.read_text(),
            "manufacturer_id":     d.read_text(),
        }
        d.skip(32)  # signature
        result["mcu_type_id"] = d.read_u8()
        if d.remaining >= 1:
            result["configuration_state"] = d.read_u8()
        if d.remaining >= 2:
            result["sample_rate_hz"] = d.read_u16()
        return result

    # ── Status FC ─────────────────────────────────────────────────────

    def get_fc_status(self) -> Optional[dict]:
        """
        Essaie MSP_STATUS_EX (150) d'abord pour plus d'infos,
        puis repli sur MSP_STATUS (101).
        """
        resp = self.msp.request(MSPCodes.MSP_STATUS_EX)
        if resp and len(resp["payload"]) >= 15:
            return self._parse_status_ex(_DataReader(resp["payload"]))

        resp = self.msp.request(MSPCodes.MSP_STATUS)
        if not resp or len(resp["payload"]) < 11:
            return None
        return self._parse_status(_DataReader(resp["payload"]))

    def _parse_status(self, d: _DataReader) -> dict:
        return {
            "cycle_time":     d.read_u16(),
            "i2c_errors":     d.read_u16(),
            "active_sensors": d.read_u16(),
            "mode_flags":     d.read_u32(),
            "profile":        d.read_u8(),
            "source":         "MSP_STATUS",
        }

    def _parse_status_ex(self, d: _DataReader) -> dict:
        result = {
            "cycle_time":     d.read_u16(),
            "i2c_errors":     d.read_u16(),
            "active_sensors": d.read_u16(),
            "mode_flags":     d.read_u32(),
            "profile":        d.read_u8(),
            "cpu_load":       d.read_u16(),
            "num_profiles":   d.read_u8(),
            "rate_profile":   d.read_u8(),
            "source":         "MSP_STATUS_EX",
        }
        byte_count = d.read_u8()
        d.skip(byte_count)
        result["arming_disable_count"] = d.read_u8()
        result["arming_disable_flags"] = d.read_u32()
        result["config_state_flag"]    = d.read_u8()
        if d.remaining >= 2:
            result["cpu_temp_c"] = round(d.read_u16() / 10.0, 1)
        return result

    # ── IMU ───────────────────────────────────────────────────────────

    def get_imu_data(self) -> Optional[dict]:
        """
        MSP_RAW_IMU (102) — Accéléromètre, gyroscope, magnétomètre.
        Scaling : accel / 2048 → g, gyro * (4/16.4) → °/s (MPU6000 ±2000°/s).
        """
        d = self._req(MSPCodes.MSP_RAW_IMU)
        if not d or d.remaining < 18:
            return None
        return {
            "accel": {
                "x": round(d.read_s16() / 2048.0, 4),
                "y": round(d.read_s16() / 2048.0, 4),
                "z": round(d.read_s16() / 2048.0, 4),
            },
            "gyro": {
                "x": round(d.read_s16() * 4 / 16.4, 2),
                "y": round(d.read_s16() * 4 / 16.4, 2),
                "z": round(d.read_s16() * 4 / 16.4, 2),
            },
            "mag": {
                "x": d.read_s16(),
                "y": d.read_s16(),
                "z": d.read_s16(),
            },
        }

    # ── Attitude / Altitude ───────────────────────────────────────────

    def get_attitude(self) -> Optional[dict]:
        """MSP_ATTITUDE (108) — Roulis, tangage, cap (°)."""
        d = self._req(MSPCodes.MSP_ATTITUDE)
        if not d or d.remaining < 6:
            return None
        return {
            "roll_deg":  round(d.read_s16() / 10.0, 1),
            "pitch_deg": round(d.read_s16() / 10.0, 1),
            "yaw_deg":   d.read_s16(),
        }

    def get_altitude(self) -> Optional[dict]:
        """MSP_ALTITUDE (109) — Altitude (m) et variomètre (cm/s)."""
        d = self._req(MSPCodes.MSP_ALTITUDE)
        if not d or d.remaining < 4:
            return None
        result = {"altitude_m": round(d.read_s32() / 100.0, 2)}
        if d.remaining >= 2:
            result["variometer_cms"] = d.read_s16()
        return result

    # ── Batterie ──────────────────────────────────────────────────────

    def get_battery(self) -> Optional[dict]:
        """
        MSP_ANALOG (110) — Tension, courant, mAh, RSSI.
        La tension précise (0.01 V) en bytes 7-8 remplace la valeur legacy byte 0.
        """
        d = self._req(MSPCodes.MSP_ANALOG)
        if not d or d.remaining < 7:
            return None
        voltage_legacy = d.read_u8() / 10.0  # byte 0: 0.1 V
        mah_drawn      = d.read_u16()         # bytes 1-2
        rssi           = d.read_u16()         # bytes 3-4
        amperage       = d.read_s16() / 100.0 # bytes 5-6
        if d.remaining >= 2:
            voltage = d.read_u16() / 100.0    # bytes 7-8: 0.01 V (précis)
        else:
            voltage = voltage_legacy
        return {
            "voltage_v":  round(voltage, 2),
            "amperage_a": round(amperage, 2),
            "mah_drawn":  mah_drawn,
            "rssi":       rssi,
        }

    def get_battery_state(self) -> Optional[dict]:
        """MSP_BATTERY_STATE (130) — État détaillé de la batterie."""
        d = self._req(MSPCodes.MSP_BATTERY_STATE)
        if not d or d.remaining < 7:
            return None
        cell_count      = d.read_u8()
        capacity_mah    = d.read_u16()
        voltage_legacy  = d.read_u8() / 10.0
        mah_drawn       = d.read_u16()
        amperage        = d.read_u16() / 100.0
        battery_state   = d.read_u8()
        voltage         = d.read_u16() / 100.0 if d.remaining >= 2 else voltage_legacy
        return {
            "cell_count":    cell_count,
            "capacity_mah":  capacity_mah,
            "voltage_v":     round(voltage, 2),
            "mah_drawn":     mah_drawn,
            "amperage_a":    round(amperage, 2),
            "battery_state": _BATTERY_STATES.get(battery_state, battery_state),
        }

    def get_voltage_meters(self) -> Optional[list]:
        """MSP_VOLTAGE_METERS (128) — Tous les voltmètres disponibles."""
        d = self._req(MSPCodes.MSP_VOLTAGE_METERS)
        if not d:
            return None
        meters = []
        while d.remaining >= 2:
            meters.append({"id": d.read_u8(), "voltage_v": round(d.read_u8() / 10.0, 1)})
        return meters

    def get_current_meters(self) -> Optional[list]:
        """MSP_CURRENT_METERS (129) — Tous les ampèremètres disponibles."""
        d = self._req(MSPCodes.MSP_CURRENT_METERS)
        if not d:
            return None
        meters = []
        while d.remaining >= 5:
            meters.append({
                "id":         d.read_u8(),
                "mah_drawn":  d.read_u16(),
                "amperage_a": round(d.read_u16() / 1000.0, 3),
            })
        return meters

    # ── Canaux RC / Moteurs / Servos ──────────────────────────────────

    def get_rc(self) -> Optional[dict]:
        """MSP_RC (105) — Valeurs des canaux RC (µs, 1000-2000)."""
        d = self._req(MSPCodes.MSP_RC)
        if not d:
            return None
        channels = []
        while d.remaining >= 2:
            channels.append(d.read_u16())
        return {"channels": channels, "count": len(channels)}

    def measure_rc_noise(
        self,
        duration_s: float = 3.0,
        channels: Optional[list] = None,
    ) -> Optional[dict]:
        """
        Poll MSP_RC pendant duration_s secondes (~50 Hz) et calcule le bruit
        par canal. Retourne le 95e percentile de déviation + une suggestion de
        deadband (p95 + 5 µs) pour chaque canal demandé.
        """
        deadline = time.monotonic() + duration_s
        samples: list[list[int]] = []

        while time.monotonic() < deadline:
            rc = self.get_rc()
            if rc:
                samples.append(rc["channels"])
            time.sleep(0.02)  # 50 Hz — aligne sur le taux RC de Betaflight

        if not samples:
            return None

        n_ch = len(samples[0])
        active = channels if channels is not None else list(range(n_ch))

        result: dict = {"sample_count": len(samples), "channels": {}}

        for i in active:
            if i >= n_ch:
                continue
            vals = [s[i] for s in samples]
            center = _stats.median(vals)
            deviations = sorted(abs(v - center) for v in vals)
            p95 = deviations[int(len(deviations) * 0.95)]
            name = RC_CHANNEL_NAMES[i] if i < len(RC_CHANNEL_NAMES) else f"ch{i}"
            result["channels"][name] = {
                "channel": i,
                "center_us": round(center),
                "min_us": min(vals),
                "max_us": max(vals),
                "noise_p95_us": round(p95),
                "suggested_deadband": max(1, round(p95) + 5),
            }

        return result

    def get_motors(self) -> Optional[dict]:
        """MSP_MOTOR (104) — Sorties moteurs (µs, 0 si inactif)."""
        d = self._req(MSPCodes.MSP_MOTOR)
        if not d:
            return None
        motors = []
        while d.remaining >= 2:
            motors.append(d.read_u16())
        return {"motors": motors, "count": len(motors)}

    def get_servos(self) -> Optional[dict]:
        """MSP_SERVO (103) — Positions servos (µs)."""
        d = self._req(MSPCodes.MSP_SERVO)
        if not d:
            return None
        servos = []
        while d.remaining >= 2:
            servos.append(d.read_u16())
        return {"servos": servos, "count": len(servos)}

    # ── PID ───────────────────────────────────────────────────────────

    def get_pid_values(self) -> Optional[dict]:
        """MSP_PID (112) — Valeurs P/I/D par axe (3 bytes par axe)."""
        d = self._req(MSPCodes.MSP_PID)
        if not d:
            return None
        result = {}
        for axis in _PID_AXES:
            if d.remaining < 3:
                break
            result[axis] = {"p": d.read_u8(), "i": d.read_u8(), "d": d.read_u8()}
        return result

    def set_pid_values(self, pid_dict: dict) -> bool:
        """
        MSP_SET_PID (202) — Écriture P/I/D.
        pid_dict : { 'roll': {'p':42,'i':40,'d':30}, ... }
        """
        current = self.get_pid_values()
        if not current:
            return False
        current.update(pid_dict)
        payload = b''
        for axis in _PID_AXES:
            v = current.get(axis, {"p": 0, "i": 0, "d": 0})
            payload += bytes([v["p"], v["i"], v["d"]])
        self.msp.send_command(MSPCodes.MSP_SET_PID, payload)
        return True

    # ── Rates RC ──────────────────────────────────────────────────────

    def get_rates(self) -> Optional[dict]:
        """
        MSP_RC_TUNING (111) — Rates, expo, throttle.
        Format cible : API >= 1.45 (Betaflight 4.x).
        """
        d = self._req(MSPCodes.MSP_RC_TUNING)
        if not d or d.remaining < 7:
            return None
        result = {
            "rc_rate":    round(d.read_u8() / 100.0, 2),  # byte 0
            "rc_expo":    round(d.read_u8() / 100.0, 2),  # byte 1
            "roll_rate":  round(d.read_u8() / 100.0, 2),  # byte 2
            "pitch_rate": round(d.read_u8() / 100.0, 2),  # byte 3
            "yaw_rate":   round(d.read_u8() / 100.0, 2),  # byte 4
        }
        d.skip(1)  # byte 5 : était dynamic_THR_PID (obsolète >= 1.45)
        result["throttle_mid"]  = round(d.read_u8() / 100.0, 2)  # byte 6
        result["throttle_expo"] = round(d.read_u8() / 100.0, 2)  # byte 7
        d.skip(2)  # bytes 8-9 : était dynamic_THR_breakpoint (obsolète >= 1.45)
        result["yaw_expo"]      = round(d.read_u8() / 100.0, 2)  # byte 10
        result["rc_rate_yaw"]   = round(d.read_u8() / 100.0, 2)  # byte 11
        result["rc_rate_pitch"] = round(d.read_u8() / 100.0, 2)  # byte 12
        result["pitch_expo"]    = round(d.read_u8() / 100.0, 2)  # byte 13
        result["throttle_limit_type"]    = d.read_u8()           # byte 14
        result["throttle_limit_percent"] = d.read_u8()           # byte 15
        result["roll_rate_limit"]        = d.read_u16()          # bytes 16-17 (°/s)
        result["pitch_rate_limit"]       = d.read_u16()          # bytes 18-19
        result["yaw_rate_limit"]         = d.read_u16()          # bytes 20-21
        result["rates_type"]             = d.read_u8()           # byte 22
        if d.remaining >= 1:
            result["throttle_hover"] = round(d.read_u8() / 100.0, 2)  # API >= 1.47
        return result

    def set_rates(self, rates: dict) -> bool:
        """
        MSP_SET_RC_TUNING (204) — Écriture rates (read-modify-write).
        rates : dict avec les mêmes clés que get_rates().
        """
        current = self.get_rates()
        if not current:
            return False
        current.update(rates)
        payload = bytes([
            round(current.get("rc_rate",    0.0) * 100),
            round(current.get("rc_expo",    0.0) * 100),
            round(current.get("roll_rate",  0.0) * 100),
            round(current.get("pitch_rate", 0.0) * 100),
            round(current.get("yaw_rate",   0.0) * 100),
            0,  # obsolète depuis API 1.45
            round(current.get("throttle_mid",  0.5) * 100),
            round(current.get("throttle_expo", 0.0) * 100),
        ]) + struct.pack("<H", 0  # obsolète depuis API 1.45
        ) + bytes([
            round(current.get("yaw_expo",      0.0) * 100),
            round(current.get("rc_rate_yaw",   0.0) * 100),
            round(current.get("rc_rate_pitch", 0.0) * 100),
            round(current.get("pitch_expo",    0.0) * 100),
            current.get("throttle_limit_type",    0),
            current.get("throttle_limit_percent", 100),
        ]) + struct.pack("<HHH",
            current.get("roll_rate_limit",  1998),
            current.get("pitch_rate_limit", 1998),
            current.get("yaw_rate_limit",   1998),
        ) + bytes([current.get("rates_type", 0)])
        self.msp.send_command(MSPCodes.MSP_SET_RC_TUNING, payload)
        return True

    # ── Modes RC ──────────────────────────────────────────────────────

    def get_modes(self) -> Optional[list]:
        """
        MSP_MODE_RANGES (34) — Plages de modes RC (AUX switches).
        6 bytes par entrée : box_id(1) aux(1) min(2le) max(2le).
        """
        d = self._req(MSPCodes.MSP_MODE_RANGES)
        if not d:
            return None
        modes = []
        while d.remaining >= 6:
            modes.append({
                "box_id":      d.read_u8(),
                "aux_channel": d.read_u8(),
                "min":         d.read_u16(),
                "max":         d.read_u16(),
            })
        return modes

    # ── Features ──────────────────────────────────────────────────────

    def get_feature_config(self) -> Optional[dict]:
        """MSP_FEATURE_CONFIG (36) — Bitmask des features activées."""
        d = self._req(MSPCodes.MSP_FEATURE_CONFIG)
        if not d or d.remaining < 4:
            return None
        mask    = d.read_u32()
        enabled = [name for bit, name in _FEATURES.items() if mask & (1 << bit)]
        return {"mask": mask, "enabled": enabled}

    # ── Configuration avancée ─────────────────────────────────────────

    def get_advanced_config(self) -> Optional[dict]:
        """MSP_ADVANCED_CONFIG (90) — Dénominateurs gyro/PID, protocole ESC, PWM."""
        d = self._req(MSPCodes.MSP_ADVANCED_CONFIG)
        if not d or d.remaining < 6:
            return None
        result = {
            "gyro_sync_denom":  d.read_u8(),
            "pid_process_denom": d.read_u8(),
            "use_unsynced_pwm": bool(d.read_u8()),
            "fast_pwm_protocol": d.read_u8(),
            "motor_pwm_rate":   d.read_u16(),
        }
        if d.remaining >= 2:
            result["motor_idle_percent"] = round(d.read_u16() / 100.0, 1)
        if d.remaining >= 1:
            d.skip(1)  # gyroUse32Khz (deprecated)
        if d.remaining >= 1:
            result["motor_pwm_inversion"] = bool(d.read_u8())
        if d.remaining >= 1:
            result["gyro_to_use"] = d.read_u8()
        if d.remaining >= 1:
            result["gyro_high_fsr"] = bool(d.read_u8())
        if d.remaining >= 2:
            result["gyro_calib_duration"] = d.read_u16()
        if d.remaining >= 2:
            result["gyro_offset_yaw"] = d.read_s16()
        if d.remaining >= 1:
            result["gyro_check_overflow"] = bool(d.read_u8())
        if d.remaining >= 1:
            result["debug_mode"] = d.read_u8()
        if d.remaining >= 1:
            result["debug_mode_count"] = d.read_u8()
        return result

    def get_filter_config(self) -> Optional[dict]:
        """MSP_FILTER_CONFIG (92) — Configuration des filtres gyro/Dterm/notch/RPM."""
        d = self._req(MSPCodes.MSP_FILTER_CONFIG)
        if not d or d.remaining < 9:
            return None
        result = {
            "gyro_lowpass_hz":   d.read_u8(),    # legacy u8
            "dterm_lowpass_hz":  d.read_u16(),
            "yaw_lowpass_hz":    d.read_u16(),
            "gyro_notch_hz":     d.read_u16(),
            "gyro_notch_cutoff": d.read_u16(),
            "dterm_notch_hz":    d.read_u16(),
            "dterm_notch_cutoff": d.read_u16(),
            "gyro_notch2_hz":    d.read_u16(),
            "gyro_notch2_cutoff": d.read_u16(),
        }
        if d.remaining >= 1:
            result["dterm_lowpass_type"] = d.read_u8()
        if d.remaining >= 1:
            result["gyro_hardware_lpf"] = d.read_u8()
        d.skip(1)  # gyro_32khz_hardware_lpf (deprecated)
        if d.remaining >= 2:
            result["gyro_lowpass_hz"] = d.read_u16()   # u16 override (précis)
        if d.remaining >= 2:
            result["gyro_lowpass2_hz"] = d.read_u16()
        if d.remaining >= 1:
            result["gyro_lowpass_type"] = d.read_u8()
        if d.remaining >= 1:
            result["gyro_lowpass2_type"] = d.read_u8()
        if d.remaining >= 2:
            result["dterm_lowpass2_hz"] = d.read_u16()
        if d.remaining >= 1:
            result["dterm_lowpass2_type"] = d.read_u8()
        if d.remaining >= 2:
            result["gyro_lowpass_dyn_min_hz"] = d.read_u16()
        if d.remaining >= 2:
            result["gyro_lowpass_dyn_max_hz"] = d.read_u16()
        if d.remaining >= 2:
            result["dterm_lowpass_dyn_min_hz"] = d.read_u16()
        if d.remaining >= 2:
            result["dterm_lowpass_dyn_max_hz"] = d.read_u16()
        if d.remaining >= 1:
            result["dyn_notch_width_percent"] = d.read_u8()
        if d.remaining >= 2:
            result["dyn_notch_q"] = d.read_u16()
        if d.remaining >= 2:
            result["dyn_notch_min_hz"] = d.read_u16()
        if d.remaining >= 1:
            result["gyro_rpm_notch_harmonics"] = d.read_u8()
        if d.remaining >= 1:
            result["gyro_rpm_notch_min_hz"] = d.read_u8()
        if d.remaining >= 2:
            result["dyn_notch_max_hz"] = d.read_u16()
        if d.remaining >= 1:
            result["dyn_lpf_curve_expo"] = d.read_u8()
        if d.remaining >= 1:
            result["dyn_notch_count"] = d.read_u8()
        return result

    def get_pid_advanced(self) -> Optional[dict]:
        """MSP_PID_ADVANCED (94) — Réglages PID avancés (feedforward, anti-gravity, TPA...)."""
        d = self._req(MSPCodes.MSP_PID_ADVANCED)
        if not d or d.remaining < 17:
            return None
        d.skip(6)  # legacy: rollPitchItermIgnoreRate(2) yawItermIgnoreRate(2) yaw_p_limit(2)
        result = {
            "delta_method":           d.read_u8(),
            "vbat_pid_compensation":  bool(d.read_u8()),
            "feedforward_transition": d.read_u8(),
        }
        d.skip(1)  # dtermSetpointWeight (legacy, overridden below)
        d.skip(3)  # toleranceBand, toleranceBandReduction, itermThrottleGain
        d.skip(4)  # pidMaxVelocity(2), pidMaxVelocityYaw(2)
        result["level_angle_limit"] = d.read_u8()
        d.skip(1)  # levelSensitivity (deprecated)
        result["iterm_throttle_threshold"] = d.read_u16()
        result["anti_gravity_gain"]        = d.read_u16()  # API >= 1.45
        result["dterm_setpoint_weight"]    = d.read_u16()
        result["iterm_rotation"]           = bool(d.read_u8())
        d.skip(1)  # smartFeedforward (deprecated)
        result["iterm_relax"]      = d.read_u8()
        result["iterm_relax_type"] = d.read_u8()
        d.skip(1)  # absoluteControlGain (<1.48) deprecated
        result["throttle_boost"]          = d.read_u8()
        result["acro_trainer_angle_limit"] = d.read_u8()
        result["feedforward_roll"]  = d.read_u16()
        result["feedforward_pitch"] = d.read_u16()
        result["feedforward_yaw"]   = d.read_u16()
        result["anti_gravity_mode"] = d.read_u8()
        result["d_max_roll"]  = d.read_u8()
        result["d_max_pitch"] = d.read_u8()
        result["d_max_yaw"]   = d.read_u8()
        d.skip(2)  # dMaxGain, dMaxAdvance
        result["use_integrated_yaw"]    = bool(d.read_u8())
        result["integrated_yaw_relax"]  = d.read_u8()
        if d.remaining >= 1:
            result["iterm_relax_cutoff"] = d.read_u8()
        if d.remaining >= 1:
            result["motor_output_limit"] = d.read_u8()
        if d.remaining >= 2:
            d.skip(1)  # autoProfileCellCount (s8)
            result["idle_min_rpm"] = d.read_u8()
        if d.remaining >= 4:
            result["feedforward_averaging"]       = d.read_u8()
            result["feedforward_smooth_factor"]   = d.read_u8()
            result["feedforward_boost"]           = d.read_u8()
            result["feedforward_max_rate_limit"]  = d.read_u8()
        if d.remaining >= 1:
            result["feedforward_jitter_factor"] = d.read_u8()
        if d.remaining >= 1:
            result["vbat_sag_compensation"] = d.read_u8()
        if d.remaining >= 1:
            result["thrust_linearization"] = d.read_u8()
        if d.remaining >= 1:
            result["tpa_mode"] = d.read_u8()
        if d.remaining >= 1:
            result["tpa_rate"] = round(d.read_u8() / 100.0, 2)
        if d.remaining >= 2:
            result["tpa_breakpoint"] = d.read_u16()
        return result

    def get_sensor_config(self) -> Optional[dict]:
        """MSP_SENSOR_CONFIG (96) — Accéléromètre, baro, magnétomètre."""
        d = self._req(MSPCodes.MSP_SENSOR_CONFIG)
        if not d or d.remaining < 3:
            return None
        result = {
            "acc_hardware":  d.read_u8(),
            "baro_hardware": d.read_u8(),
            "mag_hardware":  d.read_u8(),
        }
        if d.remaining >= 1:
            result["sonar_hardware"] = d.read_u8()
        if d.remaining >= 1:
            result["opticalflow_hardware"] = d.read_u8()
        return result

    # ── Commandes moteurs / RC override ──────────────────────────────

    def set_motor(self, motors: list) -> bool:
        """
        MSP_SET_MOTOR (214) — Contrôle direct des moteurs (1000-2000 µs).
        ATTENTION : FC doit être en mode moteur test. Ne jamais utiliser moteurs armés.
        """
        if len(motors) < 8:
            motors = list(motors) + [1000] * (8 - len(motors))
        payload = struct.pack("<8H", *[max(0, min(2000, m)) for m in motors[:8]])
        self.msp.send_command(MSPCodes.MSP_SET_MOTOR, payload)
        return True

    def set_raw_rc(self, channels: list) -> bool:
        """
        MSP_SET_RAW_RC (200) — Override des canaux RC (µs).
        Nécessite la feature MSP_RC activée dans Betaflight.
        """
        payload = struct.pack(f"<{len(channels)}H", *channels)
        self.msp.send_command(MSPCodes.MSP_SET_RAW_RC, payload)
        return True

    # ── Sauvegarde / Reboot ───────────────────────────────────────────

    def save_config(self) -> bool:
        """MSP_EEPROM_WRITE (250) — Sauvegarde la config en EEPROM. Toujours appeler après un SET."""
        self.msp.send_command(MSPCodes.MSP_EEPROM_WRITE)
        logger.info("Config sauvegardée en EEPROM")
        return True

    def reboot_fc(self) -> bool:
        """MSP_SET_REBOOT (68) — Redémarre le Flight Controller."""
        self.msp.send_command(MSPCodes.MSP_SET_REBOOT)
        logger.info("FC redémarré")
        return True
