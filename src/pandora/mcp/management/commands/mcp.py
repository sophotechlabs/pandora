from __future__ import annotations

from importlib import import_module
from typing import Any

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Serve Pandora's read-only MCP tools over stdio"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--name", default="pandora", help="server name agents see")

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            server = import_module("pandora.mcp.server")
        except ImportError as error:
            raise CommandError(
                "the mcp extra is not installed — pip install 'pandora[mcp]'"
            ) from error

        server.build(options["name"]).run(transport="stdio")
