"""
System Cockpit & Intelligence Module.

Fitur Utama:
1. Live Dashboard (HUD) : Monitor Realtime Sci-Fi style (Watts/Amps Presisi).
2. Battery Intelligence : History Tracker, Smart Charging Detect, Time Travel Comparison.
3. Storage Analytics    : S.M.A.R.T Wrapper + TBW History Tracker.
4. Network Diagnositcs  : Jitter Test, Chipset Detector, Live Ping Graph.

Path History: ./workspace/mon_history.json
"""

import os
import sys
import time
import json
import shutil
import socket
import datetime
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional

from ai_logic.ui import ansi

# --- Dependency Check ---
try:
    import psutil
except ImportError:
    ansi.print_brief_error("Library 'psutil' wajib diinstal.")
    print("Cara: sudo dnf install python3-psutil")
    sys.exit(1)


# ==========================================================
# 1. SMART HISTORY ENGINE
# ==========================================================
# Logika: Simpan di workspace/, append-only harian.

BASE_DIR = Path.cwd()
WORKSPACE_DIR = BASE_DIR / "workspace"
HISTORY_FILE = WORKSPACE_DIR / "mon_history.json"

def _ensure_history_ready():
    if not WORKSPACE_DIR.exists():
        try: WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
        except: pass

def _load_db() -> dict:
    if not HISTORY_FILE.exists(): return {}
    try:
        with open(HISTORY_FILE, 'r') as f: return json.load(f)
    except: return {}

def _save_db(data: dict):
    _ensure_history_ready()
    try:
        with open(HISTORY_FILE, 'w') as f: json.dump(data, f, indent=2)
    except: pass

def snapshot_metric(category: str, item_id: str, metric: str, value: float) -> bool:
    """
    Simpan data HANYA JIKA hari ini belum ada rekam jejak.
    return: True (Disimpan), False (Sudah ada, skip).
    """
    db = _load_db()
    today = datetime.date.today().isoformat()

    # Init structure
    if category not in db: db[category] = {}
    if item_id not in db[category]: db[category][item_id] = {}
    if metric not in db[category][item_id]: db[category][item_id][metric] = {}

    # Check existence
    if today in db[category][item_id][metric]:
        return False # Sudah ada data hari ini, jangan timpa/spam

    # Save
    db[category][item_id][metric][today] = value
    _save_db(db)
    return True

def get_comparison_text(category: str, item_id: str, metric: str, current: float, unit: str="") -> str:
    """Bandingkan current vs data TERLAMA yang tersimpan."""
    db = _load_db()
    try:
        series = db.get(category, {}).get(item_id, {}).get(metric, {})
        if not series: return ""

        # Cari tanggal terlama
        sorted_dates = sorted(series.keys())
        oldest_date = sorted_dates[0]

        if oldest_date == datetime.date.today().isoformat():
            return f"(Mulai tracking hari ini)"

        old_val = series[oldest_date]
        diff = current - old_val

        days = (datetime.date.today() - datetime.date.fromisoformat(oldest_date)).days

        icon = "⚪"
        if diff > 0: icon = "📈+"
        if diff < 0: icon = "📉"

        return f"{ansi.c_dim()}vs {days} hari lalu: {old_val} -> {current} ({icon}{diff:+.2f} {unit}){ansi.c_reset()}"
    except: return ""


# ==========================================================
# 2. HARDWARE SCANNERS (Core Logic)
# ==========================================================

def _sh(cmd):
    try: return subprocess.check_output(cmd, shell=True, stderr=subprocess.DEVNULL, text=True).strip()
    except: return ""

