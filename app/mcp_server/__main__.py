"""Run the MCP server: `python -m app.mcp_server [stdio|streamable-http]` (default: stdio)."""

import sys

from app.mcp_server.server import mcp

if __name__ == "__main__":
    transport = sys.argv[1] if len(sys.argv) > 1 else "stdio"
    if transport not in ("stdio", "streamable-http"):
        raise SystemExit("usage: python -m app.mcp_server [stdio|streamable-http]")
    mcp.run(transport=transport)
