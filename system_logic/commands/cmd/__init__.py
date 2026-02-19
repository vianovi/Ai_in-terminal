"""
commands/cmd/__init__.py
========================
Public entry-point untuk `cmd` command.

Exports:
    handle(argv, cfg) -> int
"""
from system_logic.commands.cmd.command import handle

__all__ = ["handle"]