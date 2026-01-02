"""
AI-Term • MON (System Cockpit & Intelligence) — V3

Tujuan:
- `ai mon live`     : HUD realtime yang enak dipandang + informatif (tanpa noise).
- `ai mon sensors`  : tampilkan SEMUA sensor/field yang bisa dibaca (psutil + sysfs + optional tools).
- `ai mon batt`     : laporan battery intelligence + history tracking.
- `ai mon disk`     : laporan storage SMART/TBW + history tracking.
- `ai mon net`      : laporan network diagnostics + wifi detail.
- `ai mon net live` : live ping graph (alt-screen).

Catatan desain:
- Dependency utama: `psutil` (wajib untuk fitur monitoring).
  Install Fedora: sudo dnf install python3-psutil
- History path: ai_logic.common.MON_HISTORY_PATH (fallback aman bila common belum update).
- ANSI-only (tanpa rich), konsisten dengan project.
- Tidak memunculkan ERROR yang bikin panik untuk kondisi wajar (sensor tidak tersedia).
- Disk SMART/TBW: default tidak memaksa sudo (tidak munculkan prompt password).
  Kalau butuh data lengkap: `sudo ai mon disk`.
- GPU usage (opsional):
  - Prioritas: pynvml (NVML) -> nvidia-smi -> sysfs DRM gpu_busy_percent.

Update besar V3 (dibanding V2):
- GPU usage (util%, VRAM, temp bila ada).
- Disk I/O realtime (active% + read/write speed) via /sys/block/<dev>/stat (tanpa sudo).
- SWAP usage.
- Ping latency + jitter di HUD (background sampler, tidak mengganggu UI).
- NVMe extra: model NVMe (footer) + disk device selection dari mount '/'.
- Footer: OS/Kernel/Arch + hardware penting (CPU model, NVMe model, GPU hint).

Subcommands:
  ai mon live [--interval N] [--compact] [--no-ping] [--target HOST] [--iface IFACE]
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
import sys
import time
import json
import shutil
import socket
import datetime as _dt
import subprocess
import threading
from collections import deque
from pathlib import Path
from typing import Any, Optional, List

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

# --- Optional Dependency: NVML (GPU NVIDIA) ---
_pynvml = None
try:
    import pynvml as _pynvml  # type: ignore
except Exception:
    _pynvml = None


# ==========================================================
# 0) UTILITIES (safe io, shell, formatting)
# ==========================================================

def _now_ts() -> str:
    return _dt.datetime.now().strftime("%A | %H:%M:%S")


def _sh(cmd: str, timeout: int = 10) -> tuple[int, str]:
    """
    Shell helper:
    - return (exit_code, stdout_text)
    - stderr suppressed (untuk UI yang tidak berisik)
    """
    try:
        p = subprocess.run(
            cmd,
            shell=True,
            executable="/bin/bash",
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=timeout,
        )
        return int(p.returncode), (p.stdout or "").strip()
    except subprocess.TimeoutExpired:
        return 124, ""
    except Exception:
        return 1, ""


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


def _tcol(v: Optional[float]) -> str:
    if v is None:
        return ansi.c_dim()
    if v < 70:
        return ansi.c_green()
    if v < 85:
        return ansi.c_yellow()
    return ansi.c_red()


def _sigcol_dbm(dbm: Optional[float]) -> str:
    # Rough Wi-Fi signal tier
    if dbm is None:
        return ansi.c_dim()
    if dbm >= -55:
        return ansi.c_green()
    if dbm >= -67:
        return ansi.c_yellow()
    return ansi.c_red()


def _ping_col(ms: Optional[float]) -> str:
    if ms is None:
        return ansi.c_dim()
    if ms <= 40:
        return ansi.c_green()
    if ms <= 120:
        return ansi.c_yellow()
    return ansi.c_red()


def _safe_center(s: str, width: int) -> str:
    if width <= 0:
        return s
    if len(s) >= width:
        return s[:width]
    pad = width - len(s)
    left = pad // 2
    right = pad - left
    return (" " * left) + s + (" " * right)


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
            "psutil": bool(psutil),
            "pynvml(NVML)": bool(_pynvml),
            "sensors(lm_sensors)": bool(shutil.which("sensors")),
            "smartctl(smartmontools)": bool(shutil.which("smartctl")),
            "iw(iw)": bool(shutil.which("iw")),
            "lspci(pciutils)": bool(shutil.which("lspci")),
            "ip(iproute2)": bool(shutil.which("ip")),
            "nvidia-smi": bool(shutil.which("nvidia-smi")),
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
                for e in entries[:10]:
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
                for e in entries[:10]:
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
            out["swap_psutil"] = {
                "total": getattr(sw, "total", None),
                "used": getattr(sw, "used", None),
                "free": getattr(sw, "free", None),
                "percent": getattr(sw, "percent", None),
            }
        except Exception as ex:
            out["swap_psutil_err"] = str(ex)

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
    def sysfs_drm_gpu_tree() -> dict:
        root = Path("/sys/class/drm")
        d: dict[str, Any] = {"exists": root.exists(), "cards": []}
        if not root.exists():
            return d

        for card in sorted(root.glob("card*")):
            if not card.is_dir():
                continue
            device = card / "device"
            if not device.exists():
                continue

            interesting: list[str] = []
            try:
                for f in sorted(device.iterdir()):
                    if not f.is_file():
                        continue
                    nm = f.name
                    if "busy" in nm or "gpu" in nm or "mem" in nm or nm in ("vendor", "device", "subsystem_vendor", "subsystem_device"):
                        interesting.append(nm)
            except Exception:
                interesting = []

            d["cards"].append({
                "card": card.name,
                "device": str(device),
                "interesting_files": interesting[:80],
                "busy_candidates": [x for x in interesting if "busy" in x],
            })
        return d

    @staticmethod
    def sysfs_block_tree(limit: int = 16) -> dict:
        root = Path("/sys/block")
        d: dict[str, Any] = {"exists": root.exists(), "devices": []}
        if not root.exists():
            return d

        cnt = 0
        for dev in sorted(root.iterdir()):
            if not dev.is_dir():
                continue
            name = dev.name
            if name.startswith(("loop", "zram", "ram", "sr")):
                continue
            stat_p = dev / "stat"
            ss_p = dev / "queue" / "hw_sector_size"
            d["devices"].append({
                "name": name,
                "has_stat": stat_p.exists(),
                "sector_size": _read_text(ss_p) if ss_p.exists() else "",
            })
            cnt += 1
            if cnt >= limit:
                break
        return d


# ==========================================================
# 3) CORE READERS (battery/power, temps, fan, net, ping, disk I/O, gpu)
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

        # Status raw
        st = _read_text(batt / "status") or "Unknown"
        res["status_raw"] = st

        # psutil percent/time-left
        if psutil is not None:
            try:
                b = psutil.sensors_battery()
                if b:
                    res["percent"] = int(getattr(b, "percent", 0) or 0)
                    res["plugged"] = bool(getattr(b, "power_plugged", False))
                    res["secs_left"] = getattr(b, "secsleft", None)
            except Exception:
                pass

        # Sysfs: voltage_now (uV), power_now (uW) or current_now (uA)
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
            res["watt"] = float(p_uw) / 1e6
        elif (c_ua is not None) and (res["volt"] is not None):
            a = float(c_ua) / 1e6
            res["amp"] = a
            res["watt"] = float(res["volt"]) * a

        # Amp from watt/volt if missing
        if (res["amp"] is None) and (res["watt"] is not None) and (res["volt"] is not None) and (res["volt"] > 0):
            res["amp"] = float(res["watt"]) / float(res["volt"])

        # String formatting
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
        """
        Read design vs full capacity (Wh/Ah) + cycle count if available.
        """
        out: dict[str, Any] = {"ok": False}
        batt = self._pick_battery()
        if batt is None:
            return out

        model = _read_text(batt / "model_name") or batt.name
        out["model"] = model

        # prefer energy_* (Wh)
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
    """
    Pilih sensor yang “bernilai” untuk HUD:
    - CPU package/tctl
    - NVMe composite
    - WiFi-ish
    Selain itu bisa diinspeksi via `ai mon sensors`.
    """
    @staticmethod
    def read_key_temps() -> dict[str, Optional[float]]:
        res: dict[str, Optional[float]] = {"cpu": None, "ssd": None, "wifi": None}
        if psutil is None:
            return res
        try:
            temps = psutil.sensors_temperatures(fahrenheit=False) or {}
        except Exception:
            return res

        # CPU package / tctl
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

        # NVMe composite
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

        # WiFi-ish
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
    """
    Throughput realtime (RX/TX) via psutil.net_io_counters.
    Default: aggregated all interfaces.
    """
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
    """
    Baca SSID/signal/bitrate (kalau `iw` ada).
    """
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


class PingSampler:
    """
    Background ping sampler untuk HUD:
    - last latency
    - jitter (window max-min)
    """
    def __init__(self, target: str, interval: float = 5.0):
        self.target = target
        self.interval = float(_clamp(interval, 1.0, 30.0))
        self.last_ms: Optional[float] = None
        self.window = deque(maxlen=20)  # ms
        self.last_err: str = ""
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._loop, daemon=True)

    def start(self) -> None:
        if not self._t.is_alive():
            self._t.start()

    def stop(self) -> None:
        self._stop.set()

    def jitter(self) -> Optional[float]:
        if len(self.window) < 3:
            return None
        return max(self.window) - min(self.window)

    def _loop(self) -> None:
        if not shutil.which("ping"):
            self.last_err = "no-ping"
            return

        while not self._stop.is_set():
            t0 = time.time()
            ms = self._ping_once()
            if ms is None:
                self.last_ms = None
                self.last_err = "timeout"
            else:
                self.last_ms = ms
                self.window.append(ms)
                self.last_err = ""

            dt = time.time() - t0
            wait = self.interval - dt
            if wait > 0:
                self._stop.wait(wait)

    def _ping_once(self) -> Optional[float]:
        try:
            p = subprocess.run(
                ["ping", "-c", "1", "-W", "1", self.target],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=2,
            )
            out = (p.stdout or "")
            if "time=" in out:
                s = out.split("time=", 1)[1].split()[0]
                return float(s)
        except Exception:
            return None
        return None


class DiskIOMeter:
    """
    Disk Active Time (%) + Read/Write speed via /sys/block/<dev>/stat.
    Works without sudo.

    Indeks stat (Linux):
      2 = sectors_read
      6 = sectors_written
      9 = io_time_ms
    """
    def __init__(self, dev: str):
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
        return int(v) if v and v > 0 else 512

    def _read_stat(self) -> Optional[List[int]]:
        p = Path(f"/sys/block/{self.dev}/stat")
        raw = _read_text(p)
        if not raw:
            return None
        try:
            return [int(x) for x in raw.split()]
        except Exception:
            return None

    def update(self) -> None:
        cur = self._read_stat()
        if cur is None or self.prev is None:
            self.prev = cur
            self.t0 = time.time()
            self.active_pct = 0.0
            self.read_bps = 0.0
            self.write_bps = 0.0
            return

        t1 = time.time()
        dt = t1 - self.t0
        if dt <= 0:
            return

        try:
            d_read_sect = cur[2] - self.prev[2]
            d_write_sect = cur[6] - self.prev[6]
            d_io_ms = cur[9] - self.prev[9]

            self.read_bps = (d_read_sect * self.sector_size) / dt
            self.write_bps = (d_write_sect * self.sector_size) / dt
            self.active_pct = float(_clamp((d_io_ms / (dt * 1000.0)) * 100.0, 0.0, 100.0))
        except Exception:
            self.active_pct = 0.0
            self.read_bps = 0.0
            self.write_bps = 0.0

        self.prev = cur
        self.t0 = t1


class GPUReader:
    """
    GPU usage reader (opsional) dengan prioritas:
      1) NVML (pynvml)     -> util%, vram, temp, name
      2) nvidia-smi        -> util%, vram, temp, name
      3) sysfs drm         -> gpu_busy_percent (util only) + name hint (lspci)
    """
    def __init__(self) -> None:
        self.backend = "none"
        self._nvml_ready = False

        if _pynvml is not None:
            try:
                _pynvml.nvmlInit()
                self._nvml_ready = True
                self.backend = "pynvml"
            except Exception:
                self._nvml_ready = False

        if self.backend == "none" and shutil.which("nvidia-smi"):
            self.backend = "nvidia-smi"

        if self.backend == "none" and self._has_sysfs_busy():
            self.backend = "sysfs"

    def _has_sysfs_busy(self) -> bool:
        root = Path("/sys/class/drm")
        if not root.exists():
            return False
        for card in sorted(root.glob("card*")):
            dev = card / "device"
            if not dev.exists():
                continue
            if (dev / "gpu_busy_percent").exists():
                return True
            try:
                for f in dev.iterdir():
                    if f.is_file() and f.name.endswith("busy_percent"):
                        return True
            except Exception:
                pass
        return False

    def read(self) -> dict[str, Any]:
        # Canonical keys (konsisten):
        out: dict[str, Any] = {
            "ok": False,
            "backend": self.backend,
            "name": "",
            "util_gpu": None,   # percent
            "util_mem": None,   # percent (if available)
            "mem_used": None,   # bytes (if available)
            "mem_total": None,  # bytes (if available)
            "temp": None,       # C (if available)
            "note": "",
        }

        if self.backend == "pynvml":
            return self._read_nvml(out)
        if self.backend == "nvidia-smi":
            return self._read_nvidia_smi(out)
        if self.backend == "sysfs":
            return self._read_sysfs(out)

        out["note"] = "GPU backend not available"
        return out

    def _read_nvml(self, out: dict[str, Any]) -> dict[str, Any]:
        if not self._nvml_ready or _pynvml is None:
            out["note"] = "NVML not initialized"
            return out
        try:
            h = _pynvml.nvmlDeviceGetHandleByIndex(0)
            name = _pynvml.nvmlDeviceGetName(h)
            if isinstance(name, bytes):
                name = name.decode("utf-8", errors="replace")
            out["name"] = str(name)

            util = _pynvml.nvmlDeviceGetUtilizationRates(h)
            out["util_gpu"] = float(getattr(util, "gpu", None)) if util else None
            out["util_mem"] = float(getattr(util, "memory", None)) if util else None

            try:
                mem = _pynvml.nvmlDeviceGetMemoryInfo(h)
                out["mem_used"] = int(getattr(mem, "used", 0))
                out["mem_total"] = int(getattr(mem, "total", 0))
            except Exception:
                pass

            try:
                out["temp"] = float(_pynvml.nvmlDeviceGetTemperature(h, _pynvml.NVML_TEMPERATURE_GPU))
            except Exception:
                pass

            out["ok"] = True
            return out
        except Exception as ex:
            out["note"] = f"NVML error: {type(ex).__name__}"
            return out

    def _read_nvidia_smi(self, out: dict[str, Any]) -> dict[str, Any]:
        cmd = "nvidia-smi --query-gpu=name,utilization.gpu,utilization.memory,temperature.gpu,memory.used,memory.total --format=csv,noheader,nounits"
        code, txt = _sh(cmd, timeout=3)
        if code != 0 or not txt:
            out["note"] = "nvidia-smi query failed"
            return out

        line = txt.splitlines()[0].strip()
        parts = [x.strip() for x in line.split(",")]
        try:
            out["name"] = parts[0] if len(parts) > 0 else ""
            out["util_gpu"] = float(parts[1]) if len(parts) > 1 and parts[1] else None
            out["util_mem"] = float(parts[2]) if len(parts) > 2 and parts[2] else None
            out["temp"] = float(parts[3]) if len(parts) > 3 and parts[3] else None

            # memory in MiB (nounits)
            if len(parts) > 5:
                used_mib = float(parts[4]) if parts[4] else 0.0
                total_mib = float(parts[5]) if parts[5] else 0.0
                out["mem_used"] = int(used_mib * 1024**2)
                out["mem_total"] = int(total_mib * 1024**2)

            out["ok"] = True
        except Exception as ex:
            out["note"] = f"parse error: {type(ex).__name__}"
        return out

    def _read_sysfs(self, out: dict[str, Any]) -> dict[str, Any]:
        root = Path("/sys/class/drm")
        if not root.exists():
            out["note"] = "no /sys/class/drm"
            return out

        # choose card0 else first card*
        candidates = sorted([c for c in root.glob("card*") if c.is_dir()])
        chosen = None
        for c in candidates:
            if c.name == "card0":
                chosen = c
                break
        if chosen is None and candidates:
            chosen = candidates[0]
        if chosen is None:
            out["note"] = "no drm cards"
            return out

        dev = chosen / "device"
        if not dev.exists():
            out["note"] = "card has no device"
            return out

        util_file = None
        if (dev / "gpu_busy_percent").exists():
            util_file = dev / "gpu_busy_percent"
        else:
            try:
                for f in sorted(dev.iterdir()):
                    if f.is_file() and f.name.endswith("busy_percent"):
                        util_file = f
                        break
            except Exception:
                util_file = None

        if util_file and util_file.exists():
            v = _read_float(util_file)
            if v is not None:
                out["util_gpu"] = float(v)
                out["ok"] = True

        # name hint via lspci if possible
        if shutil.which("lspci"):
            try:
                pci_addr = os.path.basename(os.readlink(str(dev)))
            except Exception:
                pci_addr = ""
            if pci_addr:
                code, t = _sh(f"lspci -s {pci_addr} | cut -d: -f3-", timeout=2)
                if code == 0 and t:
                    out["name"] = t.strip()

        out["note"] = out["note"] or f"{chosen.name} util={out.get('util_gpu')}"
        return out


# ==========================================================
# 4) FEATURES: SENSORS, BATTERY, DISK, NETWORK
# ==========================================================

def run_sensors_dump() -> int:
    ansi.print_system("SENSORS INVENTORY (FULL DISCOVERY MODE)")

    # tooling detect
    tools = SensorProbe.tool_presence()
    print(f"\n{ansi.c_bold()}[ TOOLING DETECT ]{ansi.c_reset()}")
    for k, ok in tools.items():
        mark = f"{ansi.c_green()}OK ✅{ansi.c_reset()}" if ok else f"{ansi.c_yellow()}MISSING ⚠{ansi.c_reset()}"
        print(f"  - {k:<24}: {mark}")

    print(f"\n{ansi.c_dim()}Rekomendasi Fedora (opsional):{ansi.c_reset()}")
    print("  sudo dnf install -y lm_sensors smartmontools pciutils iw iproute")
    print("  sudo dnf install -y python3-psutil  (wajib)")
    print("  pip install pynvml  (opsional, NVIDIA util lebih stabil)")

    # psutil
    print(f"\n{ansi.c_bold()}[ PSUTIL PROBE ]{ansi.c_reset()}")
    if psutil is None:
        ansi.print_brief_error("psutil belum terpasang — MON tidak bisa jalan tanpa ini.")
        print("Install Fedora: sudo dnf install python3-psutil")
        return 1

    ov = SensorProbe.psutil_overview()
    v = ov.get("psutil", {})
    print(f"  psutil       : {('OK ✅' if v.get('ok') else 'FAIL ❌')} (v{v.get('version', '?')})")

    # temperature keys + sample
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
            for e in entries[:12]:
                lab = (e.get("label") or "-").strip()
                cur = e.get("current")
                hi = e.get("high")
                cr = e.get("critical")
                print(f"    - {lab:<18} cur={cur}°C  high={hi}  crit={cr}")

    # fan keys + sample
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
            for e in entries[:12]:
                lab = (e.get("label") or "-").strip()
                cur = e.get("current")
                print(f"    - {lab:<18} rpm={cur}")

    # power_supply sysfs tree
    print(f"\n{ansi.c_bold()}[ POWER_SUPPLY SYSFS ]{ansi.c_reset()}")
    tree = SensorProbe.sysfs_power_supply_tree()
    if not tree.get("exists"):
        print("  /sys/class/power_supply tidak ada (mungkin non-Linux).")
    else:
        items = tree.get("items", [])
        if not items:
            print("  (tidak ada entry)")
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

    # drm gpu sysfs
    print(f"\n{ansi.c_bold()}[ DRM GPU SYSFS ]{ansi.c_reset()}")
    g = SensorProbe.sysfs_drm_gpu_tree()
    if not g.get("exists"):
        print("  /sys/class/drm tidak ada.")
    else:
        cards = g.get("cards", [])
        if not cards:
            print("  (tidak ada card*)")
        for c in cards[:8]:
            print(f"\n  {ansi.c_cyan()}{c.get('card')}{ansi.c_reset()}  {ansi.c_dim()}{c.get('device')}{ansi.c_reset()}")
            bc = c.get("busy_candidates", [])
            if bc:
                print(f"    busy candidates: {', '.join(bc[:8])}")
            else:
                print("    busy candidates: (none)")

    # block stats
    print(f"\n{ansi.c_bold()}[ BLOCK STATS (DISK I/O) ]{ansi.c_reset()}")
    b = SensorProbe.sysfs_block_tree(limit=20)
    if not b.get("exists"):
        print("  /sys/block tidak ada.")
    else:
        for dev in b.get("devices", []):
            name = dev.get("name")
            hs = dev.get("has_stat")
            ss = dev.get("sector_size") or "?"
            print(f"  - {name:<10} stat={'OK' if hs else 'NO'}  sector={ss}")

    # gpu reader quick test
    print(f"\n{ansi.c_bold()}[ GPU READER TEST ]{ansi.c_reset()}")
    gr = GPUReader()
    r = gr.read()
    print(f"  backend       : {r.get('backend')}")
    if r.get("ok"):
        print(f"  name          : {r.get('name') or '-'}")
        print(f"  util_gpu      : {r.get('util_gpu')}%")
        if r.get("util_mem") is not None:
            print(f"  util_mem      : {r.get('util_mem')}%")
        if r.get("temp") is not None:
            print(f"  temp          : {r.get('temp')}°C")
    else:
        print(f"  status        : N/A  {ansi.c_dim()}{r.get('note','')}{ansi.c_reset()}")

    print(f"\n{ansi.c_dim()}Tip:{ansi.c_reset()} data HUD paling berguna: CPU package, NVMe composite, watt/volt/amp, disk util% + R/W, wifi ssid/signal, ping latency/jitter, GPU util.")
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

    # Health
    if cap.get("ok"):
        health = float(cap["health_pct"])
        full = float(cap["full"])
        design = float(cap["design"])
        unit = str(cap["unit"])
        cyc = str(cap.get("cycle") or "?")

        col = ansi.c_green() if health >= 80 else (ansi.c_yellow() if health >= 60 else ansi.c_red())
        print(f"  Health        : {col}{health:.2f}%{ansi.c_reset()}  {ansi.c_dim()}(usable vs design){ansi.c_reset()}")
        print(f"  Capacity      : {full:.2f}/{design:.2f} {unit}   Cycles: {cyc}")

        # Snapshot history (consistent keys)
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

    # Live power
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
    """
    Robust lsblk (JSON) to detect disks (exclude loop/zram).
    """
    cmd = "lsblk -J -d -o NAME,MODEL,SIZE,TYPE,TRAN,ROTA"
    code, out = _sh(cmd, timeout=3)
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
    """
    Parse smartctl output (NVMe + SATA fallback).
    """
    out: dict[str, Any] = {
        "health": "Unknown",
        "health_color": ansi.c_dim(),
        "temp": "N/A",
        "power_on": "N/A",
        "writes_gb": None,
        "has_writes": False,
    }

    # NVMe: Percentage Used => health = 100 - used
    for ln in smart_a.splitlines():
        if "Percentage Used" in ln:
            try:
                used = int(ln.split(":")[-1].replace("%", "").strip())
                h = 100 - used
                out["health"] = f"{h}%"
                out["health_color"] = ansi.c_green() if h >= 80 else ansi.c_yellow()
            except Exception:
                pass

        # Temperature: NVMe variants
        if "Temperature:" in ln and "Celsius" in ln:
            try:
                t = ln.split("Temperature:", 1)[1].replace("Celsius", "").strip()
                out["temp"] = t.replace(" ", "") + "°C"
            except Exception:
                pass

        # SATA style "194 Temperature_Celsius ..."
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

        # NVMe TBW: Data Units Written ... [x.xx TB]
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

        # SATA TBW: Total_LBAs_Written
        if "Total_LBAs_Written" in ln:
            try:
                lba = int(ln.split()[-1])
                gb = (lba * 512.0) / (1024.0**3)
                out["writes_gb"] = float(gb)
                out["has_writes"] = True
            except Exception:
                pass

    # SATA fallback health from -H
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

    # local interfaces
    print(f"\n{ansi.c_bold()}[ LOCAL INTERFACES ]{ansi.c_reset()}")
    rows = _list_ifaces_summary()
    if not rows:
        print("  (tidak ada interface)")
    else:
        for r in rows:
            col = ansi.c_green() if r["up"] == "UP" else ansi.c_red()
            print(f"  • {ansi.c_cyan()}{r['iface']:<8}{ansi.c_reset()} : {col}{r['up']:<4}{ansi.c_reset()} | {r['ip']}")

    # wifi detail
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

    # ping quality
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

    # DNS check
    print(f"\n{ansi.c_bold()}[ DNS CHECK ]{ansi.c_reset()}")
    try:
        ip2 = socket.gethostbyname(target)
        print(f"  Local Resolve : {ansi.c_green()}OK ✅{ansi.c_reset()} ({target} -> {ip2})")
    except Exception:
        print(f"  Local Resolve : {ansi.c_red()}FAIL ❌{ansi.c_reset()}")

    print(f"  Global Check  : {ansi.c_cyan()}https://dnschecker.org/#A/{target}{ansi.c_reset()}")
    return 0


def run_net_live(argv: list[str]) -> int:
    """
    Live ping graph in alt-screen.
    """
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
                    cnt = int(ms / 8.0)  # 1 bar per ~8ms
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
# 5) LIVE COCKPIT (HUD)
# ==========================================================

def _disk_usage_root() -> dict[str, Any]:
    """
    Disk usage for '/' (useful & not noisy).
    """
    try:
        du = shutil.disk_usage("/")
        used_pct = (du.used / du.total) * 100.0 if du.total else 0.0
        return {"ok": True, "used_pct": used_pct, "used": du.used, "total": du.total}
    except Exception:
        return {"ok": False}


def _uptime_str() -> str:
    # prefer psutil.boot_time; fallback /proc/uptime
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


def _loadavg_str() -> str:
    try:
        la = os.getloadavg()
        return f"{la[0]:.2f} {la[1]:.2f} {la[2]:.2f}"
    except Exception:
        return "-"


def _swap_info() -> dict[str, Any]:
    out = {"ok": False, "pct": 0.0, "used": 0.0, "total": 0.0}
    if psutil is None:
        return out
    try:
        sw = psutil.swap_memory()
        out["ok"] = True
        out["pct"] = float(sw.percent)
        out["used"] = float(sw.used)
        out["total"] = float(sw.total)
    except Exception:
        pass
    return out


def _top_processes_snapshot() -> dict[str, str]:
    """
    Lightweight top CPU/MEM (sampled, not every frame ideally).
    """
    out = {"cpu": "-", "mem": "-"}
    if psutil is None:
        return out

    try:
        procs = []
        for p in psutil.process_iter(attrs=["name", "cpu_percent", "memory_info"]):
            procs.append(p.info)

        # top mem
        best_mem = None
        for x in procs:
            mi = x.get("memory_info")
            rss = getattr(mi, "rss", 0) if mi else 0
            if best_mem is None or rss > best_mem[0]:
                best_mem = (rss, x.get("name") or "?")
        if best_mem:
            out["mem"] = f"{best_mem[1]} ({_human_bytes(best_mem[0])})"

        # top cpu (cpu_percent may be stale but still helpful)
        best_cpu = None
        for x in procs:
            c = float(x.get("cpu_percent") or 0.0)
            if best_cpu is None or c > best_cpu[0]:
                best_cpu = (c, x.get("name") or "?")
        if best_cpu and best_cpu[0] > 0:
            out["cpu"] = f"{best_cpu[1]} ({best_cpu[0]:.0f}%)"
    except Exception:
        return out

    return out


def _os_pretty_name() -> str:
    p = Path("/etc/os-release")
    if not p.exists():
        return ""
    raw = _read_text(p)
    for ln in raw.splitlines():
        if ln.startswith("PRETTY_NAME="):
            return ln.split("=", 1)[1].strip().strip('"')
    return ""


def _kernel_str() -> str:
    code, out = _sh("uname -r", timeout=2)
    return out.strip() if code == 0 else ""


def _arch_str() -> str:
    code, out = _sh("uname -m", timeout=2)
    return out.strip() if code == 0 else ""


def _cpu_model() -> str:
    code, out = _sh("cat /proc/cpuinfo | grep -m1 'model name' | cut -d: -f2-", timeout=2)
    return out.strip() if code == 0 else ""


def _gpu_hint() -> str:
    if not shutil.which("lspci"):
        return ""
    code, out = _sh("lspci | grep -iE 'vga|3d|display' | head -n 1", timeout=3)
    if code != 0 or not out:
        return ""
    return out.split(": ", 1)[-1].strip()


def _nvme_model(dev: str) -> str:
    code, out = _sh(f"lsblk -d -n -o MODEL /dev/{dev} 2>/dev/null | head -n 1", timeout=2)
    return out.strip() if code == 0 else ""


def _pick_root_block_device() -> Optional[str]:
    """
    Determine base disk device for '/' mount.
    Examples:
      /dev/nvme0n1p2 -> nvme0n1
      /dev/sda2      -> sda
      /dev/mmcblk0p2 -> mmcblk0
    """
    try:
        src = ""
        with open("/proc/mounts", "r", encoding="utf-8", errors="replace") as f:
            for ln in f:
                parts = ln.split()
                if len(parts) >= 2 and parts[1] == "/":
                    src = parts[0]
                    break
        if not src.startswith("/dev/"):
            return None

        real = os.path.realpath(src)
        base = os.path.basename(real)

        # nvme0n1p2 -> nvme0n1
        if base.startswith("nvme") and "p" in base:
            import re
            m = re.match(r"^(nvme\d+n\d+)p\d+$", base)
            if m:
                return m.group(1)

        # mmcblk0p2 -> mmcblk0
        if base.startswith("mmcblk") and "p" in base:
            import re
            m = re.match(r"^(mmcblk\d+)p\d+$", base)
            if m:
                return m.group(1)

        # sda2 -> sda (generic letters+digits)
        import re
        m = re.match(r"^([a-zA-Z]+)\d+$", base)
        if m:
            return m.group(1)

        return base
    except Exception:
        return None


def _fallback_first_nvme_or_disk() -> Optional[str]:
    try:
        root = Path("/sys/block")
        if root.exists():
            devs = sorted([p.name for p in root.iterdir() if p.is_dir()])
            devs = [d for d in devs if not d.startswith(("loop", "zram", "ram", "sr"))]
            nv = [d for d in devs if d.startswith("nvme")]
            if nv:
                return nv[0]
            if devs:
                return devs[0]
    except Exception:
        pass

    code, out = _sh("lsblk -d -n -o NAME,TYPE | awk '$2==\"disk\"{print $1; exit}'", timeout=2)
    if code == 0 and out:
        return out.strip()
    return None


class HardwareCache:
    """
    Cache expensive calls for footer (uname/os-release/lsblk/lspci).
    """
    def __init__(self):
        self.last = 0.0
        self.every = 30.0
        self.data: dict[str, str] = {}

    def update(self, disk_dev: Optional[str]) -> None:
        if time.time() - self.last < self.every:
            return
        self.last = time.time()
        self.data["os"] = _os_pretty_name()
        self.data["kernel"] = _kernel_str()
        self.data["arch"] = _arch_str()
        self.data["cpu"] = _cpu_model()
        self.data["gpu"] = _gpu_hint()
        self.data["nvme"] = _nvme_model(disk_dev) if disk_dev else ""


def run_live_cockpit(argv: list[str]) -> int:
    """
    HUD realtime. Default interval 1.0s

    Options:
      --interval N   (float)
      --compact      (lebih ringkas)
      --no-ping      (disable ping sampler di HUD)
      --target HOST  (ping target untuk HUD)
      --iface IFACE  (throughput per interface)
    """
    if psutil is None:
        ansi.print_brief_error("Fitur Monitoring butuh library 'psutil'.")
        print("Install Fedora: sudo dnf install python3-psutil")
        return 1

    # parse options
    interval = 1.0
    compact = False
    ping_enabled = True
    ping_target = "8.8.8.8"
    iface: Optional[str] = None

    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--compact", "compact"):
            compact = True
        elif a == "--no-ping":
            ping_enabled = False
        elif a == "--interval" and i + 1 < len(argv):
            try:
                interval = float(argv[i + 1])
                i += 1
            except Exception:
                pass
        elif a == "--target" and i + 1 < len(argv):
            ping_target = argv[i + 1].strip() or ping_target
            i += 1
        elif a == "--iface" and i + 1 < len(argv):
            iface = argv[i + 1].strip() or None
            i += 1
        else:
            # allow `ai mon live 0.5`
            if i == 0:
                try:
                    interval = float(a)
                except Exception:
                    pass
        i += 1

    interval = float(_clamp(interval, 0.2, 5.0))

    pr = PowerReader()
    ns = NetSpeedometer(iface=iface)
    ns.update()

    # disk io meter: choose root disk
    disk_dev = _pick_root_block_device() or _fallback_first_nvme_or_disk()
    disk_io = DiskIOMeter(disk_dev) if disk_dev else None

    # gpu
    gr = GPUReader()

    # ping sampler
    pinger = PingSampler(ping_target, interval=5.0) if ping_enabled else None
    if pinger:
        pinger.start()

    # history snapshot throttle
    last_snap = 0.0
    SNAP_EVERY = 600.0  # 10 min

    # process sampling throttle
    last_proc = 0.0
    PROC_EVERY = 5.0
    proc_info = {"cpu": "-", "mem": "-"}

    # wifi sampling throttle
    last_wifi = 0.0
    WIFI_EVERY = 4.0
    wifi_info: dict[str, Any] = {"ok": False}

    # hardware footer cache
    hw = HardwareCache()
    hw.update(disk_dev)

    input_muter = ansi.MuteInputDuringWait()
    ansi.alt_screen_enter()
    ansi.cursor_hide()
    try:
        # prime cpu_percent
        try:
            psutil.cpu_percent(interval=None)
        except Exception:
            pass

        with input_muter:
            while True:
                cols, _rows = ansi.term_size()
                usable = min(cols, 110)

                # --- collect core metrics ---
                try:
                    cpu_pct = float(psutil.cpu_percent(interval=None))
                except Exception:
                    cpu_pct = 0.0

                try:
                    vm = psutil.virtual_memory()
                    ram_pct = float(vm.percent)
                    ram_used = float(vm.used)
                    ram_total = float(vm.total)
                except Exception:
                    ram_pct, ram_used, ram_total = 0.0, 0.0, 0.0

                sw = _swap_info()

                temps = ThermalReader.read_key_temps()
                fan = ThermalReader.read_fan_rpm()
                power = pr.read()
                du = _disk_usage_root()

                # net throughput
                ns.update()

                # disk io
                if disk_io:
                    disk_io.update()

                # gpu
                gpu_state = gr.read()

                # ping metrics
                ping_ms = pinger.last_ms if pinger else None
                ping_jit = pinger.jitter() if pinger else None

                # process sampling occasionally
                if time.time() - last_proc >= PROC_EVERY:
                    proc_info = _top_processes_snapshot()
                    last_proc = time.time()

                # wifi sampling
                if time.time() - last_wifi >= WIFI_EVERY:
                    wifi_info = WiFiReader.read()
                    last_wifi = time.time()

                # periodic background snapshot (silent)
                if time.time() - last_snap >= SNAP_EVERY:
                    cap = pr.read_capacity_health()
                    if cap.get("ok"):
                        model = str(cap.get("model") or "BAT")
                        snapshot_metric("battery", model, "health_pct", round(float(cap["health_pct"]), 2))
                        snapshot_metric("battery", model, "capacity_full", round(float(cap["full"]), 2))
                    last_snap = time.time()

                # hardware cache update
                hw.update(disk_dev)

                # --- battery display logic ---
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

                # time left estimate
                secs_left = power.get("secs_left")
                eta = ""
                if isinstance(secs_left, (int, float)) and secs_left > 0 and secs_left < 10**9:
                    h = int(secs_left // 3600)
                    m = int((secs_left % 3600) // 60)
                    eta = f"{h}h {m}m left"

                # --- render ---
                ansi.clear_screen()

                title_left = f"{ansi.c_cyan()}{ansi.c_bold()} SYSTEM COCKPIT {ansi.c_reset()}"
                title_right = f"{ansi.c_dim()}AI-Terminal{ansi.c_reset()}"
                space = max(1, usable - (len(" SYSTEM COCKPIT ") + len("AI-Terminal") + 6))
                print(f"{title_left}{' ' * space}{title_right}")
                ts = _now_ts()
                print(f"{ansi.c_dim()}{_safe_center(ts, min(usable, 80))}{ansi.c_reset()}")
                print(f"{ansi.c_dim()}Uptime:{ansi.c_reset()} {_uptime_str()}   {ansi.c_dim()}Load:{ansi.c_reset()} {_loadavg_str()}")
                print("-" * min(usable, 88))

                # CPU/RAM
                print(f"{ansi.c_bold()} CPU {ansi.c_reset()} {_draw_bar(cpu_pct, 14)} {cpu_pct:>5.1f}%   {ansi.c_dim()}Top:{ansi.c_reset()} {proc_info.get('cpu','-')}")
                print(f"{ansi.c_bold()} RAM {ansi.c_reset()} {_draw_bar(ram_pct, 14)} {ram_pct:>5.1f}%   {ram_used/(1024**3):.1f}/{ram_total/(1024**3):.1f} GB   {ansi.c_dim()}TopMem:{ansi.c_reset()} {proc_info.get('mem','-')}")

                # SWAP
                if sw.get("ok") and float(sw.get("total", 0.0)) > 0:
                    sp = float(sw["pct"])
                    print(f"{ansi.c_bold()} SWAP{ansi.c_reset()} {_draw_bar(sp, 12)} {sp:>5.1f}%  {_human_bytes(float(sw['used']))}/{_human_bytes(float(sw['total']))}")
                else:
                    print(f"{ansi.c_bold()} SWAP{ansi.c_reset()} {ansi.c_dim()}N/A{ansi.c_reset()}")

                # Disk usage (/)
                if du.get("ok"):
                    dp = float(du.get("used_pct", 0.0))
                    print(f"{ansi.c_bold()} DISK{ansi.c_reset()} {_draw_bar(dp, 14)} {dp:>5.1f}%   /   {_human_bytes(float(du['used']))}/{_human_bytes(float(du['total']))}")

                # Disk I/O line
                if disk_io:
                    util = float(disk_io.active_pct)
                    util_col = ansi.c_green() if util <= 60 else (ansi.c_yellow() if util <= 85 else ansi.c_red())
                    print(
                        f"{ansi.c_bold()} I/O {ansi.c_reset()} "
                        f"{util_col}{util:>5.1f}% active{ansi.c_reset()}   "
                        f"R:{_human_rate_bps(disk_io.read_bps):>10}  "
                        f"W:{_human_rate_bps(disk_io.write_bps):>10}   "
                        f"{ansi.c_dim()}({disk_dev}){ansi.c_reset()}"
                    )
                else:
                    print(f"{ansi.c_bold()} I/O {ansi.c_reset()} {ansi.c_dim()}N/A{ansi.c_reset()}")

                print("-" * min(usable, 88))

                # Temps / fan
                cpu_t = temps.get("cpu")
                ssd_t = temps.get("ssd")
                wifi_t = temps.get("wifi")

                cpu_t_s = f"{cpu_t:.1f}°C" if isinstance(cpu_t, (int, float)) else "-"
                ssd_t_s = f"{ssd_t:.1f}°C" if isinstance(ssd_t, (int, float)) else "-"
                wifi_t_s = f"{wifi_t:.1f}°C" if isinstance(wifi_t, (int, float)) else "-"

                fan_s = f"{int(fan)} RPM" if isinstance(fan, (int, float)) and fan else "-"

                print(
                    f"{ansi.c_bold()} TEMP{ansi.c_reset()} "
                    f"CPU:{_tcol(cpu_t)}{cpu_t_s}{ansi.c_reset()}   "
                    f"NVMe:{_tcol(ssd_t)}{ssd_t_s}{ansi.c_reset()}   "
                    f"WiFi:{_tcol(wifi_t)}{wifi_t_s}{ansi.c_reset()}   "
                    f"{ansi.c_dim()}Fan:{ansi.c_reset()} {fan_s}"
                )

                # Power line
                lvl_txt = f"{lvl_i}%" if isinstance(lvl_i, int) else "?"
                print(
                    f"{ansi.c_bold()} POWER{ansi.c_reset()} "
                    f"{bat_col}{lvl_txt}{ansi.c_reset()} [{st_txt}]   "
                    f"{ansi.c_yellow()}{power.get('watt_str','N/A')}{ansi.c_reset()} @ {power.get('volt_str','N/A')} | {power.get('amp_str','N/A')}   "
                    f"{ansi.c_dim()}{eta}{ansi.c_reset()}"
                )

                # Network line + ping + wifi
                rx = _human_rate_bps(ns.rx)
                tx = _human_rate_bps(ns.tx)

                ping_txt = "N/A"
                if ping_enabled:
                    if ping_ms is None:
                        ping_txt = f"{ansi.c_red()}TO{ansi.c_reset()}"
                    else:
                        ping_txt = f"{_ping_col(ping_ms)}{ping_ms:>4.0f}ms{ansi.c_reset()}"

                jit_txt = ""
                if ping_enabled:
                    if ping_jit is None:
                        jit_txt = f"{ansi.c_dim()}jit:-{ansi.c_reset()}"
                    else:
                        jit_txt = f"{ansi.c_dim()}jit:{ping_jit:>3.0f}ms{ansi.c_reset()}"

                wifi_tail = ""
                if wifi_info.get("ok"):
                    ssid = wifi_info.get("ssid") or "-"
                    sig = wifi_info.get("signal_dbm")
                    sig_txt = f"{sig:.0f}dBm" if isinstance(sig, (int, float)) else "-"
                    sig_col = _sigcol_dbm(sig if isinstance(sig, (int, float)) else None)
                    rxbr = (wifi_info.get("rx_bitrate") or "")
                    txbr = (wifi_info.get("tx_bitrate") or "")
                    if len(rxbr) > 20:
                        rxbr = rxbr[:20] + "…"
                    if len(txbr) > 20:
                        txbr = txbr[:20] + "…"
                    wifi_tail = f"   {ansi.c_dim()}WiFi:{ansi.c_reset()} {ssid} {sig_col}{sig_txt}{ansi.c_reset()}"
                    if rxbr or txbr:
                        wifi_tail += f" {ansi.c_dim()}rx:{rxbr} tx:{txbr}{ansi.c_reset()}"

                print(f"{ansi.c_bold()} NET {ansi.c_reset()} ↓{rx:<10}  ↑{tx:<10}   ping:{ping_txt} {jit_txt}{wifi_tail}")

                # GPU line
                if gpu_state.get("ok") and gpu_state.get("util_gpu") is not None:
                    u = float(gpu_state["util_gpu"])
                    col = ansi.c_green() if u <= 60 else (ansi.c_yellow() if u <= 85 else ansi.c_red())
                    mem_part = ""
                    if gpu_state.get("mem_used") is not None and gpu_state.get("mem_total") is not None and gpu_state["mem_total"]:
                        mu = float(gpu_state["mem_used"])
                        mt = float(gpu_state["mem_total"])
                        mem_part = f"  VRAM:{_human_bytes(mu)}/{_human_bytes(mt)}"
                    temp_part = ""
                    if gpu_state.get("temp") is not None:
                        temp_part = f"  T:{float(gpu_state['temp']):.0f}°C"
                    name = gpu_state.get("name") or ""
                    if len(name) > 42:
                        name = name[:42] + "…"
                    print(f"{ansi.c_bold()} GPU {ansi.c_reset()} {col}{u:>5.1f}%{ansi.c_reset()}{mem_part}{temp_part}  {ansi.c_dim()}{name}{ansi.c_reset()}")
                else:
                    note = gpu_state.get("note") or "N/A"
                    if len(note) > 60:
                        note = note[:60] + "…"
                    print(f"{ansi.c_bold()} GPU {ansi.c_reset()} {ansi.c_dim()}N/A{ansi.c_reset()}  {ansi.c_dim()}{note}{ansi.c_reset()}")

                # Footer
                print("")
                if compact:
                    print(f"{ansi.c_dim()}Ctrl+C untuk keluar. Tip: ai mon sensors (inventaris).{ansi.c_reset()}")
                else:
                    os_name = hw.data.get("os", "")
                    kern = hw.data.get("kernel", "")
                    arch = hw.data.get("arch", "")
                    cpu_name = hw.data.get("cpu", "")
                    nv = hw.data.get("nvme", "")
                    gpn = hw.data.get("gpu", "")

                    if os_name or kern or arch:
                        print(f"{ansi.c_dim()}OS:{ansi.c_reset()} {os_name}  {ansi.c_dim()}Kernel:{ansi.c_reset()} {kern}  {ansi.c_dim()}Arch:{ansi.c_reset()} {arch}")
                    if cpu_name:
                        print(f"{ansi.c_dim()}CPU:{ansi.c_reset()} {cpu_name}")
                    if nv:
                        print(f"{ansi.c_dim()}NVMe:{ansi.c_reset()} {nv}")
                    if gpn:
                        print(f"{ansi.c_dim()}GPU:{ansi.c_reset()} {gpn}")

                    print(f"{ansi.c_dim()}Ctrl+C untuk keluar.{ansi.c_reset()}  {ansi.c_dim()}Tip:{ansi.c_reset()} `ai mon sensors` untuk daftar sensor lengkap.  {ansi.c_dim()}Tip:{ansi.c_reset()} `sudo ai mon disk` untuk TBW/SMART detail.")

                time.sleep(interval)

    except KeyboardInterrupt:
        pass
    except Exception as ex:
        # Fail-safe agar terminal tidak “nyangkut”
        try:
            ansi.cursor_show()
            ansi.alt_screen_exit()
        except Exception:
            pass
        ansi.print_brief_error(f"Dashboard crash: {type(ex).__name__}")
        print(str(ex))
        return 1
    finally:
        if pinger:
            pinger.stop()
        try:
            ansi.cursor_show()
            ansi.alt_screen_exit()
        except Exception:
            pass

    return 0


# ==========================================================
# 6) ROUTER / ENTRYPOINT
# ==========================================================

def handle(argv: list[str], cfg: dict) -> int:
    """
    Entry point: ai mon <mode> [args]

    Modes:
      live [--interval N] [--compact] [--no-ping] [--target HOST] [--iface IFACE]
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
        ansi.print_info("AI Monitor (MON) — V3")
        print("  ai mon live [--interval N] [--compact] [--no-ping] [--target HOST] [--iface IFACE] : HUD realtime (ANSI).")
        print("  ai mon sensors                           : Daftar semua sensor/field yang bisa dibaca.")
        print("  ai mon batt                              : Battery health + history (time travel).")
        print("  ai mon disk [--sudo|--deep]              : Storage SMART/TBW + history.")
        print("  ai mon net [target]                      : Network diagnostics (ping/dns + wifi detail).")
        print("  ai mon net live [target]                 : Live ping graph (alt-screen).")
        print("")
        print("Dependency (Fedora):")
        print("  sudo dnf install -y python3-psutil")
        print("Optional tools (Fedora):")
        print("  sudo dnf install -y lm_sensors smartmontools pciutils iw iproute")
        print("GPU (NVIDIA optional):")
        print("  - nvidia-smi (biasanya ikut driver)")
        print("  - pip install pynvml  (lebih stabil untuk utilization)")
        return 0

    # Modes that do NOT require psutil strictly
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
        # support: ai mon net live <target>
        if rest and rest[0].lower() == "live":
            return run_net_live(rest[1:])
        return run_net_diag(rest)

    ansi.print_brief_error(f"Mode '{mode}' tidak dikenal.")
    print("Coba: live | sensors | batt | disk | net | help")
    return 2