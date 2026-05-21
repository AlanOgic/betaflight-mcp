#!/usr/bin/env python3
# ── Betaflight MCP Server ── Entry Point ────────────────────────────

import logging
import sys

# Configuration du logging (stderr pour ne pas polluer stdout/MCP)
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

from server.server import MCPServer


def main():
    server = MCPServer()
    server.run()


if __name__ == "__main__":
    main()
