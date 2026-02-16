"""
MON Hardware Collector.
Membaca: Battery, Disk (mounted + SMART), Thermal/Fan sensors.
"""

from pathlib import Path
from typing import Dict, Any, List, Optional
from ..utils import run_cmd, read_text, read_int, read_float

try:
    import psutil  # type: ignore
except ImportError:
    psutil = None


def collect_battery_stats() -> Dict[str, Any]:
    """Membaca statistik battery."""
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
            "time_left_sec": batt.secsleft if batt.secsleft > 0 else None,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


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
                    "device": part.device,
                    "mountpoint": part.mountpoint,
                    "fstype": part.fstype,
                    "total": usage.total,
                    "used": usage.used,
                    "free": usage.free,
                    "percent": usage.percent,
                })
            except (PermissionError, OSError):
                continue

        # SMART data (optional, requires smartctl)
        smart_data = {}
        if use_sudo:
            smart_data = _read_smart_data()

        return {
            "ok": True,
            "disks": disks,
            "smart": smart_data
        }

    except Exception as e:
        return {"ok": False, "error": str(e)}


def _read_smart_data() -> Dict[str, Any]:
    """
    Membaca SMART data menggunakan smartctl.
    Requires: smartmontools package & sudo access.
    """
    smart = {}

    # Cari NVMe devices di /dev/nvme*
    try:
        from pathlib import Path
        nvme_devices = list(Path("/dev").glob("nvme*n*"))

        for dev in nvme_devices:
            dev_name = dev.name
            rc, out = run_cmd(["sudo", "smartctl", "-A", str(dev)], timeout=5)

            if rc == 0 and out:
                # Parse SMART attributes
                attrs = {}
                for line in out.split('\n'):
                    if 'Percentage Used' in line:
                        # NVMe wear indicator
                        parts = line.split()
                        if len(parts) >= 3:
                            try:
                                attrs['wear_pct'] = int(parts[2].rstrip('%'))
                            except ValueError:
                                pass
                    elif 'Data Units Written' in line:
                        # TBW calculation
                        parts = line.split()
                        if len(parts) >= 3:
                            try:
                                # Units are usually in 512KB blocks
                                blocks = int(parts[2].replace(',', ''))
                                tbw = (blocks * 512 * 1024) / (1024**4)
                                attrs['tbw'] = round(tbw, 2)
                            except (ValueError, IndexError):
                                pass

                if attrs:
                    smart[dev_name] = attrs

    except Exception:
        pass

    return smart


def collect_thermal_stats() -> Dict[str, Any]:
    """Membaca sensor thermal (CPU temp, Fan RPM)."""
    if not psutil:
        return {"ok": False, "error": "psutil not available"}

    try:
        temps = {}
        fans = {}

        # Temperature sensors
        try:
            temp_dict = psutil.sensors_temperatures()
            for name, entries in temp_dict.items():
                for entry in entries:
                    label = entry.label or name
                    temps[label] = {
                        "current": entry.current,
                        "high": entry.high,
                        "critical": entry.critical
                    }
        except (AttributeError, OSError):
            pass

        # Fan sensors
        try:
            fan_dict = psutil.sensors_fans()
            for name, entries in fan_dict.items():
                for entry in entries:
                    label = entry.label or name
                    fans[label] = entry.current
        except (AttributeError, OSError):
            pass

        return {
            "ok": True,
            "temperatures": temps,
            "fans": fans
        }

    except Exception as e:
        return {"ok": False, "error": str(e)}


def collect_hardware_stats(use_sudo: bool = False) -> Dict[str, Any]:
    """
    Aggregator untuk semua hardware stats.

    Args:
        use_sudo: Untuk SMART data
    """
    battery = collect_battery_stats()
    disks = collect_disk_stats(use_sudo=use_sudo)
    thermal = collect_thermal_stats()

    return {
        "battery": battery,
        "disks": disks,
        "thermal": thermal
    }