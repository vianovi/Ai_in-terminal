from __future__ import annotations

from system_logic.commands.ai import ui
from system_logic.commands.ai.subcommands import registry


def handle(argv: list[str], cfg: dict) -> int:
    if not argv:
        return registry.dispatch("help", [], cfg)

    cmd = (argv[0] or "").strip().lower()
    rest = argv[1:]

    aliases = {
        "-h": "help",
        "--help": "help",
        "--status": "status",
    }
    cmd = aliases.get(cmd, cmd)

    if registry.exists(cmd):
        return registry.dispatch(cmd, rest, cfg)

    ui.unknown_subcommand(cmd)
    return 2
