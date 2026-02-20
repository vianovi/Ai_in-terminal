"""
commands/ask/__init__.py
========================
Public entry-point untuk `ask` command.

Exports:
    handle(argv, cfg) -> int
"""
from system_logic.commands.ask.command import handle

__all__ = ["handle"]