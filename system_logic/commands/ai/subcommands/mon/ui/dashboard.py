"""
MON Live Dashboard.
Core logic untuk 'ai mon live' - real-time system monitoring.
"""

import time
import shutil
from typing import Optional, List
from system_logic.terminal import ansi
from ..collectors import collect_system_stats, collect_hardware_stats, collect_network_stats, PingMonitor
from ..sentinel import SentinelEngine
from ..utils import human_bytes
from .render import draw_bar, align_columns, format_uptime, trim_string


def run_live_dashboard(args: List[str]) -> int:
    """
    Main entry point untuk live dashboard.

    Args:
        args: CLI arguments (parsed for --interval, --compact, etc.)

    Returns:
        Exit code (0 = success)
    """
    # Parse arguments
    interval = 1.0
    compact = False
    target = "8.8.8.8"
    show_disks = True
    max_width = None

    i = 0
    while i < len(args):
        arg = args[i]

        if arg in ("--interval", "-i"):
            if i + 1 < len(args):
                try:
                    interval = float(args[i + 1])
                    i += 2
                    continue
                except ValueError:
                    pass

        elif arg == "--compact":
            compact = True
            i += 1
            continue

        elif arg in ("--target", "-t"):
            if i + 1 < len(args):
                target = args[i + 1]
                i += 2
                continue

        elif arg == "--no-disks":
            show_disks = False
            i += 1
            continue

        elif arg == "--maxwidth":
            if i + 1 < len(args):
                try:
                    max_width = int(args[i + 1])
                    i += 2
                    continue
                except ValueError:
                    pass

        i += 1

    # Initialize components
    ping_monitor = PingMonitor(target=target, window=60)
    ping_monitor.start()

    sentinel = SentinelEngine()

    try:
        ansi.alt_screen_enter()
        ansi.cursor_hide()

        while True:
            # Get terminal size
            term_w, term_h = ansi.term_size()
            if max_width:
                term_w = min(term_w, max_width)

            # Clear screen
            ansi.clear_screen()

            # Collect data
            sys_stats = collect_system_stats()
            hw_stats = collect_hardware_stats()
            net_stats = collect_network_stats()
            ping_stats = ping_monitor.get_stats()
            sentinel_stats = sentinel.tick()

            # Render header
            _render_header(sys_stats, sentinel_stats, term_w, compact)

            # Render body
            print()
            _render_system_section(sys_stats, term_w)
            print()
            _render_hardware_section(hw_stats, term_w)
            print()
            _render_network_section(net_stats, ping_stats, term_w)

            if show_disks and hw_stats.get('disks', {}).get('ok'):
                print()
                _render_disk_section(hw_stats['disks'], term_w)

            # Footer
            print()
            print(f"{ansi.c_dim()}Press Ctrl+C to exit. Tip: ai mon sensors{ansi.c_reset()}")

            time.sleep(interval)

    except KeyboardInterrupt:
        pass
    finally:
        ping_monitor.stop()
        ansi.cursor_show()
        ansi.alt_screen_exit()

    return 0


def _render_header(sys_stats: dict, sentinel_stats: dict, width: int, compact: bool):
    """Render header dengan host info dan sentinel status."""
    import socket
    import platform

    hostname = socket.gethostname()
    os_name = platform.system()
    kernel = platform.release()

    # Sentinel indicator
    sentinel_status = "🛡️  OK"
    if sentinel_stats.get('alert_level', 0) >= 2:
        sentinel_status = f"⚠️  {sentinel_stats.get('status', 'WARNING')}"
    elif sentinel_stats.get('alert_level', 0) >= 3:
        sentinel_status = f"🔴 {sentinel_stats.get('status', 'KILLING')}"

    # Header line
    title = f"{ansi.c_bold()}MON • Live Cockpit{ansi.c_reset()}"
    timestamp = time.strftime("%H:%M:%S")

    line1 = align_columns(title, timestamp, width)
    print(line1)

    if not compact:
        line2 = f"{ansi.c_dim()}Host:{ansi.c_reset()} {hostname} | {os_name} {kernel}"
        print(line2)

    line3 = f"Sentinel: {sentinel_status}"
    if sys_stats.get('uptime_sec'):
        uptime_str = format_uptime(sys_stats['uptime_sec'])
        line3 += f"  {ansi.c_dim()}Uptime:{ansi.c_reset()} {uptime_str}"
    print(line3)


