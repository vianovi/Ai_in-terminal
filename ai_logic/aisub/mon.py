"""
AI-Term • MON (System Cockpit & Intelligence) — V4.1

Tujuan:
- `ai mon live`    : HUD realtime yang clean, vertikal, dan informatif.
- `ai mon sensors` : tampilkan SEMUA sensor/field yang bisa dibaca (psutil + sysfs + optional tools).
- `ai mon batt`    : laporan battery intel + history tracking.
- `ai mon disk`    : laporan storage SMART/TBW + history tracking (tanpa memaksa sudo).
- `ai mon net`     : network diagnostics (ping/dns + wifi detail).
- `ai mon net live`: live ping graph.

V4.1 Update (baru):
- LIVE: GPU utilization dihapus dari HUD (permission/akses iGPU tidak stabil) dan diganti CPU Freq/Speed.
- Header dipoles: ada pemisah + warna, Load diperjelas jadi Load avg (1/5/15) + jumlah CPU.
- TOP list dipisah dari bar CPU/RAM:
  - Top CPU (1–3) dan Top MEM (1–3), throttled agar ringan.
- Throttle indicator dirapihin:
  - Line sendiri, delta-based (ACTIVE hanya jika counter naik pada interval).
- Fan output dirapihin:
  - "Idle/Low/Med/High (RPM)" atau "N/A".
- Network dipecah 2 baris (NET + PING) supaya tidak melebar.
- DISKS dirapihin jadi tabel aligned dengan separator '|':
  - Kolom MOUNT digeser ke paling kanan.
  - Trimming mountpoint aman agar tampilan tetap vertikal.

Catatan desain:
- Dependency utama: `psutil` (Wajib untuk monitoring).
  Install Fedora: sudo dnf install python3-psutil
- Optional tools:
  sudo dnf install lm_sensors smartmontools pciutils iw iproute util-linux
  (optional iGPU advanced): sudo dnf install intel-gpu-tools
- History path: ai_logic.common.MON_HISTORY_PATH (fallback aman bila common belum update).
- Tidak memakai rich (ANSI-only, konsisten dengan project).
- Tidak memunculkan ERROR yang bikin panik untuk kondisi wajar (mis. sensor tidak tersedia).
- Disk SMART/TBW: default tidak memaksa sudo (agar tidak memunculkan prompt password).
  Untuk data lengkap: jalankan `sudo ai mon disk`.

Subcommands:
  ai mon live [--interval N] [--compact] [--target HOST] [--iface IFACE] [--disk "LABEL=TARGET"] [--no-disks] [--maxwidth N]
  ai mon sensors
  ai mon batt
  ai mon disk [--sudo|--deep]
  ai mon net [target]
  ai mon net live [target]
  ai mon help

History file:
- MON_HISTORY_PATH (default: ~/.config/ai-term/mon_history.json)
"""

from __future__ import annotations

import os
import re
import time
import json
import shutil
import socket
import datetime as _dt
import subprocess
import threading
import shlex
import textwrap
from collections import deque
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, List

# --- COMMON IMPORT (single source of truth) ---
try:
    from ai_logic.common import MON_HISTORY_PATH
except Exception:
    # Safety fallback: local workspace mode
    MON_HISTORY_PATH = Path.cwd() / "workspace" / "mon_history.json"

from ai_logic.ui import ansi

# --- Dependency: psutil (Wajib untuk MON) ---
psutil = None
try:
    import psutil  # type: ignore
except Exception:
    psutil = None


# ==========================================================
# 0) UTILITIES (safe io, shell, formatting)
# ==========================================================

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

def _vis_len(s: str) -> int:
    return len(_ANSI_RE.sub("", s))

def _align_lr(left: str, right: str, width: int) -> str:
    """Align left/right text into a single line for given terminal width (ansi-aware)."""
    w = max(20, int(width))
    l = _vis_len(left)
    r = _vis_len(right)
    if l + 1 + r >= w:
        # Hard truncate left if needed
        keep = max(5, w - (r + 1))
        # naive trunc (visible); best-effort
        raw = _ANSI_RE.sub("", left)
        if len(raw) > keep:
            raw = raw[: max(2, keep - 1)] + "…"
        left2 = raw
        return left2 + " " + right
    return left + (" " * (w - (l + r))) + right

def _now_ts() -> str:
    return _dt.datetime.now().strftime("%A | %H:%M:%S")

def _run(argv: list[str], timeout: int = 10) -> tuple[int, str]:
    """Run subprocess safely (shell=False). Return (code, stdout)."""
    try:
        p = subprocess.run(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=timeout,
            check=False,
        )
        return int(p.returncode), (p.stdout or "").strip()
    except subprocess.TimeoutExpired:
        return 124, ""
    except Exception:
        return 1, ""

def _cmd(cmd: str, timeout: int = 10) -> tuple[int, str]:
    """Compat helper: shlex-split string command then run safely (no pipes/redirection)."""
    try:
        argv = shlex.split(cmd)
    except Exception:
        return 1, ""
    if not argv:
        return 1, ""
    return _run(argv, timeout=timeout)

def _sh(cmd: str, timeout: int = 10) -> tuple[int, str]:
    """Legacy alias (V3) — now safe (no shell). Pipes/redirect are NOT supported."""
    return _cmd(cmd, timeout=timeout)

def _read_text(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace").strip()
    except Exception:
        return ""

def _read_int(p: Path) -> Optional[int]:
    try:
        s = _read_text(p)
        return int(s) if s else None
    except Exception:
        return None

def _read_float(p: Path) -> Optional[float]:
    try:
        s = _read_text(p)
        return float(s) if s else None
    except Exception:
        return None

def _clamp(n: float, lo: float, hi: float) -> float:
    if n < lo:
        return lo
    if n > hi:
        return hi
    return n

def _human_bytes(n: float) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    v = float(max(0.0, n))
    for u in units:
        if v < 1024.0:
            return f"{v:.1f}{u}"
        v /= 1024.0
    return f"{v:.1f}PB"

def _human_rate_bps(bps: float) -> str:
    return _human_bytes(bps) + "/s"

def _draw_bar(pct: float, width: int = 14) -> str:
    pct = _clamp(float(pct), 0.0, 100.0)
    fill = int(width * pct / 100.0)
    col = ansi.c_green() if pct <= 60 else (ansi.c_yellow() if pct <= 85 else ansi.c_red())
    return f"{col}{'█'*fill}{ansi.c_dim()}{'░'*(width-fill)}{ansi.c_reset()}"

def _col_by_ping(ms: Optional[float]) -> str:
    if ms is None:
        return ansi.c_dim()
    if ms <= 40:
        return ansi.c_green()
    if ms <= 120:
        return ansi.c_yellow()
    return ansi.c_red()

def _col_by_dbm(dbm: Optional[float]) -> str:
    if dbm is None:
        return ansi.c_dim()
    if dbm >= -55:
        return ansi.c_green()
    if dbm >= -67:
        return ansi.c_yellow()
    return ansi.c_red()

def _col_by_temp(v: Optional[float]) -> str:
    if v is None:
        return ansi.c_dim()
    if v < 70:
        return ansi.c_green()
    if v < 85:
        return ansi.c_yellow()
    return ansi.c_red()

def _uname_kernel() -> str:
    try:
        return os.uname().release
    except Exception:
        return _read_text(Path("/proc/sys/kernel/osrelease")) or "-"

def _distro_pretty() -> str:
    p = Path("/etc/os-release")
    if not p.exists():
        return ""
    txt = _read_text(p)
    for ln in txt.splitlines():
        if ln.startswith("PRETTY_NAME="):
            v = ln.split("=", 1)[1].strip().strip('"')
            return v
    return ""

def _trim_tail(s: str, maxlen: int) -> str:
    """Trim long strings from the left, keeping the tail (useful for mount paths)."""
    s = s or ""
    maxlen = int(max(8, maxlen))
    if len(s) <= maxlen:
        return s
    return "…" + s[-(maxlen - 1):]

def _trim_name(s: str, maxlen: int) -> str:
    """Trim long process/labels, keeping head (better for names)."""
    s = (s or "").strip()
    maxlen = int(max(4, maxlen))
    if len(s) <= maxlen:
        return s
    return s[: max(2, maxlen - 1)] + "…"


# ==========================================================
# 1) HISTORY ENGINE (append-only daily snapshots)
# ==========================================================

def _ensure_history_ready() -> None:
    p = Path(MON_HISTORY_PATH)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

def _load_db() -> dict:
    p = Path(MON_HISTORY_PATH)
    if not p.exists():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}

def _save_db(data: dict) -> None:
    _ensure_history_ready()
    try:
        Path(MON_HISTORY_PATH).write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass

def snapshot_metric(category: str, item_id: str, metric: str, value: float) -> bool:
    """
    Save First Check: simpan hanya jika hari ini belum ada.
    Return:
      True  -> snapshot tersimpan
      False -> sudah ada snapshot hari ini (skip)
    """
    db = _load_db()
    today = _dt.date.today().isoformat()

    db.setdefault(category, {})
    db[category].setdefault(item_id, {})
    db[category][item_id].setdefault(metric, {})

    if today in db[category][item_id][metric]:
        return False

    db[category][item_id][metric][today] = value
    _save_db(db)
    return True

