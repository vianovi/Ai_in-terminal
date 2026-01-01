"""
AI-Term • MON (System Cockpit & Intelligence) — V2

Tujuan:
- `ai mon live` : HUD realtime yang enak dipandang + informatif (tanpa data “noise” seperti per-core temp).
- `ai mon sensors` : tampilkan SEMUA sensor/field yang bisa dibaca (psutil + sysfs + optional tools).
- `ai mon batt|disk|net` : laporan intel ringkas + history tracking.

Catatan desain:
- Dependency utama: `psutil` (wajib untuk fitur monitoring).
  Install Fedora: sudo dnf install python3-psutil
- History path: ai_logic.common.MON_HISTORY_PATH (fallback aman bila common belum update).
- Tidak memakai rich (ANSI-only, konsisten dengan project).
- Tidak memunculkan ERROR yang bikin panik untuk kondisi “wajar” (mis. sensor tidak tersedia).
- Disk SMART/TBW: default tidak memaksa sudo (agar tidak memunculkan prompt password / false-error).
  Kalau butuh data lengkap: jalankan `sudo ai mon disk`.

Subcommands:
  ai mon live [--interval N] [--compact]
  ai mon sensors
  ai mon batt
  ai mon disk
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
    # bytes -> B/K/M/G/T
    units = ["B", "KB", "MB", "GB", "TB"]
    v = float(max(0.0, n))
    for u in units:
        if v < 1024.0:
            return f"{v:.1f}{u}"
        v /= 1024.0
    return f"{v:.1f}PB"

def _human_rate_bps(bps: float) -> str:
    # bytes/s -> B/s, KB/s...
    return _human_bytes(bps) + "/s"

def _draw_bar(pct: float, width: int = 14) -> str:
    pct = _clamp(float(pct), 0.0, 100.0)
    fill = int(width * pct / 100.0)
    col = ansi.c_green() if pct <= 60 else (ansi.c_yellow() if pct <= 85 else ansi.c_red())
    return f"{col}{'█'*fill}{ansi.c_dim()}{'░'*(width-fill)}{ansi.c_reset()}"


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

        # diff show
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
            # store short sample
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

        return out

    @staticmethod
    def sysfs_power_supply_tree() -> dict:
        """
        List semua item dan field di /sys/class/power_supply.
        """
        root = Path("/sys/class/power_supply")
        d: dict[str, Any] = {"exists": root.exists(), "items": []}
        if not root.exists():
            return d

        for item in sorted(root.iterdir()):
            if not item.is_dir():
                continue
            # list readable files only (not too huge)
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
    def tool_presence() -> dict:
        return {
            "sensors(lm_sensors)": bool(shutil.which("sensors")),
            "smartctl(smartmontools)": bool(shutil.which("smartctl")),
            "iw(iw)": bool(shutil.which("iw")),
            "lspci(pciutils)": bool(shutil.which("lspci")),
            "ip(iproute2)": bool(shutil.which("ip")),
        }


# ==========================================================
# 3) CORE READERS (battery/power, temps, fan, net)
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
            # uW -> W
            w = float(p_uw) / 1e6
            res["watt"] = w
        elif (c_ua is not None) and (res["volt"] is not None):
            # uA -> A
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
    Selain itu tetap bisa diinspeksi via `ai mon sensors`.
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

        # --- CPU (coretemp/k10temp): package/tctl prefer ---
        for key in ("coretemp", "k10temp"):
            entries = temps.get(key) or []
            best = None
            # prefer labels containing package/tctl
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

        # --- NVMe composite: keys contain nvme / composite ---
        # search any temp entry with label "Composite" or empty label on nvme group
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

        # --- WiFi-ish: drivers often appear as iwl/ath/wifi ---
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
        # prefer label contains cpu
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
    Default: all interfaces aggregated.
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
    Baca SSID/signal/bitrate (kalau `iw` ada). Tanpa fish script.
    """
    @staticmethod
    def _guess_wifi_iface() -> Optional[str]:
        # prefer interfaces that start with wl / wlan
        if psutil is None:
            return None
        try:
            stats = psutil.net_if_stats() or {}
            names = sorted(stats.keys())
        except Exception:
            return None

        # heuristic
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

        # iw dev <iface> link
        code, text = _sh(f"iw dev {iface} link", timeout=2)
        if code != 0 or not text:
            return out

        # Parse key lines
        # Example:
        #   Connected to xx:xx:... (on wlo1)
        #   SSID: Rafimur
        #   signal: -50 dBm
        #   rx bitrate: 144.4 MBit/s ...
        #   tx bitrate: 144.4 MBit/s ...
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


