from __future__ import annotations

from typing import Protocol


class Subcommand(Protocol):
    def handle(self, argv: list[str], cfg: dict) -> int: ...