def get_comparison_text(category: str, item_id: str, metric: str, current: float, unit: str = "") -> str:
    """
    Bandingkan current vs data terlama (agar “time travel” terasa).
    """
    db = _load_db()
    try:
        series = db.get(category, {}).get(item_id, {}).get(metric, {})
        if not isinstance(series, dict) or not series:
            return ""

        dates = sorted(series.keys())
        oldest = dates[0]
        if oldest == _dt.date.today().isoformat():
            return f"{ansi.c_dim()}(mulai tracking hari ini){ansi.c_reset()}"

        old_val = float(series[oldest])
        diff = float(current) - old_val
        days = (_dt.date.today() - _dt.date.fromisoformat(oldest)).days

        icon = "⚪"
        if diff > 0:
            icon = "📈+"
        elif diff < 0:
            icon = "📉"

        return f"{ansi.c_dim()}vs {days} hari lalu: {old_val:.2f} -> {current:.2f} ({icon}{diff:+.2f}{unit}){ansi.c_reset()}"
    except Exception:
        return ""


# ==========================================================
# 2) SENSOR PROBES (psutil + sysfs + optional tools)
# ==========================================================

class SensorProbe:
    """
    Mengumpulkan “semua yang bisa dibaca” agar kita tahu bahan data apa saja.
    Fokus: menampilkan daftar (bukan dashboard).
    """

    @staticmethod
    def tool_presence() -> dict:
        return {
            "sensors(lm_sensors)": bool(shutil.which("sensors")),
            "smartctl(smartmontools)": bool(shutil.which("smartctl")),
            "iw(iw)": bool(shutil.which("iw")),
            "lspci(pciutils)": bool(shutil.which("lspci")),
            "ip(iproute2)": bool(shutil.which("ip")),
            "ping(iputils)": bool(shutil.which("ping")),
            "nvidia-smi(nvidia)": bool(shutil.which("nvidia-smi")),
        }

    @staticmethod
    def psutil_overview() -> dict:
        out: dict[str, Any] = {}
        if psutil is None:
            out["psutil"] = {"ok": False, "detail": "psutil tidak terpasang"}
            return out

        out["psutil"] = {"ok": True, "version": getattr(psutil, "__version__", "?")}

        # temps
        try:
            temps = psutil.sensors_temperatures(fahrenheit=False) or {}
            out["temps_keys"] = sorted(list(temps.keys()))
            sample: dict[str, Any] = {}
            for k, entries in temps.items():
                sample[k] = []
                for e in entries[:16]:
                    sample[k].append({
                        "label": getattr(e, "label", "") or "",
                        "current": getattr(e, "current", None),
                        "high": getattr(e, "high", None),
                        "critical": getattr(e, "critical", None),
                    })
            out["temps_sample"] = sample
        except Exception as ex:
            out["temps_keys"] = []
            out["temps_err"] = str(ex)

        # fans
        try:
            fans = psutil.sensors_fans() or {}
            out["fans_keys"] = sorted(list(fans.keys()))
            sample_f: dict[str, Any] = {}
            for k, entries in fans.items():
                sample_f[k] = []
                for e in entries[:16]:
                    sample_f[k].append({
                        "label": getattr(e, "label", "") or "",
                        "current": getattr(e, "current", None),
                    })
            out["fans_sample"] = sample_f
        except Exception as ex:
            out["fans_keys"] = []
            out["fans_err"] = str(ex)

        # battery
        try:
            b = psutil.sensors_battery()
            if b:
                out["battery_psutil"] = {
                    "percent": getattr(b, "percent", None),
                    "secsleft": getattr(b, "secsleft", None),
                    "plugged": getattr(b, "power_plugged", None),
                }
            else:
                out["battery_psutil"] = None
        except Exception as ex:
            out["battery_psutil_err"] = str(ex)

        # swap
        try:
            sw = psutil.swap_memory()
            out["swap"] = {
                "total": sw.total,
                "used": sw.used,
                "free": sw.free,
                "percent": sw.percent,
                "sin": sw.sin,
                "sout": sw.sout,
            }
        except Exception as ex:
            out["swap_err"] = str(ex)

        return out

    @staticmethod
    def sysfs_power_supply_tree() -> dict:
        root = Path("/sys/class/power_supply")
        d: dict[str, Any] = {"exists": root.exists(), "items": []}
        if not root.exists():
            return d

        for item in sorted(root.iterdir()):
            if not item.is_dir():
                continue
            fields: list[str] = []
            try:
                for f in sorted(item.iterdir()):
                    if f.is_file():
                        fields.append(f.name)
            except Exception:
                fields = []

            d["items"].append({"name": item.name, "path": str(item), "fields": fields})
        return d

    @staticmethod
    def sysfs_drm_gpu_busy() -> dict[str, Any]:
        """
        Scan /sys/class/drm/card*/device/gpu_busy_percent (jika ada).
        """
        root = Path("/sys/class/drm")
        out: dict[str, Any] = {"exists": root.exists(), "cards": []}
        if not root.exists():
            return out

        for c in sorted(root.glob("card[0-9]*")):
            if not c.is_dir():
                continue
            dev = c / "device"
            f = dev / "gpu_busy_percent"
            out["cards"].append({
                "card": c.name,
                "gpu_busy_percent_path": str(f),
                "gpu_busy_percent_exists": f.exists(),
                "vendor": _read_text(dev / "vendor"),
                "device": _read_text(dev / "device"),
                "driver": (dev / "driver").exists(),
            })
        return out

    @staticmethod
    def sysfs_block_stat_inventory() -> dict[str, Any]:
        """
        List /sys/block/*/stat & queue/hw_sector_size untuk disk I/O sampling.
        """
        root = Path("/sys/block")
        out: dict[str, Any] = {"exists": root.exists(), "devices": []}
        if not root.exists():
            return out
        for d in sorted(root.iterdir()):
            if not d.is_dir():
                continue
            name = d.name
            if name.startswith(("loop", "zram", "ram", "sr")):
                continue
            stat = d / "stat"
            qsz = d / "queue" / "hw_sector_size"
            out["devices"].append({
                "name": name,
                "stat_exists": stat.exists(),
                "sector_size": _read_text(qsz) if qsz.exists() else "",
            })
        return out


# ==========================================================
# 3) CORE READERS (battery/power, temps, fan, net, swap)
# ==========================================================

