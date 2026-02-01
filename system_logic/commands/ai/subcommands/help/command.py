from __future__ import annotations

from system_logic.terminal import ansi


def handle(argv: list[str], cfg: dict) -> int:
    from system_logic.commands.ai.subcommands import registry

    print("Usage: ai <subcommand> [args...]")
    print("")
    ansi.print_info("Available AI Subcommands:")

    for name in registry.get_all_commands():
        mod = registry.get_module(name)
        doc = getattr(mod, "__doc__", "") or ""
        desc = doc.strip().split("\n")[0] or "(No description)"
        print(f"  {name:<10} : {desc}")

    return 0