def _render_system_section(stats: dict, width: int):
    """Render CPU, RAM, Swap section."""
    if not stats.get('ok'):
        print(f"{ansi.c_red()}System stats unavailable{ansi.c_reset()}")
        return

    print(f"{ansi.c_bold()}SYSTEM{ansi.c_reset()}")

    # CPU
    cpu_pct = stats.get('cpu_pct', 0.0)
    cpu_count = stats.get('cpu_count', 1)
    cpu_bar = draw_bar(cpu_pct, width=14)
    cpu_line = f"  CPU: {cpu_bar} {cpu_pct:>5.1f}%  ({cpu_count} cores)"
    print(cpu_line)

    # Load Average
    load_1m = stats.get('load_1m')
    if load_1m is not None:
        load_5m = stats.get('load_5m', 0)
        load_15m = stats.get('load_15m', 0)
        print(f"  Load: {load_1m:.2f} {load_5m:.2f} {load_15m:.2f}")

    # RAM
    ram_pct = stats.get('ram_pct', 0.0)
    ram_used = stats.get('ram_used', 0)
    ram_total = stats.get('ram_total', 1)
    ram_bar = draw_bar(ram_pct, width=14)
    ram_line = f"  RAM: {ram_bar} {ram_pct:>5.1f}%  ({human_bytes(ram_used)}/{human_bytes(ram_total)})"
    print(ram_line)

    # Swap
    swap_pct = stats.get('swap_pct', 0.0)
    swap_used = stats.get('swap_used', 0)
    swap_total = stats.get('swap_total', 1)
    swap_bar = draw_bar(swap_pct, width=14)
    swap_line = f"  SWAP: {swap_bar} {swap_pct:>5.1f}%  ({human_bytes(swap_used)}/{human_bytes(swap_total)})"
    print(swap_line)


def _render_hardware_section(stats: dict, width: int):
    """Render battery dan thermal section."""
    print(f"{ansi.c_bold()}HARDWARE{ansi.c_reset()}")

    # Battery
    batt = stats.get('battery', {})
    if batt.get('ok'):
        batt_pct = batt.get('percent', 0)
        plugged = batt.get('plugged', False)
        status_icon = "🔌" if plugged else "🔋"
        batt_line = f"  Battery: {status_icon} {batt_pct:.0f}%"

        time_left = batt.get('time_left_sec')
        if time_left and time_left > 0:
            hours = time_left // 3600
            mins = (time_left % 3600) // 60
            batt_line += f"  ({hours}h {mins}m remaining)"

        print(batt_line)
    else:
        print(f"  Battery: {ansi.c_dim()}N/A{ansi.c_reset()}")

    # Thermal
    thermal = stats.get('thermal', {})
    if thermal.get('ok'):
        temps = thermal.get('temperatures', {})
        if temps:
            # Show first/main temp sensor
            first_sensor = list(temps.values())[0]
            temp_val = first_sensor.get('current')
            if temp_val:
                temp_col = ansi.c_green() if temp_val < 70 else (ansi.c_yellow() if temp_val < 85 else ansi.c_red())
                print(f"  Temp: {temp_col}{temp_val:.1f}°C{ansi.c_reset()}")

        fans = thermal.get('fans', {})
        if fans:
            first_fan = list(fans.values())[0]
            if first_fan:
                print(f"  Fan: {first_fan:.0f} RPM")


def _render_network_section(net_stats: dict, ping_stats: dict, width: int):
    """Render network I/O dan ping section."""
    print(f"{ansi.c_bold()}NETWORK{ansi.c_reset()}")

    if net_stats.get('ok'):
        from ..utils import human_rate_bps
        rx = human_rate_bps(net_stats.get('bytes_recv', 0))
        tx = human_rate_bps(net_stats.get('bytes_sent', 0))
        print(f"  I/O: ↓{rx}  ↑{tx}")

    # Ping
    target = ping_stats.get('target', 'unknown')
    last_ping = ping_stats.get('last_ping')
    avg_ping = ping_stats.get('avg_ping')
    loss_pct = ping_stats.get('loss_pct', 0)

    if last_ping is not None:
        ping_col = ansi.c_green() if last_ping < 40 else (ansi.c_yellow() if last_ping < 120 else ansi.c_red())
        ping_txt = f"{ping_col}{last_ping:.1f}ms{ansi.c_reset()}"
    else:
        ping_txt = f"{ansi.c_red()}LOST{ansi.c_reset()}"

    avg_txt = f"{avg_ping:.1f}ms" if avg_ping else "N/A"

    print(f"  Ping [{target}]: {ping_txt}  avg:{avg_txt}  loss:{loss_pct:.1f}%")


def _render_disk_section(disk_stats: dict, width: int):
    """Render mounted disks table."""
    print(f"{ansi.c_bold()}DISKS{ansi.c_reset()}")

    disks = disk_stats.get('disks', [])
    if not disks:
        print(f"  {ansi.c_dim()}No mounted disks{ansi.c_reset()}")
        return

    for disk in disks[:5]:  # Max 5 disks
        mp = trim_string(disk['mountpoint'], 20, 'start')
        pct = disk['percent']
        free = human_bytes(disk['free'])
        total = human_bytes(disk['total'])

        bar = draw_bar(pct, width=10)
        print(f"  {mp:20} {bar} {pct:>5.1f}%  {free}/{total}")