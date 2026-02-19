"""
MON Live Dashboard - COMPLETE FIXED VERSION
✅ All wrapper functions working
✅ Proper imports
✅ CPU priming in correct location
"""

from __future__ import annotations

import os
import sys
import time
import shutil
import socket
import threading
import subprocess
from collections import deque
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
from ..collectors.hardware import collect_hardware_stats, collect_battery_stats
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
# SYSTEM WRAPPERS
# ==========================================================

def get_cpu_percent() -> float:
    """Get CPU percentage."""
    stats = collect_system_stats()
    return float(stats.get('cpu_pct', 0.0))


def get_memory_info() -> Tuple[float, float, float]:
    """Get memory info: (pct, used_bytes, total_bytes)."""
    stats = collect_system_stats()
    return (
        float(stats.get('ram_pct', 0.0)),
        float(stats.get('ram_used', 0)),
        float(stats.get('ram_total', 1))
    )


def get_swap_info() -> Dict[str, Any]:
    """Get swap info."""
    stats = collect_system_stats()
    return {
        'ok': stats.get('ok', False),
        'pct': float(stats.get('swap_pct', 0.0)),
        'used': int(stats.get('swap_used', 0)),
        'total': int(stats.get('swap_total', 0))
    }


def get_loadavg() -> Tuple[float, float, float]:
    """Get load average: (1m, 5m, 15m)."""
    stats = collect_system_stats()
    return (
        float(stats.get('load_1m', 0.0)),
        float(stats.get('load_5m', 0.0)),
        float(stats.get('load_15m', 0.0))
    )


