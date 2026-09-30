import struct
import time
import logging
import statistics as _stats
from typing import Optional
from .msp import MSPProtocol
from .msp_codes import MSPCodes
from . import rates as _rates
from . import pid_advanced as _pid_adv
from . import filter_config as _filters
from . import battery_config as _battery
from . import battery_profiles as _battery_profiles
from config.settings import EEPROM_TIMEOUT

logger = logging.getLogger(__name__)

# Ordre firmware 4.x (flight/pid.h, pidIndex_e) : PID_ITEM_COUNT = 5
PID_AXES            = ("roll", "pitch", "yaw", "level", "mag")
_PID_BYTES_PER_AXIS = 3  # P, I, D (u8 chacun)
PID_GAIN_MAX        = 250  # flight/pid.h

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

# Matrice de support : Betaflight uniquement (INAV & co. ont d'autres layouts MSP)
BETAFLIGHT_IDENTIFIER = "BTFL"
MIN_API_VERSION       = (1, 40)

# MSP_SELECT_SETTING : bit 7 = profil de rates, bit 6 = profil batterie (API 1.48)
_RATE_PROFILE_MASK    = 0x80
_BATTERY_PROFILE_MASK = 0x40
PROFILE_KINDS         = ("pid", "rate", "battery")

# BOXARM est toujours la première boîte active (msp_box.c) : bit 0 des mode flags
# de MSP_STATUS(_EX) = ARMING_FLAG(ARMED)
_ARM_MODE_BIT = 0


class WriteBlockedError(RuntimeError):
    """Écriture refusée avant envoi (FC armé ou état d'armement inconnu)."""

