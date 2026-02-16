"""
MON Static Reports.
Battery report, Disk SMART, Network diagnostics, Sensors dump.
"""

from typing import List
from system_logic.terminal import ansi
from ..collectors import collect_hardware_stats, collect_network_stats, collect_system_stats
from ..storage import get_comparison_text, list_metric_dates
from ..utils import human_bytes


def run_battery_report(args: List[str]) -> int:
    """Generate battery health report dengan history."""
    from ..collectors.hardware import collect_battery_stats

    batt = collect_battery_stats()

    if not batt.get('ok'):
        ansi.print_brief_error("Battery not found or unavailable")
        return 1

    print(f"{ansi.c_bold()}BATTERY REPORT{ansi.c_reset()}\n")

    # Current status
    pct = batt.get('percent', 0)
    plugged = batt.get('plugged', False)
    status = "Charging" if plugged else "Discharging"

    print(f"Status: {status}")
    print(f"Charge: {pct:.1f}%")

    time_left = batt.get('time_left_sec')
    if time_left and time_left > 0:
        hours = time_left // 3600
        mins = (time_left % 3600) // 60
        print(f"Time remaining: {hours}h {mins}m")

    # History comparison
    print(f"\n{ansi.c_bold()}HISTORY{ansi.c_reset()}")

    comparison = get_comparison_text(
        category="battery",
        item_id="BAT0",
        metric="health_pct",
        current=pct,
        unit="%",
        window_months=30
    )

    if comparison:
        print(comparison)
    else:
        print(f"{ansi.c_dim()}No history data available{ansi.c_reset()}")

    return 0


def run_disk_report(args: List[str]) -> int:
    """Generate disk SMART report dengan TBW/health history."""
    use_sudo = "--sudo" in args or "--deep" in args

    hw_stats = collect_hardware_stats(use_sudo=use_sudo)
    disk_stats = hw_stats.get('disks', {})

    if not disk_stats.get('ok'):
        ansi.print_brief_error("Failed to read disk stats")
        return 1

    print(f"{ansi.c_bold()}DISK REPORT{ansi.c_reset()}\n")

    # Mounted disks
    disks = disk_stats.get('disks', [])
    if disks:
        print(f"{ansi.c_bold()}Mounted Partitions:{ansi.c_reset()}")
        for disk in disks:
            mp = disk['mountpoint']
            pct = disk['percent']
            free = human_bytes(disk['free'])
            total = human_bytes(disk['total'])
            print(f"  {mp:30} {pct:>5.1f}%  {free}/{total}")
        print()

    # SMART data
    smart = disk_stats.get('smart', {})
    if smart:
        print(f"{ansi.c_bold()}SMART Data:{ansi.c_reset()}")
        for dev, attrs in smart.items():
            print(f"\n  {dev}:")
            if 'wear_pct' in attrs:
                wear = attrs['wear_pct']
                health = 100 - wear
                print(f"    Health: {health}% (Wear: {wear}%)")

            if 'tbw' in attrs:
                tbw = attrs['tbw']
                print(f"    TBW: {tbw:.2f} TB")
    else:
        if not use_sudo:
            print(f"{ansi.c_dim()}Run with --sudo for SMART data{ansi.c_reset()}")

    return 0


def run_network_diag(args: List[str]) -> int:
    """Network diagnostics: ping, DNS, WiFi."""
    target = args[0] if args else "8.8.8.8"

    print(f"{ansi.c_bold()}NETWORK DIAGNOSTICS{ansi.c_reset()}\n")

    # Network I/O
    net_stats = collect_network_stats()
    if net_stats.get('ok'):
        print(f"{ansi.c_bold()}Network I/O:{ansi.c_reset()}")
        from ..utils import human_rate_bps
        rx = human_rate_bps(net_stats.get('bytes_recv', 0))
        tx = human_rate_bps(net_stats.get('bytes_sent', 0))
        print(f"  RX: {rx}")
        print(f"  TX: {tx}\n")

    # WiFi
    from ..collectors.network import collect_wifi_stats
    wifi = collect_wifi_stats()
    if wifi.get('ok'):
        print(f"{ansi.c_bold()}WiFi:{ansi.c_reset()}")
        print(f"  Interface: {wifi.get('iface')}")
        print(f"  SSID: {wifi.get('ssid')}")
        signal = wifi.get('signal_dbm')
        if signal:
            print(f"  Signal: {signal:.1f} dBm")
        print()

    # Ping test
    print(f"{ansi.c_bold()}Ping Test [{target}]:{ansi.c_reset()}")

    from ..collectors.network import PingMonitor
    import time

    ping = PingMonitor(target=target, window=10)
    ping.start()

    try:
        # Wait 5 seconds untuk collect data
        for i in range(5):
            time.sleep(1)
            stats = ping.get_stats()
            last = stats.get('last_ping')
            if last:
                print(f"  {i+1}. {last:.1f} ms")
            else:
                print(f"  {i+1}. LOST")

        # Final stats
        stats = ping.get_stats()
        avg = stats.get('avg_ping')
        jitter = stats.get('jitter')
        loss = stats.get('loss_pct', 0)

        print(f"\nAverage: {avg:.1f} ms" if avg else "\nAverage: N/A")
        print(f"Jitter: {jitter:.1f} ms" if jitter else "Jitter: N/A")
        print(f"Loss: {loss:.1f}%")

    finally:
        ping.stop()

    return 0


def run_sensors_dump() -> int:
    """Dump semua sensor yang bisa dibaca (discovery mode)."""
    print(f"{ansi.c_bold()}MON SENSORS INVENTORY{ansi.c_reset()}\n")

    # System
    sys_stats = collect_system_stats()
    if sys_stats.get('ok'):
        print(f"{ansi.c_bold()}System:{ansi.c_reset()}")
        for key, val in sys_stats.items():
            if key != 'ok':
                print(f"  {key}: {val}")
        print()

    # Hardware
    hw_stats = collect_hardware_stats()

    # Battery
    batt = hw_stats.get('battery', {})
    if batt.get('ok'):
        print(f"{ansi.c_bold()}Battery:{ansi.c_reset()}")
        for key, val in batt.items():
            if key != 'ok':
                print(f"  {key}: {val}")
        print()

    # Thermal
    thermal = hw_stats.get('thermal', {})
    if thermal.get('ok'):
        print(f"{ansi.c_bold()}Temperatures:{ansi.c_reset()}")
        temps = thermal.get('temperatures', {})
        for label, data in temps.items():
            print(f"  {label}: {data.get('current')}°C")

        print(f"\n{ansi.c_bold()}Fans:{ansi.c_reset()}")
        fans = thermal.get('fans', {})
        for label, rpm in fans.items():
            print(f"  {label}: {rpm} RPM")
        print()

    # Disks
    disks = hw_stats.get('disks', {})
    if disks.get('ok'):
        print(f"{ansi.c_bold()}Disks:{ansi.c_reset()}")
        for disk in disks.get('disks', []):
            print(f"  {disk['device']} → {disk['mountpoint']}")
            print(f"    Total: {human_bytes(disk['total'])}")
            print(f"    Used: {disk['percent']:.1f}%")
        print()

    # Network
    net_stats = collect_network_stats()
    if net_stats.get('ok'):
        print(f"{ansi.c_bold()}Network:{ansi.c_reset()}")
        print(f"  Bytes RX: {net_stats.get('bytes_recv')}")
        print(f"  Bytes TX: {net_stats.get('bytes_sent')}")
        print()

    print(f"{ansi.c_dim()}Tip: Use 'sudo ai mon disk' for SMART data{ansi.c_reset()}")

    return 0