class PowerReader:
    """
    Power & battery reading via sysfs (lebih presisi untuk Watt/Volt/Amp).
    psutil tetap dipakai untuk percent/time-left jika tersedia.
    """
    def __init__(self) -> None:
        self.base = Path("/sys/class/power_supply")

    def _pick_battery(self) -> Optional[Path]:
        if not self.base.exists():
            return None
        bats = sorted(self.base.glob("BAT*"))
        return bats[0] if bats else None

    def read(self) -> dict[str, Any]:
        res: dict[str, Any] = {
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

        batt = self._pick_battery()
        if batt is None:
            return res

        res["batt_name"] = batt.name

        st = _read_text(batt / "status") or "Unknown"
        res["status_raw"] = st

        if psutil is not None:
            try:
                b = psutil.sensors_battery()
                if b:
                    res["percent"] = int(getattr(b, "percent", 0) or 0)
                    res["plugged"] = bool(getattr(b, "power_plugged", False))
                    res["secs_left"] = getattr(b, "secsleft", None)
            except Exception:
                pass

        v_uv = _read_float(batt / "voltage_now")
        if v_uv is not None:
            res["volt"] = float(v_uv) / 1e6

        p_uw = None
        if (batt / "power_now").exists():
            p_uw = _read_float(batt / "power_now")
        elif (batt / "power_avg").exists():
            p_uw = _read_float(batt / "power_avg")

        c_ua = None
        if (batt / "current_now").exists():
            c_ua = _read_float(batt / "current_now")

        if p_uw is not None:
            w = float(p_uw) / 1e6
            res["watt"] = w
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

    def read_capacity_health(self) -> dict[str, Any]:
        out: dict[str, Any] = {"ok": False}
        batt = self._pick_battery()
        if batt is None:
            return out

        model = _read_text(batt / "model_name") or batt.name
        out["model"] = model

        full = None
        design = None
        unit = "Wh"

        if (batt / "energy_full").exists() and (batt / "energy_full_design").exists():
            full = _read_float(batt / "energy_full")
            design = _read_float(batt / "energy_full_design")
            unit = "Wh"
        elif (batt / "charge_full").exists() and (batt / "charge_full_design").exists():
            full = _read_float(batt / "charge_full")
            design = _read_float(batt / "charge_full_design")
            unit = "Ah"

        if full is None or design is None or design == 0:
            return out

        full_v = float(full) / 1e6
        design_v = float(design) / 1e6
        health = (full_v / design_v) * 100.0

        cyc = _read_text(batt / "cycle_count") or "?"

        out.update({
            "ok": True,
            "unit": unit,
            "full": full_v,
            "design": design_v,
            "health_pct": health,
            "cycle": cyc,
        })
        return out


class ThermalReader:
    @staticmethod
    def read_key_temps() -> dict[str, Optional[float]]:
        res: dict[str, Optional[float]] = {"cpu": None, "ssd": None, "wifi": None}
        if psutil is None:
            return res
        try:
            temps = psutil.sensors_temperatures(fahrenheit=False) or {}
        except Exception:
            return res

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

        for k, entries in temps.items():
            lk = k.lower()
            if ("iwl" in lk) or ("wifi" in lk) or ("ath" in lk):
                cur = getattr(entries[0], "current", None) if entries else None
                if cur is not None:
                    res["wifi"] = float(cur)
                    break

        return res

    @staticmethod
    def read_fan_rpm() -> Optional[int]:
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


class NetSpeedometer:
    def __init__(self, iface: Optional[str] = None):
        self.iface = iface
        self.t0 = time.time()
        self.io0 = self._read_io()
        self.rx = 0.0
        self.tx = 0.0

    def _read_io(self):
        if psutil is None:
            return None
        if self.iface:
            try:
                per = psutil.net_io_counters(pernic=True) or {}
                return per.get(self.iface)
            except Exception:
                return None
        try:
            return psutil.net_io_counters()
        except Exception:
            return None

    def update(self) -> None:
        if psutil is None:
            return
        t1 = time.time()
        io1 = self._read_io()
        dt = t1 - self.t0
        if dt <= 0 or io1 is None or self.io0 is None:
            self.t0 = t1
            self.io0 = io1
            return

        try:
            self.rx = float(io1.bytes_recv - self.io0.bytes_recv) / dt
            self.tx = float(io1.bytes_sent - self.io0.bytes_sent) / dt
        except Exception:
            self.rx = 0.0
            self.tx = 0.0

        self.t0 = t1
        self.io0 = io1


class WiFiReader:
    @staticmethod
    def _guess_wifi_iface() -> Optional[str]:
        if psutil is None:
            return None
        try:
            stats = psutil.net_if_stats() or {}
            names = sorted(stats.keys())
        except Exception:
            return None

        for n in names:
            ln = n.lower()
            if ln.startswith("wl") or ln.startswith("wlan"):
                return n
        return None

    @staticmethod
    def read(iface: Optional[str] = None) -> dict[str, Any]:
        out: dict[str, Any] = {
            "ok": False,
            "iface": "",
            "ssid": "",
            "signal_dbm": None,
            "rx_bitrate": "",
            "tx_bitrate": "",
        }
        if not shutil.which("iw"):
            return out

        if not iface:
            iface = WiFiReader._guess_wifi_iface()
        if not iface:
            return out
        out["iface"] = iface

        code, text = _sh(f"iw dev {iface} link", timeout=2)
        if code != 0 or not text:
            return out

        for ln in text.splitlines():
            s = ln.strip()
            if s.startswith("SSID:"):
                out["ssid"] = s.split("SSID:", 1)[1].strip()
            elif s.startswith("signal:"):
                try:
                    v = s.split("signal:", 1)[1].strip().split()[0]
                    out["signal_dbm"] = float(v)
                except Exception:
                    pass
            elif s.startswith("rx bitrate:"):
                out["rx_bitrate"] = s.split("rx bitrate:", 1)[1].strip()
            elif s.startswith("tx bitrate:"):
                out["tx_bitrate"] = s.split("tx bitrate:", 1)[1].strip()

        out["ok"] = True
        return out


class SwapReader:
    @staticmethod
    def read() -> dict[str, Any]:
        out: dict[str, Any] = {"ok": False, "pct": 0.0, "used": 0, "total": 0}
        if psutil is None:
            return out
        try:
            sw = psutil.swap_memory()
            out.update({"ok": True, "pct": float(sw.percent), "used": int(sw.used), "total": int(sw.total)})
        except Exception:
            pass
        return out


# ==========================================================
# 4) DISK I/O SAMPLER (Active Time + R/W Speed via sysfs stat)
# ==========================================================

class DiskIOSampler:
    """
    Sampling /sys/block/<dev>/stat

    Index meaning (Linux block stat):
      2: sectors read
      6: sectors written
      9: io_time_ms  (time spent doing I/Os)
    """
    def __init__(self, dev: str) -> None:
        self.dev = dev
        self.t0 = time.time()
        self.prev = self._read_stat()
        self.sector_size = self._read_sector_size()
        self.active_pct = 0.0
        self.read_bps = 0.0
        self.write_bps = 0.0

    def _read_sector_size(self) -> int:
        p = Path(f"/sys/block/{self.dev}/queue/hw_sector_size")
        v = _read_int(p)
        return int(v) if isinstance(v, int) and v > 0 else 512

    def _read_stat(self) -> Optional[List[int]]:
        p = Path(f"/sys/block/{self.dev}/stat")
        s = _read_text(p)
        if not s:
            return None
        try:
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
            self.active_pct = _clamp((d_io_ms / (dt * 1000.0)) * 100.0, 0.0, 100.0)
        except Exception:
            self.active_pct = 0.0
            self.read_bps = 0.0
            self.write_bps = 0.0

        self.t0 = t1
        self.prev = cur


def _pick_root_disk_dev() -> Optional[str]:
    """
    Best-effort: choose disk backing '/'.
    Strategy:
    - find mount source for '/'
    - lsblk -no PKNAME to get parent disk
    - fallback: prefer nvme0n1, else first disk from lsblk
    """
    src = ""
    try:
        code, out = _run(["findmnt", "-n", "-o", "SOURCE", "/"], timeout=2)
        if code == 0 and out:
            src = out.strip()
    except Exception:
        src = ""

    if src.startswith("/dev/"):
        base = os.path.basename(src)
        code, out = _run(["lsblk", "-no", "PKNAME", f"/dev/{base}"], timeout=2)
        if code == 0 and out.strip():
            return out.strip()
        if Path(f"/sys/block/{base}").exists():
            return base

    if Path("/sys/block/nvme0n1").exists():
        return "nvme0n1"

    if shutil.which("lsblk"):
        code, out = _run(["lsblk", "-d", "-n", "-o", "NAME,TYPE"], timeout=2)
        if code == 0 and out:
            for ln in out.splitlines():
                parts = ln.split()
                if len(parts) >= 2 and parts[1] == "disk":
                    return parts[0].strip()

    return None


# ==========================================================
# 5) GPU READER (Intel iGPU-first) — (kept for sensors/probe; not used in LIVE HUD)
# ==========================================================

class GPUReader:
    """Intel iGPU-first GPU usage.

    Prefer sysfs (i915/DRM): /sys/class/drm/card*/device/gpu_busy_percent
    Notes:
    - Only monitors Intel vendor 0x8086 by default.
    - If sensor is missing, returns ok=False (no noisy fallback to NVIDIA/AMD).
    """

    INTEL_VENDOR = "0x8086"

    @staticmethod
    def _intel_cards() -> list[Path]:
        root = Path("/sys/class/drm")
        if not root.exists():
            return []
        cards: list[Path] = []
        for c in sorted(root.glob("card[0-9]*")):
            dev = c / "device"
            if not dev.exists():
                continue
            vendor = _read_text(dev / "vendor")
            if vendor.strip().lower() == GPUReader.INTEL_VENDOR:
                cards.append(c)
        return cards

    @staticmethod
    def _read_freq_mhz(dev: Path) -> tuple[Optional[int], Optional[int]]:
        cand_cur = [dev / "gt_cur_freq_mhz", dev / "device" / "gt_cur_freq_mhz"]
        cand_max = [dev / "gt_max_freq_mhz", dev / "device" / "gt_max_freq_mhz"]
        cur = None
        mx = None
        for p in cand_cur:
            v = _read_int(p)
            if isinstance(v, int):
                cur = v
                break
        for p in cand_max:
            v = _read_int(p)
            if isinstance(v, int):
                mx = v
                break
        return cur, mx

    @staticmethod
    def read_usage_pct() -> dict[str, Any]:
        for card in GPUReader._intel_cards():
            dev = card / "device"
            busy = dev / "gpu_busy_percent"
            if busy.exists():
                v = _read_float(busy)
                if isinstance(v, (int, float)):
                    cur, mx = GPUReader._read_freq_mhz(dev)
                    return {
                        "ok": True,
                        "pct": float(v),
                        "backend": "sysfs(intel gpu_busy_percent)",
                        "card": card.name,
                        "freq_cur_mhz": cur,
                        "freq_max_mhz": mx,
                    }
        return {"ok": False, "pct": None, "backend": "intel-sysfs-missing"}

    @staticmethod
    def gpu_name_hint() -> str:
        if shutil.which("lspci"):
            code, out = _run(["lspci"], timeout=3)
            if code == 0 and out:
                for ln in out.splitlines():
                    low = ln.lower()
                    if ("vga" in low) or ("3d controller" in low) or ("display" in low):
                        return ln.split(": ", 1)[-1].strip()
        return ""


# ==========================================================
# 6) PING SAMPLER (background, HUD-friendly)
# ==========================================================

class PingSampler:
    """
    Background sampler untuk ping latency + jitter.
    Tidak memblokir render HUD.
    """
    def __init__(self, target: str, interval: float = 5.0) -> None:
        self.target = target
        self.interval = float(_clamp(interval, 1.0, 30.0))
        self.last_ms: Optional[float] = None
        self.window: deque[float] = deque(maxlen=20)
        self._stop = threading.Event()
        self._thr = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        if not self._thr.is_alive():
            self._thr.start()

    def stop(self) -> None:
        self._stop.set()

    def avg(self) -> Optional[float]:
        if not self.window:
            return None
        return sum(self.window) / float(len(self.window))

    def jitter(self) -> Optional[float]:
        if len(self.window) < 3:
            return None
        return max(self.window) - min(self.window)

    def _ping_once(self) -> Optional[float]:
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


# ==========================================================
# 7) FEATURES: SENSORS, BATTERY, DISK, NETWORK
# ==========================================================

def run_sensors_dump() -> int:
    ansi.print_system("SENSORS INVENTORY (FULL DISCOVERY MODE)")

    tools = SensorProbe.tool_presence()
    print(f"\n{ansi.c_bold()}[ TOOLING DETECT ]{ansi.c_reset()}")
    for k, ok in tools.items():
        mark = f"{ansi.c_green()}OK ✅{ansi.c_reset()}" if ok else f"{ansi.c_yellow()}MISSING ⚠{ansi.c_reset()}"
        print(f"  - {k:<28}: {mark}")

    print(f"\n{ansi.c_dim()}Rekomendasi Fedora (opsional):{ansi.c_reset()}")
    print("  sudo dnf install lm_sensors smartmontools pciutils iw iproute")

    print(f"\n{ansi.c_bold()}[ PSUTIL PROBE ]{ansi.c_reset()}")
    if psutil is None:
        ansi.print_brief_error("psutil belum terpasang — MON tidak bisa jalan tanpa ini.")
        print("Install Fedora: sudo dnf install python3-psutil")
        return 1

    ov = SensorProbe.psutil_overview()
    v = ov.get("psutil", {})
    print(f"  psutil       : {('OK ✅' if v.get('ok') else 'FAIL ❌')} (v{v.get('version', '?')})")

    print(f"\n{ansi.c_bold()}[ TEMPERATURE KEYS ]{ansi.c_reset()}")
    keys = ov.get("temps_keys", [])
    if not keys:
        print("  (tidak ada / tidak didukung kernel/driver)")
    else:
        print(f"  Keys: {', '.join(keys)}")
        sample = ov.get("temps_sample", {})
        for k in keys:
            entries = sample.get(k, [])
            print(f"\n  {ansi.c_cyan()}{k}{ansi.c_reset()}")
            if not entries:
                print("    (no entries)")
                continue
            for e in entries[:16]:
                lab = (e.get("label") or "-").strip()
                cur = e.get("current")
                hi = e.get("high")
                cr = e.get("critical")
                print(f"    - {lab:<18} cur={cur}°C  high={hi}  crit={cr}")

    print(f"\n{ansi.c_bold()}[ FAN KEYS ]{ansi.c_reset()}")
    fkeys = ov.get("fans_keys", [])
    if not fkeys:
        print("  (tidak ada / tidak didukung)")
    else:
        print(f"  Keys: {', '.join(fkeys)}")
        sample_f = ov.get("fans_sample", {})
        for k in fkeys:
            entries = sample_f.get(k, [])
            print(f"\n  {ansi.c_cyan()}{k}{ansi.c_reset()}")
            if not entries:
                print("    (no entries)")
                continue
            for e in entries[:16]:
                lab = (e.get("label") or "-").strip()
                cur = e.get("current")
                print(f"    - {lab:<18} rpm={cur}")

    print(f"\n{ansi.c_bold()}[ POWER_SUPPLY SYSFS ]{ansi.c_reset()}")
    tree = SensorProbe.sysfs_power_supply_tree()
    if not tree.get("exists"):
        print("  /sys/class/power_supply tidak ada (mungkin non-Linux).")
    else:
        items = tree.get("items", [])
        if not items:
            print("  (tidak ada entry)")
        else:
            for it in items:
                name = it.get("name", "?")
                fields = it.get("fields", [])
                print(f"\n  {ansi.c_cyan()}{name}{ansi.c_reset()}  {ansi.c_dim()}({len(fields)} fields){ansi.c_reset()}")
                cols, _ = ansi.term_size()
                line = ""
                for f in fields:
                    piece = f"{f}  "
                    if len(line) + len(piece) >= min(cols, 120):
                        print("    " + line.rstrip())
                        line = piece
                    else:
                        line += piece
                if line.strip():
                    print("    " + line.rstrip())

    print(f"\n{ansi.c_bold()}[ DRM GPU (sysfs) ]{ansi.c_reset()}")
    drm = SensorProbe.sysfs_drm_gpu_busy()
    if not drm.get("exists"):
        print("  /sys/class/drm tidak ada.")
    else:
        cards = drm.get("cards", [])
        if not cards:
            print("  (no card entries)")
        else:
            for c in cards:
                ex = "yes" if c.get("gpu_busy_percent_exists") else "no"
                print(f"  - {c.get('card')} gpu_busy_percent={ex}  vendor={c.get('vendor')} device={c.get('device')}")

    print(f"\n{ansi.c_bold()}[ SYSFS BLOCK STAT ]{ansi.c_reset()}")
    blk = SensorProbe.sysfs_block_stat_inventory()
    if not blk.get("exists"):
        print("  /sys/block tidak ada.")
    else:
        devs = blk.get("devices", [])
        for d in devs[:40]:
            print(f"  - {d.get('name'):<10} stat={d.get('stat_exists')} sector={d.get('sector_size') or '-'}")
        if len(devs) > 40:
            print(f"  {ansi.c_dim()}...({len(devs)-40} lainnya){ansi.c_reset()}")

    print(f"\n{ansi.c_dim()}Tip:{ansi.c_reset()} HUD paling berguna: CPU package, NVMe composite, watt/volt/amp, swap, disk active%+R/W, wifi ssid/signal, throughput, ping/jitter.")
    return 0


def run_battery_check() -> int:
    ansi.print_system("BATTERY INTELLIGENCE (HISTORY + HEALTH)")

    pr = PowerReader()
    cap = pr.read_capacity_health()
    live = pr.read()

    if not cap.get("ok") and not live.get("ok"):
        print("Sensor baterai tidak ditemukan.")
        return 0

    model = cap.get("model") or live.get("batt_name") or "BAT"
    print(f"\n{ansi.tag(str(model), ansi.c_cyan())}")

    if cap.get("ok"):
        health = float(cap["health_pct"])
        full = float(cap["full"])
        design = float(cap["design"])
        unit = str(cap["unit"])
        cyc = str(cap.get("cycle") or "?")

        col = ansi.c_green() if health >= 80 else (ansi.c_yellow() if health >= 60 else ansi.c_red())
        print(f"  Health        : {col}{health:.2f}%{ansi.c_reset()}  {ansi.c_dim()}(usable vs design){ansi.c_reset()}")
        print(f"  Capacity      : {full:.2f}/{design:.2f} {unit}   Cycles: {cyc}")

        s1 = snapshot_metric("battery", str(model), "health_pct", round(health, 2))
        s2 = snapshot_metric("battery", str(model), "capacity_full", round(full, 2))

        c1 = get_comparison_text("battery", str(model), "health_pct", round(health, 2), "%")
        c2 = get_comparison_text("battery", str(model), "capacity_full", round(full, 2), unit)

        print(f"  {ansi.c_bold()}[ TIME TRAVEL ]{ansi.c_reset()}")
        print(f"  • Health      : {c1 if c1 else '-'}")
        print(f"  • Capacity    : {c2 if c2 else '-'}")
        if s1 or s2:
            print(f"    {ansi.c_dim()}✓ Snapshot hari ini disimpan.{ansi.c_reset()}")
    else:
        print(f"  {ansi.c_yellow()}Info:{ansi.c_reset()} kapasitas/design tidak tersedia di sysfs, hanya tampilkan live-power.")

    if live.get("ok"):
        pct = live.get("percent")
        st = str(live.get("status_raw") or "Unknown")
        w = live.get("watt_str")
        v = live.get("volt_str")
        a = live.get("amp_str")

        pct_txt = f"{pct}%" if isinstance(pct, int) else "?"
        print(f"\n  {ansi.c_bold()}[ LIVE POWER ]{ansi.c_reset()}")
        print(f"  Level         : {pct_txt}   Status: {st}")
        print(f"  Flow          : {ansi.c_yellow()}{w}{ansi.c_reset()} @ {v} | {a}")

    return 0


def _lsblk_disks() -> list[dict[str, str]]:
    if not shutil.which("lsblk"):
        return []
    code, out = _run(["lsblk", "-J", "-d", "-o", "NAME,MODEL,SIZE,TYPE,TRAN,ROTA"], timeout=3)
    if code != 0 or not out:
        return []
    try:
        d = json.loads(out)
        devs = d.get("blockdevices", [])
        res: list[dict[str, str]] = []
        for x in devs:
            if x.get("type") != "disk":
                continue
            name = str(x.get("name") or "")
            if name.startswith(("loop", "zram", "sr")):
                continue
            res.append({
                "name": name,
                "model": str(x.get("model") or "").strip(),
                "size": str(x.get("size") or "").strip(),
                "tran": str(x.get("tran") or "").strip(),
                "rota": str(x.get("rota") or "").strip(),
            })
        return res
    except Exception:
        return []

def _parse_smart_health(smart_a: str, smart_h: str) -> dict[str, Any]:
    out: dict[str, Any] = {
        "health": "Unknown",
        "health_color": ansi.c_dim(),
        "temp": "N/A",
        "power_on": "N/A",
        "writes_gb": None,
        "has_writes": False,
    }

    for ln in smart_a.splitlines():
        if "Percentage Used" in ln:
            try:
                used = int(ln.split(":")[-1].replace("%", "").strip())
                h = 100 - used
                out["health"] = f"{h}%"
                out["health_color"] = ansi.c_green() if h >= 80 else ansi.c_yellow()
            except Exception:
                pass

        if "Temperature:" in ln and "Celsius" in ln:
            try:
                t = ln.split("Temperature:", 1)[1].replace("Celsius", "").strip()
                out["temp"] = t.replace(" ", "") + "°C"
            except Exception:
                pass

        if ("Temperature" in ln or "Celsius" in ln) and ("Airflow" not in ln):
            try:
                toks = ln.split()
                if toks and toks[-1].lstrip("-").isdigit():
                    out["temp"] = toks[-1] + "°C"
            except Exception:
                pass

        if "Power On Hours" in ln or "Power_On_Hours" in ln:
            try:
                out["power_on"] = ln.split(":")[-1].strip() if ":" in ln else ln.split()[-1].strip()
            except Exception:
                pass

        if "Data Units Written" in ln and "[" in ln and "]" in ln:
            try:
                raw = ln.split("[", 1)[1].split("]", 1)[0].strip()
                num_s, unit = raw.split()[:2]
                num = float(num_s)
                gb = num * 1024.0 if "TB" in unit.upper() else (num if "GB" in unit.upper() else None)
                if gb is not None:
                    out["writes_gb"] = float(gb)
                    out["has_writes"] = True
            except Exception:
                pass

        if "Total_LBAs_Written" in ln:
            try:
                lba = int(ln.split()[-1])
                gb = (lba * 512.0) / (1024.0**3)
                out["writes_gb"] = float(gb)
                out["has_writes"] = True
            except Exception:
                pass

    if out["health"] == "Unknown":
        if "PASSED" in smart_h:
            out["health"] = "PASSED"
            out["health_color"] = ansi.c_green()
        elif "FAILED" in smart_h:
            out["health"] = "FAILED"
            out["health_color"] = ansi.c_red()

    return out

def run_disk_check(argv: list[str]) -> int:
    ansi.print_system("STORAGE HEALTH (INTELLIGENT MODE)")

    if not shutil.which("smartctl"):
        ansi.print_brief_error("Butuh 'smartmontools'. Install: sudo dnf install smartmontools")
        return 1

    disks = _lsblk_disks()
    if not disks:
        print("Tidak ada disk fisik yang terdeteksi.")
        return 0

    want_sudo = (os.geteuid() == 0) or any(a in argv for a in ("--sudo", "sudo", "--deep", "deep"))

    if not want_sudo and os.geteuid() != 0:
        print(f"{ansi.c_yellow()}Info:{ansi.c_reset()} beberapa metrik (TBW/health detail) mungkin butuh sudo.")
        print(f"  Jalankan: {ansi.c_cyan()}sudo ai mon disk{ansi.c_reset()}  (untuk data lengkap)")

    for d in disks:
        name = d.get("name", "?")
        model = (d.get("model") or "").strip() or "-"
        size = d.get("size") or "-"
        tran = d.get("tran") or "-"
        dev = f"/dev/{name}"

        print(f"\n{ansi.tag(name, ansi.c_cyan())} {ansi.c_dim()}({size}, {tran}){ansi.c_reset()}")
        print(f"  Model         : {model}")

        cmdA = f"smartctl -A {dev}"
        cmdH = f"smartctl -H {dev}"
        codeA, outA = _sh(cmdA, timeout=6)
        codeH, outH = _sh(cmdH, timeout=6)

        need_priv = ("permission denied" in outA.lower()) or ("requires root" in outA.lower()) or (codeA != 0 and not outA)
        if need_priv and want_sudo and os.geteuid() != 0:
            codeA, outA = _sh(f"sudo {cmdA}", timeout=10)
            codeH, outH = _sh(f"sudo {cmdH}", timeout=10)

        if not outA and not outH:
            print(f"  Health        : {ansi.c_yellow()}N/A ⚠{ansi.c_reset()}  {ansi.c_dim()}(smartctl tidak memberi output){ansi.c_reset()}")
            continue

        parsed = _parse_smart_health(outA, outH)

        hc = parsed["health_color"]
        print(f"  Health        : {hc}{parsed['health']}{ansi.c_reset()}")
        print(f"  Temperature   : {parsed['temp']}")
        print(f"  Power On      : {parsed['power_on']}")

        if parsed["has_writes"] and parsed["writes_gb"] is not None:
            gb = float(parsed["writes_gb"])
            print(f"  Total Written : {gb:.2f} GB")

            uniq = f"{name}_{model.replace(' ', '_')}"
            did = snapshot_metric("disk", uniq, "tbw_gb", round(gb, 2))
            diff = get_comparison_text("disk", uniq, "tbw_gb", round(gb, 2), "GB")

            print(f"  {ansi.c_bold()}[ HISTORY ]{ansi.c_reset()} {diff if diff else '-'}")
            if did:
                print(f"    {ansi.c_dim()}✓ Snapshot hari ini disimpan.{ansi.c_reset()}")
        else:
            print(f"  Total Written : {ansi.c_dim()}N/A{ansi.c_reset()}  {ansi.c_dim()}(butuh sudo / device tidak expose metrik){ansi.c_reset()}")

    return 0


def _pick_default_target(user_target: str | None) -> str:
    t = (user_target or "").strip()
    return t if t else "google.com"

def _ip_of_target(target: str) -> str:
    try:
        return socket.gethostbyname(target)
    except Exception:
        return "-"

def _list_ifaces_summary() -> list[dict[str, str]]:
    if psutil is None:
        return []
    try:
        info = psutil.net_if_addrs() or {}
        stat = psutil.net_if_stats() or {}
    except Exception:
        return []

    rows: list[dict[str, str]] = []
    for iface in sorted(info.keys()):
        if iface == "lo":
            continue
        up = "DOWN"
        try:
            if iface in stat and stat[iface].isup:
                up = "UP"
        except Exception:
            pass
        ip = "-"
        for a in info.get(iface, []):
            if getattr(a, "family", None) == socket.AF_INET:
                ip = str(getattr(a, "address", "-"))
                break
        rows.append({"iface": iface, "up": up, "ip": ip})
    return rows

def run_net_diag(argv: list[str]) -> int:
    if psutil is None:
        ansi.print_brief_error("Fitur net butuh psutil.")
        print("Install Fedora: sudo dnf install python3-psutil")
        return 1

    target = argv[0].strip() if argv else ""
    if not target:
        default = "google.com"
        print(f"\n{ansi.c_cyan()}Target domain/IP{ansi.c_reset()} [{default}]: ", end="", flush=True)
        try:
            inp = input().strip()
        except KeyboardInterrupt:
            print("")
            return 0
        target = inp or default

    target = _pick_default_target(target)
    ansi.print_system(f"NETWORK DIAGNOSTICS -> {target}")

    print(f"\n{ansi.c_bold()}[ LOCAL INTERFACES ]{ansi.c_reset()}")
    rows = _list_ifaces_summary()
    if not rows:
        print("  (tidak ada interface)")
    else:
        for r in rows:
            col = ansi.c_green() if r["up"] == "UP" else ansi.c_red()
            print(f"  • {ansi.c_cyan()}{r['iface']:<8}{ansi.c_reset()} : {col}{r['up']:<4}{ansi.c_reset()} | {r['ip']}")

    w = WiFiReader.read()
    if w.get("ok"):
        ssid = w.get("ssid") or "-"
        sig = w.get("signal_dbm")
        sig_txt = f"{sig:.0f} dBm" if isinstance(sig, (int, float)) else "-"
        print(f"\n{ansi.c_bold()}[ WIFI DETAIL ]{ansi.c_reset()}")
        print(f"  IFACE         : {w.get('iface')}")
        print(f"  SSID          : {ssid}")
        print(f"  SIGNAL        : {sig_txt}")
        if w.get("rx_bitrate"):
            print(f"  RX BITRATE    : {w.get('rx_bitrate')}")
        if w.get("tx_bitrate"):
            print(f"  TX BITRATE    : {w.get('tx_bitrate')}")

    ip = _ip_of_target(target)
    print(f"\n{ansi.c_bold()}[ CONNECTION QUALITY ]{ansi.c_reset()}")
    print(f"  Target IP     : {ip}")

    code, out = _sh(f"ping -c 5 -i 0.2 {target}", timeout=6)
    times: list[float] = []
    if out:
        for ln in out.splitlines():
            if "time=" in ln:
                try:
                    t = float(ln.split("time=", 1)[1].split()[0])
                    times.append(t)
                except Exception:
                    pass

    if times:
        avg = sum(times) / len(times)
        jit = max(times) - min(times)
        qc = "EXCELLENT ✅"
        col = ansi.c_green()
        if jit > 10:
            qc = "GOOD 🙂"
            col = ansi.c_yellow()
        if jit > 50:
            qc = "UNSTABLE ⚠"
            col = ansi.c_red()

        print(f"  Avg Latency   : {avg:.1f} ms")
        print(f"  Jitter        : {jit:.1f} ms ({col}{qc}{ansi.c_reset()})")
    else:
        print(f"  Result        : {ansi.c_red()}TIMEOUT/RTO ❌{ansi.c_reset()}  {ansi.c_dim()}(host unreachable / ICMP blocked){ansi.c_reset()}")

    print(f"\n{ansi.c_bold()}[ DNS CHECK ]{ansi.c_reset()}")
    try:
        ip2 = socket.gethostbyname(target)
        print(f"  Local Resolve : {ansi.c_green()}OK ✅{ansi.c_reset()} ({target} -> {ip2})")
    except Exception:
        print(f"  Local Resolve : {ansi.c_red()}FAIL ❌{ansi.c_reset()}")

    print(f"  Global Check  : {ansi.c_cyan()}https://dnschecker.org/#A/{target}{ansi.c_reset()}")
    return 0


def run_net_live(argv: list[str]) -> int:
    target = _pick_default_target(argv[0] if argv else "google.com")
    input_muter = ansi.MuteInputDuringWait()

    ansi.alt_screen_enter()
    ansi.cursor_hide()
    try:
        print(f"{ansi.c_bold()}{ansi.c_cyan()} REALTIME NET MONITOR {ansi.c_reset()} -> {target}")
        print(f"{ansi.c_dim()}Ctrl+C untuk keluar.{ansi.c_reset()}")
        print(f"{'TIME':<10} {'LAT':<10} GRAPH")
        print("-" * 60)

        with input_muter:
            while True:
                ts = _dt.datetime.now().strftime("%H:%M:%S")
                code, out = _sh(f"ping -c 1 -W 1 {target}", timeout=2)
                ms = None
                if out and "time=" in out:
                    try:
                        ms = float(out.split("time=", 1)[1].split()[0])
                    except Exception:
                        ms = None

                if ms is None:
                    print(f"{ts:<10} {'TO':<10} {ansi.c_red()}X{ansi.c_reset()}")
                else:
                    cnt = int(ms / 8.0)
                    cnt = int(_clamp(cnt, 1, 40))
                    col = ansi.c_green()
                    if ms > 50:
                        col = ansi.c_yellow()
                    if ms > 150:
                        col = ansi.c_red()
                    bar = f"{col}{'█'*cnt}{ansi.c_reset()}"
                    print(f"{ts:<10} {ms:>6.1f}ms   {bar}")
                time.sleep(0.8)

    except KeyboardInterrupt:
        pass
    finally:
        ansi.cursor_show()
        ansi.alt_screen_exit()
    return 0


# ==========================================================
# 8) LIVE COCKPIT (HUD V4.1)
# ==========================================================

_IGNORED_FS_TYPES = {
    "proc", "sysfs", "devtmpfs", "devpts", "tmpfs", "cgroup", "cgroup2", "pstore",
    "securityfs", "debugfs", "tracefs", "configfs", "efivarfs", "mqueue", "hugetlbfs",
    "fusectl", "autofs", "overlay", "squashfs",
}

def _mounted_partitions() -> list[dict[str, str]]:
    """Return mounted partitions (non-virtual) — mirrors KDE System Monitor behavior."""
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
    if mountpoint == "/":
        return "root"
    if mountpoint == "/home":
        return "home"
    base = os.path.basename(mountpoint.rstrip("/"))
    if base:
        return base
    return os.path.basename(device) or "disk"


def _parse_disk_specs(argv: list[str]) -> tuple[list[tuple[str, str]], list[str]]:
    """Parse --disk "LABEL=TARGET" (repeatable). Return (specs, remaining_argv)."""
    specs: list[tuple[str, str]] = []
    rest: list[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--disk" and i + 1 < len(argv):
            raw = (argv[i + 1] or "").strip()
            i += 2
            if "=" in raw:
                label, target = raw.split("=", 1)
                label = label.strip() or "disk"
                target = target.strip()
                if target:
                    specs.append((label, target))
            continue
        rest.append(a)
        i += 1
    return specs, rest


def _resolve_disk_target(target: str, mounts: list[dict[str, str]]) -> Optional[str]:
    """Resolve TARGET to mountpoint (supports mountpoint or device path/name)."""
    t = target.strip()
    if not t:
        return None
    for m in mounts:
        if m["mount"] == t:
            return m["mount"]
    for m in mounts:
        if m["device"] == t:
            return m["mount"]
    for m in mounts:
        if os.path.basename(m["device"]) == t:
            return m["mount"]
    return None


def _net_quality(ping_avg_ms: Optional[float], jitter_ms: Optional[float], wifi_dbm: Optional[float]) -> tuple[str, str]:
    """Return (label, color)."""
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


class ThrottleReader:
    """Best-effort thermal throttling indicator (Intel-friendly)."""

    def __init__(self) -> None:
        self.prev_core: Optional[int] = None
        self.prev_pkg: Optional[int] = None

    def read(self) -> dict[str, Any]:
        base = Path("/sys/devices/system/cpu/cpu0/thermal_throttle")
        core_p = base / "core_throttle_count"
        pkg_p = base / "package_throttle_count"
        if not core_p.exists() and not pkg_p.exists():
            return {"ok": False}
        core = _read_int(core_p) if core_p.exists() else None
        pkg = _read_int(pkg_p) if pkg_p.exists() else None

        delta_core = None
        delta_pkg = None
        if isinstance(core, int) and isinstance(self.prev_core, int):
            delta_core = core - self.prev_core
        if isinstance(pkg, int) and isinstance(self.prev_pkg, int):
            delta_pkg = pkg - self.prev_pkg

        self.prev_core = core if isinstance(core, int) else self.prev_core
        self.prev_pkg = pkg if isinstance(pkg, int) else self.prev_pkg

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


def _uptime_str() -> str:
    if psutil is not None:
        try:
            secs = time.time() - float(psutil.boot_time())
            d = int(secs // 86400)
            secs %= 86400
            h = int(secs // 3600)
            m = int((secs % 3600) // 60)
            return f"{d}d {h}h {m}m"
        except Exception:
            pass

    try:
        s = _read_text(Path("/proc/uptime"))
        up = float(s.split()[0])
        d = int(up // 86400)
        up %= 86400
        h = int(up // 3600)
        m = int((up % 3600) // 60)
        return f"{d}d {h}h {m}m"
    except Exception:
        return "?"

def _loadavg() -> tuple[float, float, float]:
    try:
        la = os.getloadavg()
        return float(la[0]), float(la[1]), float(la[2])
    except Exception:
        return 0.0, 0.0, 0.0

def _cpu_model() -> str:
    try:
        txt = _read_text(Path("/proc/cpuinfo"))
        for ln in txt.splitlines():
            if ln.lower().startswith("model name"):
                return ln.split(":", 1)[-1].strip()
    except Exception:
        pass
    return ""

def _nvme_model_hint(dev: Optional[str]) -> str:
    if not dev:
        return ""
    sys_p = Path(f"/sys/block/{dev}/device/model")
    if sys_p.exists():
        return _read_text(sys_p)
    if shutil.which("lsblk"):
        code, out = _run(["lsblk", "-d", "-n", "-o", "MODEL", f"/dev/{dev}"], timeout=2)
        if code == 0 and out:
            return out.splitlines()[0].strip()
    return ""

def _fan_label(rpm: Optional[int]) -> str:
    if not isinstance(rpm, int) or rpm <= 0:
        return f"{ansi.c_dim()}N/A{ansi.c_reset()}"
    # Simple bands; adjustable if needed
    if rpm < 2500:
        lvl = f"{ansi.c_green()}Idle{ansi.c_reset()}"
    elif rpm < 4500:
        lvl = f"{ansi.c_green()}Low{ansi.c_reset()}"
    elif rpm < 6500:
        lvl = f"{ansi.c_yellow()}Med{ansi.c_reset()}"
    else:
        lvl = f"{ansi.c_red()}High{ansi.c_reset()}"
    return f"{lvl}{ansi.c_dim()} ({rpm} RPM){ansi.c_reset()}"

def _cpu_freq_info() -> dict[str, Any]:
    """
    Best-effort CPU frequency + governor.
    Returns:
      {"ok": bool, "ghz": float|None, "gov": str|None}
    """
    gov = None
    gov_p = Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor")
    if gov_p.exists():
        g = _read_text(gov_p).strip()
        gov = g or None

    # Prefer sysfs scaling_cur_freq across available CPUs (kHz)
    vals: list[float] = []
    base = Path("/sys/devices/system/cpu")
    try:
        for p in sorted(base.glob("cpu[0-9]*/cpufreq/scaling_cur_freq")):
            v = _read_int(p)
            if isinstance(v, int) and v > 0:
                # kHz -> GHz
                vals.append(float(v) / 1e6)
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

    # Fallback /proc/cpuinfo average MHz (may be noisy)
    try:
        txt = _read_text(Path("/proc/cpuinfo"))
        mhz_vals: list[float] = []
        for ln in txt.splitlines():
            if ln.lower().startswith("cpu mhz"):
                try:
                    mhz_vals.append(float(ln.split(":", 1)[-1].strip()))
                except Exception:
                    continue
        if mhz_vals:
            return {"ok": True, "ghz": (sum(mhz_vals) / len(mhz_vals)) / 1000.0, "gov": gov}
    except Exception:
        pass

    return {"ok": False, "ghz": None, "gov": gov}

def _topmem_processes(n: int = 3) -> list[tuple[str, float]]:
    """Return list of (name, rss_gib)."""
    if psutil is None:
        return []
    try:
        rows: list[tuple[str, float]] = []
        for p in psutil.process_iter(attrs=["name", "memory_info"]):
            mi = p.info.get("memory_info")
            rss = getattr(mi, "rss", 0) if mi else 0
            if rss and rss > 0:
                rows.append((p.info.get("name") or "?", float(rss) / (1024.0**3)))
        rows.sort(key=lambda x: x[1], reverse=True)
        return rows[: max(0, int(n))]
    except Exception:
        return []

def _topcpu_processes(total_cpu_pct: float, n: int = 3) -> list[tuple[str, float, float]]:
    """
    Return list of (name, cpu_pct, share_of_total_cpu_pct).
    Note: caller must have primed p.cpu_percent(None) earlier (done in run_live_cockpit).
    """
    if psutil is None:
        return []
    try:
        rows: list[tuple[str, float, float]] = []
        for p in psutil.process_iter(attrs=["name"]):
            try:
                pct = float(p.cpu_percent(interval=None) or 0.0)
            except Exception:
                continue
            if pct <= 0.0:
                continue
            share = 0.0
            if total_cpu_pct > 0.1:
                share = (pct / total_cpu_pct) * 100.0
            share = _clamp(share, 0.0, 100.0)
            rows.append(((p.info.get("name") or "?"), pct, share))
        rows.sort(key=lambda x: x[1], reverse=True)
        return rows[: max(0, int(n))]
    except Exception:
        return []

def _fmt_topcpu(rows: list[tuple[str, float, float]], name_w: int = 16) -> list[str]:
    out: list[str] = []
    for i, (nm, pct, share) in enumerate(rows, start=1):
        nm2 = _trim_name(nm, name_w)
        if i == 1:
            out.append(f"  {i}) {nm2:<{name_w}} {pct:>5.1f}%   {ansi.c_dim()}(≈{share:.0f}% of total){ansi.c_reset()}")
        else:
            out.append(f"  {i}) {nm2:<{name_w}} {pct:>5.1f}%")
    return out

def _fmt_topmem(rows: list[tuple[str, float]], name_w: int = 16) -> list[str]:
    out: list[str] = []
    for i, (nm, gib) in enumerate(rows, start=1):
        nm2 = _trim_name(nm, name_w)
        out.append(f"  {i}) {nm2:<{name_w}} {gib:>5.2f} GiB")
    return out


# ==========================================================
# 9) LIVE COCKPIT runner
# ==========================================================

def run_live_cockpit(argv: list[str]) -> int:
    """HUD realtime (V4.1).

    Options:
      --interval N           (float)
      --compact             (hide footer/tips)
      --target HOST         (ping sampler target)
      --iface IFACE         (net speedometer per NIC)
      --disk "LABEL=TARGET" (repeatable; TARGET mountpoint or device)
      --no-disks            (hide disks section)
      --maxwidth N          (limit render width)
    """
    if psutil is None:
        ansi.print_brief_error("Fitur Monitoring butuh library 'psutil'.")
        print("Install Fedora: sudo dnf install python3-psutil")
        return 1

    disk_specs, argv2 = _parse_disk_specs(argv)

    interval = 1.0
    compact = False
    target = "google.com"
    iface: Optional[str] = None
    show_disks = True
    maxwidth: Optional[int] = None

    i = 0
    while i < len(argv2):
        a = argv2[i]
        if a in ("--compact", "compact"):
            compact = True
        elif a == "--no-disks":
            show_disks = False
        elif a == "--interval" and i + 1 < len(argv2):
            try:
                interval = float(argv2[i + 1])
            except Exception:
                pass
            i += 1
        elif a == "--target" and i + 1 < len(argv2):
            target = argv2[i + 1].strip() or target
            i += 1
        elif a == "--iface" and i + 1 < len(argv2):
            iface = argv2[i + 1].strip() or None
            i += 1
        elif a == "--maxwidth" and i + 1 < len(argv2):
            try:
                maxwidth = int(float(argv2[i + 1]))
            except Exception:
                maxwidth = None
            i += 1
        else:
            if i == 0:
                try:
                    interval = float(a)
                except Exception:
                    pass
        i += 1

    interval = float(_clamp(interval, 0.2, 5.0))
    target = _pick_default_target(target)

    pr = PowerReader()
    ns = NetSpeedometer(iface=iface)
    ns.update()

    disk_dev = _pick_root_disk_dev()
    disk_io = DiskIOSampler(disk_dev) if disk_dev else None

    ping = PingSampler(target=target, interval=max(2.0, interval * 2.0))
    ping.start()

    thr = ThrottleReader()

    last_snap = 0.0
    SNAP_EVERY = 600.0

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
    disk_cache: list[dict[str, Any]] = []

    last_freq = 0.0
    FREQ_EVERY = 2.0
    cpu_freq_str = f"{ansi.c_dim()}N/A{ansi.c_reset()}"

    input_muter = ansi.MuteInputDuringWait()
    ansi.alt_screen_enter()
    ansi.cursor_hide()

    try:
        # prime cpu_percent and process cpu_percent
        try:
            psutil.cpu_percent(interval=None)
        except Exception:
            pass
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

                # core metrics
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

                sw = SwapReader.read()
                temps = ThermalReader.read_key_temps()
                fan_rpm = ThermalReader.read_fan_rpm()
                power = pr.read()
                wifi = WiFiReader.read(iface=iface)  # honor iface if it's wifi
                ns.update()
                if disk_io:
                    disk_io.update()

                # throttled CPU freq
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

                # throttled top processes
                if time.time() - last_top >= TOP_EVERY:
                    t_cpu = _topcpu_processes(cpu, n=3)
                    t_mem = _topmem_processes(n=3)
                    top_cpu_rows = _fmt_topcpu(t_cpu, name_w=16) if t_cpu else [f"  {ansi.c_dim()}(no data){ansi.c_reset()}"]
                    top_mem_rows = _fmt_topmem(t_mem, name_w=16) if t_mem else [f"  {ansi.c_dim()}(no data){ansi.c_reset()}"]
                    last_top = time.time()

                # throttled disks inventory (mounted-only)
                if show_disks and (time.time() - last_disks >= DISK_EVERY):
                    mounts = _mounted_partitions()
                    disk_cache = []

                    overrides: dict[str, str] = {}
                    for label, target_spec in disk_specs:
                        mp = _resolve_disk_target(target_spec, mounts)
                        if mp:
                            overrides[mp] = label

                    for m in mounts:
                        mp = m["mount"]
                        dev = m["device"]
                        label = overrides.get(mp) or _default_disk_label(mp, dev)
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

                # throttled hardware footer
                if time.time() - last_hw >= HW_EVERY:
                    hw_cpu = _cpu_model()
                    hw_nvme = _nvme_model_hint(disk_dev)
                    last_hw = time.time()

                # battery logic
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

                # periodic background snapshot battery health
                if time.time() - last_snap >= SNAP_EVERY:
                    cap = pr.read_capacity_health()
                    if cap.get("ok"):
                        model = str(cap.get("model") or "BAT")
                        snapshot_metric("battery", model, "health_pct", round(float(cap["health_pct"]), 2))
                        snapshot_metric("battery", model, "capacity_full", round(float(cap["full"]), 2))
                    last_snap = time.time()

                # render
                ansi.clear_screen()

                # Header (polished + colored + separated)
                title_left = f"{ansi.c_cyan()}{ansi.c_bold()}AI MONITOR{ansi.c_reset()} {ansi.c_dim()}| LIVE SYSTEM COCKPIT{ansi.c_reset()}"
                title_right = f"{ansi.c_dim()}AI-Terminal{ansi.c_reset()}"
                print(_align_lr(title_left, title_right, usable))

                sep = f"{ansi.c_dim()}{'─' * min(usable, 96)}{ansi.c_reset()}"

                ts = _now_ts()
                meta = f"{ansi.c_dim()}{ts}{ansi.c_reset()}  {ansi.c_dim()}|{ansi.c_reset()}  Host: {host}  Interval: {interval:.1f}s  Target: {target}"
                if iface:
                    meta += f"  Iface: {iface}"
                print(meta)

                line_os = f"OS: {distro or '-'}"
                line_kr = f"Kernel: {kernel}"
                print(f"{ansi.c_dim()}{line_os:<36}  {line_kr}{ansi.c_reset()}")

                la1, la5, la15 = _loadavg()
                cpu_count = os.cpu_count() or 1
                load_line = f"Uptime: {_uptime_str()}   Load avg (1/5/15): {la1:.2f}/{la5:.2f}/{la15:.2f}   CPUs: {cpu_count}"
                print(f"{ansi.c_dim()}{load_line}{ansi.c_reset()}")
                print(sep)

                # CPU / RAM / SWAP / I/O (no Top here; moved below)
                print(f"{ansi.c_bold()} CPU {ansi.c_reset()} {_draw_bar(cpu)} {cpu:>5.1f}%   {ansi.c_dim()}Freq:{ansi.c_reset()} {cpu_freq_str}")
                print(f"{ansi.c_bold()} RAM {ansi.c_reset()} {_draw_bar(ram_pct)} {ram_pct:>5.1f}%   {ram_used/(1024**3):.1f}/{ram_total/(1024**3):.1f} GiB")

                if sw.get("ok") and sw.get("total", 0) > 0:
                    sw_pct = float(sw.get("pct", 0.0))
                    sw_used = int(sw.get("used", 0))
                    sw_total = int(sw.get("total", 0))
                    print(f"{ansi.c_bold()}SWAP {ansi.c_reset()} {_draw_bar(sw_pct)} {sw_pct:>5.1f}%   {_human_bytes(sw_used)}/{_human_bytes(sw_total)}")
                else:
                    print(f"{ansi.c_bold()}SWAP {ansi.c_reset()} {ansi.c_dim()}(not available){ansi.c_reset()}")

                if disk_io and disk_dev:
                    util = float(disk_io.active_pct)
                    col_u = ansi.c_green() if util <= 60 else (ansi.c_yellow() if util <= 85 else ansi.c_red())
                    r = _human_rate_bps(disk_io.read_bps)
                    w = _human_rate_bps(disk_io.write_bps)
                    print(f"{ansi.c_bold()} I/O {ansi.c_reset()} {col_u}{util:>5.1f}%{ansi.c_reset()}  R:{r:<11}  W:{w:<11}  {ansi.c_dim()}({disk_dev}){ansi.c_reset()}")

                print(sep)

                # Top lists
                print(f"{ansi.c_bold()}Top CPU (1–3){ansi.c_reset()}")
                for ln in top_cpu_rows:
                    print(ln)
                print("")
                print(f"{ansi.c_bold()}Top MEM (1–3){ansi.c_reset()}")
                for ln in top_mem_rows:
                    print(ln)

                print(sep)

                # Temps + Fan (fan label)
                cpu_t = temps.get("cpu")
                ssd_t = temps.get("ssd")
                wifi_t = temps.get("wifi")

                cpu_t_s = f"{cpu_t:.1f}°C" if isinstance(cpu_t, (int, float)) else "-"
                ssd_t_s = f"{ssd_t:.1f}°C" if isinstance(ssd_t, (int, float)) else "-"
                wifi_t_s = f"{wifi_t:.1f}°C" if isinstance(wifi_t, (int, float)) else "-"

                fan_s = _fan_label(int(fan_rpm) if isinstance(fan_rpm, (int, float)) else None)

                print(
                    f"{ansi.c_bold()}TEMP {ansi.c_reset()}"
                    f"CPU:{_col_by_temp(cpu_t)}{cpu_t_s}{ansi.c_reset()}   "
                    f"NVMe:{_col_by_temp(ssd_t)}{ssd_t_s}{ansi.c_reset()}   "
                    f"WiFi:{_col_by_temp(wifi_t)}{wifi_t_s}{ansi.c_reset()}   "
                    f"{ansi.c_dim()}Fan:{ansi.c_reset()} {fan_s}"
                )

                # Throttle (separate line)
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
                        print(f"{ansi.c_bold()}THROT{ansi.c_reset()} {ansi.c_red()}ACTIVE{ansi.c_reset()}{extra}")
                    else:
                        print(f"{ansi.c_bold()}THROT{ansi.c_reset()} {ansi.c_green()}OK{ansi.c_reset()}")
                else:
                    print(f"{ansi.c_bold()}THROT{ansi.c_reset()} {ansi.c_dim()}N/A{ansi.c_reset()}")

                # Power
                lvl_txt = f"{lvl_i}%" if isinstance(lvl_i, int) else "?"
                print(
                    f"{ansi.c_bold()}POWER{ansi.c_reset()} "
                    f"{bat_col}{lvl_txt}{ansi.c_reset()} [{st_txt}]   "
                    f"{ansi.c_yellow()}{power.get('watt_str','N/A')}{ansi.c_reset()} @ {power.get('volt_str','N/A')} | {power.get('amp_str','N/A')}   "
                    f"{ansi.c_dim()}{eta}{ansi.c_reset()}"
                )

                print(sep)

                # Network (2 lines)
                rx = _human_rate_bps(ns.rx)
                tx = _human_rate_bps(ns.tx)

                ping_ms = ping.last_ms
                ping_avg = ping.avg()
                jit = ping.jitter()

                ping_txt = "TO" if ping_ms is None else f"{ping_ms:.0f}ms"
                avg_txt = "-" if ping_avg is None else f"{ping_avg:.0f}ms"
                jit_txt = "-" if jit is None else f"{jit:.0f}ms"
                pcol = _col_by_ping(ping_ms)

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
                    ping_line += f"   {ansi.c_dim()}WiFi:{ansi.c_reset()} { _trim_name(ssid, 14) } ({_col_by_dbm(sig)}{sig_txt}{ansi.c_reset()})"
                print(ping_line)

                # DISKS (mounted-only) — table aligned, mount at right
                if show_disks:
                    print(sep)
                    print(f"{ansi.c_bold()}DISKS{ansi.c_reset()} {ansi.c_dim()}(mounted partitions){ansi.c_reset()}")

                    if not disk_cache:
                        print(f"  {ansi.c_dim()}(no mounted disks found){ansi.c_reset()}")
                    else:
                        # Column sizing (best-effort)
                        name_w = 6
                        free_w = 9
                        bar_w = 10
                        used_w = 15
                        # Remaining for mount column
                        fixed = 2 + name_w + 3 + free_w + 3 + (bar_w + 5) + 3 + used_w + 3 + 7  # rough
                        mount_w = max(12, min(usable - fixed, 42))

                        usedpct_w = bar_w + 5
                        hdr = (
                            f"  {ansi.c_dim()}"
                            f"{'NAME':<{name_w}} | "
                            f"{'FREE':<{free_w}} | "
                            f"{'USED%':<{usedpct_w}} | "
                            f"{'USED/TOTAL':<{used_w}} | "
                            f"MOUNT"
                            f"{ansi.c_reset()}"
                        )

                        if usable >= 70:
                            print(hdr)

                        for d in disk_cache:
                            pct = float(d.get("pct", 0.0))
                            used = float(d.get("used", 0.0))
                            total = float(d.get("total", 0.0))
                            free = float(d.get("free", 0.0))
                            label = str(d.get("label") or "disk")
                            mp = str(d.get("mount") or "-")

                            name_disp = _trim_name(label, name_w)
                            free_disp = _human_bytes(free)
                            used_disp = f"{_human_bytes(used)}/{_human_bytes(total)}"
                            mp_disp = _trim_tail(mp, mount_w)

                            bar = _draw_bar(pct, width=bar_w)
                            pct_disp = f"{pct:>3.0f}%"

                            # Align: mount at far right
                            line = (
                                f"  {ansi.c_bold()}{name_disp:<{name_w}}{ansi.c_reset()} | "
                                f"{free_disp:<{free_w}} | "
                                f"{bar} {pct_disp:<4} | "
                                f"{used_disp:<{used_w}} | "
                                f"({mp_disp})"
                            )
                            print(line)

                # Footer: hardware hints (wrapped, not sideways)
                if not compact:
                    hw_parts = []
                    if hw_cpu:
                        hw_parts.append(f"CPU: {hw_cpu.strip()}")
                    if hw_nvme:
                        hw_parts.append(f"NVMe: {hw_nvme.strip()}")
                    if hw_parts:
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


# ==========================================================
# 10) ROUTER / ENTRYPOINT
# ==========================================================

def handle(argv: list[str], cfg: dict) -> int:
    """
    Entry point: ai mon <mode> [args]

    Modes:
      live [--interval N] [--compact] [--target HOST] [--iface IFACE] [--disk "LABEL=TARGET"] [--no-disks] [--maxwidth N]
      sensors
      batt
      disk [--sudo|--deep]
      net [target]
      net live [target]
      help
    """
    if not argv:
        mode = "live"
        rest: list[str] = []
    else:
        mode = (argv[0] or "").strip().lower()
        rest = argv[1:]

    if mode in ("help", "-h", "--help"):
        ansi.print_info("AI Monitor (MON)")
        print('  ai mon live [--interval N] [--compact] [--target HOST] [--iface IFACE] [--disk "LABEL=TARGET"] [--no-disks] [--maxwidth N] : HUD realtime (ANSI).')
        print("  ai mon sensors                                                      : Daftar semua sensor/field yang bisa dibaca.")
        print("  ai mon batt                                                         : Battery health + history (time travel).")
        print("  ai mon disk [--sudo|--deep]                                         : Storage SMART/TBW + history.")
        print("  ai mon net [target]                                                 : Network diagnostics (ping/dns + wifi detail).")
        print("  ai mon net live [target]                                            : Live ping graph (alt-screen).")
        print("")
        print("Dependency (Fedora):")
        print("  sudo dnf install python3-psutil")
        print("Optional tools (Fedora):")
        print("  sudo dnf install util-linux iproute iw pciutils lm_sensors smartmontools")
        print("Optional iGPU advanced (Fedora):")
        print("  sudo dnf install intel-gpu-tools")
        print("")
        print("Catatan sudo PATH:")
        print("  Jika `sudo ai ...` tidak ketemu, gunakan:")
        print('    sudo env "PATH=$PATH" ai mon disk')
        return 0

    if mode in ("sensors", "probe", "inventory"):
        return run_sensors_dump()

    if psutil is None:
        ansi.print_brief_error("Fitur Monitoring butuh library 'psutil'.")
        print("Install Fedora: sudo dnf install python3-psutil")
        return 1

    if mode in ("live", "dashboard", "now"):
        return run_live_cockpit(rest)

    if mode in ("batt", "battery", "power"):
        return run_battery_check()

    if mode in ("disk", "storage", "smart"):
        return run_disk_check(rest)

    if mode in ("net", "wifi", "ping"):
        if rest and rest[0].lower() == "live":
            return run_net_live(rest[1:])
        return run_net_diag(rest)

    ansi.print_brief_error(f"Mode '{mode}' tidak dikenal.")
    print("Coba: live | sensors | batt | disk | net | help")
    return 2
