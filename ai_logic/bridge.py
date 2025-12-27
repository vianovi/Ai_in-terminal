from __future__ import annotations

from ai_logic.ui.ansi import print_brief_error, print_info

# Root command handlers (argv-style, "gaya C")
from ai_logic import ask, cmd, ai


def _root_help() -> None:
    print_info("Format penggunaan:")
    print('  ./ai-term ask "..."')
    print("  ./ai-term ask --chat")
    print('  ./ai-term cmd "..."')
    print("  ./ai-term ai status")
    print("  ./ai-term ai help")


def dispatch(argv: list[str], cfg: dict) -> int:
    """
    Router utama / single source of truth untuk root command.
    - argv di sini adalah argv mentah setelah nama program.
    - Semua handler menerima argv list (gaya C), sesuai blueprint.
    """
    if not argv or argv[0] in ("-h", "--help", "help"):
        _root_help()
        return 2

    root_cmd = argv[0].strip().lower()
    rest = argv[1:]

    if root_cmd == "ask":
        return ask.handle(rest, cfg)
    if root_cmd == "cmd":
        return cmd.handle(rest, cfg)
    if root_cmd == "ai":
        return ai.handle(rest, cfg)

    print_brief_error(f"Command '{root_cmd}' tidak dikenal.")
    _root_help()
    return 2
