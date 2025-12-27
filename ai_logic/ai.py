from __future__ import annotations

from ai_logic import status


def _ai_help() -> None:
    print("Usage:")
    print("  ./ai-term ai status")
    print("  ./ai-term ai help")
    print("Alias:")
    print("  ./ai-term ai --status")
    print("  ./ai-term ai --help")
    print("  ./ai-term ai -h")


def handle(argv: list[str], cfg: dict) -> int:
    """
    Router internal subcommand `ai`.
    Baseline: status, help + alias (sesuai blueprint).
    """
    if not argv:
        _ai_help()
        return 2

    a0 = argv[0].strip().lower()

    if a0 in ("-h", "--help", "help"):
        _ai_help()
        return 0

    if a0 in ("--status", "status"):
        return status.run_status(cfg)

    # Futureproof placeholders (tidak mengganggu baseline)
    if a0 in ("last-error", "--last-error"):
        return status.run_last_error(cfg)

    if a0 in ("info", "--info"):
        return status.run_info(cfg)

    _ai_help()
    return 2
