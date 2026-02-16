"""
Router (File) untuk command 'ai'.
Menerima request dari bridge, lalu melempar ke sub-handler di folder aisub/
"""
from ai_logic.ui import ansi
# Arahkan ke folder baru "aisub" agar tidak bentrok dengan nama file ini "ai.py"
from ai_logic.aisub import registry

def handle(argv: list[str], cfg: dict) -> int:
    # 1. Handle default/no-arg -> Help
    if not argv:
        return registry.dispatch("help", [], cfg)

    # 2. Normalize subcommand (handle alias)
    cmd = argv[0].lower().strip()
    rest = argv[1:]

    # Alias mapping
    aliases = {
        "-h": "help",
        "--help": "help",
        "--status": "status",
    }
    cmd = aliases.get(cmd, cmd)

    # 3. Dispatch ke registry (yang ada di folder aisub)
    if registry.exists(cmd):
        return registry.dispatch(cmd, rest, cfg)

    # 4. Handle unknown
    ansi.print_brief_error(f"Unknown ai subcommand: '{cmd}'")
    ansi.print_info("Try './ai-term ai help' to see available commands.")
    return 2