# ==========================================================
# 4) FEATURES: SENSORS, BATTERY, DISK, NETWORK
# ==========================================================

def run_sensors_dump() -> int:
    ansi.print_system("SENSORS INVENTORY (FULL DISCOVERY MODE)")

    # 0) deps
    tools = SensorProbe.tool_presence()
    print(f"\n{ansi.c_bold()}[ TOOLING DETECT ]{ansi.c_reset()}")
    for k, ok in tools.items():
        mark = f"{ansi.c_green()}OK ✅{ansi.c_reset()}" if ok else f"{ansi.c_yellow()}MISSING ⚠{ansi.c_reset()}"
        print(f"  - {k:<28}: {mark}")

    print(f"\n{ansi.c_dim()}Rekomendasi Fedora (opsional):{ansi.c_reset()}")
    print("  sudo dnf install lm_sensors smartmontools pciutils iw iproute")

    # 1) psutil
    print(f"\n{ansi.c_bold()}[ PSUTIL PROBE ]{ansi.c_reset()}")
    if psutil is None:
        ansi.print_brief_error("psutil belum terpasang — MON tidak bisa jalan tanpa ini.")
        print("Install Fedora: sudo dnf install python3-psutil")
        return 1

    ov = SensorProbe.psutil_overview()
    v = ov.get("psutil", {})
    print(f"  psutil       : {('OK ✅' if v.get('ok') else 'FAIL ❌')} (v{v.get('version', '?')})")

    # 2) temperature keys + sample
    print(f"\n{ansi.c_bold()}[ TEMPERATURE KEYS ]{ansi.c_reset()}")
    keys = ov.get("temps_keys", [])
    if not keys:
        print("  (tidak ada / tidak didukung kernel/driver)")
    else:
        print(f"  Keys: {', '.join(keys)}")
        # show sample in a readable way
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

    # 3) fan keys + sample
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

    # 4) battery via sysfs
    print(f"\n{ansi.c_bold()}[ POWER_SUPPLY SYSFS ]{ansi.c_reset()}")
    tree = SensorProbe.sysfs_power_supply_tree()
    if not tree.get("exists"):
        print("  /sys/class/power_supply tidak ada (mungkin non-Linux).")
        return 0
    items = tree.get("items", [])
    if not items:
        print("  (tidak ada entry)")
        return 0

    for it in items:
        name = it.get("name", "?")
        fields = it.get("fields", [])
        print(f"\n  {ansi.c_cyan()}{name}{ansi.c_reset()}  {ansi.c_dim()}({len(fields)} fields){ansi.c_reset()}")
        # list fields (wrap manually)
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

    print(f"\n{ansi.c_dim()}Tip:{ansi.c_reset()} data yang paling berguna untuk HUD biasanya: CPU package, NVMe composite, watt/volt/amp, wifi ssid/signal, throughput.")
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

        # status label
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
            if name.startswith("loop") or name.startswith("zram") or name.startswith("sr"):
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

        if ("Temperature" in ln or "Celsius" in ln) and ("Airflow" not in ln):
            # SATA style "194 Temperature_Celsius ..."
            # Keep lightweight: take last token if looks like number
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

    # policy: do not force sudo unless user explicitly wants deep
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

        # run smartctl (no-sudo first, then sudo if allowed)
        cmdA = f"smartctl -A {dev}"
        cmdH = f"smartctl -H {dev}"
        codeA, outA = _sh(cmdA, timeout=6)
        codeH, outH = _sh(cmdH, timeout=6)

        need_priv = ("permission denied" in outA.lower()) or ("requires root" in outA.lower()) or (codeA != 0 and not outA)
        if need_priv and want_sudo and os.geteuid() != 0:
            # try sudo (still may prompt password)
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

def _iface_ipv4(iface: str) -> str:
    if psutil is None:
        return "-"
    try:
        addrs = psutil.net_if_addrs() or {}
        for a in addrs.get(iface, []):
            if getattr(a, "family", None) == socket.AF_INET:
                return str(getattr(a, "address", "-"))
    except Exception:
        pass
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

    # target: argv[0] may be target (if not 'live')
    target = ""
    if argv:
        target = argv[0].strip()
    if not target:
        # interactive prompt (simple, no fish)
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

    # 1) local interfaces
    print(f"\n{ansi.c_bold()}[ LOCAL INTERFACES ]{ansi.c_reset()}")
    rows = _list_ifaces_summary()
    if not rows:
        print("  (tidak ada interface)")
    else:
        for r in rows:
            col = ansi.c_green() if r["up"] == "UP" else ansi.c_red()
            print(f"  • {ansi.c_cyan()}{r['iface']:<8}{ansi.c_reset()} : {col}{r['up']:<4}{ansi.c_reset()} | {r['ip']}")

    # 2) wifi detail (optional)
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

    # 3) ping quality
    ip = _ip_of_target(target)
    print(f"\n{ansi.c_bold()}[ CONNECTION QUALITY ]{ansi.c_reset()}")
    print(f"  Target IP     : {ip}")

    # Use ping -c 5 -i 0.2
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

    # 4) DNS check
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

