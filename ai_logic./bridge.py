"""AI_IN-TERMINAL — ai_logic.bridge
Version: 1.5 (2026-01-04)

Changelog (1.5)
- Made root router lighter via lazy imports (faster startup, fewer circular risks).
- Root help now prints program name dynamically and includes fish usage hint.

Compatibility
- dispatch(argv, cfg) signature unchanged.
"""

from __future__ import annotations

from ai_logic.ui.ansi import print_brief_error, print_info


def _prog_name() -> str:
    # Best-effort: prefer argv[0] when available; fallback to './ai-term'.
    try:
        import sys
        return sys.argv[0] or './ai-term'
    except Exception:
        return './ai-term'


def _root_help() -> None:
    prog = _prog_name()
    print_info("Format penggunaan:")
    print(f"  {prog} ask \"...\"")
    print(f"  {prog} ask --chat")
    print(f"  {prog} cmd \"...\"")
    print(f"  {prog} ai status")
    print(f"  {prog} ai help")
    print("\nFish tip:")
    print("  Kamu bisa pakai fungsi fish (kalau sudah dipasang): ask / cmd / ai")


def dispatch(argv: list[str], cfg: dict) -> int:
    """Router utama / single source of truth untuk root command.

    - argv di sini adalah argv mentah setelah nama program.
    - Semua handler menerima argv list (gaya C), sesuai blueprint.
    """
    if not argv or argv[0] in ("-h", "--help", "help"):
        _root_help()
        return 2

    root_cmd = argv[0].strip().lower()
    rest = argv[1:]

    if root_cmd == "ask":
        from ai_logic import ask
        return ask.handle(rest, cfg)
    if root_cmd == "cmd":
        from ai_logic import cmd
        return cmd.handle(rest, cfg)
    if root_cmd == "ai":
        from ai_logic import ai
        return ai.handle(rest, cfg)

    print_brief_error(f"Command '{root_cmd}' tidak dikenal.")
    _root_help()
    return 2
