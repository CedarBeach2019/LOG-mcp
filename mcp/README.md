# mcp/

Model Context Protocol (MCP) server for LOG-mcp.

## What It Does

Exposes LOG-mcp's vault operations as MCP tools, allowing AI assistants (Claude, etc.) to interact with the vault directly through the MCP protocol.

## Files

| File | Purpose |
|------|---------|
| `server.py` | MCP JSON-RPC server, tool definitions, request routing |

## Usage

Start the MCP server:

```bash
python -m mcp.server
```

Then configure your MCP client (Claude Desktop, etc.) to connect to the server. Tools exposed include vault read/write, entity lookup, and session management.

## Protocol

Implements the [MCP specification](https://modelcontextprotocol.io/):
- `initialize` — server info and capabilities
- `tools/list` — available vault tools
- `tools/call` — execute vault operations
