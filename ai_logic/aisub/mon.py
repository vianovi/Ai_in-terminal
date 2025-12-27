"""
Advanced System Cockpit & Network Monitor.
Requirements: psutil (wajib), lm_sensors (opsional untuk fan lebih detail)
"""

import os
import sys
import time
import datetime
import threading
import socket        # <--- Sudah ditambahkan
import subprocess    # <--- Sudah ditambahkan
from pathlib import Path
from typing import Dict, Any

from ai_logic.ui import ansi

# Dependency Check: psutil wajib untuk fitur monitoring berat
try:
    import psutil
except ImportError:
    ansi.print_brief_error("Library 'psutil' wajib untuk fitur advanced ini.")
    print("Cara install:")
    print("  sudo dnf install python3-psutil   (System wide/Fedora)")
    print("  ATAU: pip install psutil          (Local venv)")
    sys.exit(1)


# ==========================================================
# 1. HARDWARE DATA GATHERING (SENSORS)
# ==========================================================

class SysScanner:
    @staticmethod
    def get_uptime_str() -> str:
        boot = psutil.boot_time()
        delta = time.time() - boot
        d = int(delta // 86400)
        h = int((delta % 86400) // 3600)
        m = int((delta % 3600) // 60)
        return f"{d}d {h}h {m}m"

    @staticmethod
    def get_thermal_info() -> Dict[str, float]:
        data = {"cpu": 0.0, "wifi": 0.0, "ssd": 0.0}
        temps = psutil.sensors_temperatures()
        pkgs = temps.get("coretemp") or temps.get("k10temp") or []

        # CPU
        for s in pkgs:
            if "package" in s.label.lower() or "tctl" in s.label.lower():
                data["cpu"] = s.current
                break
        if data["cpu"] == 0.0 and pkgs:
             data["cpu"] = pkgs[0].current

        # WiFi & SSD
        for name, entries in temps.items():
            name_l = name.lower()
            if "iwl" in name_l or "wifi" in name_l:
                data["wifi"] = entries[0].current
            if "nvme" in name_l or "composite" in name_l:
                for entry in entries:
                    if "composite" in entry.label.lower() or not entry.label:
                        data["ssd"] = entry.current
                        break
        return data

    @staticmethod
    def get_fan_speed() -> int:
        fans = psutil.sensors_fans()
        # Prioritas 1: cari fan yg namanya 'cpu'
        for name, entries in fans.items():
            for entry in entries:
                if "cpu" in entry.label.lower():
                    return entry.current
        # Prioritas 2: max rpm
        max_rpm = 0
        for name, entries in fans.items():
            for entry in entries:
                if entry.current > max_rpm: max_rpm = entry.current
        return max_rpm

    @staticmethod
    def get_power_consumption() -> Dict[str, str]:
        """
        OUTPUT LENGKAP: watt_str (5.5 W / 130 mW), volt_str, amp_str, status_raw
        """
        res = {
            "watt_str": "0 mW",
            "volt_str": "0.0 V",
            "amp_str": "0.00 A",
            "raw_status": "Unknown", # Charging, Discharging, Not Charging (Cut-off), Full
            "tdp_cpu": "N/A"
        }

        batt_path = Path("/sys/class/power_supply")
        batt = None

        # Auto detect baterai utama
        for b in batt_path.glob("BAT*"):
            stat_path = b / "status"
            if stat_path.exists():
                st = stat_path.read_text().strip()
                res["raw_status"] = st # Save status raw driver linux

                # Kita anggap ini baterai aktif (bahkan kalau status Full/Unknown)
                batt = b
                if st == "Discharging": break # Prioritas kalau sedang discharge

        if batt:
            try:
                # 1. Voltage
                uv_path = batt / "voltage_now"
                v_val = 0.0
                if uv_path.exists():
                    v_val = float(uv_path.read_text().strip()) / 1000000.0
                    res["volt_str"] = f"{v_val:.2f} V"

                # 2. Power (Watts)
                uw_path = batt / "power_now"
                if not uw_path.exists(): uw_path = batt / "power_avg"

                if uw_path.exists():
                    mw_val = float(uw_path.read_text().strip()) / 1000.0 # to milliWatt

                    # Logika tampilan Watt vs MilliWatt
                    if mw_val < 1000:
                        res["watt_str"] = f"{int(mw_val)} mW"
                    else:
                        res["watt_str"] = f"{mw_val / 1000:.2f} W"

                    # 3. Hitung Arus (Amperes) Hukum Ohm: I = P / V
                    if v_val > 0 and mw_val > 0:
                        amps = (mw_val / 1000.0) / v_val
                        res["amp_str"] = f"{amps:.3f} A"
            except: pass

        # Baca TDP/Limit CPU (Intel RAPL)
        rapl = Path("/sys/class/powercap/intel-rapl/intel-rapl:0/constraint_0_power_limit_uw")
        if rapl.exists():
                try:
                    uw = float(rapl.read_text().strip())
                    res["tdp_cpu"] = f"{uw / 1000000:.0f} W"
                except: pass

        return res

class NetSpeedometer:
    """Class helper untuk menghitung kecepatan download/upload per detik."""
    def __init__(self):
        self.t0 = time.time()
        self.io0 = psutil.net_io_counters()
        self.rx_spd = 0.0
        self.tx_spd = 0.0

    def update(self):
        t1 = time.time()
        io1 = psutil.net_io_counters()
        dt = t1 - self.t0
        if dt > 0:
            # Delta Bytes / Delta Time = Bytes per second
            self.rx_spd = (io1.bytes_recv - self.io0.bytes_recv) / dt
            self.tx_spd = (io1.bytes_sent - self.io0.bytes_sent) / dt
        self.t0 = t1
        self.io0 = io1

    def human_spd(self, bytes_sec: float) -> str:
        # Konversi angka mentah ke KB/s, MB/s
        for unit in ['B', 'KB', 'MB', 'GB']:
            if bytes_sec < 1024:
                return f"{bytes_sec:.1f}{unit}/s"
            bytes_sec /= 1024
        return f"{bytes_sec:.1f}GB/s"


# ==========================================================
# 2. VISUALIZATION (UI)
# ==========================================================

def _draw_bar(pct: float, width=20) -> str:
    """Membuat text-based progress bar."""
    if pct < 0: pct = 0
    if pct > 100: pct = 100
    fill = int(width * pct / 100)
    bar = "█" * fill
    empty = "░" * (width - fill)

    # Warna dinamis
    col = ansi.c_green()
    if pct > 60: col = ansi.c_yellow()
    if pct > 85: col = ansi.c_red()

    return f"{col}{bar}{ansi.c_dim()}{empty}{ansi.c_reset()}"

def run_live_cockpit():
    """Main Dashboard Loop dengan input muting & tampilan visual baru."""

    input_muter = ansi.MuteInputDuringWait()
    net_meter = NetSpeedometer()
    net_meter.update()

    ansi.alt_screen_enter()
    ansi.cursor_hide()

    try:
        with input_muter:
            while True:
                # 1. --- GET RAW DATA ---
                cpu_pct = psutil.cpu_percent(interval=None)
                ram = psutil.virtual_memory()
                temps = SysScanner.get_thermal_info()
                uptime = SysScanner.get_uptime_str()
                rpm = SysScanner.get_fan_speed()
                pwr = SysScanner.get_power_consumption()

                net_meter.update()

                # 2. --- BATTERY LOGIC CLEANUP ---
                batt = psutil.sensors_battery()

                batt_level_int = 0
                is_plugged = False

                if batt:
                    batt_level_int = int(round(batt.percent)) # Bulatkan ke Int (0-100)
                    is_plugged = batt.power_plugged

                # Logic Keterangan Text AC/DC yang PINTAR
                # Priority: raw_status linux (paling akurat) -> psutil plugged
                raw_st = pwr["raw_status"] # Charging, Discharging, Not charging, Full

                batt_text_status = "Unknown"
                color_batt = ansi.c_green()

                if not is_plugged:
                    # Mode Baterai
                    batt_text_status = "DC (On Battery)"
                    if batt_level_int < 20: color_batt = ansi.c_red()
                else:
                    # Mode Colok Listrik (AC)
                    if raw_st == "Full" or batt_level_int == 100:
                        batt_text_status = "AC (Full Charged)"
                    elif raw_st == "Not charging":
                        # Ini DETEKSI CUT-OFF CHARGING (Fitur Battery Health)
                        batt_text_status = "AC (Idle / Cut-off)"
                        color_batt = ansi.c_cyan() # Warna biru muda penanda smart charging
                    elif raw_st == "Charging":
                         batt_text_status = "AC (Charging)"
                    else:
                         batt_text_status = "AC (Plugged)"

                # Timestamp
                now_str = datetime.datetime.now().strftime("%A, %d %b %Y | %H:%M:%S")

                # 3. --- RENDER UI ---
                ansi.clear_screen()

                # [ HEADER ]
                print(f"{ansi.c_cyan()}{ansi.c_bold()} SYSTEM COCKPIT (AI-TERM) {ansi.c_reset()}")
                print(f"{ansi.c_dim()}{now_str.center(50)}{ansi.c_reset()}")
                print(f"{ansi.c_yellow()} UPTIME : {uptime}{ansi.c_reset()}")
                print("-" * 55)

                # [ ROW 1: CORE PERFORMA ]
                cpu_t_str = f"{temps['cpu']}°C" if temps['cpu'] else "-"
                print(f"{ansi.c_bold()} CPU  :{ansi.c_reset()} {_draw_bar(cpu_pct, 15)} {cpu_pct:>4.1f}%")
                # Jika TDP N/A, tidak usah ditampilkan biar bersih
                tdp_txt = f"| TDP {pwr['tdp_cpu']} " if "N/A" not in pwr['tdp_cpu'] else ""
                print(f"        Temp: {cpu_t_str} {tdp_txt}| Fan: {rpm} RPM 🌪️")

                print(f"{ansi.c_bold()} RAM  :{ansi.c_reset()} {_draw_bar(ram.percent, 15)} {ram.percent:>4.1f}%")
                print(f"        Used: {ram.used / (1024**3):.1f} GB / {ram.total / (1024**3):.1f} GB")
                print("-" * 55)

                # [ ROW 2: ELECTRICAL STATUS ]
                print(f"{ansi.c_bold()} POWER:{ansi.c_reset()} {color_batt}{batt_level_int}%{ansi.c_reset()} [{batt_text_status}]")

                # Format: "Flow : 130 mW   @ 12.25 V" atau "5.25 W   @ 12.25 V"
                print(f"        Flow : {ansi.c_yellow()}{pwr['watt_str']:<8}{ansi.c_reset()} @ {pwr['volt_str']}")
                print(f"        Arus : {pwr['amp_str']}")

                # [ ROW 3: SENSORS & NETWORK ]
                ssd_t_str = f"{temps['ssd']}°C" if temps['ssd'] else "-"
                wifi_t_str = f"{temps['wifi']}°C" if temps['wifi'] else "-"

                print(f"\n{ansi.c_bold()} [ PERIPHERALS ]{ansi.c_reset()}")
                print(f" NVMe : Temp {ssd_t_str}")
                print(f" WiFi : Temp {wifi_t_str}")
                print(f" NET  : ↓ {net_meter.human_spd(net_meter.rx_spd)}   ↑ {net_meter.human_spd(net_meter.tx_spd)}")

                print("\n" * 2)
                print(f"{ansi.c_dim()}[Ctrl+C] to Exit (Input dibisukan){ansi.c_reset()}")

                time.sleep(1.0)

    except KeyboardInterrupt:
        pass
    except Exception as e:
        ansi.alt_screen_exit()
        print(f"Error: {e}")
    finally:
        ansi.cursor_show()
        ansi.alt_screen_exit()
        print("Monitoring closed.")

    return 0

# ==========================================================
# 3. NETWORK DIAGNOSTICS
# ==========================================================
def run_net_diag():
    ansi.print_system("NETWORK DIAGNOSTICS & STABILITY TEST")

    # 1. Interface Overview
    addrs = psutil.net_if_addrs()
    stats = psutil.net_if_stats()

    print("\n[ LOCAL INTERFACES ]")
    found_iface = False
    for iface, addr_list in addrs.items():
        if iface == "lo": continue # Skip Loopback
        is_up = "DOWN"
        if iface in stats and stats[iface].isup:
            is_up = "UP"

        ipv4 = "No IP"
        for a in addr_list:
            if a.family == socket.AF_INET:
                ipv4 = a.address

        # Warna hijau kalau UP
        col = ansi.c_green() if is_up == "UP" else ansi.c_red()
        print(f"  • {ansi.c_cyan()}{iface:<10}{ansi.c_reset()} : {col}{is_up}{ansi.c_reset()} | {ipv4}")
        found_iface = True

    if not found_iface:
        print("  (Tidak ada interface aktif ditemukan)")

    # 2. Stability Ping Check (Penting untuk cek kualitas koneksi)
    target_ip = "8.8.8.8" # Google DNS
    print(f"\n[ QUALITY CHECK ] Pinging {target_ip} (5x)... ", end="", flush=True)

    times = []
    try:
        # Memanggil perintah sistem 'ping'
        cmd = ["ping", "-c", "5", "-i", "0.2", target_ip]
        output = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True)

        # Parse output ping linux: "time=12.4 ms"
        for line in output.splitlines():
            if "time=" in line:
                try:
                    t_str = line.split("time=")[1].split(" ")[0]
                    times.append(float(t_str))
                except: pass

        if times:
            avg_ms = sum(times) / len(times)
            jitter_ms = max(times) - min(times)

            print("Done.")
            print(f"  Avg Latency : {ansi.c_bold()}{avg_ms:.1f} ms{ansi.c_reset()}")
            print(f"  Jitter      : {jitter_ms:.1f} ms", end=" ")

            # Analisis Kualitas
            if jitter_ms < 10:
                print(f"({ansi.c_green()}STABIL - Bagus untuk Game/Call{ansi.c_reset()})")
            elif jitter_ms < 50:
                 print(f"({ansi.c_yellow()}CUKUP - Agak naik turun{ansi.c_reset()})")
            else:
                 print(f"({ansi.c_red()}BURUK - Lag Spike terdeteksi{ansi.c_reset()})")
        else:
            print("No Data (Semua packet RTO?).")

    except Exception as e:
        print("FAIL.")
        print(f"  Gagal menjalankan ping: {e}")

    return 0

# ==========================================================
# ENTRY POINT
# ==========================================================
def handle(argv: list[str], cfg: dict) -> int:
    """
    Subcommand handler.
    Default (tanpa argumen) masuk ke mode 'live'.
    """
    mode = "live"
    if argv:
        mode = argv[0].lower().strip()

    if mode in ("live", "dashboard", "now"):
        return run_live_cockpit()

    if mode in ("net", "network", "ping"):
        return run_net_diag()

    if mode in ("help", "-h"):
        ansi.print_info("Monitoring Dashboard")
        print("  ai mon live : Buka dashboard sistem (CPU/Power/Net).")
        print("  ai mon net  : Cek stabilitas koneksi internet.")
        return 0

    ansi.print_brief_error(f"Mode monitor '{mode}' tidak dikenal.")
    return 2