def _top_processes_snapshot() -> dict[str, str]:
    """
    Lightweight top CPU/MEM (sampled, not every frame ideally).
    """
    out = {"cpu": "-", "mem": "-"}
    if psutil is None:
        return out

    try:
        # CPU: sort by cpu_percent needs prior call; we will do a cheap heuristic using memory+name fallback.
        # We'll compute top memory process reliably, and CPU using a single iteration (may be 0.0 sometimes).
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

def run_live_cockpit(argv: list[str]) -> int:
    """
    HUD realtime. Default interval 1.0s
    Options:
      --interval N   (float)
      --compact      (lebih ringkas)
    """
    if psutil is None:
        ansi.print_brief_error("Fitur Monitoring butuh library 'psutil'.")
        print("Install Fedora: sudo dnf install python3-psutil")
        return 1

    # parse options
    interval = 1.0
    compact = False
    # allow `ai mon live 0.5`
    for i, a in enumerate(argv):
        if a in ("--compact", "compact"):
            compact = True
        if a in ("--interval",):
            if i + 1 < len(argv):
                try:
                    interval = float(argv[i + 1])
                except Exception:
                    pass
        # numeric shortcut
        if i == 0:
            try:
                interval = float(a)
            except Exception:
                pass

    interval = float(_clamp(interval, 0.2, 5.0))

    pr = PowerReader()
    ns = NetSpeedometer()
    ns.update()

    # background snapshot throttle (battery health)
    last_snap = 0.0
    SNAP_EVERY = 600.0  # 10 min

    # process sampling throttle (avoid heavy loop)
    last_proc = 0.0
    PROC_EVERY = 5.0
    proc_info = {"cpu": "-", "mem": "-"}

    input_muter = ansi.MuteInputDuringWait()
    ansi.alt_screen_enter()
    ansi.cursor_hide()
    try:
        # prime cpu_percent for psutil
        try:
            psutil.cpu_percent(interval=None)
        except Exception:
            pass

        with input_muter:
            while True:
                cols, rows = ansi.term_size()

                # --- collect core metrics ---
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

                temps = ThermalReader.read_key_temps()
                fan = ThermalReader.read_fan_rpm()
                power = pr.read()
                du = _disk_usage_root()
                wifi = WiFiReader.read()

                ns.update()

                # process sampling occasionally
                if time.time() - last_proc >= PROC_EVERY:
                    proc_info = _top_processes_snapshot()
                    last_proc = time.time()

                # --- battery display logic ---
                lvl = power.get("percent")
                lvl_i = int(lvl) if isinstance(lvl, int) else None
                st_raw = str(power.get("status_raw") or "Unknown")
                plugged = power.get("plugged")
                plugged_b = bool(plugged) if isinstance(plugged, bool) else False

                # status text
                st_txt = "Unknown"
                bat_col = ansi.c_green()

                if plugged_b:
                    # Charging vs Full vs Not charging
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

                # --- periodic background snapshot (silent) ---
                if time.time() - last_snap >= SNAP_EVERY:
                    cap = pr.read_capacity_health()
                    if cap.get("ok"):
                        model = str(cap.get("model") or "BAT")
                        snapshot_metric("battery", model, "health_pct", round(float(cap["health_pct"]), 2))
                        snapshot_metric("battery", model, "capacity_full", round(float(cap["full"]), 2))
                    last_snap = time.time()

                # --- render ---
                ansi.clear_screen()

                title_left = f"{ansi.c_cyan()}{ansi.c_bold()} SYSTEM COCKPIT {ansi.c_reset()}"
                title_right = f"{ansi.c_dim()}AI-Terminal{ansi.c_reset()}"
                # align right
                space = max(1, min(cols, 120) - (len(" SYSTEM COCKPIT ") + len("AI-Terminal") + 6))
                print(f"{title_left}{' ' * space}{title_right}")
                ts = _now_ts()
                print(f"{ansi.c_dim()}{ts.center(min(cols, 80))}{ansi.c_reset()}")
                print(f"{ansi.c_dim()}Uptime:{ansi.c_reset()} {_uptime_str()}   {ansi.c_dim()}Load:{ansi.c_reset()} {_loadavg_str()}")
                print("-" * min(cols, 80))

                # CPU/RAM bars
                print(f"{ansi.c_bold()} CPU {ansi.c_reset()} {_draw_bar(cpu)} {cpu:>5.1f}%   {ansi.c_dim()}Top:{ansi.c_reset()} {proc_info.get('cpu','-')}")
                print(f"{ansi.c_bold()} RAM {ansi.c_reset()} {_draw_bar(ram_pct)} {ram_pct:>5.1f}%   {ram_used/(1024**3):.1f}/{ram_total/(1024**3):.1f} GB   {ansi.c_dim()}TopMem:{ansi.c_reset()} {proc_info.get('mem','-')}")

                # Disk usage (/)
                if du.get("ok"):
                    dp = float(du.get("used_pct", 0.0))
                    print(f"{ansi.c_bold()} DISK{ansi.c_reset()} {_draw_bar(dp)} {dp:>5.1f}%   /   {_human_bytes(float(du['used']))}/{_human_bytes(float(du['total']))}")

                print("-" * min(cols, 80))

                # Temps / fan
                cpu_t = temps.get("cpu")
                ssd_t = temps.get("ssd")
                wifi_t = temps.get("wifi")

                cpu_t_s = f"{cpu_t:.1f}°C" if isinstance(cpu_t, (int, float)) else "-"
                ssd_t_s = f"{ssd_t:.1f}°C" if isinstance(ssd_t, (int, float)) else "-"
                wifi_t_s = f"{wifi_t:.1f}°C" if isinstance(wifi_t, (int, float)) else "-"

                fan_s = f"{int(fan)} RPM" if isinstance(fan, (int, float)) and fan else "-"

                # Temperature coloring (simple threshold)
                def _tcol(v: Optional[float]) -> str:
                    if v is None:
                        return ansi.c_dim()
                    if v < 70:
                        return ansi.c_green()
                    if v < 85:
                        return ansi.c_yellow()
                    return ansi.c_red()

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

                # Network line
                rx = _human_rate_bps(ns.rx)
                tx = _human_rate_bps(ns.tx)

                net_line = f"{ansi.c_bold()} NET {ansi.c_reset()} ↓{rx}  ↑{tx}"
                if wifi.get("ok"):
                    ssid = wifi.get("ssid") or "-"
                    sig = wifi.get("signal_dbm")
                    sig_txt = f"{sig:.0f} dBm" if isinstance(sig, (int, float)) else "-"
                    net_line += f"   {ansi.c_dim()}WiFi:{ansi.c_reset()} {ssid} ({sig_txt})"

                print(net_line)

                # Footer tips
                print("")
                if compact:
                    print(f"{ansi.c_dim()}Ctrl+C untuk keluar. Tip: ai mon sensors (lihat semua sensor).{ansi.c_reset()}")
                else:
                    print(f"{ansi.c_dim()}Ctrl+C untuk keluar.{ansi.c_reset()}  {ansi.c_dim()}Tip:{ansi.c_reset()} `ai mon sensors` untuk daftar sensor lengkap.")
                    print(f"{ansi.c_dim()}Tip:{ansi.c_reset()} `sudo ai mon disk` untuk TBW/SMART detail.")

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
      live [--interval N] [--compact]
      sensors
      batt
      disk [--sudo|--deep]
      net [target]
      net live [target]
      help
    """
    # Soft fail early if psutil missing (except help)
    if not argv:
        mode = "live"
        rest: list[str] = []
    else:
        mode = (argv[0] or "").strip().lower()
        rest = argv[1:]

    if mode in ("help", "-h", "--help"):
        ansi.print_info("AI Monitor (MON)")
        print("  ai mon live [--interval N] [--compact] : HUD realtime (ANSI).")
        print("  ai mon sensors                         : Daftar semua sensor/field yang bisa dibaca.")
        print("  ai mon batt                            : Battery health + history (time travel).")
        print("  ai mon disk [--sudo|--deep]            : Storage SMART/TBW + history.")
        print("  ai mon net [target]                    : Network diagnostics (ping/dns + wifi detail).")
        print("  ai mon net live [target]               : Live ping graph (alt-screen).")
        print("")
        print("Dependency (Fedora):")
        print("  sudo dnf install python3-psutil")
        print("Optional tools (Fedora):")
        print("  sudo dnf install lm_sensors smartmontools pciutils iw iproute")
        print("")
        print("Tip:")
        print("  - Jika `ai mon live` butuh data lebih banyak, kita bisa tambah bertahap setelah lihat hasil `ai mon sensors`.")
        return 0

    # Modes that do NOT require psutil strictly (sensors still nicer with psutil but can show sysfs/tooling)
    if mode in ("sensors", "probe", "inventory"):
        return run_sensors_dump()

    # The rest mostly requires psutil
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
