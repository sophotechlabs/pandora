from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator
from typing import Any

from asgiref.sync import sync_to_async
from django.db import connections
from mcp.server.mcpserver import MCPServer

from pandora.core.models import IngestToken
from pandora.mcp import tools

TOKEN_ENV = "PANDORA_MCP_TOKEN"
INSTRUCTIONS = """Pandora is a self-hosted event tracker. Issues group occurrences
from Alertmanager webhooks and from Sentry SDKs. Search with the same query
language the operator UI uses: is:unresolved, level:error, env:p-mk1,
label:namespace=payments, tag:pod=ledger-1, seen:1h, age:1d, or free text.
Every tool is read-only and scoped to one project."""


def token() -> IngestToken:
    value = os.environ.get(TOKEN_ENV, "").strip()
    if not value:
        raise tools.ToolError(f"{TOKEN_ENV} is not set")
    return tools.resolve_token(value)


def build(name: str = "pandora") -> MCPServer:
    server = MCPServer(name=name, instructions=INSTRUCTIONS)

    @server.tool()
    async def search_issues(
        query: str = "", limit: int | None = None
    ) -> dict[str, Any]:
        """Search issues with Pandora's query language. Returns the newest first."""
        return await sync_to_async(_search)(query, limit)

    @server.tool()
    async def get_issue(issue_id: int) -> dict[str, Any]:
        """Read one issue with its episodes and its tag breakdown."""
        return await sync_to_async(_issue)(issue_id)

    @server.tool()
    async def get_issue_events(
        issue_id: int, limit: int | None = None
    ) -> dict[str, Any]:
        """Read the stored occurrences of one issue, newest first, with stack traces."""
        return await sync_to_async(_events)(issue_id, limit)

    @server.tool()
    async def issue_as_markdown(issue_id: int) -> str:
        """Render one issue as Markdown — the form to paste into a ticket or a chat."""
        return await sync_to_async(_markdown)(issue_id)

    return server


def _search(query: str, limit: int | None) -> dict[str, Any]:
    with _connection():
        return tools.search_issues(token(), query, limit)


def _issue(issue_id: int) -> dict[str, Any]:
    with _connection():
        return tools.get_issue(token(), issue_id)


def _events(issue_id: int, limit: int | None) -> dict[str, Any]:
    with _connection():
        return tools.get_issue_events(token(), issue_id, limit)


def _markdown(issue_id: int) -> str:
    with _connection():
        return tools.issue_as_markdown(token(), issue_id)


@contextlib.contextmanager
def _connection() -> Iterator[None]:
    try:
        yield
    finally:
        connections.close_all()
