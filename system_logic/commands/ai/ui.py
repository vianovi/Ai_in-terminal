from __future__ import annotations

from system_logic.terminal.ansi import print_info, print_brief_error


def print_root_help() -> None:
    print_info("Usage: ai <subcommand> [args...]")
    print_info("Coba: ai help")


def unknown_subcommand(name: str) -> None:
    print_brief_error(f"Unknown ai subcommand: '{name}'")
    print_info("Coba: ai help")
