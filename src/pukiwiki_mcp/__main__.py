"""Entry point: ``python -m pukiwiki_mcp`` runs the MCP server.

Transport is selected with ``PUKIWIKI_MCP_TRANSPORT``:

- ``stdio`` (default): the client launches this process and talks over
  stdin/stdout. Used by Claude Desktop / Claude Code and ``docker run -i``.
- ``streamable-http`` / ``sse``: run as a long-lived HTTP service (bind via
  ``PUKIWIKI_MCP_HOST`` / ``PUKIWIKI_MCP_PORT``, default 127.0.0.1:8000).
  Used by ``docker compose up``. There is no authentication: keep it on
  localhost or behind an authenticating proxy.
"""

from __future__ import annotations

import os

_VALID_TRANSPORTS = {"stdio", "streamable-http", "sse"}


def main() -> None:
    # Import here so that ConfigError (missing PUKIWIKI_URL) surfaces at run
    # time with a clear message rather than at module import.
    from .server import client, mcp

    transport = os.environ.get("PUKIWIKI_MCP_TRANSPORT", "stdio").strip().lower()
    if transport not in _VALID_TRANSPORTS:
        raise SystemExit(
            f"Invalid PUKIWIKI_MCP_TRANSPORT={transport!r}; "
            f"expected one of {sorted(_VALID_TRANSPORTS)}"
        )

    try:
        mcp.run(transport=transport)
    finally:
        client.close()


if __name__ == "__main__":
    main()
