"""Entry point for python -m sbl_debugger."""

from sbl_debugger.server import mcp

mcp.run(transport="stdio")
