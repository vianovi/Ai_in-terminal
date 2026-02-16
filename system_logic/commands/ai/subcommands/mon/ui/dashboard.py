"""
MON Live Dashboard - FIXED VERSION
Wrapper functions added to bridge dashboard with collectors
"""

from __future__ import annotations

import os
import time
import shutil
import socket
from typing import Optional, List, Tuple, Dict, Any

try:
    import psutil
except ImportError:
    psutil = None

from system_logic.terminal import ansi
from system_logic.core.paths import MON_HISTORY_DB, MON_HISTORY_LOCK
from system_logic.core.storage import (
    snapshot_metric_sql,
    ensure_dir,
)

from ..config import load_config
from ..collectors.system import collect_system_stats
from ..collectors.hardware import collect_hardware_stats, collect_battery_stats, collect_thermal_stats
from ..collectors.network import collect_network_stats, collect_wifi_stats, PingMonitor
from ..collectors.process import collect_process_stats

from ..utils import (
    draw_bar,
    human_bytes,
    human_rate_bps,
    align_lr,
    now_ts,
    clamp,
    trim_name,
    trim_tail,
    col_by_temp,
    col_by_ping,
    col_by_dbm,
)


# ==========================================================
# WRAPPER FUNCTIONS (Bridge collectors with dashboard)
# ==========================================================

def get_cpu_percent() -> float:
    """Wrapper for CPU percentage."""
    stats = collect_system_stats()
    return float(stats.get('cpu_pct', 0.0))


def get_memory_info() -> Tuple[float, float, float]:
    """Wrapper for memory info. Returns (pct, used, total)."""
    stats = collect_system_stats()
    return (
        float(stats.get('ram_pct', 0.0)),
        float(stats.get('ram_used', 0)),
        float(stats.get('ram_total', 1))
    )


def get_swap_info() -> Dict[str, Any]:
    """Wrapper for swap info."""
    stats = collect_system_stats()
    return {
        'ok': stats.get('ok', False),
        'pct': float(stats.get('swap_pct', 0.0)),
        'used': int(stats.get('swap_used', 0)),
        'total': int(stats.get('swap_total', 0))
    }


def get_loadavg() -> Tuple[float, float, float]:
    """Wrapper for load average."""
    stats = collect_system_stats()
    return (
        float(stats.get('load_1m', 0.0)),
        float(stats.get('load_5m', 0.0)),
        float(stats.get('load_15m', 0.0))
    )


