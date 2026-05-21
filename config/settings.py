# ── Betaflight MCP Server ── Configuration ──────────────────────────
# Surcharger via variables d'environnement ou fichier .env

import os
import sys

def _default_port() -> str:
    return "COM3" if sys.platform == "win32" else "/dev/ttyUSB0"

SERIAL_PORT = os.environ.get("BETAFLIGHT_PORT",    _default_port())
BAUD_RATE   = int(os.environ.get("BETAFLIGHT_BAUD",    "115200"))
TIMEOUT     = float(os.environ.get("BETAFLIGHT_TIMEOUT", "2.0"))

MCP_SERVER_NAME    = "betaflight-mcp"
MCP_SERVER_VERSION = "0.1.0"
