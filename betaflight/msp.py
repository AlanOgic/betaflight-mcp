import struct
import logging
import threading
from contextlib import contextmanager
from typing import Iterator, Optional
from .serial_conn import SerialConnection
from .msp_codes import MSPCodes

logger = logging.getLogger(__name__)

# Alias pour la compatibilité avec le code existant
MSPCommand = MSPCodes

# Trame reçue en entier mais invalide (checksum/CRC) : le flux reste exploitable
_CORRUPT = object()


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
    DIR_OK      = ord('>')
    DIR_ERROR   = ord('!')

    # Trames d'une autre commande (réponses tardives) tolérées avant abandon
    MAX_STALE_FRAMES = 8
    # Octets parasites parcourus au maximum pour retrouver un début de trame
    MAX_RESYNC_BYTES = 64

    def __init__(self, connection: SerialConnection):
        self.conn  = connection
        self._lock = threading.RLock()  # une seule transaction MSP à la fois

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

    def _is_frame_start(self, header: bytes) -> bool:
        return (len(header) == 3
                and header[:2] in (self.PREAMBLE_V1, self.PREAMBLE_V2)
                and header[2] in (self.DIR_OK, self.DIR_ERROR))

    def _read_frame_start(self) -> Optional[bytes]:
        """
        Lit préambule + direction (3 octets). Si le flux est désynchronisé,
        avance octet par octet jusqu'au prochain début de trame valide.
        """
        header = self.conn.read(3)
        if len(header) < 3:
            return None
        if self._is_frame_start(header):
            return header

        logger.warning("Flux MSP désynchronisé (%s), resynchronisation", header.hex())
        window = header[header.find(b'$', 1):] if b'$' in header[1:] else b''
        for _ in range(self.MAX_RESYNC_BYTES):
            byte = self.conn.read(1)
            if not byte:
                return None
            window = (window + byte)[-3:]
            if self._is_frame_start(window):
                return window
        logger.warning("Aucun début de trame MSP après %d octets", self.MAX_RESYNC_BYTES)
        return None

    def read_response(self) -> Optional[dict]:
        """
        Lit une trame de réponse. Retourne {cmd, payload, version, ok[, flag]}
        où ok=False signale une réponse d'erreur du FC ('!'). None si timeout,
        trame invalide ou erreur de lecture.
        """
        frame = self._read_frame()
        return None if frame is _CORRUPT else frame

    def _read_frame(self):
        """Comme read_response, mais distingue une trame corrompue (_CORRUPT) d'un timeout (None)."""
        try:
            header = self._read_frame_start()
            if header is None:
                return None

            if header[:2] == self.PREAMBLE_V1:
                meta = self.conn.read(2)
                if len(meta) < 2:
                    return None
                size = meta[0]
                data = self.conn.read(size + 1)
                if len(data) < size + 1:
                    return None
                result = self._parse_v1(meta, data)
            else:
                # flag(1) + cmd(2) + size(2)
                v2_header = self.conn.read(5)
                if len(v2_header) < 5:
                    return None
                size = struct.unpack_from("<H", v2_header, 3)[0]
                data = self.conn.read(size + 1)
                if len(data) < size + 1:
                    return None
                result = self._parse_v2(v2_header, data)

            if result is None:
                return _CORRUPT
            result = {**result, "ok": header[2] == self.DIR_OK}
            logger.debug("MSP RX v%d: cmd=%d size=%d ok=%s",
                         result["version"], result["cmd"], len(result["payload"]), result["ok"])
            return result

        except Exception as e:
            logger.error("Erreur lecture MSP : %s", e)
            return None

    # ── Helper ────────────────────────────────────────────────────────

    @contextmanager
    def _timeout(self, timeout: Optional[float]) -> Iterator[None]:
        """Applique un timeout de lecture ponctuel, restauré en sortie."""
        if timeout is None:
            yield
            return
        previous          = self.conn.timeout
        self.conn.timeout = timeout
        try:
            yield
        finally:
            self.conn.timeout = previous

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """
        Réserve le lien série pour plusieurs requêtes consécutives
        (ex. read-modify-write). Réentrant : request() peut être appelé dedans.
        """
        with self._lock:
            yield

    def request(self, cmd: int, payload: bytes = b'',
                timeout: Optional[float] = None) -> Optional[dict]:
        """
        Transaction atomique : envoie cmd puis retourne la réponse correspondante.
        Les trames d'autres commandes (réponses tardives) et les trames corrompues
        sont ignorées, dans la limite de MAX_STALE_FRAMES. Le timeout ponctuel
        s'applique à chaque lecture. La réponse peut avoir ok=False (commande
        refusée par le FC). None si aucune réponse valide ou si l'envoi échoue.
        """
        with self._lock, self._timeout(timeout):
            try:
                self.send_command(cmd, payload)
            except OSError as e:
                logger.error("Envoi MSP cmd=%d impossible : %s", cmd, e)
                return None
            for _ in range(self.MAX_STALE_FRAMES):
                resp = self._read_frame()
                if resp is None:
                    return None
                if resp is _CORRUPT:
                    continue
                if resp["cmd"] == cmd:
                    return resp
                logger.warning("Trame MSP ignorée : cmd=%d reçue, cmd=%d attendue",
                               resp["cmd"], cmd)
            logger.warning("Pas de réponse à cmd=%d après %d trames", cmd, self.MAX_STALE_FRAMES)
            return None
