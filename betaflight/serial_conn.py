# ── Betaflight MCP Server ── Serial Connection Layer ────────────────
# Gestion de la connexion port série avec pyserial

import serial
import serial.tools.list_ports
from typing import Optional
import logging

logger = logging.getLogger(__name__)

# Chaîne produit USB de tous les firmwares Betaflight (platform.h : USBD_PRODUCT_STRING)
_BETAFLIGHT_PRODUCT_PREFIX = "Betaflight"
# VID:PID du port VCP Betaflight quand l'OS n'expose pas la chaîne produit (ex. Windows) :
# STM32 (0483), AT32 (2E3C), APM32/Geehy (314B). Les ID Pico SDK sont trop génériques.
_BETAFLIGHT_USB_IDS = frozenset({(0x0483, 0x5740), (0x2E3C, 0x5740), (0x314B, 0x5740)})


def is_betaflight_port(info) -> bool:
    """Port série d'un FC Betaflight, d'après un ListPortInfo pyserial."""
    for text in (getattr(info, "product", None), getattr(info, "description", None)):
        if text and text.startswith(_BETAFLIGHT_PRODUCT_PREFIX):
            return True
    return (getattr(info, "vid", None), getattr(info, "pid", None)) in _BETAFLIGHT_USB_IDS


class SerialConnection:
    """
    Gère la connexion série vers le Flight Controller Betaflight.
    Utilise pyserial pour l'accès bas niveau au port USB/UART.
    """

    def __init__(self, port: str, baudrate: int = 115200, timeout: float = 2.0):
        self.port     = port
        self.baudrate = baudrate
        self._timeout = timeout
        self._serial: Optional[serial.Serial] = None
        self.last_error: Optional[str] = None  # cause du dernier échec d'ouverture

    @property
    def timeout(self) -> float:
        return self._timeout

    @timeout.setter
    def timeout(self, value: float) -> None:
        """Change le timeout de lecture, y compris sur un port déjà ouvert."""
        self._timeout = value
        if self.is_connected:
            self._serial.timeout = value

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
                timeout=self._timeout
            )
            logger.info(f"Connecté sur {self.port} @ {self.baudrate} baud")
            self.last_error = None
            return True
        except serial.SerialException as e:
            logger.error(f"Erreur connexion série : {e}")
            self.last_error = str(e)
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
        Retourne une liste de dicts avec port, description, hwid, is_betaflight.
        """
        return [
            {
                "port":          port.device,
                "description":   port.description,
                "hwid":          port.hwid,
                "is_betaflight": is_betaflight_port(port),
            }
            for port in serial.tools.list_ports.comports()
        ]

    @staticmethod
    def find_betaflight_ports() -> list[str]:
        """Ports des FC Betaflight branchés (chaîne produit USB ou VID:PID connus)."""
        return [port.device for port in serial.tools.list_ports.comports() if is_betaflight_port(port)]
