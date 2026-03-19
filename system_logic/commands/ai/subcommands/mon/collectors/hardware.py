"""
MON Hardware Collector.
Membaca: Battery (live + capacity/health), Disk (mounted + SMART), Thermal/Fan sensors.

Classes:
    PowerReader  — Baca sysfs battery untuk watt/volt/amp/health (pindahan dari dashboard.py)

Functions:
    collect_battery_stats()     — Battery level via psutil (simple)
    collect_disk_stats()        — Mounted disks + optional SMART
    collect_thermal_stats()     — CPU temp, fan RPM
    collect_hardware_stats()    — Aggregator semua hardware stats
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Any, List, Optional

from ..utils import run_cmd, read_text, read_int, read_float

try:
    import psutil  # type: ignore
except ImportError:
    psutil = None


# ==========================================================
# PowerReader — Battery health + live power via sysfs
# (Logic dipindah dari ui/dashboard.py agar collectors tetap
#  menjadi satu-satunya sumber data hardware)
# ==========================================================

class PowerReader:
    """
    Membaca data battery lengkap langsung dari sysfs.
    Lebih presisi dari psutil untuk watt/volt/amp dan health%.

    Usage:
        pr = PowerReader()
        cap  = pr.read_capacity_health()   # health, full, design, cycle
        live = pr.read()                   # percent, status, watt, volt, amp
    """

    def __init__(self) -> None:
        self._batt_path: Optional[Path] = self._find_battery()

    @staticmethod
    def _find_battery() -> Optional[Path]:
        """Cari path battery pertama di sysfs."""
        base = Path("/sys/class/power_supply")
        if not base.exists():
            return None
        bats = sorted(base.glob("BAT*"))
        return bats[0] if bats else None

    @staticmethod
    def _sysfs_text(p: Path) -> Optional[str]:
        try:
            return p.read_text().strip() or None
        except Exception:
            return None

    @staticmethod
    def _sysfs_float(p: Path) -> Optional[float]:
        try:
            return float(p.read_text().strip())
        except Exception:
            return None

    def read_capacity_health(self) -> Dict[str, Any]:
        """
        Membaca kapasitas dan health battery dari sysfs.

        Returns dict:
            ok (bool), model (str), health_pct (float),
            full (float), design (float), unit (str), cycle (str|None)
        """
        batt = self._batt_path
        if batt is None:
            return {"ok": False, "error": "No battery found"}

        # Coba energy (Wh) dulu, fallback ke charge (mAh)
        full_now   = self._sysfs_float(batt / "energy_full")
        design_now = self._sysfs_float(batt / "energy_full_design")
        unit = "Wh"

        if full_now is None or design_now is None:
            full_now   = self._sysfs_float(batt / "charge_full")
            design_now = self._sysfs_float(batt / "charge_full_design")
            unit = "mAh"

        if full_now is None or design_now is None or design_now == 0:
            return {"ok": False, "error": "Capacity data not available"}

        # Konversi dari µWh / µAh ke Wh / mAh
        divisor = 1_000_000.0 if unit == "Wh" else 1_000.0
        full   = full_now   / divisor
        design = design_now / divisor

        health_pct = round((full / design) * 100.0, 2) if design > 0 else 0.0

        # Cycle count (opsional)
        cycle_raw = self._sysfs_text(batt / "cycle_count")
        cycle = cycle_raw if cycle_raw and cycle_raw.isdigit() else None

        # Model name
        model = self._sysfs_text(batt / "model_name") or batt.name

        return {
            "ok": True,
            "model": model,
            "health_pct": health_pct,
            "full": round(full, 2),
            "design": round(design, 2),
            "unit": unit,
            "cycle": cycle,
        }

    def read(self) -> Dict[str, Any]:
        """
        Membaca live power data (percent, status, watt, volt, amp).

        Returns dict:
            ok (bool), status_raw (str), percent (int|None),
            plugged (bool|None), secs_left (int|None),
            watt (float|None), volt (float|None), amp (float|None),
            watt_str (str), volt_str (str), amp_str (str),
            batt_name (str)
        """
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

        batt = self._batt_path
        if batt is None:
            return res

        res["batt_name"]   = batt.name
        res["status_raw"]  = self._sysfs_text(batt / "status") or "Unknown"

        # Percent + plugged + secs_left via psutil
        if psutil is not None:
            try:
                b = psutil.sensors_battery()
                if b:
                    res["percent"]   = int(getattr(b, "percent", 0) or 0)
                    res["plugged"]   = bool(getattr(b, "power_plugged", False))
                    res["secs_left"] = getattr(b, "secsleft", None)
            except Exception:
                pass

        # Voltage
        v_uv = self._sysfs_float(batt / "voltage_now")
        if v_uv is not None:
            res["volt"] = round(float(v_uv) / 1e6, 3)

        # Power (watt)
        p_uw = None
        for fname in ("power_now", "power_avg"):
            if (batt / fname).exists():
                p_uw = self._sysfs_float(batt / fname)
                break

        # Current (amp)
        c_ua = None
        if (batt / "current_now").exists():
            c_ua = self._sysfs_float(batt / "current_now")

        if p_uw is not None:
            res["watt"] = round(float(p_uw) / 1e6, 3)
        elif c_ua is not None and res["volt"] is not None:
            a = round(float(c_ua) / 1e6, 3)
            res["amp"]  = a
            res["watt"] = round(float(res["volt"]) * a, 3)

        # Derive amp dari watt/volt jika belum ada
        if (res["amp"] is None
                and res["watt"] is not None
                and res["volt"] is not None
                and float(res["volt"]) > 0):
            res["amp"] = round(float(res["watt"]) / float(res["volt"]), 3)

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


# ==========================================================
# Simple battery stats via psutil (untuk dashboard live HUD)
# ==========================================================

def collect_battery_stats() -> Dict[str, Any]:
    """
    Membaca statistik battery sederhana via psutil.
    Untuk data lengkap (health/watt/volt), gunakan PowerReader.
    """
    if not psutil:
        return {"ok": False, "error": "psutil not available"}

    try:
        batt = psutil.sensors_battery()
        if not batt:
            return {"ok": False, "error": "No battery found"}

        return {
            "ok": True,
            "percent": batt.percent,
            "plugged": batt.power_plugged,
            "time_left_sec": batt.secsleft if batt.secsleft and batt.secsleft > 0 else None,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ==========================================================
# Disk stats
# ==========================================================

def collect_disk_stats(use_sudo: bool = False) -> Dict[str, Any]:
    """
    Membaca statistik disk mounted + SMART (optional).

    Args:
        use_sudo: Jika True, gunakan sudo untuk smartctl
    """
    if not psutil:
        return {"ok": False, "error": "psutil not available"}

    try:
        partitions = psutil.disk_partitions(all=False)
        disks = []

        for part in partitions:
            try:
                usage = psutil.disk_usage(part.mountpoint)
                disks.append({
                    "device":     part.device,
                    "mountpoint": part.mountpoint,
                    "fstype":     part.fstype,
                    "total":      usage.total,
                    "used":       usage.used,
                    "free":       usage.free,
                    "percent":    usage.percent,
                })
            except (PermissionError, OSError):
                continue

        smart_data = _read_smart_data() if use_sudo else {}

        return {
            "ok":    True,
            "disks": disks,
            "smart": smart_data,
        }

    except Exception as e:
        return {"ok": False, "error": str(e)}


def _read_smart_data() -> Dict[str, Any]:
    """
    Membaca SMART data menggunakan smartctl.
    Requires: smartmontools package & sudo access.
    """
    smart: Dict[str, Any] = {}

    try:
        nvme_devices = list(Path("/dev").glob("nvme*n*"))

        for dev in nvme_devices:
            dev_name = dev.name
            rc, out = run_cmd(["sudo", "smartctl", "-A", str(dev)], timeout=5)

            if rc == 0 and out:
                attrs: Dict[str, Any] = {}
                for line in out.split("\n"):
                    if "Percentage Used" in line:
                        parts = line.split()
                        if len(parts) >= 3:
                            try:
                                attrs["wear_pct"] = int(parts[2].rstrip("%"))
                            except ValueError:
                                pass
                    elif "Data Units Written" in line:
                        parts = line.split()
                        if len(parts) >= 3:
                            try:
                                blocks = int(parts[2].replace(",", ""))
                                tbw = (blocks * 512 * 1024) / (1024 ** 4)
                                attrs["tbw"] = round(tbw, 2)
                            except (ValueError, IndexError):
                                pass

                if attrs:
                    smart[dev_name] = attrs

    except Exception:
        pass

    return smart


# ==========================================================
# Thermal stats
# ==========================================================

def collect_thermal_stats() -> Dict[str, Any]:
    """Membaca sensor thermal (CPU temp, Fan RPM)."""
    if not psutil:
        return {"ok": False, "error": "psutil not available"}

    try:
        temps: Dict[str, Any] = {}
        fans:  Dict[str, Any] = {}

        try:
            temp_dict = psutil.sensors_temperatures()
            for name, entries in temp_dict.items():
                for entry in entries:
                    label = entry.label or name
                    temps[label] = {
                        "current":  entry.current,
                        "high":     entry.high,
                        "critical": entry.critical,
                    }
        except (AttributeError, OSError):
            pass

        try:
            fan_dict = psutil.sensors_fans()
            for name, entries in fan_dict.items():
                for entry in entries:
                    label = entry.label or name
                    fans[label] = entry.current
        except (AttributeError, OSError):
            pass

        return {"ok": True, "temperatures": temps, "fans": fans}

    except Exception as e:
        return {"ok": False, "error": str(e)}


# ==========================================================
# Aggregator
# ==========================================================

def collect_hardware_stats(use_sudo: bool = False) -> Dict[str, Any]:
    """
    Aggregator untuk semua hardware stats.

    Args:
        use_sudo: Untuk SMART data
    """
    return {
        "battery": collect_battery_stats(),
        "disks":   collect_disk_stats(use_sudo=use_sudo),
        "thermal": collect_thermal_stats(),
    }