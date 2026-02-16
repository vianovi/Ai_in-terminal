from ai_logic.ui import ansi

def handle(argv: list[str], cfg: dict) -> int:
    # IMPORT RAHASIA: Import di dalam fungsi untuk hindari circular loop
    # Pastikan kita import dari folder baru: ai_logic.aisub
    from ai_logic.aisub import registry

    cmds = registry.get_all_commands()

    print("Usage: ai <subcommand> [args...]")
    print("")
    ansi.print_info("Available AI Subcommands:")

    for cmd_name in cmds:
        mod = registry.get_module(cmd_name)
        doc = getattr(mod, '__doc__', '') or ""
        desc = doc.strip().split('\n')[0] or "(No description)"
        print(f"  {cmd_name:<10} : {desc}")

    print("\nAlias:")
    print("  ai --status -> ai status")
    print("  ai -h       -> ai help")
    return 0