def get_uptime_str() -> str:
    """Get formatted uptime string."""
    stats = collect_system_stats()
    secs = stats.get('uptime_sec', 0)
    if not secs:
        return "?"

    d = int(secs // 86400)
    secs %= 86400
    h = int(secs // 3600)
    m = int((secs % 3600) // 60)
    return f"{d}d {h}h {m}m"


# ==========================================================
# HARDWARE WRAPPERS
# ==========================================================

def read_temperatures() -> Dict[str, Optional[float]]:
    """
    Get temperature readings.
    Logic sama dengan ThermalReader.read_key_temps() di mon.py asli.
    Langsung pakai psutil untuk menghindari indirection.
    """
    res: Dict[str, Optional[float]] = {"cpu": None, "ssd": None, "wifi": None}
    if psutil is None:
        return res
    try:
        temps = psutil.sensors_temperatures(fahrenheit=False) or {}
    except Exception:
        return res

    # CPU temp: cari coretemp/k10temp dengan label 'package' atau 'tctl'
    for key in ("coretemp", "k10temp"):
        entries = temps.get(key) or []
        best = None
        for e in entries:
            lab = (getattr(e, "label", "") or "").lower()
            if ("package" in lab) or ("tctl" in lab):
                best = getattr(e, "current", None)
                break
        if best is None and entries:
            best = getattr(entries[0], "current", None)
        if best is not None:
            res["cpu"] = float(best)
            break

    # SSD/NVMe temp: cari key yang mengandung 'nvme' atau 'composite'
    for k, entries in temps.items():
        lk = k.lower()
        if "nvme" in lk or "composite" in lk:
            for e in entries:
                lab = (getattr(e, "label", "") or "").lower()
                if ("composite" in lab) or (lab == ""):
                    cur = getattr(e, "current", None)
                    if cur is not None:
                        res["ssd"] = float(cur)
                        break
            if res["ssd"] is not None:
                break

    # WiFi temp: cari key yang mengandung 'iwl', 'wifi', atau 'ath'
    for k, entries in temps.items():
        lk = k.lower()
        if ("iwl" in lk) or ("wifi" in lk) or ("ath" in lk):
            cur = getattr(entries[0], "current", None) if entries else None
            if cur is not None:
                res["wifi"] = float(cur)
                break

    return res


def read_fan_rpm() -> Optional[int]:
    """
    Get fan RPM.
    Logic sama dengan ThermalReader.read_fan_rpm() di mon.py asli.
    Langsung pakai psutil untuk akurasi.
    """
    if psutil is None:
        return None
    try:
        fans = psutil.sensors_fans() or {}
    except Exception:
        return None

    best: Optional[int] = None
    for _k, entries in fans.items():
        for e in entries:
            lab = (getattr(e, "label", "") or "").lower()
            cur = getattr(e, "current", None)
            if cur is None:
                continue
            if "cpu" in lab:
                return int(cur)
            if best is None or int(cur) > best:
                best = int(cur)
    return best


def read_power() -> Dict[str, Any]:
    """
    Get power/battery info via sysfs.
    Logic sama dengan PowerReader.read() di mon.py asli.
    Baca langsung dari sysfs untuk watt/volt/amp yang presisi.
    """
    from pathlib import Path

    def _sysfs_text(p: Path) -> Optional[str]:
        try:
            return p.read_text().strip() or None
        except Exception:
            return None

    def _sysfs_float(p: Path) -> Optional[float]:
        try:
            return float(p.read_text().strip())
        except Exception:
            return None

    res: Dict[str, Any] = {
        "ok": False,
        "status_raw": "Unknown",
        "percent": None,
        "secs_left": None,
        "plugged": None,
        "watt": None,
        "volt": None,
        "amp": None,
        "watt_str": "N/A",
        "volt_str": "N/A",
        "amp_str": "N/A",
        "batt_name": "",
    }

    # Find battery path
    base = Path("/sys/class/power_supply")
    batt = None
    if base.exists():
        bats = sorted(base.glob("BAT*"))
        batt = bats[0] if bats else None

    if batt is None:
        return res

    res["batt_name"] = batt.name
    res["status_raw"] = _sysfs_text(batt / "status") or "Unknown"

    # Percent + plugged + secs_left via psutil
    if psutil is not None:
        try:
            b = psutil.sensors_battery()
            if b:
                res["percent"] = int(getattr(b, "percent", 0) or 0)
                res["plugged"] = bool(getattr(b, "power_plugged", False))
                res["secs_left"] = getattr(b, "secsleft", None)
        except Exception:
            pass

    # Voltage
    v_uv = _sysfs_float(batt / "voltage_now")
    if v_uv is not None:
        res["volt"] = float(v_uv) / 1e6

    # Power (watt)
    p_uw = None
    if (batt / "power_now").exists():
        p_uw = _sysfs_float(batt / "power_now")
    elif (batt / "power_avg").exists():
        p_uw = _sysfs_float(batt / "power_avg")

    # Current (amp)
    c_ua = None
    if (batt / "current_now").exists():
        c_ua = _sysfs_float(batt / "current_now")

    if p_uw is not None:
        res["watt"] = float(p_uw) / 1e6
    elif (c_ua is not None) and (res["volt"] is not None):
        a = float(c_ua) / 1e6
        res["amp"] = a
        res["watt"] = float(res["volt"]) * a

    # Derive amp from watt/volt if missing
    if (res["amp"] is None) and (res["watt"] is not None) and (res["volt"] is not None) and (res["volt"] > 0):
        res["amp"] = float(res["watt"]) / float(res["volt"])

    # Format strings
    if res["watt"] is not None:
        w = float(res["watt"])
        res["watt_str"] = f"{w*1000:.0f} mW" if w < 1.0 else f"{w:.2f} W"
    if res["volt"] is not None:
        res["volt_str"] = f"{float(res['volt']):.2f} V"
    if res["amp"] is not None:
        res["amp_str"] = f"{float(res['amp']):.3f} A"

    res["ok"] = True
    return res


def read_power_capacity() -> Dict[str, Any]:
    """Get battery capacity/health info."""
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


# ==========================================================
# PROCESS WRAPPERS (FIXED)
# ==========================================================

def get_top_cpu_processes(total_cpu: float, n: int = 3) -> List[Tuple[str, float, float]]:
    """
    Get top CPU processes.
    Returns: List of (name, cpu_pct, share_of_total)
    """
    procs = collect_process_stats(limit=20)
    if not procs.get('ok'):
        return []

    processes = procs.get('processes', [])
    if not processes:
        return []

    # Sort by CPU percentage
    sorted_procs = sorted(processes, key=lambda x: x.get('cpu_pct', 0.0), reverse=True)

    result = []
    for p in sorted_procs[:n]:
        name = p.get('name', '?')
        cpu_pct = float(p.get('cpu_pct', 0.0))

        # Calculate share
        share = 0.0
        if total_cpu > 0.1:
            share = (cpu_pct / total_cpu) * 100.0
        share = clamp(share, 0.0, 100.0)

        result.append((name, cpu_pct, share))

    return result


def get_top_mem_processes(n: int = 3) -> List[Tuple[str, float]]:
    """
    Get top memory processes.
    Returns: List of (name, rss_gib)
    """
    procs = collect_process_stats(limit=n)
    if not procs.get('ok'):
        return []

    processes = procs.get('processes', [])
    if not processes:
        return []

    result = []
    for p in processes:
        name = p.get('name', '?')
        rss_gib = float(p.get('rss_mb', 0.0)) / 1024.0
        result.append((name, rss_gib))

    return result


# ==========================================================
# NETWORK WRAPPERS
# ==========================================================

class NetSpeedometer:
    """Network speed monitor."""

    def __init__(self, iface: Optional[str] = None):
        self.iface = iface
        self.rx = 0.0
        self.tx = 0.0
        self.prev_stats = None
        self.prev_time = time.time()

    def update(self):
        """Update network speed readings."""
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
    """WiFi stats reader."""

    @staticmethod
    def read(iface: Optional[str] = None) -> Dict[str, Any]:
        """Get WiFi stats."""
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
    """
    Background ping sampler.
    Self-contained, persis dari mon.py asli - tidak memakai PingMonitor.
    """
    def __init__(self, target: str, interval: float = 5.0) -> None:
        self.target = target
        self.interval = float(clamp(interval, 1.0, 30.0))
        self.last_ms: Optional[float] = None
        self.window: deque = deque(maxlen=20)
        self._stop = threading.Event()
        self._thr = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        """Start background ping thread."""
        if not self._thr.is_alive():
            self._thr.start()

    def stop(self) -> None:
        """Stop background ping thread."""
        self._stop.set()

    def avg(self) -> Optional[float]:
        """Get average ping dari window."""
        if not self.window:
            return None
        return sum(self.window) / float(len(self.window))

    def jitter(self) -> Optional[float]:
        """Get jitter (max-min dari window)."""
        if len(self.window) < 3:
            return None
        return max(self.window) - min(self.window)

    def _ping_once(self) -> Optional[float]:
        """Single ping, return ms atau None."""
        if not shutil.which("ping"):
            return None
        try:
            out = subprocess.check_output(
                ["ping", "-c", "1", "-W", "1", self.target],
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=2,
            )
            if "time=" in out:
                s = out.split("time=", 1)[1].split()[0]
                return float(s)
        except Exception:
            return None
        return None

    def _run(self) -> None:
        """Background thread loop."""
        while not self._stop.is_set():
            t0 = time.time()
            ms = self._ping_once()
            if ms is not None:
                self.last_ms = ms
                self.window.append(ms)
            else:
                self.last_ms = None
            dt = time.time() - t0
            wait = self.interval - dt
            if wait > 0:
                self._stop.wait(wait)
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
# THROTTLE READER (Intel thermal throttle indicator)
# Persis dari ThrottleReader di mon.py asli
# ==========================================================

class ThrottleReader:
    """Best-effort thermal throttling indicator (Intel-friendly)."""

    def __init__(self) -> None:
        self.prev_core: Optional[int] = None
        self.prev_pkg: Optional[int] = None

    def read(self) -> Dict[str, Any]:
        from pathlib import Path as _Path
        base = _Path("/sys/devices/system/cpu/cpu0/thermal_throttle")
        core_p = base / "core_throttle_count"
        pkg_p  = base / "package_throttle_count"
        if not core_p.exists() and not pkg_p.exists():
            return {"ok": False}

        def _ri(p: Any) -> Optional[int]:
            try:
                return int(p.read_text().strip())
            except Exception:
                return None

        core = _ri(core_p) if core_p.exists() else None
        pkg  = _ri(pkg_p)  if pkg_p.exists()  else None

        delta_core = None
        delta_pkg  = None
        if isinstance(core, int) and isinstance(self.prev_core, int):
            delta_core = core - self.prev_core
        if isinstance(pkg, int) and isinstance(self.prev_pkg, int):
            delta_pkg = pkg - self.prev_pkg

        self.prev_core = core if isinstance(core, int) else self.prev_core
        self.prev_pkg  = pkg  if isinstance(pkg,  int) else self.prev_pkg

        active = False
        if isinstance(delta_core, int) and delta_core > 0:
            active = True
        if isinstance(delta_pkg, int) and delta_pkg > 0:
            active = True

        return {
            "ok": True,
            "core": core,
            "pkg": pkg,
            "delta_core": delta_core,
            "delta_pkg": delta_pkg,
            "active": active,
        }

# ==========================================================
# INTERNAL HELPERS (direct readers, no wrapper overhead)
# Diambil langsung dari logika mon.py asli
# ==========================================================

def _swap_read() -> Dict[str, Any]:
    """Baca swap memory langsung via psutil."""
    out: Dict[str, Any] = {"ok": False, "pct": 0.0, "used": 0, "total": 0}
    if psutil is None:
        return out
    try:
        sw = psutil.swap_memory()
        out.update({"ok": True, "pct": float(sw.percent), "used": int(sw.used), "total": int(sw.total)})
    except Exception:
        pass
    return out


def _read_key_temps() -> Dict[str, Optional[float]]:
    """
    Baca temperatur kunci (CPU/NVMe/WiFi) langsung via psutil.
    Logic persis dari ThermalReader.read_key_temps() di mon.py asli.
    """
    res: Dict[str, Optional[float]] = {"cpu": None, "ssd": None, "wifi": None}
    if psutil is None:
        return res
    try:
        temps = psutil.sensors_temperatures(fahrenheit=False) or {}
    except Exception:
        return res

    # CPU: coretemp (Intel) atau k10temp (AMD), label 'package' atau 'tctl'
    for key in ("coretemp", "k10temp"):
        entries = temps.get(key) or []
        best = None
        for e in entries:
            lab = (getattr(e, "label", "") or "").lower()
            if ("package" in lab) or ("tctl" in lab):
                best = getattr(e, "current", None)
                break
        if best is None and entries:
            best = getattr(entries[0], "current", None)
        if best is not None:
            res["cpu"] = float(best)
            break

    # SSD/NVMe: key mengandung 'nvme' atau 'composite'
    for k, entries in temps.items():
        lk = k.lower()
        if "nvme" in lk or "composite" in lk:
            for e in entries:
                lab = (getattr(e, "label", "") or "").lower()
                if ("composite" in lab) or (lab == ""):
                    cur = getattr(e, "current", None)
                    if cur is not None:
                        res["ssd"] = float(cur)
                        break
            if res["ssd"] is not None:
                break

    # WiFi: key mengandung 'iwl', 'wifi', atau 'ath'
    for k, entries in temps.items():
        lk = k.lower()
        if ("iwl" in lk) or ("wifi" in lk) or ("ath" in lk):
            cur = getattr(entries[0], "current", None) if entries else None
            if cur is not None:
                res["wifi"] = float(cur)
                break

    return res


def _read_fan_rpm() -> Optional[int]:
    """
    Baca fan RPM langsung via psutil.
    Logic persis dari ThermalReader.read_fan_rpm() di mon.py asli.
    """
    if psutil is None:
        return None
    try:
        fans = psutil.sensors_fans() or {}
    except Exception:
        return None

    best: Optional[int] = None
    for _k, entries in fans.items():
        for e in entries:
            lab = (getattr(e, "label", "") or "").lower()
            cur = getattr(e, "current", None)
            if cur is None:
                continue
            if "cpu" in lab:
                return int(cur)
            if best is None or int(cur) > best:
                best = int(cur)
    return best


def _read_power() -> Dict[str, Any]:
    """
    Baca power/battery via sysfs + psutil.
    Logic persis dari PowerReader.read() di mon.py asli.
    """
    from pathlib import Path as _Path

    def _rt(p: Any) -> Optional[str]:
        try:
            return p.read_text().strip() or None
        except Exception:
            return None

    def _rf(p: Any) -> Optional[float]:
        try:
            return float(p.read_text().strip())
        except Exception:
            return None

    res: Dict[str, Any] = {
        "ok": False, "status_raw": "Unknown",
        "percent": None, "secs_left": None, "plugged": None,
        "watt": None, "volt": None, "amp": None,
        "watt_str": "N/A", "volt_str": "N/A", "amp_str": "N/A",
        "batt_name": "",
    }

    base = _Path("/sys/class/power_supply")
    batt = None
    if base.exists():
        bats = sorted(base.glob("BAT*"))
        batt = bats[0] if bats else None

    if batt is None:
        return res

    res["batt_name"] = batt.name
    res["status_raw"] = _rt(batt / "status") or "Unknown"

    if psutil is not None:
        try:
            b = psutil.sensors_battery()
            if b:
                res["percent"] = int(getattr(b, "percent", 0) or 0)
                res["plugged"] = bool(getattr(b, "power_plugged", False))
                res["secs_left"] = getattr(b, "secsleft", None)
        except Exception:
            pass

    v_uv = _rf(batt / "voltage_now")
    if v_uv is not None:
        res["volt"] = float(v_uv) / 1e6

    p_uw = None
    if (batt / "power_now").exists():
        p_uw = _rf(batt / "power_now")
    elif (batt / "power_avg").exists():
        p_uw = _rf(batt / "power_avg")

    c_ua = None
    if (batt / "current_now").exists():
        c_ua = _rf(batt / "current_now")

    if p_uw is not None:
        res["watt"] = float(p_uw) / 1e6
    elif (c_ua is not None) and (res["volt"] is not None):
        a = float(c_ua) / 1e6
        res["amp"] = a
        res["watt"] = float(res["volt"]) * a

    if (res["amp"] is None) and (res["watt"] is not None) and (res["volt"] is not None) and (res["volt"] > 0):
        res["amp"] = float(res["watt"]) / float(res["volt"])

    if res["watt"] is not None:
        w = float(res["watt"])
        res["watt_str"] = f"{w*1000:.0f} mW" if w < 1.0 else f"{w:.2f} W"
    if res["volt"] is not None:
        res["volt_str"] = f"{float(res['volt']):.2f} V"
    if res["amp"] is not None:
        res["amp_str"] = f"{float(res['amp']):.3f} A"

    res["ok"] = True
    return res


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
    thr = ThrottleReader()
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

                # Core metrics - direct psutil calls (sama persis dengan original)
                try:
                    cpu = float(psutil.cpu_percent(interval=None))
                except Exception:
                    cpu = 0.0

                try:
                    vm = psutil.virtual_memory()
                    ram_pct = float(vm.percent)
                    ram_used = float(vm.used)
                    ram_total = float(vm.total)
                except Exception:
                    ram_pct, ram_used, ram_total = 0.0, 0.0, 0.0

                swap = _swap_read()
                temps = _read_key_temps()
                fan_rpm = _read_fan_rpm()
                power = _read_power()
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
                # === SINGLE-WRITE RENDER (smooth, no flicker) ===
                import sys as _sys
                _out: list[str] = []
                _ap = _out.append

                # Move cursor home + erase below (no blank flash)
                _sys.stdout.write('\x1b[H\x1b[J')

                # Header
                ts = now_ts()
                title_left = f"{ansi.c_cyan()}{ansi.c_bold()}AI MON{ansi.c_reset()} {ansi.c_dim()}• Live Cockpit{ansi.c_reset()}"
                title_right = f"{ansi.c_dim()}{ts}{ansi.c_reset()}"
                _ap(align_lr(title_left, title_right, usable))

                sep = f"{ansi.c_dim()}{ansi.hr(min(usable, 96))}{ansi.c_reset()}"

                # System info lines
                left_sys = f"{ansi.c_dim()}Host:{ansi.c_reset()} {host}  {ansi.c_dim()}OS:{ansi.c_reset()} {distro or '-'}"
                right_sys = f"{ansi.c_dim()}Kernel:{ansi.c_reset()} {kernel}"
                _ap(align_lr(left_sys, right_sys, usable))

                cpu_count = os.cpu_count() or 1
                uptime = get_uptime_str()
                left_run = f"{ansi.c_dim()}Uptime:{ansi.c_reset()} {uptime}  {ansi.c_dim()}CPUs:{ansi.c_reset()} {cpu_count}"
                right_run = f"{ansi.c_dim()}Interval:{ansi.c_reset()} {interval:.1f}s  {ansi.c_dim()}Target:{ansi.c_reset()} {target}"
                _ap(align_lr(left_run, right_run, usable))

                # Load average
                la1, la5, la15 = get_loadavg()
                per1 = (la1 / cpu_count) if cpu_count else 0.0
                per5 = (la5 / cpu_count) if cpu_count else 0.0
                per15 = (la15 / cpu_count) if cpu_count else 0.0

                left_load = f"{ansi.c_dim()}Load avg (1m/5m/15m):{ansi.c_reset()} {la1:.2f}/{la5:.2f}/{la15:.2f}"
                right_load = f"{ansi.c_dim()}Load/CPU:{ansi.c_reset()} {per1:.2f}/{per5:.2f}/{per15:.2f}"
                _ap(align_lr(left_load, right_load, usable))

                _ap(sep)

                # System metrics
                _ap(f"{ansi.c_bold()} CPU {ansi.c_reset()} {draw_bar(cpu)} {cpu:>5.1f}%   {ansi.c_dim()}Freq:{ansi.c_reset()} {cpu_freq_str}")
                _ap(f"{ansi.c_bold()} RAM {ansi.c_reset()} {draw_bar(ram_pct)} {ram_pct:>5.1f}%   {ram_used/(1024**3):.1f}/{ram_total/(1024**3):.1f} GiB")

                if swap.get("ok") and swap.get("total", 0) > 0:
                    sw_pct = float(swap.get("pct", 0.0))
                    sw_used = int(swap.get("used", 0))
                    sw_total = int(swap.get("total", 0))
                    _ap(f"{ansi.c_bold()}SWAP {ansi.c_reset()} {draw_bar(sw_pct)} {sw_pct:>5.1f}%   {human_bytes(sw_used)}/{human_bytes(sw_total)}")
                else:
                    _ap(f"{ansi.c_bold()}SWAP {ansi.c_reset()} {ansi.c_dim()}(not available){ansi.c_reset()}")

                if disk_io and disk_dev:
                    util = float(disk_io.active_pct)
                    col_u = ansi.c_green() if util <= 60 else (ansi.c_yellow() if util <= 85 else ansi.c_red())
                    r = human_rate_bps(disk_io.read_bps)
                    w = human_rate_bps(disk_io.write_bps)
                    _ap(f"{ansi.c_bold()} I/O {ansi.c_reset()} {col_u}{util:>5.1f}%{ansi.c_reset()}  R:{r:<11}  W:{w:<11}  {ansi.c_dim()}({disk_dev}){ansi.c_reset()}")

                _ap(sep)

                # Top processes
                _ap(f"{ansi.c_bold()}Top CPU (1–3){ansi.c_reset()}")
                for ln in top_cpu_rows:
                    _ap(ln)
                _ap("")
                _ap(f"{ansi.c_bold()}Top MEM (1–3){ansi.c_reset()}")
                for ln in top_mem_rows:
                    _ap(ln)

                _ap(sep)

                # Temperatures & Fan
                cpu_t = temps.get("cpu")
                ssd_t = temps.get("ssd")
                wifi_t = temps.get("wifi")

                cpu_t_s = f"{cpu_t:.1f}°C" if isinstance(cpu_t, (int, float)) else "-"
                ssd_t_s = f"{ssd_t:.1f}°C" if isinstance(ssd_t, (int, float)) else "-"
                wifi_t_s = f"{wifi_t:.1f}°C" if isinstance(wifi_t, (int, float)) else "-"

                fan_s = _fan_label(int(fan_rpm) if isinstance(fan_rpm, (int, float)) else None)

                _ap(
                    f"{ansi.c_bold()}TEMP {ansi.c_reset()}"
                    f"CPU:{col_by_temp(cpu_t)}{cpu_t_s}{ansi.c_reset()}   "
                    f"NVMe:{col_by_temp(ssd_t)}{ssd_t_s}{ansi.c_reset()}   "
                    f"WiFi:{col_by_temp(wifi_t)}{wifi_t_s}{ansi.c_reset()}   "
                    f"{ansi.c_dim()}Fan:{ansi.c_reset()} {fan_s}"
                )

                # Throttle
                th = thr.read()
                if th.get("ok"):
                    if th.get("active"):
                        dc = th.get("delta_core")
                        dp = th.get("delta_pkg")
                        parts = []
                        if isinstance(dc, int) and dc > 0:
                            parts.append(f"+core {dc}")
                        if isinstance(dp, int) and dp > 0:
                            parts.append(f"+pkg {dp}")
                        extra = f" {ansi.c_dim()}({', '.join(parts)}){ansi.c_reset()}" if parts else ""
                        _ap(f"{ansi.c_bold()}THROT{ansi.c_reset()} {ansi.c_red()}ACTIVE{ansi.c_reset()}{extra}")
                    else:
                        _ap(f"{ansi.c_bold()}THROT{ansi.c_reset()} {ansi.c_green()}OK{ansi.c_reset()}")
                else:
                    _ap(f"{ansi.c_bold()}THROT{ansi.c_reset()} {ansi.c_dim()}N/A{ansi.c_reset()}")

                # Power
                lvl_txt = f"{lvl_i}%" if isinstance(lvl_i, int) else "?"
                _ap(
                    f"{ansi.c_bold()}POWER{ansi.c_reset()} "
                    f"{bat_col}{lvl_txt}{ansi.c_reset()} [{st_txt}]   "
                    f"{ansi.c_yellow()}{power.get('watt_str','N/A')}{ansi.c_reset()} @ {power.get('volt_str','N/A')} | {power.get('amp_str','N/A')}   "
                    f"{ansi.c_dim()}{eta}{ansi.c_reset()}"
                )

                _ap(sep)

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

                _ap(
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

                _ap(ping_line)

                # Disks
                if show_disks:
                    _ap(sep)
                    _ap(f"{ansi.c_bold()}DISKS{ansi.c_reset()} {ansi.c_dim()}(mounted partitions){ansi.c_reset()}")

                    if not disk_cache:
                        _ap(f"  {ansi.c_dim()}(no mounted disks found){ansi.c_reset()}")
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
                            _ap(hdr)

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
                            _ap(line)

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
                            _ap(f"{ansi.c_dim()}{ln}{ansi.c_reset()}")

                _ap("")
                if compact:
                    _ap(f"{ansi.c_dim()}Ctrl+C untuk keluar. Tip: ai mon sensors{ansi.c_reset()}")
                else:
                    _ap(f"{ansi.c_dim()}Ctrl+C untuk keluar.{ansi.c_reset()}  {ansi.c_dim()}Tip:{ansi.c_reset()} `ai mon sensors` untuk daftar sensor lengkap.")
                    _ap(f"{ansi.c_dim()}Tip:{ansi.c_reset()} `sudo ai mon disk` untuk TBW/SMART detail.")
                    _ap(f"{ansi.c_dim()}Tip:{ansi.c_reset()} `ai mon help` untuk opsi `--disk` dan tuning layout.")


                # Write all at once + flush
                _sys.stdout.write('\n'.join(_out) + '\n')
                _sys.stdout.flush()

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


# ==========================================================
# NET LIVE DASHBOARD (Ping Graph + WiFi Stats)
# ==========================================================

def run_net_live_dashboard(args: List[str]) -> int:
    """
    Live ping monitoring dashboard dengan graph penuh dan WiFi stats.

    Usage: ai mon net live [target] [--interval N] [--window N]
    """
    if psutil is None:
        ansi.print_brief_error("Fitur monitoring butuh library 'psutil'.")
        print("Install (Fedora): sudo dnf install python3-psutil")
        return 1

    # Parse arguments
    target = "google.com"
    interval = 1.0
    window = 60

    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("--interval", "-i") and i + 1 < len(args):
            try:
                interval = float(args[i + 1])
                interval = max(0.2, min(5.0, interval))
            except ValueError:
                pass
            i += 2
        elif arg in ("--window", "-w") and i + 1 < len(args):
            try:
                window = int(args[i + 1])
                window = max(20, min(300, window))
            except ValueError:
                pass
            i += 2
        elif not arg.startswith("-"):
            target = arg.strip()
            i += 1
        else:
            i += 1

    # Initialize monitoring
    ping = PingMonitor(target=target, window=window)
    ping.start()

    # Network speed tracker
    ns_tracker = _NetSpeedTracker()

    # Box drawing characters (auto-fallback)
    if ansi.supports_unicode():
        BOX = {
            'tl': '┌', 'tr': '┐', 'bl': '└', 'br': '┘',
            'h': '─', 'v': '│',
            'lt': '├', 'rt': '┤', 'tt': '┬', 'bt': '┴', 'x': '┼'
        }
    else:
        BOX = {
            'tl': '+', 'tr': '+', 'bl': '+', 'br': '+',
            'h': '-', 'v': '|',
            'lt': '+', 'rt': '+', 'tt': '+', 'bt': '+', 'x': '+'
        }

    # Alternate screen
    ansi.alt_screen_enter()
    ansi.cursor_hide()

    input_muter = ansi.MuteInputDuringWait()

    try:
        with input_muter:
            while True:
                cols, rows = ansi.term_size()
                width = min(cols, 120)

                # Get stats
                stats = ping.get_stats()
                wifi = collect_wifi_stats()
                ns_tracker.update()

                # Build output
                out: List[str] = []

                # Header
                now_time = time.strftime("%A | %H:%M:%S")
                header_left = f"{ansi.c_cyan()}{ansi.c_bold()}AI MON {ansi.c_dim()}•{ansi.c_reset()} {ansi.c_cyan()}{ansi.c_bold()}NET LIVE{ansi.c_reset()}"
                header_right = f"{ansi.c_dim()}{now_time}{ansi.c_reset()}"

                # Top border
                out.append(BOX['tl'] + BOX['h'] * (width - 2) + BOX['tr'])

                # Header line
                out.append(_box_line(header_left, header_right, width, BOX['v']))

                # Separator
                out.append(BOX['lt'] + BOX['h'] * (width - 2) + BOX['rt'])

                # Target info
                resolved = stats.get('resolved_ip', target)
                target_line = f"{ansi.c_bold()}TARGET{ansi.c_reset()}  {target}"
                if resolved and resolved != target:
                    target_line += f" {ansi.c_dim()}({resolved}){ansi.c_reset()}"
                out.append(_box_line(target_line, "", width, BOX['v']))

                # Config
                config_line = f"{ansi.c_bold()}CONFIG{ansi.c_reset()}  interval: {interval}s    window: {window} samples"
                out.append(_box_line(config_line, "", width, BOX['v']))

                # Separator
                out.append(BOX['lt'] + BOX['h'] * (width - 2) + BOX['rt'])

                # WiFi stats (if available)
                if wifi.get('ok'):
                    ssid = wifi.get('ssid', '?')
                    sig = wifi.get('signal_dbm')
                    sig_str = f"{sig:.0f} dBm" if isinstance(sig, (int, float)) else "N/A"
                    sig_col = col_by_dbm(sig) if isinstance(sig, (int, float)) else ansi.c_dim()

                    # WiFi line 1: SSID + Signal + Channel
                    wifi1 = f"{ansi.c_bold()}WiFi{ansi.c_reset()}    {ssid} {sig_col}({sig_str}){ansi.c_reset()}"

                    # Get channel/freq from iface info if possible
                    rx_rate = wifi.get('rx_bitrate', 'N/A')
                    tx_rate = wifi.get('tx_bitrate', 'N/A')

                    # Try to get temperature
                    wifi_temp = _get_wifi_temperature()
                    temp_str = f"Temp: {wifi_temp}°C" if wifi_temp else "Temp: N/A"

                    wifi1_right = f"  {ansi.c_dim()}{temp_str}{ansi.c_reset()}"
                    out.append(_box_line(wifi1, wifi1_right, width, BOX['v']))

                    # WiFi line 2: Link speeds
                    link_line = f"{ansi.c_bold()}Link{ansi.c_reset()}    TX {ansi.c_green()}{tx_rate}{ansi.c_reset()}  RX {ansi.c_green()}{rx_rate}{ansi.c_reset()}"
                    out.append(_box_line(link_line, "", width, BOX['v']))

                    # WiFi line 3: Current traffic
                    rx_speed = human_rate_bps(ns_tracker.rx * 8)  # bytes/s to bits/s
                    tx_speed = human_rate_bps(ns_tracker.tx * 8)
                    traffic_line = f"{ansi.c_bold()}Traffic{ansi.c_reset()} ↓ {ansi.c_cyan()}{rx_speed}{ansi.c_reset()}    ↑ {ansi.c_yellow()}{tx_speed}{ansi.c_reset()}"
                    out.append(_box_line(traffic_line, "", width, BOX['v']))

                    # Separator
                    out.append(BOX['lt'] + BOX['h'] * (width - 2) + BOX['rt'])

                # Ping stats
                last = stats.get('last_ping')
                avg = stats.get('avg_ping')
                jitter = stats.get('jitter')
                loss = stats.get('loss_pct', 0.0)

                last_str = f"{last:.0f}ms" if last else "TO"
                avg_str = f"{avg:.0f}ms" if avg else "N/A"
                jit_str = f"{jitter:.0f}ms" if jitter else "N/A"
                loss_str = f"{loss:.0f}%"

                last_col = col_by_ping(last) if last else ansi.c_red()
                avg_col = col_by_ping(avg) if avg else ansi.c_dim()
                jit_col = ansi.c_green() if (jitter and jitter < 10) else (ansi.c_yellow() if (jitter and jitter < 50) else ansi.c_red())
                loss_col = ansi.c_green() if loss < 1 else (ansi.c_yellow() if loss < 5 else ansi.c_red())

                ping_line = (
                    f"{ansi.c_bold()}PING{ansi.c_reset()}    "
                    f"LAST {last_col}{last_str}{ansi.c_reset()}    "
                    f"AVG {avg_col}{avg_str}{ansi.c_reset()}    "
                    f"JITTER {jit_col}{jit_str}{ansi.c_reset()}    "
                    f"LOSS {loss_col}{loss_str}{ansi.c_reset()}"
                )
                out.append(_box_line(ping_line, "", width, BOX['v']))

                # Separator
                out.append(BOX['lt'] + BOX['h'] * (width - 2) + BOX['rt'])

                # Graph section
                history = stats.get('history', [])
                graph_lines = _draw_ping_graph(history, width - 4)

                for line in graph_lines:
                    out.append(f"{BOX['v']} {line:<{width-4}} {BOX['v']}")

                # Legend
                legend = (
                    f"{ansi.c_green()}●{ansi.c_reset()} 0-50ms  "
                    f"{ansi.c_cyan()}●{ansi.c_reset()} 50-100ms  "
                    f"{ansi.c_yellow()}●{ansi.c_reset()} 100-200ms  "
                    f"{ansi.c_red()}●{ansi.c_reset()} >200ms  "
                    f"{ansi.c_red()}×{ansi.c_reset()} timeout"
                )
                out.append(_box_line(f"Legend: {legend}", "", width, BOX['v']))

                # Bottom border
                out.append(BOX['bl'] + BOX['h'] * (width - 2) + BOX['br'])

                # Footer tip
                out.append(f"{ansi.c_dim()}Ctrl+C untuk keluar.   Tip: 'ai mon net {target}' untuk diagnosis (dns + wifi).{ansi.c_reset()}")

                # Render (cursor home + erase + single write)
                sys.stdout.write('\x1b[H\x1b[J')
                sys.stdout.write('\n'.join(out) + '\n')
                sys.stdout.flush()

                time.sleep(interval)

    except KeyboardInterrupt:
        pass
    finally:
        ping.stop()
        ansi.cursor_show()
        ansi.alt_screen_exit()

    return 0


def _box_line(left: str, right: str, width: int, vert: str) -> str:
    """Create a box line with left and right content."""
    import re
    ansi_re = re.compile(r'\x1b\[[0-9;]*m')

    left_vis = len(ansi_re.sub('', left))
    right_vis = len(ansi_re.sub('', right))

    padding = width - 4 - left_vis - right_vis
    if padding < 0:
        padding = 0

    return f"{vert} {left}{' ' * padding}{right} {vert}"


def _draw_ping_graph(history: List[Optional[float]], width: int) -> List[str]:
    """
    Draw ping graph dengan Y-axis labels dan full width.
    Returns list of lines untuk di-render.
    """
    if not history:
        return [" " * width]

    # Get latest data points (as many as width allows for graph area)
    graph_width = width - 10  # Reserve 10 chars for Y-axis labels
    visible = history[-graph_width:] if len(history) > graph_width else history

    # Calculate scale
    valid = [v for v in visible if v is not None]
    if not valid:
        return ["No data yet..."]

    max_val = max(valid)
    min_val = 0  # Always start from 0 for ping

    # Round max to nice number
    if max_val < 50:
        scale_max = 50
    elif max_val < 100:
        scale_max = 100
    elif max_val < 200:
        scale_max = 200
    elif max_val < 500:
        scale_max = 500
    else:
        scale_max = int((max_val + 99) // 100 * 100)

    # Y-axis labels (5 levels)
    levels = [scale_max, scale_max * 3 // 4, scale_max // 2, scale_max // 4, 0]

    # Graph height
    graph_height = 6

    lines = []

    # Draw from top to bottom
    for row in range(graph_height):
        level_idx = row * len(levels) // graph_height
        if level_idx < len(levels):
            label = f"{levels[level_idx]:>5.0f}ms"
        else:
            label = "      "

        # Draw line
        if row == 0:
            line_char = '┤'
        elif row == graph_height - 1:
            line_char = '└'
        else:
            line_char = '│'

        row_str = f"{label} {line_char}"

        # Add data points
        for val in visible:
            if val is None:
                # Timeout
                row_str += f"{ansi.c_red()}×{ansi.c_reset()}"
            else:
                # Calculate which row this point should appear in
                ratio = (val - min_val) / (scale_max - min_val) if scale_max > min_val else 0
                ratio = max(0.0, min(1.0, ratio))

                # Invert Y (top = high value)
                point_row = int((1.0 - ratio) * (graph_height - 1))

                if point_row == row:
                    # Color based on value
                    if val < 50:
                        col = ansi.c_green()
                    elif val < 100:
                        col = ansi.c_cyan()
                    elif val < 200:
                        col = ansi.c_yellow()
                    else:
                        col = ansi.c_red()
                    row_str += f"{col}●{ansi.c_reset()}"
                else:
                    row_str += " "

        lines.append(row_str)

    # X-axis
    x_axis = "   0ms " + '└' + '─' * graph_width
    lines.append(x_axis)

    # Time label
    time_label = " " * 7 + f"◄{'─' * ((graph_width - 35) // 2)} TIME (oldest ← newest) {'─' * ((graph_width - 35) // 2)}►"
    lines.append(time_label[:width])

    return lines


def _get_wifi_temperature() -> Optional[int]:
    """Get WiFi chip temperature if available."""
    if not psutil:
        return None

    try:
        temps = psutil.sensors_temperatures()
        # Look for iwlwifi, ath10k, etc
        for key in temps:
            if 'iwl' in key.lower() or 'ath' in key.lower() or 'wifi' in key.lower():
                entries = temps[key]
                if entries:
                    return int(entries[0].current)
    except Exception:
        pass

    return None


class _NetSpeedTracker:
    """Track network speed for traffic display."""

    def __init__(self, iface: Optional[str] = None):
        self.iface = iface
        self.rx = 0.0
        self.tx = 0.0
        self.prev_stats = None
        self.prev_time = time.time()

    def update(self):
        """Update speed readings."""
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