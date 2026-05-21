# ── Betaflight MCP Server ── MCP Server Loop ────────────────────────
# Gère la communication MCP (JSON-RPC over stdio)
# Compatible avec le protocole MCP standard

import json
import sys
import logging
from server.tools import MCP_TOOLS
from config.settings import MCP_SERVER_NAME, MCP_SERVER_VERSION

logger = logging.getLogger(__name__)


class MCPServer:
    """
    Serveur MCP minimaliste utilisant stdio (stdin/stdout).
    Compatible avec les clients MCP (Claude Desktop, LLM custom, etc.)
    """

    def __init__(self):
        self.name    = MCP_SERVER_NAME
        self.version = MCP_SERVER_VERSION

    def _send(self, obj: dict):
        """Envoie une réponse JSON sur stdout."""
        line = json.dumps(obj)
        sys.stdout.write(line + "\n")
        sys.stdout.flush()

    def _handle_initialize(self, msg_id: int, params: dict) -> dict:
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": self.name, "version": self.version},
                "capabilities": {"tools": {}},
            }
        }

    def _handle_tools_list(self, msg_id: int) -> dict:
        tools = []
        for name, meta in MCP_TOOLS.items():
            params   = meta.get("parameters", {})
            required = meta.get("required", [])
            schema   = {"type": "object", "properties": params}
            if required:
                schema["required"] = required
            tools.append({
                "name":        name,
                "description": meta["description"],
                "inputSchema": schema,
            })
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": tools}}

    def _handle_tools_call(self, msg_id: int, params: dict) -> dict:
        tool_name = params.get("name")
        args      = params.get("arguments", {})

        if tool_name not in MCP_TOOLS:
            return {
                "jsonrpc": "2.0", "id": msg_id,
                "error": {"code": -32601, "message": f"Tool '{tool_name}' inconnu"}
            }

        try:
            result = MCP_TOOLS[tool_name]["fn"](**args)
            return {
                "jsonrpc": "2.0", "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(result, indent=2)}]
                }
            }
        except Exception as e:
            return {
                "jsonrpc": "2.0", "id": msg_id,
                "error": {"code": -32000, "message": str(e)}
            }

    def run(self):
        """Boucle principale : lit les messages MCP sur stdin."""
        logger.info(f"MCP Server '{self.name}' v{self.version} démarré (stdio)")

        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg    = json.loads(line)
                method = msg.get("method", "")
                msg_id = msg.get("id")
                params = msg.get("params", {})

                if method == "initialize":
                    self._send(self._handle_initialize(msg_id, params))
                    self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

                elif method == "tools/list":
                    self._send(self._handle_tools_list(msg_id))

                elif method == "tools/call":
                    self._send(self._handle_tools_call(msg_id, params))

                else:
                    logger.warning(f"Méthode inconnue : {method}")

            except json.JSONDecodeError as e:
                logger.error(f"JSON invalide : {e}")
