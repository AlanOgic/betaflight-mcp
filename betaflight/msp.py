# ── Betaflight MCP Server ── MSP Protocol Layer ─────────────────────
# Implémentation du protocole MSP (MultiWii Serial Protocol)
#
# Structure trame MSP :
#   '$'  'M'  direction  size  cmd  [payload...]  checksum
#   direction : '<' envoi au FC | '>' réponse du FC

import struct
import logging
from typing import Optional
from .serial_conn import SerialConnection

logger = logging.getLogger(__name__)

# ── Codes de commandes MSP ─────────────────────────────────────────────
class MSPCommand:
    # Lecture (Request)
    MSP_STATUS        = 101
    MSP_RAW_IMU       = 102
    MSP_ANALOG        = 110
    MSP_RC_TUNING     = 111
    MSP_PID           = 112
    MSP_MODE_RANGES   = 34
    MSP_FEATURE       = 36
    MSP_FC_VARIANT    = 2
    MSP_FC_VERSION    = 3
    MSP_API_VERSION   = 1

    # Écriture (Set)
    MSP_SET_PID       = 202
    MSP_SET_RC_TUNING = 204
    MSP_EEPROM_WRITE  = 250
    MSP_REBOOT        = 68


class MSPProtocol:
    """
    Encode et décode les trames MSP.
    Communique avec le FC via SerialConnection.
    """

    PREAMBLE = b'$M'

    def __init__(self, connection: SerialConnection):
        self.conn = connection

    # ── Encodage ──────────────────────────────────────────────────────

    def _build_frame(self, cmd: int, payload: bytes = b'') -> bytes:
        """
        Construit une trame MSP complète.
        Format : $M< [size:1] [cmd:1] [payload:size] [checksum:1]
        """
        size     = len(payload)
        checksum = size ^ cmd
        for byte in payload:
            checksum ^= byte

        frame = self.PREAMBLE + b'<'
        frame += bytes([size, cmd])
        frame += payload
        frame += bytes([checksum])
        return frame

    # ── Envoi / Réception ─────────────────────────────────────────────

    def send_command(self, cmd: int, payload: bytes = b'') -> bool:
        """Envoie une commande MSP au FC."""
        frame = self._build_frame(cmd, payload)
        logger.debug(f"MSP TX: cmd={cmd} payload={payload.hex() if payload else 'none'}")
        self.conn.flush()
        self.conn.write(frame)
        return True

    def read_response(self) -> Optional[dict]:
        """
        Lit et décode la réponse MSP du FC.
        Retourne un dict : { 'cmd': int, 'payload': bytes } ou None
        """
        try:
            # Lire le préambule $M
            header = self.conn.read(3)
            if len(header) < 3 or header[:2] != self.PREAMBLE:
                logger.warning(f"Préambule MSP invalide : {header}")
                return None

            direction = chr(header[2])
            if direction not in ('>', '!'):
                logger.warning(f"Direction MSP inattendue : {direction}")
                return None

            # Lire size + cmd
            meta = self.conn.read(2)
            if len(meta) < 2:
                return None
            size, cmd = meta[0], meta[1]

            # Lire payload + checksum
            data = self.conn.read(size + 1)
            if len(data) < size + 1:
                return None

            payload  = data[:size]
            checksum = data[size]

            # Vérifier checksum
            expected = size ^ cmd
            for b in payload:
                expected ^= b

            if checksum != expected:
                logger.warning(f"Checksum MSP invalide (reçu={checksum}, attendu={expected})")
                return None

            logger.debug(f"MSP RX: cmd={cmd} size={size} payload={payload.hex()}")
            return {"cmd": cmd, "payload": payload}

        except Exception as e:
            logger.error(f"Erreur lecture MSP : {e}")
            return None

    # ── Helper : envoyer et recevoir ──────────────────────────────────

    def request(self, cmd: int, payload: bytes = b'') -> Optional[dict]:
        """Envoie une commande et attend la réponse."""
        self.send_command(cmd, payload)
        return self.read_response()