class SysScanner:
    @staticmethod
    def get_uptime_str() -> str:
        d = time.time() - psutil.boot_time()
        days = int(d // 86400)
        rem = d % 86400
        hrs = int(rem // 3600)
        mins = int((rem % 3600) // 60)
        return f"{days}d {hrs}h {mins}m"

    @staticmethod
    def get_temps():
        d = {"cpu": 0.0, "wifi": 0.0, "ssd": 0.0}
        try:
            temps = psutil.sensors_temperatures()
            # CPU
            pkgs = temps.get("coretemp") or temps.get("k10temp") or []
            for s in pkgs:
                if "package" in s.label.lower() or "tctl" in s.label.lower():
                    d["cpu"] = s.current; break
            if not d["cpu"] and pkgs: d["cpu"] = pkgs[0].current

            # Others
            for name, entries in temps.items():
                n_l = name.lower()
                if "iwl" in n_l or "wifi" in n_l or "ath" in n_l:
                    d["wifi"] = entries[0].current
                if "nvme" in n_l or "composite" in n_l:
                    for e in entries:
                        if "composite" in e.label.lower() or not e.label:
                            d["ssd"] = e.current; break
        except: pass
        return d

    @staticmethod
    def get_fan_rpm():
        rpm = 0
        try:
            fans = psutil.sensors_fans()
            # Priority Search
            for name, entries in fans.items():
                for e in entries:
                    if "cpu" in e.label.lower(): return e.current
            # Fallback Max
            for name, entries in fans.items():
                for e in entries:
                    if e.current > rpm: rpm = e.current
        except: pass
        return rpm

    @staticmethod
    def get_power():
        res = {
            "watt_str": "0 mW", "volt_str": "0 V",
            "amp_str": "0.00 A", "raw_st": "Unknown", "tdp": "N/A"
        }
        # Battery Path
        path = Path("/sys/class/power_supply")
        batt = None
        for b in path.glob("BAT*"):
            try:
                st = (b/"status").read_text().strip()
                res["raw_st"] = st
                batt = b
                if st == "Discharging": break
            except: pass

        if batt:
            try:
                # Voltage (uV -> V)
                v_f = batt/"voltage_now"
                v_val = 0.0
                if v_f.exists():
                    v_val = float(v_f.read_text().strip()) / 1e6
                    res["volt_str"] = f"{v_val:.2f} V"

                # Power (uW -> W/mW)
                p_f = batt/"power_now" if (batt/"power_now").exists() else batt/"power_avg"
                if p_f.exists():
                    mw_val = float(p_f.read_text().strip()) / 1000.0
                    res["watt_str"] = f"{int(mw_val)} mW" if mw_val < 1000 else f"{mw_val/1000:.2f} W"
                    if v_val > 0 and mw_val > 0:
                        amps = (mw_val / 1000) / v_val
                        res["amp_str"] = f"{amps:.3f} A"
            except: pass

        # CPU RAPL (PL1 TDP Estimate)
        rapl = Path("/sys/class/powercap/intel-rapl/intel-rapl:0/constraint_0_power_limit_uw")
        if rapl.exists():
            try: res["tdp"] = f"{float(rapl.read_text().strip())/1e6:.0f} W"
            except: pass
        return res


class NetSpeedometer:
    def __init__(self):
        self.t0 = time.time()
        self.io0 = psutil.net_io_counters()
        self.rx = 0.0; self.tx = 0.0
    def update(self):
        t1 = time.time(); io1 = psutil.net_io_counters(); dt = t1 - self.t0
        if dt > 0:
            self.rx = (io1.bytes_recv - self.io0.bytes_recv) / dt
            self.tx = (io1.bytes_sent - self.io0.bytes_sent) / dt
        self.t0 = t1; self.io0 = io1
    def fmt(self, val):
        for u in ['B','K','M','G']:
            if val < 1024: return f"{val:.1f}{u}/s"
            val /= 1024
        return f"{val:.1f}G/s"

def _draw_bar(pct, width=15):
    if pct<0: pct=0
    if pct>100: pct=100
    fill = int(width*pct/100)
    col = ansi.c_green() if pct <= 60 else (ansi.c_yellow() if pct <= 85 else ansi.c_red())
    return f"{col}{'█'*fill}{ansi.c_dim()}{'░'*(width-fill)}{ansi.c_reset()}"


# ==========================================================
# 3. FITUR: DISK CHECK (Sudo needed)
# ==========================================================

def run_disk_check():
    if not shutil.which("smartctl"):
        ansi.print_brief_error("Butuh paket 'smartmontools'. Install dengan DNF/APT.")
        return 1

    ansi.print_system("STORAGE HEALTH & HISTORY LOG")
    if os.geteuid() != 0:
        print(f"{ansi.c_yellow()}Info: Gunakan 'sudo' agar Data Written terbaca & History berfungsi.{ansi.c_reset()}")

    ls = _sh("lsblk -d -o NAME,MODEL,SIZE,TYPE | grep disk")
    if not ls:
        print("Tidak ada disk fisik.")
        return 0

    for l in ls.splitlines():
        if "loop" in l: continue
        p = l.split()
        dev=p[0]; model=" ".join(p[1:])
        dev_path = f"/dev/{dev}"

        print(f"\n{ansi.tag(dev, ansi.c_cyan())} : {model}")

        # S.M.A.R.T
        res = _sh(f"sudo smartctl -A {dev_path}")
        temp="N/A"; write_gb=0.0; hrs="N/A"; has_data=False

        # Parsing NVMe / SATA Hybrid logic
        for line in res.splitlines():
            # Writes
            if "Data Units Written" in line:
                try: # "[5.5 TB]" format check
                    raw = line.split("[")[-1].split("]")[0]
                    num = float(raw.split()[0])
                    unit = raw.split()[1]
                    if "TB" in unit: write_gb = num * 1024
                    elif "GB" in unit: write_gb = num
                    elif "MB" in unit: write_gb = num / 1024
                    has_data = True
                except: pass

            if "Total_LBAs_Written" in line:
                try:
                    sectors = int(line.split()[-1])
                    write_gb = (sectors * 512) / (1024**3)
                    has_data = True
                except: pass

            # Temp
            if "Temperature" in line or "Celsius" in line:
                if "Airflow" in line: continue
                try: temp = line.split()[-1] + "°C"
                except: pass
            if "Temperature:" in line: # NVMe format
                try: temp = line.split(":")[-1].replace("Celsius","").strip() + "°C"
                except: pass

            # Hours
            if "Power On Hours" in line or "Power_On_Hours" in line:
                try: hrs = line.split(":")[-1].strip() if ":" in line else line.split()[-1]
                except: pass

        # Show Live
        print(f"  Temp Now      : {temp}")
        print(f"  Power On      : {hrs}")
        print(f"  Total Written : {write_gb:.2f} GB")

        if has_data:
            # === SAVE HISTORY ===
            # Create Unique ID (Model + Name)
            uniq_id = f"{dev}_{model.replace(' ','-')}"

            saved = snapshot_metric("disk", uniq_id, "tbw_gb", round(write_gb, 2))

            # === SHOW DIFF ===
            diff = get_comparison_text("disk", uniq_id, "tbw_gb", round(write_gb, 2), "GB")

            print(f"  {ansi.c_bold()}[ HISTORY LOG ]{ansi.c_reset()}")
            if diff:
                print(f"  • Growth Trend: {diff}")
            else:
                print(f"  • Growth Trend: - (Data awal baru direkam)")

            if saved:
                print(f"    {ansi.c_dim()}✓ Data hari ini berhasil direkam.{ansi.c_reset()}")

    return 0


# ==========================================================
# 4. FITUR: BATTERY CHECK (History + Health)
# ==========================================================

def run_battery_check():
    ansi.print_system("BATTERY INTELLIGENCE REPORT")
    p = Path("/sys/class/power_supply")
    batteries = list(p.glob("BAT*"))

    if not batteries:
        print("Sensor baterai tidak ditemukan.")
        return 0

    for b in batteries:
        model = (b/"model_name").read_text().strip() if (b/"model_name").exists() else b.name

        # Init
        path_f = b/"energy_full"
        path_d = b/"energy_full_design"
        unit = "Wh"
        div = 1e6

        # Fallback to charge (Ah)
        if not path_f.exists():
            path_f = b/"charge_full"
            path_d = b/"charge_full_design"
            unit = "Ah"

        if not path_f.exists():
            print(f"\n[{model}] Info kapasitas tidak tersedia di kernel.")
            continue

        full = float(path_f.read_text()) / div
        design = float(path_d.read_text()) / div
        health = (full / design) * 100.0
        cycle = (b/"cycle_count").read_text().strip() if (b/"cycle_count").exists() else "?"

        # Visual
        print(f"\n{ansi.tag(model, ansi.c_cyan())}")
        col = ansi.c_green()
        if health < 80: col=ansi.c_yellow()
        if health < 60: col=ansi.c_red()

        print(f"  Health State   : {col}{health:.2f}%{ansi.c_reset()} (Usable vs Design)")
        print(f"  Real Capacity  : {full:.2f} {unit}")
        print(f"  Design Capacity: {design:.2f} {unit}")
        print(f"  Cycles         : {cycle}")

        # === HISTORY SAVE ===
        s1 = snapshot_metric("battery", model, "health", round(health,2))
        s2 = snapshot_metric("battery", model, "cap_full", round(full,2))

        # === COMPARE ===
        d1 = get_comparison_text("battery", model, "health", round(health,2), "%")
        d2 = get_comparison_text("battery", model, "cap_full", round(full,2), unit)

        print(f"  {ansi.c_bold()}[ TRACKING LOG ]{ansi.c_reset()}")
        print(f"  • Degradasi    : {d1 if d1 else '-'}")
        print(f"  • Cap. Change  : {d2 if d2 else '-'}")

        if s1 or s2:
            print(f"    {ansi.c_dim()}✓ Snapshot disimpan.{ansi.c_reset()}")

    return 0


# ==========================================================
# 5. FITUR: NETWORK INTELLIGENCE
# ==========================================================

def get_wifi_chip(iface):
    try:
        # Check /sys/class/net/wlo1/device/driver -> symlink name
        dpath = Path(f"/sys/class/net/{iface}/device/driver")
        if dpath.exists():
            return os.path.basename(os.readlink(str(dpath)))
    except: pass
    return "Unknown"

def run_net_diag():
    ansi.print_system("NETWORK INTELLIGENCE & DNS CHECKER")

    # 1. Interface
    info = psutil.net_if_addrs()
    stat = psutil.net_if_stats()

    print(f"\n{ansi.c_bold()}[ LOCAL HARDWARE ]{ansi.c_reset()}")
    active = False
    for k, v in info.items():
        if k=="lo": continue
        is_up = "DOWN"
        if k in stat and stat[k].isup: is_up="UP"

        ip = "-"
        for a in v:
            if a.family == socket.AF_INET: ip = a.address

        chip = get_wifi_chip(k)
        drv_info = f"({chip})" if chip != "Unknown" else ""

        col = ansi.c_green() if is_up == "UP" else ansi.c_red()
        print(f"  • {ansi.c_cyan()}{k:<8}{ansi.c_reset()} : {col}{is_up:<4}{ansi.c_reset()} | {ip:<15} {ansi.c_dim()}{drv_info}{ansi.c_reset()}")
        active = True

    if not active: print("  No interface found.")

    # 2. Ping Check
    target="8.8.8.8"
    print(f"\n{ansi.c_bold()}[ CONNECTION QUALITY ]{ansi.c_reset()} Ping {target}...")
    try:
        res = _sh("ping -c 5 -i 0.2 8.8.8.8")
        times = []
        for l in res.splitlines():
            if "time=" in l:
                try: times.append(float(l.split("time=")[1].split()[0]))
                except: pass

        if times:
            avg = sum(times)/len(times)
            jit = max(times)-min(times)
            qc = ansi.c_green()+"PERFECT"
            if jit > 10: qc = ansi.c_yellow()+"GOOD"
            if jit > 50: qc = ansi.c_red()+"UNSTABLE"

            print(f"  Avg Latency    : {avg:.1f} ms")
            print(f"  Jitter (Noise) : {jit:.1f} ms ({qc}{ansi.c_reset()})")
        else:
            print(f"  Result         : {ansi.c_red()}TIMEOUT (RTO){ansi.c_reset()}")
    except: print("  Result         : Error execution.")

    # 3. DNS Resolve
    target_web = "google.com"
    print(f"\n{ansi.c_bold()}[ GLOBAL DNS RESOLVER ]{ansi.c_reset()}")
    try:
        ip = socket.gethostbyname(target_web)
        print(f"  DNS Local      : {ansi.c_green()}RESOLVED{ansi.c_reset()} ({target_web} -> {ip})")
        print(f"  Cek Global     : {ansi.c_cyan()}https://dnschecker.org/#A/{target_web}{ansi.c_reset()}")
    except:
        print(f"  DNS Local      : {ansi.c_red()}FAILED TO RESOLVE{ansi.c_reset()}")

    return 0

def run_net_live():
    """Live Scrolling Graph Ping."""
    input_muter = ansi.MuteInputDuringWait()
    target = "8.8.8.8"

    ansi.alt_screen_enter()
    ansi.cursor_hide()

    try:
        print(f"{ansi.c_bold()}{ansi.c_cyan()} LIVE LATENCY MONITOR {ansi.c_reset()} -> {target}")
        print(f"{ansi.c_dim()}(Ctrl+C to Stop){ansi.c_reset()}")
        print(f"{'TIMESTAMP':<12} {'LATENCY':<10} {'GRAPH REPRESENATION':<30}")
        print("-" * 60)

        with input_muter:
            while True:
                ts = datetime.datetime.now().strftime("%H:%M:%S")
                # One shot ping
                out = _sh(f"ping -c 1 -W 1 {target}")

                # Default display
                ms_txt = "TO"
                bar = f"{ansi.c_red()}X{ansi.c_reset()}"

                if "time=" in out:
                    try:
                        ms = float(out.split("time=")[1].split()[0])
                        ms_txt = f"{ms:.1f}ms"

                        # Graph logic
                        col = ansi.c_green()
                        if ms > 50: col = ansi.c_yellow()
                        if ms > 150: col = ansi.c_red()

                        cnt = int(ms / 10) # 1 char = 10ms
                        if cnt < 1: cnt=1
                        if cnt > 30: cnt=30
                        bar = col + ("█" * cnt) + ansi.c_reset()

                    except: pass

                print(f"{ts:<12} {ms_txt:<10} {bar}")
                time.sleep(1)

    except KeyboardInterrupt: pass
    finally:
        ansi.cursor_show()
        ansi.alt_screen_exit()
    return 0


# ==========================================================
# 6. LIVE COCKPIT (HUD UTAMA)
# ==========================================================

def run_live_cockpit():
    """HUD Dashboard Paling Lengkap."""
    input_muter = ansi.MuteInputDuringWait()
    net = NetSpeedometer()
    net.update()

    ansi.alt_screen_enter()
    ansi.cursor_hide()

    try:
        with input_muter:
            while True:
                # 1. Update Data
                cpu = psutil.cpu_percent(interval=None)
                ram = psutil.virtual_memory()
                temp = SysScanner.get_temps()
                upt = SysScanner.get_uptime_str()
                rpm = SysScanner.get_fan_rpm()
                pwr = SysScanner.get_power()
                net.update()

                # 2. Logic Smart Status Battery (Cut-off logic)
                batt = psutil.sensors_battery()
                lvl = int(batt.percent) if batt else 0
                plug = batt.power_plugged if batt else False
                st_raw = pwr["raw_st"]

                status_txt = "Unknown"
                c_bat = ansi.c_green()

                if not plug:
                    status_txt = "DC (On Battery)"
                    if lvl < 20: c_bat = ansi.c_red()
                else:
                    if st_raw == "Full" or lvl == 100:
                         status_txt = "AC (Full Charged)"
                    elif st_raw == "Not charging":
                         status_txt = "AC (Idle / Smart Protection)"
                         c_bat = ansi.c_cyan()
                    else:
                         status_txt = f"AC ({st_raw})"

                # 3. BACKGROUND HISTORY SAVE
                # Walaupun user cuma buka Live dashboard seharian,
                # kita tetap mau snapshot terekam diam-diam di background.
                if batt:
                    try:
                         # Hacky way re-detect model & values
                         # Agar simple, kita panggil helper internal tanpa print
                         path = Path("/sys/class/power_supply")
                         b_obj = list(path.glob("BAT*"))[0]
                         mn = (b_obj/"model_name").read_text().strip() if (b_obj/"model_name").exists() else b_obj.name
                         # Read Raw
                         f_p = b_obj/"energy_full" if (b_obj/"energy_full").exists() else b_obj/"charge_full"
                         d_p = b_obj/"energy_full_design" if (b_obj/"energy_full_design").exists() else b_obj/"charge_full_design"
                         val_f = float(f_p.read_text())/1e6
                         val_d = float(d_p.read_text())/1e6
                         pct = (val_f/val_d)*100

                         snapshot_metric("battery", mn, "health_pct", round(pct, 2))
                         snapshot_metric("battery", mn, "capacity", round(val_f, 2))
                    except: pass

                # 4. RENDER UI SCI-FI FULL
                now = datetime.datetime.now().strftime("%A, %d %b | %H:%M:%S")
                ansi.clear_screen()

                print(f"{ansi.c_cyan()}{ansi.c_bold()} SYSTEM COCKPIT {ansi.c_reset()}                   {ansi.c_dim()}AI-Terminal{ansi.c_reset()}")
                print(f" {ansi.c_dim()}{now.center(40)}{ansi.c_reset()}")
                print(f" {ansi.c_yellow()}UPTIME:{ansi.c_reset()} {upt}")
                print("-" * 55)

                # --- CORE HARDWARE ---
                # Bar: 20 chars
                bar_cpu = _draw_bar(cpu, 15)
                cpu_inf = f"{cpu:>4.1f}%"
                t_cpu = f"{temp['cpu']}°C"
                tdp_v = f"| TDP {pwr['tdp']}" if "N/A" not in pwr['tdp'] else ""

                print(f"{ansi.c_bold()} CPU  :{ansi.c_reset()} {bar_cpu} {cpu_inf} {t_cpu}{tdp_v} {rpm} RPM")

                bar_ram = _draw_bar(ram.percent, 15)
                ram_us = ram.used / (1024**3)
                ram_tot = ram.total / (1024**3)
                print(f"{ansi.c_bold()} RAM  :{ansi.c_reset()} {bar_ram} {ram.percent:>4.1f}% {ram_us:.1f}/{ram_tot:.1f} GB")

                print("-" * 55)

                # --- ELECTRICAL ---
                # Style: POWER: 80% [AC Status]
                print(f"{ansi.c_bold()} POWER:{ansi.c_reset()} {c_bat}{lvl}%{ansi.c_reset()} [{status_txt}]")

                # Detail flow precision
                w_str = f"{ansi.c_yellow()}{pwr['watt_str']:<8}{ansi.c_reset()}"
                v_str = pwr['volt_str']
                a_str = pwr['amp_str']
                print(f"        Flow : {w_str} @ {v_str} | Arus: {a_str}")

                # --- CONNECTIVITY ---
                print("-" * 55)
                t_ssd = f"{temp['ssd']}°C" if temp['ssd'] else "-"
                t_wifi = f"{temp['wifi']}°C" if temp['wifi'] else "-"

                rx_fmt = net.fmt(net.rx)
                tx_fmt = net.fmt(net.tx)

                print(f"{ansi.c_bold()} NVMe :{ansi.c_reset()} {t_ssd}           {ansi.c_bold()}WiFi:{ansi.c_reset()} {t_wifi}")
                print(f"{ansi.c_bold()} NET  :{ansi.c_reset()} ↓{rx_fmt}   ↑{tx_fmt}")

                print("\n" * 2)
                print(f"{ansi.c_dim()}Press [Ctrl+C] to Exit dashboard.{ansi.c_reset()}")

                time.sleep(1) # Render FPS 1s

    except KeyboardInterrupt:
        pass
    except Exception as e:
        # Fallback error catch supaya terminal ga hang
        ansi.alt_screen_exit()
        print(f"Dashboard Crash: {e}")
    finally:
        ansi.cursor_show()
        ansi.alt_screen_exit()
        print("Monitoring Closed.")

    return 0


# ==========================================================
# 7. ROUTING
# ==========================================================

def handle(argv: list[str], cfg: dict) -> int:
    # Parsing arg
    mode = "live"
    sub_arg = None
    if argv:
        mode = argv[0].lower().strip()
        if len(argv) > 1:
            sub_arg = argv[1].lower().strip()

    # -- ROUTER LOGIC --

    # 1. LIVE HUD
    if mode in ("live", "dashboard", "now"):
        return run_live_cockpit()

    # 2. NETWORK
    if mode in ("net", "wifi", "ping"):
        # Support: ai mon net live
        if sub_arg == "live":
            return run_net_live()
        return run_net_diag()

    # 3. DISK / STORAGE
    if mode in ("disk", "storage", "smart"):
        return run_disk_check()

    # 4. BATTERY
    if mode in ("batt", "battery", "power"):
        return run_battery_check()

    # 5. HELP
    if mode in ("help", "-h"):
        ansi.print_info("System Monitor (AI-TERM)")
        print("  ai mon live      : Dashboard System Sci-Fi HUD.")
        print("  ai mon batt      : Battery Health, History Log & Status.")
        print("  ai mon disk      : Storage S.M.A.R.T Analyzer & TBW Log.")
        print("  ai mon net       : Jitter Check, DNS, & Chipset Info.")
        print("  ai mon net live  : Real-time Ping Graph Visualization.")
        return 0

    ansi.print_brief_error(f"Mode monitor '{mode}' tidak dikenal.")
    print("Coba: live, net, disk, batt")
    return 2