"""MCP server exposing PukiWiki operations as tools.

See docs/03_detailed_design.md (3). Tools normalize known PukiWiki errors
into structured results instead of raising, so the LLM gets actionable
feedback and the process never crashes on expected conditions.
"""

from __future__ import annotations

import logging
import os

from mcp.server.fastmcp import FastMCP

from .client import (
    PageNotFoundError,
    PukiWikiClient,
    PukiWikiConflictError,
    PukiWikiError,
    PukiWikiReadOnlyError,
)
from .config import Settings

logging.basicConfig(
    level=os.environ.get("PUKIWIKI_LOG_LEVEL", "INFO").upper(),
    # stdout is reserved for the MCP JSON-RPC stream; log to stderr.
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

settings = Settings.from_env()
client = PukiWikiClient(settings)

# MCP transport binding (only used by HTTP transports sse / streamable-http).
# stdio (the default) ignores host/port. The HTTP transports have no
# authentication, so the default binds to localhost only; a container sets
# 0.0.0.0 and publishes the port on the host's 127.0.0.1 (see compose.yml).
mcp = FastMCP(
    "pukiwiki",
    host=os.environ.get("PUKIWIKI_MCP_HOST", "127.0.0.1"),
    port=int(os.environ.get("PUKIWIKI_MCP_PORT", "8000")),
)


def _require_page(page: str) -> str | None:
    if not page or not page.strip():
        return "page name must not be empty"
    return None


# --------------------------------------------------------------------------
# Read tools
# --------------------------------------------------------------------------
@mcp.tool()
def list_pages() -> dict:
    """List all wiki page names (excluding hidden/system pages by default)."""
    try:
        pages = client.list_pages()
        return {"pages": pages, "count": len(pages)}
    except PukiWikiError as exc:
        return {"error": str(exc)}


@mcp.tool()
def read_page(page: str) -> dict:
    """Return the raw PukiWiki source text of a page.

    If the page does not exist, ``exists`` is false and ``content`` is empty.
    """
    if err := _require_page(page):
        return {"page": page, "error": err}
    try:
        content = client.read_page(page)
        return {"page": page, "exists": True, "content": content}
    except PageNotFoundError:
        return {"page": page, "exists": False, "content": ""}
    except PukiWikiError as exc:
        return {"page": page, "error": str(exc)}


@mcp.tool()
def page_exists(page: str) -> dict:
    """Check whether a page exists."""
    if err := _require_page(page):
        return {"page": page, "error": err}
    try:
        return {"page": page, "exists": client.page_exists(page)}
    except PukiWikiError as exc:
        return {"page": page, "error": str(exc)}


@mcp.tool()
def search_pages(query: str, and_search: bool = True) -> dict:
    """Full-text search. Returns the names of matching pages.

    ``and_search=True`` requires all keywords (AND); False matches any (OR).
    """
    if not query or not query.strip():
        return {"query": query, "error": "query must not be empty"}
    try:
        pages = client.search_pages(query, and_search=and_search)
        return {"query": query, "pages": pages, "count": len(pages)}
    except PukiWikiError as exc:
        return {"query": query, "error": str(exc)}


@mcp.tool()
def get_page_diff(page: str) -> dict:
    """Return the latest diff (added/removed lines) for a page."""
    if err := _require_page(page):
        return {"page": page, "error": err}
    try:
        return {"page": page, "diff": client.get_diff(page)}
    except PukiWikiError as exc:
        return {"page": page, "error": str(exc)}


@mcp.tool()
def get_recent_changes(limit: int = 20) -> dict:
    """Return recently updated pages (newest first), up to ``limit`` items."""
    try:
        changes = client.get_recent_changes(limit=limit)
        return {"changes": changes, "count": len(changes)}
    except PukiWikiError as exc:
        return {"error": str(exc)}


# --------------------------------------------------------------------------
# Write tools
# --------------------------------------------------------------------------
def _write_error(page: str, exc: Exception) -> dict:
    if isinstance(exc, PukiWikiConflictError):
        msg = "conflict: page was modified concurrently; re-read and retry"
    elif isinstance(exc, PukiWikiReadOnlyError):
        msg = "readonly: page is frozen or the wiki is read-only"
    else:
        msg = str(exc)
    return {"page": page, "ok": False, "error": msg}


@mcp.tool()
def create_or_update_page(page: str, content: str) -> dict:
    """Create a page or overwrite its entire content with ``content``."""
    if err := _require_page(page):
        return {"page": page, "ok": False, "error": err}
    try:
        action = client.write_page(page, content)
        return {"page": page, "ok": True, "action": action}
    except PukiWikiError as exc:
        return _write_error(page, exc)


@mcp.tool()
def append_to_page(page: str, content: str, to_top: bool = False) -> dict:
    """Append ``content`` to a page (end by default, or top if ``to_top``)."""
    if err := _require_page(page):
        return {"page": page, "ok": False, "error": err}
    try:
        action = client.write_page(page, content, add=True, add_top=to_top)
        return {"page": page, "ok": True, "action": action}
    except PukiWikiError as exc:
        return _write_error(page, exc)


@mcp.tool()
def delete_page(page: str) -> dict:
    """Delete a page (writes empty content, which removes the page)."""
    if err := _require_page(page):
        return {"page": page, "ok": False, "error": err}
    try:
        client.delete_page(page)
        return {"page": page, "ok": True, "action": "deleted"}
    except PageNotFoundError:
        return {"page": page, "ok": False, "error": "page does not exist"}
    except PukiWikiError as exc:
        return _write_error(page, exc)
