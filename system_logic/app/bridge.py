from __future__ import annotations

from system_logic.terminal.ansi import print_brief_error, print_info


def _prog_name() -> str:
    try:
        import sys
        return sys.argv[0] or "./ai-term"
    except Exception:
        return "./ai-term"


def _root_help() -> None:
    prog = _prog_name()
    print_info("Format penggunaan:")
    print(f"  {prog} ask \"...\"")
    print(f"  {prog} ask --chat")
    print(f"  {prog} cmd \"...\"")
    print(f"  {prog} ai status")
    print(f"  {prog} ai help")
    print(f"  {prog} toolkit help")
    print(f"  {prog} toolkit deps")
    print("\nFish tip:")
    print("  Kamu bisa pakai fungsi fish (kalau sudah dipasang): ask / cmd / ai / toolkit")


def dispatch(argv: list[str], cfg: dict) -> int:
    if not argv or argv[0] in ("-h", "--help", "help"):
        _root_help()
        return 2

    root_cmd = argv[0].strip().lower()
    rest = argv[1:]

    if root_cmd == "ask":
        from system_logic.commands.ask.command import handle
        return handle(rest, cfg)

    if root_cmd == "cmd":
        from system_logic.commands.cmd.command import handle
        return handle(rest, cfg)

    if root_cmd == "ai":
        from system_logic.commands.ai.command import handle
        return handle(rest, cfg)

    if root_cmd == "toolkit":
        from system_logic.commands.toolkit.command import handle
        return handle(rest, cfg)

    print_brief_error(f"Command '{root_cmd}' tidak dikenal.")
    _root_help()
    return 2
