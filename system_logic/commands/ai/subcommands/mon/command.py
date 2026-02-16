"""
MON Command Router.
Entry point untuk semua subcommand 'ai mon'.
"""

from typing import List
from system_logic.terminal import ansi
from . import const
from .ui import (
    run_live_dashboard,
    run_battery_report,
    run_disk_report,
    run_network_diag,
    run_sensors_dump
)


def handle(argv: List[str], cfg: dict) -> int:
    """
    Main router untuk 'ai mon' command.

    Args:
        argv: Command line arguments setelah 'ai mon'
        cfg: Global config dict

    Returns:
        Exit code (0 = success)

    Supported commands:
        - live [args]
        - sensors
        - batt [args]
        - disk [args]
        - net [args]
        - net live [args]
        - help
    """
    # Check psutil availability
    try:
        import psutil
    except ImportError:
        psutil = None

    if not argv:
        mode = "live"
        rest = []
    else:
        mode = (argv[0] or "").strip().lower()
        rest = argv[1:]

    # Help command
    if mode in ("help", "-h", "--help"):
        _print_help()
        return 0

    # Sensors dump (no psutil check needed for partial info)
    if mode in ("sensors", "probe", "inventory"):
        return run_sensors_dump()

    # Check psutil for other commands
    if psutil is None:
        ansi.print_brief_error("Monitoring features require 'psutil' library.")
        print("Install (Fedora): sudo dnf install python3-psutil")
        return 1

    # Route to appropriate handler
    if mode in ("live", "dashboard", "now"):
        return run_live_dashboard(rest)

    elif mode in ("batt", "battery", "power"):
        return run_battery_report(rest)

    elif mode in ("disk", "storage", "smart"):
        return run_disk_report(rest)

    elif mode in ("net", "wifi", "ping", "network"):
        # Check for 'net live' subcommand
        if rest and rest[0].lower() == "live":
            return _run_net_live(rest[1:])
        return run_network_diag(rest)

    else:
        ansi.print_brief_error(f"Unknown mode: '{mode}'")
        print("Try: live | sensors | batt | disk | net | help")
        return 2


def _print_help():
    """Print help text."""
    ansi.print_info("AI Monitor (MON) — System Cockpit & Intelligence")
    print()

    print(f"{ansi.c_bold()}USAGE{ansi.c_reset()}")
    print("  ai mon live [--interval N] [--compact] [--target HOST] [--no-disks] [--maxwidth N]")
    print("  ai mon sensors")
    print("  ai mon batt  [--list|--history] [--pick] [--compare YYYY-MM-DD]")
    print("  ai mon disk  [--sudo|--deep] [--only DEV]")
    print("  ai mon net   [target]")
    print("  ai mon net live [target] [--interval N] [--window N]")
    print()

    print(f"{ansi.c_bold()}COMMANDS{ansi.c_reset()}")
    print("  live      : Real-time HUD (CPU, RAM, Network, Thermals)")
    print("  sensors   : Dump all available sensors")
    print("  batt      : Battery health + history")
    print("  disk      : SMART data + TBW/health history")
    print("  net       : Network diagnostics (ping, DNS, WiFi)")
    print("  net live  : Live ping graph")
    print()

    print(f"{ansi.c_bold()}EXAMPLES{ansi.c_reset()}")
    print("  ai mon live --interval 0.5 --target 1.1.1.1")
    print("  ai mon batt")
    print("  sudo ai mon disk --sudo   # Full SMART data")
    print("  ai mon net google.com")
    print("  ai mon net live 8.8.8.8 --window 60")
    print()

    print(f"{ansi.c_bold()}DEPENDENCIES{ansi.c_reset()}")
    print("  Required: python3-psutil")
    print("  Optional: lm_sensors smartmontools iw iproute pciutils")
    print()
    print("  Install (Fedora):")
    print("    sudo dnf install python3-psutil")
    print("    sudo dnf install lm_sensors smartmontools iw iproute")
    print()

    print(f"{ansi.c_dim()}History file:{ansi.c_reset()} {const.MON_HISTORY_PATH}")


def _run_net_live(args: List[str]) -> int:
    """
    Run live ping graph (alternate screen, non-scrolling).
    TODO: Implement if needed, or merge into dashboard.
    """
    ansi.print_brief_error("net live: Not yet implemented in refactor")
    print("Use: ai mon live --target <host>")
    return 1