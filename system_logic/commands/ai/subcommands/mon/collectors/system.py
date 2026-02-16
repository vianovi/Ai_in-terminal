"""
MON System Collector.
Membaca: CPU, RAM, Swap, Load Average, Uptime.
"""

from typing import Dict, Any, Optional

try:
    import psutil  # type: ignore
except ImportError:
    psutil = None


def collect_system_stats() -> Dict[str, Any]:
    """
    Membaca statistik sistem dasar.

    Returns:
        Dict berisi: cpu_pct, cpu_count, load_1m/5m/15m, ram_*, swap_*, uptime
    """
    if not psutil:
        return {
            "ok": False,
            "error": "psutil not available"
        }

    try:
        # CPU
        cpu_pct = psutil.cpu_percent(interval=0.1)
        cpu_count = psutil.cpu_count(logical=True) or 1

        # Load Average
        try:
            load = psutil.getloadavg()
            load_1m, load_5m, load_15m = load
        except (AttributeError, OSError):
            load_1m = load_5m = load_15m = None

        # RAM
        vm = psutil.virtual_memory()
        ram_total = vm.total
        ram_used = vm.used
        ram_free = vm.available
        ram_pct = vm.percent

        # Swap
        sw = psutil.swap_memory()
        swap_total = sw.total
        swap_used = sw.used
        swap_free = sw.free
        swap_pct = sw.percent

        # Uptime
        try:
            boot_time = psutil.boot_time()
            import time
            uptime_sec = int(time.time() - boot_time)
        except (AttributeError, OSError):
            uptime_sec = None

        return {
            "ok": True,
            "cpu_pct": cpu_pct,
            "cpu_count": cpu_count,
            "load_1m": load_1m,
            "load_5m": load_5m,
            "load_15m": load_15m,
            "ram_total": ram_total,
            "ram_used": ram_used,
            "ram_free": ram_free,
            "ram_pct": ram_pct,
            "swap_total": swap_total,
            "swap_used": swap_used,
            "swap_free": swap_free,
            "swap_pct": swap_pct,
            "uptime_sec": uptime_sec,
        }

    except Exception as e:
        return {
            "ok": False,
            "error": str(e)
        }