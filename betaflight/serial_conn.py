# ── Betaflight MCP Server ── Serial Connection Layer ────────────────
# Gestion de la connexion port série avec pyserial

import serial
import serial.tools.list_ports
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class SerialConnection:
    """
    Gère la connexion série vers le Flight Controller Betaflight.
    Utilise pyserial pour l'accès bas niveau au port USB/UART.
    """

    def __init__(self, port: str, baudrate: int = 115200, timeout: float = 2.0):
        self.port     = port
        self.baudrate = baudrate
        self.timeout  = timeout
        self._serial: Optional[serial.Serial] = None

    # ── Connexion ──────────────────────────────────────────────────────

    def connect(self) -> bool:
        """Ouvre la connexion série vers le FC."""
        try:
            self._serial = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=self.timeout
            )
            logger.info(f"Connecté sur {self.port} @ {self.baudrate} baud")
            return True
        except serial.SerialException as e:
            logger.error(f"Erreur connexion série : {e}")
            return False

    def disconnect(self):
        """Ferme proprement la connexion série."""
        if self._serial and self._serial.is_open:
            self._serial.close()
            logger.info("Connexion série fermée")

    @property
    def is_connected(self) -> bool:
        return self._serial is not None and self._serial.is_open

    # ── Lecture / Écriture ─────────────────────────────────────────────

    def write(self, data: bytes) -> int:
        """Envoie des bytes bruts vers le FC."""
        if not self.is_connected:
            raise ConnectionError("Port série non connecté")
        return self._serial.write(data)

    def read(self, size: int) -> bytes:
        """Lit N bytes depuis le FC."""
        if not self.is_connected:
            raise ConnectionError("Port série non connecté")
        return self._serial.read(size)

    def read_until(self, expected: bytes = b'\n', size: int = None) -> bytes:
        """Lit jusqu'à trouver le byte attendu."""
        if not self.is_connected:
            raise ConnectionError("Port série non connecté")
        return self._serial.read_until(expected, size)

    def flush(self):
        """Vide les buffers série."""
        if self.is_connected:
            self._serial.reset_input_buffer()
            self._serial.reset_output_buffer()

    # ── Utilitaires ────────────────────────────────────────────────────

    @staticmethod
    def list_available_ports() -> list[dict]:
        """
        Liste tous les ports série disponibles sur le système.
        Retourne une liste de dicts avec port, description, hwid.
        """
        ports = []
        for port in serial.tools.list_ports.comports():
            ports.append({
                "port":        port.device,
                "description": port.description,
                "hwid":        port.hwid,
            })
        return ports
