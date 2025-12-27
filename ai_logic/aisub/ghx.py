"""AI-powered GitHub CLI wrapper."""
from ai_logic.ui import ansi

def handle(argv: list[str], cfg: dict) -> int:
    ansi.print_info(f"STUB: GitHub AI Extension args: {argv}")
    ansi.print_brief_error("Not implemented yet.")
    return 0