def get_uptime_str() -> str:
    """Wrapper for uptime string."""
    stats = collect_system_stats()
    secs = stats.get('uptime_sec', 0)
    if not secs:
        return "?"

    d = int(secs // 86400)
    secs %= 86400
    h = int(secs // 3600)
    m = int((secs % 3600) // 60)
    return f"{d}d {h}h {m}m"


def read_temperatures() -> Dict[str, Optional[float]]:
    """Wrapper for temperatures."""
    hw = collect_hardware_stats()
    thermal = hw.get('thermal', {})
    temps = thermal.get('temperatures', {})

    return {
        'cpu': temps.get('cpu_package', {}).get('current'),
        'ssd': temps.get('nvme', {}).get('current'),
        'wifi': temps.get('wifi', {}).get('current')
    }


def read_fan_rpm() -> Optional[int]:
    """Wrapper for fan RPM."""
    hw = collect_hardware_stats()
    thermal = hw.get('thermal', {})
    fans = thermal.get('fans', {})

    # Return first fan found
    for rpm in fans.values():
        if isinstance(rpm, (int, float)) and rpm > 0:
            return int(rpm)
    return None


def read_power() -> Dict[str, Any]:
    """Wrapper for power info."""
    batt = collect_battery_stats()
    return {
        'ok': batt.get('ok', False),
        'percent': batt.get('percent'),
        'status_raw': batt.get('status', 'Unknown'),
        'plugged': batt.get('plugged', False),
        'watt_str': batt.get('power_w_str', 'N/A'),
        'volt_str': batt.get('voltage_v_str', 'N/A'),
        'amp_str': batt.get('current_a_str', 'N/A'),
        'secs_left': batt.get('time_left_sec')
    }


def read_power_capacity() -> Dict[str, Any]:
    """Wrapper for battery capacity."""
    batt = collect_battery_stats()
    return {
        'ok': batt.get('health_ok', False),
        'model': batt.get('model', 'BAT'),
        'health_pct': batt.get('health_pct', 0.0),
        'full': batt.get('capacity_full', 0.0),
        'design': batt.get('capacity_design', 0.0),
        'unit': batt.get('unit', 'Wh'),
        'cycle': batt.get('cycle_count', '?')
    }


def get_top_cpu_processes(total_cpu: float, n: int = 3) -> List[Tuple[str, float, float]]:
    """Wrapper for top CPU processes."""
    procs = collect_process_stats(limit=n)
    if not procs.get('ok'):
        return []

    top = procs.get('top_cpu', [])
    result = []

    for p in top[:n]:
        name = p.get('name', '?')
        pct = float(p.get('cpu_pct', 0.0))
        share = (pct / total_cpu * 100.0) if total_cpu > 0.1 else 0.0
        share = clamp(share, 0.0, 100.0)
        result.append((name, pct, share))

    return result


def get_top_mem_processes(n: int = 3) -> List[Tuple[str, float]]:
    """Wrapper for top memory processes."""
    procs = collect_process_stats(limit=n)
    if not procs.get('ok'):
        return []

    top = procs.get('top_mem', [])
    result = []

    for p in top[:n]:
        name = p.get('name', '?')
        rss_gib = float(p.get('rss_mb', 0.0)) / 1024.0
        result.append((name, rss_gib))

    return result


class NetSpeedometer:
    """Wrapper for network speedometer."""
    def __init__(self, iface: Optional[str] = None):
        self.iface = iface
        self.rx = 0.0
        self.tx = 0.0
        self.prev_stats = None
        self.prev_time = time.time()

    def update(self):
        stats = collect_network_stats(iface=self.iface)
        if not stats.get('ok'):
            return

        curr_time = time.time()
        dt = curr_time - self.prev_time

        if dt <= 0:
            return

        curr_rx = stats.get('bytes_recv', 0)
        curr_tx = stats.get('bytes_sent', 0)

        if self.prev_stats:
            prev_rx = self.prev_stats.get('bytes_recv', 0)
            prev_tx = self.prev_stats.get('bytes_sent', 0)

            self.rx = (curr_rx - prev_rx) / dt
            self.tx = (curr_tx - prev_tx) / dt

        self.prev_stats = {'bytes_recv': curr_rx, 'bytes_sent': curr_tx}
        self.prev_time = curr_time


class WiFiReader:
    @staticmethod
    def read(iface: Optional[str] = None) -> Dict[str, Any]:
        """Wrapper for WiFi stats."""
        wifi = collect_wifi_stats(iface=iface)
        return {
            'ok': wifi.get('ok', False),
            'iface': wifi.get('iface', ''),
            'ssid': wifi.get('ssid', ''),
            'signal_dbm': wifi.get('signal_dbm'),
            'rx_bitrate': wifi.get('rx_bitrate', ''),
            'tx_bitrate': wifi.get('tx_bitrate', '')
        }


class PingSampler:
    """Wrapper for ping sampler."""
    def __init__(self, target: str, interval: float = 5.0):
        self.monitor = PingMonitor(target=target, window=60)
        self.last_ms = None

    def start(self):
        self.monitor.start()

    def stop(self):
        self.monitor.stop()

    def avg(self) -> Optional[float]:
        stats = self.monitor.get_stats()
        return stats.get('avg_ping')

    def jitter(self) -> Optional[float]:
        stats = self.monitor.get_stats()
        return stats.get('jitter')

    def update_last(self):
        stats = self.monitor.get_stats()
        self.last_ms = stats.get('last_ping')



# ==========================================================
# DISK I/O SAMPLER (same as original)
# ==========================================================

class DiskIOSampler:
    """Sampling /sys/block/<dev>/stat for active% and R/W speed."""

    def __init__(self, dev: str) -> None:
        self.dev = dev
        self.t0 = time.time()
        self.prev = self._read_stat()
        self.sector_size = self._read_sector_size()
        self.active_pct = 0.0
        self.read_bps = 0.0
        self.write_bps = 0.0

    def _read_sector_size(self) -> int:
        from pathlib import Path
        p = Path(f"/sys/block/{self.dev}/queue/hw_sector_size")
        try:
            v = int(p.read_text().strip())
            return v if v > 0 else 512
        except Exception:
            return 512

    def _read_stat(self) -> Optional[List[int]]:
        from pathlib import Path
        p = Path(f"/sys/block/{self.dev}/stat")
        try:
            s = p.read_text().strip()
            return [int(x) for x in s.split()]
        except Exception:
            return None

    def update(self) -> None:
        cur = self._read_stat()
        t1 = time.time()
        dt = t1 - self.t0

        if cur is None or self.prev is None or dt <= 0:
            self.t0 = t1
            self.prev = cur
            self.active_pct = 0.0
            self.read_bps = 0.0
            self.write_bps = 0.0
            return

        try:
            d_read_sect = cur[2] - self.prev[2]
            d_write_sect = cur[6] - self.prev[6]
            d_io_ms = cur[9] - self.prev[9]

            self.read_bps = (d_read_sect * self.sector_size) / dt
            self.write_bps = (d_write_sect * self.sector_size) / dt
            self.active_pct = clamp((d_io_ms / (dt * 1000.0)) * 100.0, 0.0, 100.0)
        except Exception:
            self.active_pct = 0.0
            self.read_bps = 0.0
            self.write_bps = 0.0

        self.t0 = t1
        self.prev = cur


def _pick_root_disk_dev() -> Optional[str]:
    """Best-effort: choose disk backing '/'."""
    from pathlib import Path

    # Try findmnt
    try:
        import subprocess
        p = subprocess.run(
            ["findmnt", "-n", "-o", "SOURCE", "/"],
            capture_output=True,
            text=True,
            timeout=2
        )
        if p.returncode == 0 and p.stdout:
            src = p.stdout.strip()
            if src.startswith("/dev/"):
                base = os.path.basename(src)
                # Try lsblk to get parent disk
                p2 = subprocess.run(
                    ["lsblk", "-no", "PKNAME", f"/dev/{base}"],
                    capture_output=True,
                    text=True,
                    timeout=2
                )
                if p2.returncode == 0 and p2.stdout.strip():
                    return p2.stdout.strip()
                if Path(f"/sys/block/{base}").exists():
                    return base
    except Exception:
        pass

    # Fallback: prefer nvme0n1
    if Path("/sys/block/nvme0n1").exists():
        return "nvme0n1"

    # Last resort: first disk from lsblk
    if shutil.which("lsblk"):
        try:
            import subprocess
            p = subprocess.run(
                ["lsblk", "-d", "-n", "-o", "NAME,TYPE"],
                capture_output=True,
                text=True,
                timeout=2
            )
            if p.returncode == 0 and p.stdout:
                for ln in p.stdout.splitlines():
                    parts = ln.split()
                    if len(parts) >= 2 and parts[1] == "disk":
                        return parts[0].strip()
        except Exception:
            pass

    return None


# ==========================================================
# MOUNTED PARTITIONS (same as original)
# ==========================================================

_IGNORED_FS_TYPES = {
    "proc", "sysfs", "devtmpfs", "devpts", "tmpfs", "cgroup", "cgroup2",
    "pstore", "securityfs", "debugfs", "tracefs", "configfs", "efivarfs",
    "mqueue", "hugetlbfs", "fusectl", "autofs", "overlay", "squashfs",
}

def _mounted_partitions() -> list[dict[str, str]]:
    """Return mounted partitions (non-virtual)."""
    if psutil is None:
        return []

    items: list[dict[str, str]] = []
    try:
        parts = psutil.disk_partitions(all=False)
    except Exception:
        return []

    for p in parts:
        mp = (getattr(p, "mountpoint", "") or "").strip()
        dev = (getattr(p, "device", "") or "").strip()
        fstype = (getattr(p, "fstype", "") or "").strip().lower()

        if not mp or not dev:
            continue
        if fstype in _IGNORED_FS_TYPES:
            continue
        if mp.startswith(("/proc", "/sys", "/dev")):
            continue
        if mp.startswith("/run") and not mp.startswith("/run/media"):
            continue

        bdev = os.path.basename(dev)
        if bdev.startswith(("loop", "zram", "ram", "sr")):
            continue

        items.append({"mount": mp, "device": dev, "fstype": fstype})

    def _key(x: dict[str, str]) -> tuple[int, str]:
        mp = x.get("mount", "")
        if mp == "/":
            return (0, mp)
        if mp == "/home":
            return (1, mp)
        return (2, mp)

    return sorted(items, key=_key)


def _default_disk_label(mountpoint: str, device: str) -> str:
    """Generate default label for disk."""
    if mountpoint == "/":
        return "root"
    if mountpoint == "/home":
        return "home"
    base = os.path.basename(mountpoint.rstrip("/"))
    if base:
        return base
    return os.path.basename(device) or "disk"


# ==========================================================
# NETWORK QUALITY INDICATOR
# ==========================================================

def _net_quality(
    ping_avg_ms: Optional[float],
    jitter_ms: Optional[float],
    wifi_dbm: Optional[float]
) -> tuple[str, str]:
    """Return (label, color) based on network metrics."""
    score = 0

    if isinstance(ping_avg_ms, (int, float)):
        if ping_avg_ms <= 40:
            score += 2
        elif ping_avg_ms <= 120:
            score += 1

    if isinstance(jitter_ms, (int, float)):
        if jitter_ms <= 15:
            score += 2
        elif jitter_ms <= 40:
            score += 1

    if isinstance(wifi_dbm, (int, float)):
        if wifi_dbm >= -60:
            score += 2
        elif wifi_dbm >= -70:
            score += 1
    else:
        score += 1

    if score >= 5:
        return "FAST", ansi.c_green()
    if score >= 3:
        return "OK", ansi.c_yellow()
    return "SLOW", ansi.c_red()


# ==========================================================
# FAN LABEL (human-friendly)
# ==========================================================

def _fan_label(rpm: Optional[int]) -> str:
    """Fan label with bands."""
    if rpm is None or not isinstance(rpm, int):
        return f"{ansi.c_dim()}N/A{ansi.c_reset()}"
    if rpm < 0:
        return f"{ansi.c_dim()}N/A{ansi.c_reset()}"
    if rpm == 0:
        return f"{ansi.c_dim()}0 RPM (OFF){ansi.c_reset()}"

    if rpm <= 1000:
        lvl = f"{ansi.c_green()}Very Low{ansi.c_reset()}"
    elif rpm <= 3000:
        lvl = f"{ansi.c_green()}Low{ansi.c_reset()}"
    elif rpm <= 4500:
        lvl = f"{ansi.c_yellow()}Medium{ansi.c_reset()}"
    else:
        lvl = f"{ansi.c_red()}High{ansi.c_reset()}"

    return f"{lvl}{ansi.c_dim()} ({rpm} RPM){ansi.c_reset()}"


# ==========================================================
# SYSTEM INFO HELPERS
# ==========================================================

def _distro_pretty() -> str:
    """Get pretty distro name from /etc/os-release."""
    from pathlib import Path
    p = Path("/etc/os-release")
    if not p.exists():
        return ""
    try:
        txt = p.read_text()
        for ln in txt.splitlines():
            if ln.startswith("PRETTY_NAME="):
                v = ln.split("=", 1)[1].strip().strip('"')
                return v
    except Exception:
        pass
    return ""


def _uname_kernel() -> str:
    """Get kernel version."""
    try:
        return os.uname().release
    except Exception:
        from pathlib import Path
        try:
            return Path("/proc/sys/kernel/osrelease").read_text().strip()
        except Exception:
            return "-"


def _cpu_model() -> str:
    """Get CPU model name from /proc/cpuinfo."""
    from pathlib import Path
    try:
        txt = Path("/proc/cpuinfo").read_text()
        for ln in txt.splitlines():
            if ln.lower().startswith("model name"):
                return ln.split(":", 1)[-1].strip()
    except Exception:
        pass
    return ""


def _nvme_model_hint(dev: Optional[str]) -> str:
    """Get NVMe model name."""
    if not dev:
        return ""
    from pathlib import Path
    sys_p = Path(f"/sys/block/{dev}/device/model")
    if sys_p.exists():
        try:
            return sys_p.read_text().strip()
        except Exception:
            pass

    if shutil.which("lsblk"):
        try:
            import subprocess
            p = subprocess.run(
                ["lsblk", "-d", "-n", "-o", "MODEL", f"/dev/{dev}"],
                capture_output=True,
                text=True,
                timeout=2
            )
            if p.returncode == 0 and p.stdout:
                return p.stdout.splitlines()[0].strip()
        except Exception:
            pass
    return ""


# ==========================================================
# CPU FREQ INFO
# ==========================================================

def _cpu_freq_info() -> dict[str, any]:
    """Get CPU frequency + governor."""
    from pathlib import Path

    gov = None
    gov_p = Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor")
    if gov_p.exists():
        try:
            g = gov_p.read_text().strip()
            gov = g or None
        except Exception:
            pass

    # Prefer sysfs scaling_cur_freq
    vals: list[float] = []
    base = Path("/sys/devices/system/cpu")
    try:
        for p in sorted(base.glob("cpu[0-9]*/cpufreq/scaling_cur_freq")):
            try:
                v = int(p.read_text().strip())
                if v > 0:
                    vals.append(float(v) / 1e6)  # kHz -> GHz
            except Exception:
                continue
    except Exception:
        vals = []

    if vals:
        return {"ok": True, "ghz": sum(vals) / len(vals), "gov": gov}

    # Fallback psutil
    if psutil is not None:
        try:
            fr = psutil.cpu_freq()
            if fr and getattr(fr, "current", None):
                mhz = float(fr.current)
                return {"ok": True, "ghz": mhz / 1000.0, "gov": gov}
        except Exception:
            pass

    return {"ok": False, "ghz": None, "gov": gov}


# ==========================================================
# FORMAT TOP PROCESSES
# ==========================================================

def _fmt_topcpu(rows: list[tuple[str, float, float]], name_w: int = 16) -> list[str]:
    """Format top CPU processes."""
    out: list[str] = []
    for i, (nm, pct, share) in enumerate(rows, start=1):
        nm2 = trim_name(nm, name_w)
        if i == 1:
            out.append(
                f"  {i}) {nm2:<{name_w}} {pct:>5.1f}%   "
                f"{ansi.c_dim()}(≈{share:.0f}% of total){ansi.c_reset()}"
            )
        else:
            out.append(f"  {i}) {nm2:<{name_w}} {pct:>5.1f}%")
    return out


def _fmt_topmem(rows: list[tuple[str, float]], name_w: int = 16) -> list[str]:
    """Format top memory processes."""
    out: list[str] = []
    for i, (nm, gib) in enumerate(rows, start=1):
        nm2 = trim_name(nm, name_w)
        out.append(f"  {i}) {nm2:<{name_w}} {gib:>5.2f} GiB")
    return out


# ==========================================================
# MAIN DASHBOARD RUNNER
# ==========================================================

def run_live_dashboard(argv: list[str]) -> int:
    """
    Live HUD (V4.1 - PRESERVED LAYOUT).

    Options:
      --interval N
      --compact
      --target HOST
      --no-disks
      --maxwidth N
    """
    if psutil is None:
        ansi.print_brief_error("Monitoring requires 'psutil' library.")
        print("Install (Fedora): sudo dnf install python3-psutil")
        return 1

    # Parse options
    interval = 1.0
    compact = False
    target = "google.com"
    show_disks = True
    maxwidth: Optional[int] = None

    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--compact", "compact"):
            compact = True
        elif a == "--no-disks":
            show_disks = False
        elif a == "--interval" and i + 1 < len(argv):
            try:
                interval = float(argv[i + 1])
            except Exception:
                pass
            i += 1
        elif a == "--target" and i + 1 < len(argv):
            target = argv[i + 1].strip() or target
            i += 1
        elif a == "--maxwidth" and i + 1 < len(argv):
            try:
                maxwidth = int(float(argv[i + 1]))
            except Exception:
                maxwidth = None
            i += 1
        i += 1

    interval = float(clamp(interval, 0.2, 5.0))

    # Initialize samplers
    ns = NetSpeedometer()
    ns.update()

    disk_dev = _pick_root_disk_dev()
    disk_io = DiskIOSampler(disk_dev) if disk_dev else None

    ping = PingSampler(target=target, interval=max(2.0, interval * 2.0))
    ping.start()

    # Throttling timers
    last_snap = 0.0
    SNAP_EVERY = 600.0  # Battery snapshot every 10 min

    last_hw = 0.0
    HW_EVERY = 30.0
    hw_cpu = ""
    hw_nvme = ""
    distro = _distro_pretty()
    kernel = _uname_kernel()
    host = socket.gethostname()

    last_top = 0.0
    TOP_EVERY = 5.0
    top_cpu_rows: list[str] = []
    top_mem_rows: list[str] = []

    last_disks = 0.0
    DISK_EVERY = 10.0
    disk_cache: list[dict[str, any]] = []

    last_freq = 0.0
    FREQ_EVERY = 2.0
    cpu_freq_str = f"{ansi.c_dim()}N/A{ansi.c_reset()}"

    # Alternate screen + mute input
    input_muter = ansi.MuteInputDuringWait()
    ansi.alt_screen_enter()
    ansi.cursor_hide()

    try:
        # Prime CPU percent
        try:
            psutil.cpu_percent(interval=None)
        except Exception:
            pass

        # Prime process CPU percent
        try:
            for p in psutil.process_iter():
                try:
                    p.cpu_percent(interval=None)
                except Exception:
                    continue
        except Exception:
            pass

        with input_muter:
            while True:
                cols, _rows = ansi.term_size()
                usable = min(cols, 96)
                if isinstance(maxwidth, int) and maxwidth > 40:
                    usable = min(usable, int(maxwidth))

                # Collect metrics
                cpu = get_cpu_percent()
                ram_pct, ram_used, ram_total = get_memory_info()
                swap = get_swap_info()
                temps = read_temperatures()
                fan_rpm = read_fan_rpm()
                power = read_power()
                wifi = WiFiReader.read()

                ns.update()
                if disk_io:
                    disk_io.update()

                # Throttled CPU freq
                if time.time() - last_freq >= FREQ_EVERY:
                    fi = _cpu_freq_info()
                    if fi.get("ok") and isinstance(fi.get("ghz"), (int, float)):
                        ghz = float(fi["ghz"])
                        gov = fi.get("gov") or ""
                        gov_txt = f"{ansi.c_dim()}({gov}){ansi.c_reset()}" if gov else ""
                        cpu_freq_str = f"{ghz:.2f} GHz {gov_txt}".rstrip()
                    else:
                        gov = fi.get("gov") or ""
                        gov_txt = f"{ansi.c_dim()}({gov}){ansi.c_reset()}" if gov else ""
                        cpu_freq_str = f"{ansi.c_dim()}N/A{ansi.c_reset()} {gov_txt}".rstrip()
                    last_freq = time.time()

                # Throttled top processes
                if time.time() - last_top >= TOP_EVERY:
                    t_cpu = get_top_cpu_processes(cpu, n=3)
                    t_mem = get_top_mem_processes(n=3)
                    top_cpu_rows = _fmt_topcpu(t_cpu) if t_cpu else [f"  {ansi.c_dim()}(no data){ansi.c_reset()}"]
                    top_mem_rows = _fmt_topmem(t_mem) if t_mem else [f"  {ansi.c_dim()}(no data){ansi.c_reset()}"]
                    last_top = time.time()

                # Throttled disks
                if show_disks and (time.time() - last_disks >= DISK_EVERY):
                    mounts = _mounted_partitions()
                    disk_cache = []

                    for m in mounts:
                        mp = m["mount"]
                        dev = m["device"]
                        label = _default_disk_label(mp, dev)
                        try:
                            du = shutil.disk_usage(mp)
                            used_pct = (du.used / du.total) * 100.0 if du.total else 0.0
                            disk_cache.append({
                                "label": label,
                                "mount": mp,
                                "device": dev,
                                "used": du.used,
                                "total": du.total,
                                "free": du.free,
                                "pct": used_pct,
                            })
                        except Exception:
                            continue

                    last_disks = time.time()

                # Throttled hardware footer
                if time.time() - last_hw >= HW_EVERY:
                    hw_cpu = _cpu_model()
                    hw_nvme = _nvme_model_hint(disk_dev)
                    last_hw = time.time()

                # Battery status
                lvl = power.get("percent")
                lvl_i = int(lvl) if isinstance(lvl, int) else None
                st_raw = str(power.get("status_raw") or "Unknown")
                plugged = power.get("plugged")
                plugged_b = bool(plugged) if isinstance(plugged, bool) else False

                st_txt = "Unknown"
                bat_col = ansi.c_green()

                if plugged_b:
                    if st_raw.lower() == "full" or (lvl_i == 100):
                        st_txt = "AC (Full)"
                        bat_col = ansi.c_cyan()
                    elif st_raw.lower() == "not charging":
                        st_txt = "AC (Smart Cut-off)"
                        bat_col = ansi.c_cyan()
                    elif "charg" in st_raw.lower():
                        st_txt = "AC (Charging)"
                        bat_col = ansi.c_green()
                    else:
                        st_txt = f"AC ({st_raw})"
                        bat_col = ansi.c_green()
                else:
                    st_txt = "DC (On Battery)"
                    if lvl_i is not None and lvl_i < 20:
                        bat_col = ansi.c_red()
                    elif lvl_i is not None and lvl_i < 35:
                        bat_col = ansi.c_yellow()

                secs_left = power.get("secs_left")
                eta = ""
                if isinstance(secs_left, (int, float)) and 0 < secs_left < 10**9:
                    h = int(secs_left // 3600)
                    m = int((secs_left % 3600) // 60)
                    eta = f"{h}h {m}m left"

                # Periodic battery snapshot (SQLite)
                if time.time() - last_snap >= SNAP_EVERY:
                    cap = read_power_capacity()
                    if cap.get("ok"):
                        model = str(cap.get("model") or "BAT")
                        health = round(float(cap["health_pct"]), 2)
                        full = round(float(cap["full"]), 2)

                        # Use SQLite storage
                        ensure_dir(MON_HISTORY_DB.parent)
                        snapshot_metric_sql(
                            MON_HISTORY_DB,
                            MON_HISTORY_LOCK,
                            "battery",
                            model,
                            "health_pct",
                            health
                        )
                        snapshot_metric_sql(
                            MON_HISTORY_DB,
                            MON_HISTORY_LOCK,
                            "battery",
                            model,
                            "capacity_full",
                            full
                        )
                    last_snap = time.time()

                # === RENDER START ===
                ansi.clear_screen()

                # Header
                ts = now_ts()
                title_left = f"{ansi.c_cyan()}{ansi.c_bold()}AI MON{ansi.c_reset()} {ansi.c_dim()}• Live Cockpit{ansi.c_reset()}"
                title_right = f"{ansi.c_dim()}{ts}{ansi.c_reset()}"
                print(align_lr(title_left, title_right, usable))

                sep = f"{ansi.c_dim()}{ansi.hr(min(usable, 96))}{ansi.c_reset()}"

                # System info lines
                left_sys = f"{ansi.c_dim()}Host:{ansi.c_reset()} {host}  {ansi.c_dim()}OS:{ansi.c_reset()} {distro or '-'}"
                right_sys = f"{ansi.c_dim()}Kernel:{ansi.c_reset()} {kernel}"
                print(align_lr(left_sys, right_sys, usable))

                cpu_count = os.cpu_count() or 1
                uptime = get_uptime_str()
                left_run = f"{ansi.c_dim()}Uptime:{ansi.c_reset()} {uptime}  {ansi.c_dim()}CPUs:{ansi.c_reset()} {cpu_count}"
                right_run = f"{ansi.c_dim()}Interval:{ansi.c_reset()} {interval:.1f}s  {ansi.c_dim()}Target:{ansi.c_reset()} {target}"
                print(align_lr(left_run, right_run, usable))

                # Load average
                la1, la5, la15 = get_loadavg()
                per1 = (la1 / cpu_count) if cpu_count else 0.0
                per5 = (la5 / cpu_count) if cpu_count else 0.0
                per15 = (la15 / cpu_count) if cpu_count else 0.0

                left_load = f"{ansi.c_dim()}Load avg (1m/5m/15m):{ansi.c_reset()} {la1:.2f}/{la5:.2f}/{la15:.2f}"
                right_load = f"{ansi.c_dim()}Load/CPU:{ansi.c_reset()} {per1:.2f}/{per5:.2f}/{per15:.2f}"
                print(align_lr(left_load, right_load, usable))

                print(sep)

                # System metrics
                print(f"{ansi.c_bold()} CPU {ansi.c_reset()} {draw_bar(cpu)} {cpu:>5.1f}%   {ansi.c_dim()}Freq:{ansi.c_reset()} {cpu_freq_str}")
                print(f"{ansi.c_bold()} RAM {ansi.c_reset()} {draw_bar(ram_pct)} {ram_pct:>5.1f}%   {ram_used/(1024**3):.1f}/{ram_total/(1024**3):.1f} GiB")

                if swap.get("ok") and swap.get("total", 0) > 0:
                    sw_pct = float(swap.get("pct", 0.0))
                    sw_used = int(swap.get("used", 0))
                    sw_total = int(swap.get("total", 0))
                    print(f"{ansi.c_bold()}SWAP {ansi.c_reset()} {draw_bar(sw_pct)} {sw_pct:>5.1f}%   {human_bytes(sw_used)}/{human_bytes(sw_total)}")
                else:
                    print(f"{ansi.c_bold()}SWAP {ansi.c_reset()} {ansi.c_dim()}(not available){ansi.c_reset()}")

                if disk_io and disk_dev:
                    util = float(disk_io.active_pct)
                    col_u = ansi.c_green() if util <= 60 else (ansi.c_yellow() if util <= 85 else ansi.c_red())
                    r = human_rate_bps(disk_io.read_bps)
                    w = human_rate_bps(disk_io.write_bps)
                    print(f"{ansi.c_bold()} I/O {ansi.c_reset()} {col_u}{util:>5.1f}%{ansi.c_reset()}  R:{r:<11}  W:{w:<11}  {ansi.c_dim()}({disk_dev}){ansi.c_reset()}")

                print(sep)

                # Top processes
                print(f"{ansi.c_bold()}Top CPU (1–3){ansi.c_reset()}")
                for ln in top_cpu_rows:
                    print(ln)
                print("")
                print(f"{ansi.c_bold()}Top MEM (1–3){ansi.c_reset()}")
                for ln in top_mem_rows:
                    print(ln)

                print(sep)

                # Temperatures & Fan
                cpu_t = temps.get("cpu")
                ssd_t = temps.get("ssd")
                wifi_t = temps.get("wifi")

                cpu_t_s = f"{cpu_t:.1f}°C" if isinstance(cpu_t, (int, float)) else "-"
                ssd_t_s = f"{ssd_t:.1f}°C" if isinstance(ssd_t, (int, float)) else "-"
                wifi_t_s = f"{wifi_t:.1f}°C" if isinstance(wifi_t, (int, float)) else "-"

                fan_s = _fan_label(int(fan_rpm) if isinstance(fan_rpm, (int, float)) else None)

                print(
                    f"{ansi.c_bold()}TEMP {ansi.c_reset()}"
                    f"CPU:{col_by_temp(cpu_t)}{cpu_t_s}{ansi.c_reset()}   "
                    f"NVMe:{col_by_temp(ssd_t)}{ssd_t_s}{ansi.c_reset()}   "
                    f"WiFi:{col_by_temp(wifi_t)}{wifi_t_s}{ansi.c_reset()}   "
                    f"{ansi.c_dim()}Fan:{ansi.c_reset()} {fan_s}"
                )

                # Power
                lvl_txt = f"{lvl_i}%" if isinstance(lvl_i, int) else "?"
                print(
                    f"{ansi.c_bold()}POWER{ansi.c_reset()} "
                    f"{bat_col}{lvl_txt}{ansi.c_reset()} [{st_txt}]   "
                    f"{ansi.c_yellow()}{power.get('watt_str','N/A')}{ansi.c_reset()} @ {power.get('volt_str','N/A')} | {power.get('amp_str','N/A')}   "
                    f"{ansi.c_dim()}{eta}{ansi.c_reset()}"
                )

                print(sep)

                # Network
                rx = human_rate_bps(ns.rx)
                tx = human_rate_bps(ns.tx)

                ping_ms = ping.last_ms
                ping_avg = ping.avg()
                jit = ping.jitter()

                ping_txt = "TO" if ping_ms is None else f"{ping_ms:.0f}ms"
                avg_txt = "-" if ping_avg is None else f"{ping_avg:.0f}ms"
                jit_txt = "-" if jit is None else f"{jit:.0f}ms"
                pcol = col_by_ping(ping_ms)

                wifi_sig = wifi.get("signal_dbm") if wifi.get("ok") else None
                q_lbl, q_col = _net_quality(ping_avg, jit, wifi_sig if isinstance(wifi_sig, (int, float)) else None)

                print(
                    f"{ansi.c_bold()}NET  {ansi.c_reset()}↓{rx}  ↑{tx}   "
                    f"Quality:{q_col}{q_lbl}{ansi.c_reset()}"
                )

                ping_line = (
                    f"{ansi.c_bold()}PING {ansi.c_reset()}{target}   "
                    f"last:{pcol}{ping_txt}{ansi.c_reset()}  "
                    f"avg:{ansi.c_dim()}{avg_txt}{ansi.c_reset()}  "
                    f"jitter:{ansi.c_dim()}{jit_txt}{ansi.c_reset()}"
                )

                if wifi.get("ok"):
                    ssid = wifi.get("ssid") or "-"
                    sig = wifi.get("signal_dbm")
                    sig_txt = f"{sig:.0f} dBm" if isinstance(sig, (int, float)) else "-"
                    ping_line += f"   {ansi.c_dim()}WiFi:{ansi.c_reset()} {trim_name(ssid, 14)} ({col_by_dbm(sig)}{sig_txt}{ansi.c_reset()})"

                print(ping_line)

                # Disks
                if show_disks:
                    print(sep)
                    print(f"{ansi.c_bold()}DISKS{ansi.c_reset()} {ansi.c_dim()}(mounted partitions){ansi.c_reset()}")

                    if not disk_cache:
                        print(f"  {ansi.c_dim()}(no mounted disks found){ansi.c_reset()}")
                    else:
                        name_w = 6
                        free_w = 9
                        bar_w = 10
                        used_w = 15
                        fixed = 2 + name_w + 3 + free_w + 3 + (bar_w + 5) + 3 + used_w + 3 + 7
                        mount_w = max(12, min(usable - fixed, 42))

                        usedpct_w = bar_w + 5

                        if usable >= 70:
                            hdr = (
                                f"  {ansi.c_dim()}"
                                f"{'NAME':<{name_w}} | "
                                f"{'FREE':<{free_w}} | "
                                f"{'USED%':<{usedpct_w}} | "
                                f"{'USED/TOTAL':<{used_w}} | "
                                f"MOUNT"
                                f"{ansi.c_reset()}"
                            )
                            print(hdr)

                        for d in disk_cache:
                            pct = float(d.get("pct", 0.0))
                            used = float(d.get("used", 0.0))
                            total = float(d.get("total", 0.0))
                            free = float(d.get("free", 0.0))
                            label = str(d.get("label") or "disk")
                            mp = str(d.get("mount") or "-")

                            name_disp = trim_name(label, name_w)
                            free_disp = human_bytes(free)
                            used_disp = f"{human_bytes(used)}/{human_bytes(total)}"
                            mp_disp = trim_tail(mp, mount_w)

                            bar = draw_bar(pct, width=bar_w)
                            pct_disp = f"{pct:>3.0f}%"

                            line = (
                                f"  {ansi.c_bold()}{name_disp:<{name_w}}{ansi.c_reset()} | "
                                f"{free_disp:<{free_w}} | "
                                f"{bar} {pct_disp:<4} | "
                                f"{used_disp:<{used_w}} | "
                                f"({mp_disp})"
                            )
                            print(line)

                # Footer
                if not compact:
                    hw_parts = []
                    if hw_cpu:
                        hw_parts.append(f"CPU: {hw_cpu.strip()}")
                    if hw_nvme:
                        hw_parts.append(f"NVMe: {hw_nvme.strip()}")
                    if hw_parts:
                        import textwrap
                        footer = " | ".join(hw_parts)
                        for ln in textwrap.wrap(footer, width=min(usable, 92)):
                            print(f"{ansi.c_dim()}{ln}{ansi.c_reset()}")

                print("")
                if compact:
                    print(f"{ansi.c_dim()}Ctrl+C untuk keluar. Tip: ai mon sensors{ansi.c_reset()}")
                else:
                    print(f"{ansi.c_dim()}Ctrl+C untuk keluar.{ansi.c_reset()}  {ansi.c_dim()}Tip:{ansi.c_reset()} `ai mon sensors` untuk daftar sensor lengkap.")
                    print(f"{ansi.c_dim()}Tip:{ansi.c_reset()} `sudo ai mon disk` untuk TBW/SMART detail.")
                    print(f"{ansi.c_dim()}Tip:{ansi.c_reset()} `ai mon help` untuk opsi `--disk` dan tuning layout.")

                time.sleep(interval)

    except KeyboardInterrupt:
        pass
    except Exception as ex:
        try:
            ansi.cursor_show()
            ansi.alt_screen_exit()
        except Exception:
            pass
        ansi.print_brief_error(f"Dashboard crash: {type(ex).__name__}")
        print(str(ex))
        return 1
    finally:
        try:
            ping.stop()
        except Exception:
            pass
        try:
            ansi.cursor_show()
            ansi.alt_screen_exit()
        except Exception:
            pass

    return 0