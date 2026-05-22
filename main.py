#!/usr/bin/env python3
import logging
import sys

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

from mcp.server.fastmcp import FastMCP
from server.tools import MCP_TOOLS
from config.settings import MCP_SERVER_NAME

app = FastMCP(MCP_SERVER_NAME)

for _name, _meta in MCP_TOOLS.items():
    app.add_tool(_meta["fn"], name=_name, description=_meta["description"])


def main():
    app.run()


if __name__ == "__main__":
    main()