# Offsets des octets de MSP_RC_TUNING (msp.c). Les octets 5 et 8-9 sont obsolètes.
_RC_TUNING_OFFSETS: dict[str, int] = {
    "roll_rc_rate":  0,  "roll_expo":  1,  "roll_rate":  2,
    "pitch_rate":    3,  "yaw_rate":   4,
    "throttle_mid":  6,  "throttle_expo": 7,
    "yaw_expo":     10,  "yaw_rc_rate":  11,
    "pitch_rc_rate": 12, "pitch_expo":  13,
    "throttle_limit_type": 14, "throttle_limit_percent": 15,
    "roll_rate_limit": 16, "pitch_rate_limit": 18, "yaw_rate_limit": 20,  # u16
    "rates_type":   22,  # API >= 1.43
    "throttle_hover": 23,  # API >= 1.47
}
_RC_TUNING_THROTTLE_FIELDS = ("throttle_mid", "throttle_expo")
# Champs modifiables par set_rates (octets u8)
RATES_WRITABLE_FIELDS = tuple(
    f"{axis}_{field}" for axis in _rates.AXES for field in _rates.RATE_FIELDS
) + _RC_TUNING_THROTTLE_FIELDS


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
        """Lecture : None si pas de réponse ou si le FC a répondu par une erreur."""
        resp = self.msp.request(cmd, payload)
        if not resp or not resp["ok"]:
            return None
        return _DataReader(resp["payload"])

    def is_armed(self) -> Optional[bool]:
        """True si le FC est armé, None si l'état ne peut pas être lu."""
        status = self.get_fc_status()
        if not status:
            return None
        return bool(status["mode_flags"] & (1 << _ARM_MODE_BIT))

    def _require_disarmed(self) -> None:
        """
        Garde de sécurité avant toute écriture : lève WriteBlockedError si le FC est
        armé ou si son état est illisible (fail-closed). À appeler dans la transaction
        de l'écriture pour qu'aucune autre requête ne s'intercale.
        """
        armed = self.is_armed()
        if armed is None:
            raise WriteBlockedError(
                "État d'armement inconnu (MSP_STATUS illisible) : écriture refusée par sécurité."
            )
        if armed:
            raise WriteBlockedError("FC armé : écriture refusée. Désarmer le FC puis réessayer.")

    def _write(self, cmd: int, payload: bytes = b'', timeout: Optional[float] = None) -> bool:
        """Écriture : True uniquement si le FC a acquitté la commande."""
        resp = self.msp.request(cmd, payload, timeout=timeout)
        if not resp or not resp["ok"]:
            logger.warning("Commande MSP %d refusée ou sans réponse", cmd)
            return False
        return True

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
        d = self._req(MSPCodes.MSP_FC_VARIANT)
        if not d or d.remaining < 4:
            return None
        return {"identifier": d.read_bytes(4).decode("ascii", errors="replace")}

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
        d = self._req(MSPCodes.MSP_STATUS_EX)
        if d and d.remaining >= 15:
            return self._parse_status_ex(d)

        d = self._req(MSPCodes.MSP_STATUS)
        if not d or d.remaining < 11:
            return None
        return self._parse_status(d)

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
        amperage        = d.read_s16() / 100.0     # signé : ±320 A (msp.c)
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

    def detect_rc_mapping(self, duration_s: float = 30.0) -> Optional[dict]:
        """
        Échantillonne les canaux RC pendant duration_s secondes (~50 Hz).
        Classifie chaque canal (throttle / stick / switch_2pos / switch_3pos / unused)
        à partir du range et de la position médiane au repos.
        Devine la convention TAER/AETR depuis l'index du canal throttle.
        roll/pitch/yaw restent ambigus — utiliser detect_rc_channel_move pour lever l'ambiguïté.
        """
        deadline = time.monotonic() + duration_s
        samples: list[list[int]] = []

        while time.monotonic() < deadline:
            rc = self.get_rc()
            if rc:
                samples.append(rc["channels"])
            time.sleep(0.02)

        if not samples:
            return None

        n_ch = len(samples[0])
        channel_info = []

        for i in range(n_ch):
            vals = [s[i] for s in samples if i < len(s)]
            if not vals:
                continue
            lo, hi = min(vals), max(vals)
            rng = hi - lo
            center = round(_stats.median(vals))

            if rng < 30:
                role = "unused"
            elif rng >= 400:
                role = "throttle" if center < 1200 else "stick"
            else:
                # Nombre de positions distinctes (arrondi à 100 µs)
                positions = sorted(set(round(v / 100) * 100 for v in vals))
                role = "switch_3pos" if len(positions) >= 3 else "switch_2pos"

            name = RC_CHANNEL_NAMES[i] if i < len(RC_CHANNEL_NAMES) else f"ch{i}"
            channel_info.append({
                "index": i, "name": name, "role": role,
                "min_us": lo, "max_us": hi, "range_us": rng, "center_us": center,
            })

        throttle_chs = [c for c in channel_info if c["role"] == "throttle"]
        sticks       = [c for c in channel_info if c["role"] == "stick"]
        switches     = [c for c in channel_info if c["role"].startswith("switch")]

        # Conventions Betaflight par index du throttle (T=Throttle, A=Aileron/Roll,
        # E=Elevator/Pitch, R=Rudder/Yaw). Plusieurs conventions partagent le même
        # index throttle → on retourne tous les candidats.
        _CONVENTIONS_BY_THROTTLE_IDX: dict[int, list[str]] = {
            0: ["TAER1234"],
            1: ["ATEX"],           # rare, garde-fou
            2: ["AETR1234", "RETA1234"],
            3: ["AERT1234", "EART1234"],
        }

        mapping: dict = {}
        convention_candidates: list[str] = []

        if throttle_chs:
            t_idx = throttle_chs[0]["index"]
            mapping["throttle"] = t_idx
            convention_candidates = _CONVENTIONS_BY_THROTTLE_IDX.get(t_idx, [])

        for j, s in enumerate(sticks[:3]):
            mapping[f"stick_{j}"] = s["index"]

        for sw in switches:
            mapping[sw["name"]] = sw["index"]

        return {
            "sample_count": len(samples),
            "duration_s": duration_s,
            "convention_candidates": convention_candidates,
            "convention_ambiguous": len(convention_candidates) > 1,
            "mapping": mapping,
            "channels": {c["name"]: c for c in channel_info},
            "sticks_ambiguous": [s["index"] for s in sticks],
            "note": (
                "stick_0/1/2 = canaux à grand débattement centrés à 1500 µs, "
                "mais roll/pitch/yaw sont indiscernables passivement. "
                "Appeler detect_rc_channel_move pour identifier chaque axe."
            ) if len(sticks) > 1 else None,
        }

    def detect_rc_channel_move(
        self,
        baseline: list[int],
        duration_s: float = 5.0,
        threshold: int = 300,
    ) -> Optional[dict]:
        """
        Poll MSP_RC pendant duration_s secondes et retourne le canal dont le
        pic de delta depuis la baseline a été le plus grand.
        Protocole guidé : demander à l'utilisateur de bouger un seul contrôle,
        puis appeler cet outil — répéter pour chaque axe.
        """
        deadline = time.monotonic() + duration_s
        peak = [0] * len(baseline)

        while time.monotonic() < deadline:
            rc = self.get_rc()
            if rc:
                for i, cur in enumerate(rc["channels"]):
                    if i < len(baseline):
                        d = abs(cur - baseline[i])
                        if d > peak[i]:
                            peak[i] = d
            time.sleep(0.02)

        max_delta = max(peak) if peak else 0
        all_deltas = {
            RC_CHANNEL_NAMES[i] if i < len(RC_CHANNEL_NAMES) else f"ch{i}": d
            for i, d in enumerate(peak)
        }

        if max_delta < threshold:
            return {
                "detected": False,
                "max_delta_us": max_delta,
                "threshold_us": threshold,
                "all_peak_deltas": all_deltas,
            }

        winner = peak.index(max_delta)
        name = RC_CHANNEL_NAMES[winner] if winner < len(RC_CHANNEL_NAMES) else f"ch{winner}"
        return {
            "detected": True,
            "channel": winner,
            "name": name,
            "max_delta_us": max_delta,
            "threshold_us": threshold,
            "all_peak_deltas": all_deltas,
        }

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

    def _read_pid_payload(self) -> Optional[bytes]:
        """MSP_PID (112) brut : 3 octets (P, I, D) par axe, dans l'ordre de PID_AXES."""
        d = self._req(MSPCodes.MSP_PID)
        if not d:
            return None
        return d.read_bytes(d.remaining)

    def get_pid_values(self) -> Optional[dict]:
        """MSP_PID (112) — Valeurs P/I/D par axe. Les axes inconnus en fin de payload sont ignorés."""
        raw = self._read_pid_payload()
        if raw is None:
            return None
        offsets = range(0, len(raw) - _PID_BYTES_PER_AXIS + 1, _PID_BYTES_PER_AXIS)
        return {
            axis: {"p": raw[o], "i": raw[o + 1], "d": raw[o + 2]}
            for axis, o in zip(PID_AXES, offsets)
        }

    def set_pid_values(self, pid_dict: dict) -> bool:
        """
        MSP_SET_PID (202) — Écriture P/I/D (read-modify-write).
        pid_dict : { 'roll': {'p':42,'i':40,'d':30}, ... }
        Le payload relu est renvoyé tel quel hormis les axes modifiés : le firmware
        lit PID_ITEM_COUNT axes et met à 0 tout octet manquant, donc on refuse
        d'écrire si la relecture est incomplète.
        """
        unknown = set(pid_dict) - set(PID_AXES)
        if unknown:
            logger.warning("Axes PID inconnus : %s", sorted(unknown))
            return False
        with self.msp.transaction():
            self._require_disarmed()
            raw = self._read_pid_payload()
            if raw is None or len(raw) < len(PID_AXES) * _PID_BYTES_PER_AXIS:
                logger.warning("Relecture PID incomplète (%s octets), écriture annulée",
                               None if raw is None else len(raw))
                return False
            patched = bytearray(raw)
            for axis, gains in pid_dict.items():
                o = PID_AXES.index(axis) * _PID_BYTES_PER_AXIS
                patched[o:o + _PID_BYTES_PER_AXIS] = bytes([gains["p"], gains["i"], gains["d"]])
            return self._write(MSPCodes.MSP_SET_PID, bytes(patched))

    # ── Rates RC ──────────────────────────────────────────────────────

    def _read_rc_tuning_payload(self) -> Optional[bytes]:
        """MSP_RC_TUNING (111) brut."""
        d = self._req(MSPCodes.MSP_RC_TUNING)
        if not d:
            return None
        return d.read_bytes(d.remaining)

    @staticmethod
    def _rates_type_of(raw: bytes) -> int:
        """rates_type n'existe qu'à partir de l'API 1.43 ; avant, seules les rates Betaflight."""
        offset = _RC_TUNING_OFFSETS["rates_type"]
        return raw[offset] if len(raw) > offset else _rates.RatesType.BETAFLIGHT

    def get_rates(self) -> Optional[dict]:
        """
        MSP_RC_TUNING (111) — Rates par axe dans les unités du configurateur pour
        le rates_type actif, avec la vitesse max plein manche (°/s).
        """
        raw = self._read_rc_tuning_payload()
        if raw is None or len(raw) < _RC_TUNING_OFFSETS["pitch_expo"] + 1:
            return None
        rates_type = self._rates_type_of(raw)
        result     = {
            "rates_type":    _rates.type_name(rates_type),
            "rates_type_id": rates_type,
        }
        limit_offset = _RC_TUNING_OFFSETS["yaw_rate_limit"] + 2
        for axis in _rates.AXES:
            rc_raw    = raw[_RC_TUNING_OFFSETS[f"{axis}_rc_rate"]]
            rate_raw  = raw[_RC_TUNING_OFFSETS[f"{axis}_rate"]]
            expo_raw  = raw[_RC_TUNING_OFFSETS[f"{axis}_expo"]]
            rate_limit = (struct.unpack_from("<H", raw, _RC_TUNING_OFFSETS[f"{axis}_rate_limit"])[0]
                          if len(raw) >= limit_offset else _rates.SETPOINT_RATE_LIMIT_DPS)
            result[axis] = self._axis_view(rates_type, rc_raw, rate_raw, expo_raw, rate_limit)
        if _rates.is_supported(rates_type):
            result["labels"] = _rates.labels(rates_type)
        for name in _RC_TUNING_THROTTLE_FIELDS + ("throttle_hover",):
            offset = _RC_TUNING_OFFSETS[name]
            if len(raw) > offset:
                result[name] = round(raw[offset] * _rates.THROTTLE_SCALE, 2)
        for name in ("throttle_limit_type", "throttle_limit_percent"):
            offset = _RC_TUNING_OFFSETS[name]
            if len(raw) > offset:
                result[name] = raw[offset]
        return result

    @staticmethod
    def _axis_view(rates_type: int, rc_raw: int, rate_raw: int, expo_raw: int,
                   rate_limit: int) -> dict:
        if not _rates.is_supported(rates_type):
            return {"rc_rate_raw": rc_raw, "rate_raw": rate_raw, "expo_raw": expo_raw,
                    "rate_limit_dps": rate_limit}
        return {
            "rc_rate":        _rates.to_display(rates_type, "rc_rate", rc_raw),
            "rate":           _rates.to_display(rates_type, "rate", rate_raw),
            "expo":           _rates.to_display(rates_type, "expo", expo_raw),
            "rate_limit_dps": rate_limit,
            "max_rate_dps":   _rates.max_rate_dps(rates_type, rc_raw, rate_raw, expo_raw, rate_limit),
        }

    def set_rates(self, updates: dict, expected_rates_type: int) -> bool:
        """
        MSP_SET_RC_TUNING (204) — read-modify-write des rates.
        updates : {"roll_rate": 800, "pitch_expo": 0.3, "throttle_mid": 0.5, ...}
        dans les unités du configurateur pour expected_rates_type.
        Seuls les octets demandés changent ; la longueur relue est conservée.
        Refus si le rates_type du FC diffère (les unités n'auraient plus de sens),
        si un champ est inconnu ou absent du payload de ce firmware.
        """
        unknown = set(updates) - set(RATES_WRITABLE_FIELDS)
        if unknown:
            logger.warning("Champs rates inconnus : %s", sorted(unknown))
            return False
        with self.msp.transaction():
            self._require_disarmed()
            raw = self._read_rc_tuning_payload()
            if raw is None:
                return False
            rates_type = self._rates_type_of(raw)
            if rates_type != expected_rates_type or not _rates.is_supported(rates_type):
                logger.warning("rates_type du FC = %d, attendu %d : écriture annulée",
                               rates_type, expected_rates_type)
                return False
            patched = bytearray(raw)
            for name, value in updates.items():
                offset = _RC_TUNING_OFFSETS[name]
                if offset >= len(raw):
                    logger.warning("Champ %s absent du payload firmware (%d octets)", name, len(raw))
                    return False
                byte = self._rates_raw_value(rates_type, name, value)
                if not 0 <= byte <= 0xFF:
                    logger.warning("Valeur %s=%s hors octet (%d)", name, value, byte)
                    return False
                patched[offset] = byte
            return self._write(MSPCodes.MSP_SET_RC_TUNING, bytes(patched))

    @staticmethod
    def _rates_raw_value(rates_type: int, name: str, value: float) -> int:
        if name in _RC_TUNING_THROTTLE_FIELDS:
            return round(value / _rates.THROTTLE_SCALE)
        field = name.split("_", 1)[1]
        return _rates.to_raw(rates_type, field, value)

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

    # ── Profils ───────────────────────────────────────────────────────

    def get_profiles(self) -> Optional[dict]:
        """Profils actifs : PID (et nombre de profils PID), rates, batterie (API >= 1.48)."""
        status = self.get_fc_status()
        if not status:
            return None
        result = {"pid_profile": status["profile"], "rate_profile": status.get("rate_profile", 0)}
        if "num_profiles" in status:  # MSP_STATUS_EX uniquement
            result["pid_profile_count"] = status["num_profiles"]
        if self.api_version >= _battery_profiles.WRITE_MIN_API:
            active = self._read_payload(MSPCodes.MSP2_BATTERY_PROFILE)
            if active:
                result["battery_profile"] = active[0]
        return result

    def select_profile(self, kind: str, index: int) -> bool:
        """
        MSP_SELECT_SETTING (210) — Active un profil PID, de rates ou batterie (API >= 1.48).
        Le firmware ignore un changement de profil PID si le FC est armé et ramène à 0 un
        index hors plage, tout en acquittant : le succès est vérifié par relecture.
        """
        if kind not in PROFILE_KINDS or index < 0:
            return False
        if kind == "battery" and (self.api_version < _battery_profiles.WRITE_MIN_API
                                  or index >= _battery_profiles.PROFILE_COUNT):
            return False
        value = {"pid": index, "rate": index | _RATE_PROFILE_MASK,
                 "battery": index | _BATTERY_PROFILE_MASK}[kind]
        with self.msp.transaction():
            self._require_disarmed()
            if not self._write(MSPCodes.MSP_SELECT_SETTING, bytes([value])):
                return False
            profiles = self.get_profiles() or {}
        return profiles.get(f"{kind}_profile") == index

    def get_battery_profiles(self) -> Optional[list]:
        """MSP2_BATTERY_PROFILE (0x300E) — Les profils batterie (API >= 1.48), index inclus."""
        if self.api_version < _battery_profiles.WRITE_MIN_API:
            return None
        profiles = []
        for index in range(_battery_profiles.PROFILE_COUNT):
            raw = self._read_payload(MSPCodes.MSP2_BATTERY_PROFILE, bytes([index]))
            if not raw:
                return None
            profiles.append(_battery_profiles.parse(raw))
        return profiles

    def get_battery_profile(self, index: int) -> Optional[dict]:
        if self.api_version < _battery_profiles.WRITE_MIN_API or not 0 <= index < _battery_profiles.PROFILE_COUNT:
            return None
        raw = self._read_payload(MSPCodes.MSP2_BATTERY_PROFILE, bytes([index]))
        return _battery_profiles.parse(raw) if raw else None

    def set_battery_profile(self, index: int, updates: dict) -> bool:
        """
        MSP2_SET_BATTERY_PROFILE (0x300F) — read-modify-write d'un profil batterie par index.
        Refus si l'API < 1.48, index hors plage, valeur invalide, FC armé (WriteBlockedError)
        ou écriture non acquittée (ex. ordre min <= warning <= full <= max violé).
        """
        if (self.api_version < _battery_profiles.WRITE_MIN_API
                or not 0 <= index < _battery_profiles.PROFILE_COUNT):
            return False
        try:
            raw_updates = {n: _battery_profiles.to_raw(n, v) for n, v in updates.items()}
        except ValueError as e:
            logger.warning("%s", e)
            return False
        with self.msp.transaction():
            self._require_disarmed()
            raw = self._read_payload(MSPCodes.MSP2_BATTERY_PROFILE, bytes([index]))
            if not raw or raw[0] != index:
                return False
            try:
                payload = _battery_profiles.patch(raw, raw_updates)
            except ValueError as e:
                logger.warning("%s", e)
                return False
            return self._write(MSPCodes.MSP2_SET_BATTERY_PROFILE, payload)

    def get_battery_config(self) -> Optional[dict]:
        """
        MSP_BATTERY_CONFIG (32) — Tensions cellule min/warning/max (0.01 V), capacité (mAh),
        sources de mesure tension/courant, nommées comme la CLI.
        """
        raw = self._read_payload(MSPCodes.MSP_BATTERY_CONFIG)
        if not raw:
            return None
        return _battery.parse(raw)

    def set_battery_config(self, updates: dict) -> bool:
        """
        MSP_SET_BATTERY_CONFIG (33) — read-modify-write des champs demandés.
        Les sources de mesure ne s'appliquent qu'après sauvegarde et redémarrage.
        Refus si l'API < battery_config.WRITE_MIN_API, valeur invalide, FC armé
        (WriteBlockedError) ou écriture non acquittée (ex. ordre min/warning/max invalide).
        """
        if self.api_version < _battery.WRITE_MIN_API:
            logger.warning("Écriture batterie non vérifiée pour l'API %s", self.api_version)
            return False
        try:
            raw_updates = {name: _battery.to_raw(name, value) for name, value in updates.items()}
        except ValueError as e:
            logger.warning("%s", e)
            return False
        with self.msp.transaction():
            self._require_disarmed()
            return self._patch_and_write(_battery, MSPCodes.MSP_BATTERY_CONFIG,
                                         MSPCodes.MSP_SET_BATTERY_CONFIG, raw_updates)

    def get_filter_config(self) -> Optional[dict]:
        """
        MSP_FILTER_CONFIG (92) — Filtres nommés comme la CLI : lowpass gyro/D-term
        (statiques et dynamiques), notches, dyn notch, filtre RPM, yaw_lowpass_hz.
        """
        raw = self._read_payload(MSPCodes.MSP_FILTER_CONFIG)
        if not raw:
            return None
        return _filters.parse(raw)

    def set_filter_config(self, updates: dict) -> bool:
        """
        MSP_SET_FILTER_CONFIG (93) — read-modify-write des champs demandés (noms CLI,
        énumérations en libellé ou index, rpm_filter_weights en "a,b,c").
        Le firmware ré-initialise les filtres et peut corriger des valeurs
        (validateAndFixGyroConfig) : relire pour connaître les valeurs appliquées.
        Refus si l'API < filter_config.WRITE_MIN_API, valeur invalide, champ absent du
        payload firmware, FC armé (WriteBlockedError) ou écriture non acquittée.
        """
        if self.api_version < _filters.WRITE_MIN_API:
            logger.warning("Écriture des filtres non vérifiée pour l'API %s", self.api_version)
            return False
        try:
            raw_updates = {name: _filters.to_raw(name, value) for name, value in updates.items()}
        except ValueError as e:
            logger.warning("%s", e)
            return False
        with self.msp.transaction():
            self._require_disarmed()
            return self._patch_and_write(_filters, MSPCodes.MSP_FILTER_CONFIG,
                                         MSPCodes.MSP_SET_FILTER_CONFIG, raw_updates)

    def _read_payload(self, cmd: int, payload: bytes = b'') -> Optional[bytes]:
        d = self._req(cmd, payload)
        if not d:
            return None
        return d.read_bytes(d.remaining)

    def get_pid_advanced(self) -> Optional[dict]:
        """
        MSP_PID_ADVANCED (94) — Réglages PID avancés nommés comme la CLI du FC
        (feedforward, D-max/D-min, iterm relax, anti-gravity, TPA…), plus
        simplified_pids_mode (MSP_SIMPLIFIED_TUNING).
        """
        raw = self._read_payload(MSPCodes.MSP_PID_ADVANCED)
        if not raw:
            return None
        result = _pid_adv.parse(raw, self.api_version)
        simplified = self._read_payload(MSPCodes.MSP_SIMPLIFIED_TUNING)
        mode = _pid_adv.parse_simplified_mode(simplified or b'')
        if mode is not None:
            result[_pid_adv.SIMPLIFIED_PIDS_MODE_FIELD] = mode
        return result

    def set_pid_advanced(self, updates: dict) -> bool:
        """
        MSP_SET_PID_ADVANCED (95) — read-modify-write des champs demandés (noms CLI,
        énumérations en libellé ou index). simplified_pids_mode passe par
        MSP_SET_SIMPLIFIED_TUNING, écrit en premier : avec OFF, le firmware ne
        recalcule plus les PIDs depuis les curseurs.
        Refus si l'API < pid_advanced.WRITE_MIN_API, valeur invalide, champ absent du
        payload firmware, FC armé (WriteBlockedError) ou écriture non acquittée.
        """
        if self.api_version < _pid_adv.WRITE_MIN_API:
            logger.warning("Écriture PID avancés non vérifiée pour l'API %s", self.api_version)
            return False
        try:
            raw_updates = {name: _pid_adv.to_raw(name, value) for name, value in updates.items()}
        except ValueError as e:
            logger.warning("%s", e)
            return False
        simplified_mode = raw_updates.pop(_pid_adv.SIMPLIFIED_PIDS_MODE_FIELD, None)
        with self.msp.transaction():
            self._require_disarmed()
            if simplified_mode is not None and not self._write_simplified_mode(simplified_mode):
                return False
            if not raw_updates:
                return True
            return self._patch_and_write(_pid_adv, MSPCodes.MSP_PID_ADVANCED,
                                         MSPCodes.MSP_SET_PID_ADVANCED, raw_updates)

    def _patch_and_write(self, table, read_cmd: int, write_cmd: int, raw_updates: dict) -> bool:
        """
        Read-modify-write d'un payload décrit par une table (pid_advanced, filter_config) :
        seuls les octets des champs demandés changent, la longueur relue est conservée.
        À appeler dans une transaction, garde d'armement déjà passée.
        """
        raw = self._read_payload(read_cmd)
        if not raw:
            return False
        try:
            payload = table.patch(raw, raw_updates)
        except ValueError as e:
            logger.warning("%s", e)
            return False
        return self._write(write_cmd, payload)

    def _write_simplified_mode(self, mode: int) -> bool:
        """Relit MSP_SIMPLIFIED_TUNING et ne change que l'octet 0 (simplified_pids_mode)."""
        raw = self._read_payload(MSPCodes.MSP_SIMPLIFIED_TUNING)
        if not raw:
            return False
        return self._write(MSPCodes.MSP_SET_SIMPLIFIED_TUNING, bytes([mode]) + raw[1:])

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
        with self.msp.transaction():
            self._require_disarmed()
            return self._write(MSPCodes.MSP_SET_MOTOR, payload)

    def set_raw_rc(self, channels: list) -> bool:
        """
        MSP_SET_RAW_RC (200) — Override des canaux RC (µs).
        Nécessite la feature MSP_RC activée dans Betaflight.
        """
        payload = struct.pack(f"<{len(channels)}H", *channels)
        return self._write(MSPCodes.MSP_SET_RAW_RC, payload)

    # ── Sauvegarde / Reboot ───────────────────────────────────────────

    def save_config(self) -> bool:
        """
        MSP_EEPROM_WRITE (250) — Sauvegarde la config en EEPROM. Toujours appeler après un SET.
        Refusé si le FC est armé (le firmware le refuse aussi).
        """
        with self.msp.transaction():
            self._require_disarmed()
            saved = self._write(MSPCodes.MSP_EEPROM_WRITE, timeout=EEPROM_TIMEOUT)
        if saved:
            logger.info("Config sauvegardée en EEPROM")
        return saved

    def reboot_fc(self) -> bool:
        """
        MSP_SET_REBOOT (68) — Redémarre le Flight Controller. Refusé si le FC est armé.
        Le firmware répond puis redémarre, mais le reset USB avale souvent la réponse :
        seul un refus explicite ('!') compte comme un échec, une réponse absente veut
        dire que le FC redémarre.
        """
        with self.msp.transaction():
            self._require_disarmed()
            resp = self.msp.request(MSPCodes.MSP_SET_REBOOT)
            rebooting = resp is None or resp["ok"]
            if resp is None:
                logger.info("Pas d'acquittement du reboot : port USB coupé par le redémarrage")
        if rebooting:
            logger.info("FC redémarré")
        return rebooting
