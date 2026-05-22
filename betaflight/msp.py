import struct
import logging
from typing import Optional
from .serial_conn import SerialConnection
from .msp_codes import MSPCodes

logger = logging.getLogger(__name__)

# Alias pour la compatibilité avec le code existant
MSPCommand = MSPCodes


def _crc8_dvb_s2(data: bytes) -> int:
    """CRC8/DVB-S2 utilisé pour les trames MSP v2."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0xD5) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


class MSPProtocol:
    """
    Encode et décode les trames MSP v1 et v2.
    Communique avec le FC Betaflight via SerialConnection.

    MSP v1 : $M< size(1) cmd(1) payload checksum(xor)
    MSP v2 : $X< flag(1) cmd(2le) size(2le) payload crc8dvb
    """

    PREAMBLE_V1 = b'$M'
    PREAMBLE_V2 = b'$X'

    def __init__(self, connection: SerialConnection):
        self.conn = connection

    # ── MSP v1 ────────────────────────────────────────────────────────

    def _build_frame(self, cmd: int, payload: bytes = b'') -> bytes:
        size     = len(payload)
        checksum = size ^ cmd
        for byte in payload:
            checksum ^= byte
        return self.PREAMBLE_V1 + b'<' + bytes([size, cmd]) + payload + bytes([checksum])

    def _parse_v1(self, meta: bytes, payload_and_crc: bytes) -> Optional[dict]:
        size, cmd    = meta[0], meta[1]
        payload      = payload_and_crc[:size]
        checksum     = payload_and_crc[size]
        expected     = size ^ cmd
        for b in payload:
            expected ^= b
        if checksum != expected:
            logger.warning("Checksum MSP v1 invalide (reçu=%d, attendu=%d)", checksum, expected)
            return None
        return {"cmd": cmd, "payload": payload, "version": 1}

    # ── MSP v2 ────────────────────────────────────────────────────────

    def _build_frame_v2(self, cmd: int, payload: bytes = b'', flag: int = 0) -> bytes:
        header = bytes([flag]) + struct.pack("<HH", cmd, len(payload))
        crc    = _crc8_dvb_s2(header + payload)
        return self.PREAMBLE_V2 + b'<' + header + payload + bytes([crc])

    def _parse_v2(self, header: bytes, payload_and_crc: bytes) -> Optional[dict]:
        # header = flag(1) + cmd(2le) + size(2le)
        flag = header[0]
        cmd  = struct.unpack_from("<H", header, 1)[0]
        size = struct.unpack_from("<H", header, 3)[0]
        if len(payload_and_crc) < size + 1:
            return None
        payload  = payload_and_crc[:size]
        crc      = payload_and_crc[size]
        expected = _crc8_dvb_s2(header + payload)
        if crc != expected:
            logger.warning("CRC MSP v2 invalide (reçu=%d, attendu=%d)", crc, expected)
            return None
        return {"cmd": cmd, "payload": payload, "version": 2, "flag": flag}

    # ── Envoi ─────────────────────────────────────────────────────────

    def send_command(self, cmd: int, payload: bytes = b'') -> bool:
        frame = self._build_frame_v2(cmd, payload) if cmd > 255 else self._build_frame(cmd, payload)
        logger.debug("MSP TX: cmd=%d payload=%s", cmd, payload.hex() if payload else 'none')
        self.conn.flush()
        self.conn.write(frame)
        return True

    # ── Réception ─────────────────────────────────────────────────────

    def read_response(self) -> Optional[dict]:
        try:
            header = self.conn.read(3)  # preamble(2) + direction(1)
            if len(header) < 3:
                return None

            preamble  = header[:2]
            direction = chr(header[2])

            if direction not in ('>', '!'):
                logger.warning("Direction MSP inattendue : %s", direction)
                return None

            if preamble == self.PREAMBLE_V1:
                meta = self.conn.read(2)
                if len(meta) < 2:
                    return None
                size = meta[0]
                data = self.conn.read(size + 1)
                if len(data) < size + 1:
                    return None
                result = self._parse_v1(meta, data)

            elif preamble == self.PREAMBLE_V2:
                # flag(1) + cmd(2) + size(2)
                v2_header = self.conn.read(5)
                if len(v2_header) < 5:
                    return None
                size = struct.unpack_from("<H", v2_header, 3)[0]
                data = self.conn.read(size + 1)
                if len(data) < size + 1:
                    return None
                result = self._parse_v2(v2_header, data)

            else:
                logger.warning("Préambule MSP inconnu : %s", preamble)
                return None

            if result:
                logger.debug("MSP RX v%d: cmd=%d size=%d",
                             result["version"], result["cmd"], len(result["payload"]))
            return result

        except Exception as e:
            logger.error("Erreur lecture MSP : %s", e)
            return None

    # ── Helper ────────────────────────────────────────────────────────

    def request(self, cmd: int, payload: bytes = b'') -> Optional[dict]:
        self.send_command(cmd, payload)
        return self.read_response()
