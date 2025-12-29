"""
Advanced System Cockpit & Network Intelligence.

FEATURES:
1. Live Cockpit   : HUD Realtime (Optimized I/O).
2. Battery Logic  : Time Travel History, Consistent Metrics.
3. Storage Logic  : Filter zram/loop, Show Health %, Auto-Snapshot.
4. Net Logic      : Detailed Hardware ID, DNS Resolver, User Input Target.

Rules:
- Dependency 'psutil' is optional (graceful fail).
- History path via ai_logic.common.MON_HISTORY_PATH.
- Smart Filter (No zram junk).
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

# --- COMMON IMPORT ---
try:
    from ai_logic.common import MON_HISTORY_PATH
except ImportError:
    # Fallback local path jika common belum updated/tidak ketemu (Safety)
    MON_HISTORY_PATH = Path.cwd() / "workspace" / "mon_history.json"

from ai_logic.ui import ansi

# --- SOFT DEPENDENCY ---
psutil = None
try:
    import psutil
except ImportError:
    psutil = None


# ==========================================================
# 1. SMART HISTORY ENGINE
# ==========================================================

def _ensure_history_ready():
    p = Path(MON_HISTORY_PATH)
    if not p.parent.exists():
        try: p.parent.mkdir(parents=True, exist_ok=True)
        except: pass

def _load_db() -> dict:
    if not Path(MON_HISTORY_PATH).exists(): return {}
    try:
        with open(MON_HISTORY_PATH, 'r') as f: return json.load(f)
    except: return {}

def _save_db(data: dict):
    _ensure_history_ready()
    try:
        with open(MON_HISTORY_PATH, 'w') as f: json.dump(data, f, indent=2)
    except: pass

def snapshot_metric(category: str, item_id: str, metric: str, value: float) -> bool:
    """
    Logika: Append Only per Hari (Save First Check).
    Tidak overwrite data hari ini jika sudah ada, melainkan skip.
    """
    db = _load_db()
    today = datetime.date.today().isoformat()

    if category not in db: db[category] = {}
    if item_id not in db[category]: db[category][item_id] = {}
    if metric not in db[category][item_id]: db[category][item_id][metric] = {}

    # Save First Check: Kalau hari ini sudah ada, biarkan.
    if today in db[category][item_id][metric]:
        return False

    db[category][item_id][metric][today] = value
    _save_db(db)
    return True

def get_comparison_text(category: str, item_id: str, metric: str, current: float, unit: str="") -> str:
    db = _load_db()
    try:
        series = db.get(category, {}).get(item_id, {}).get(metric, {})
        if not series: return ""

        dates = sorted(series.keys())
        oldest_date = dates[0]

        if oldest_date == datetime.date.today().isoformat():
            return f"(Mulai hari ini)"

        old_val = series[oldest_date]
        diff = current - old_val

        days = (datetime.date.today() - datetime.date.fromisoformat(oldest_date)).days

        icon = "⚪"
        if diff > 0: icon = "📈+"
        if diff < 0: icon = "📉"

        return f"{ansi.c_dim()}vs {days}hr lalu: {old_val} -> {current} ({icon}{diff:.2f} {unit}){ansi.c_reset()}"
    except: return ""


# ==========================================================
# 2. HELPER (Shell & Hardware Info)
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
    def get_thermal() -> Dict[str, float]:
        d = {"cpu": 0.0, "wifi": 0.0, "ssd": 0.0}
        try:
            temps = psutil.sensors_temperatures()
            pkgs = temps.get("coretemp") or temps.get("k10temp") or []
            for s in pkgs:
                if "package" in s.label.lower() or "tctl" in s.label.lower():
                    d["cpu"] = s.current; break
            if not d["cpu"] and pkgs: d["cpu"] = pkgs[0].current

            for name, entries in temps.items():
                nl = name.lower()
                if "iwl" in nl or "wifi" in nl: d["wifi"] = entries[0].current
                if "nvme" in nl or "composite" in nl:
                    for e in entries:
                        if "composite" in e.label.lower() or not e.label: d["ssd"] = e.current; break
        except: pass
        return d

    @staticmethod
    def get_fan():
        m = 0
        try:
            fans = psutil.sensors_fans()
            for k,v in fans.items():
                for f in v:
                    if "cpu" in f.label.lower(): return f.current
                    if f.current > m: m=f.current
        except: pass
        return m

    @staticmethod
    def get_power():
        res = {"w_str":"0 mW", "v_str":"0 V", "a_str":"0 A", "raw":"Unknown", "tdp":"N/A"}
        path = Path("/sys/class/power_supply")
        batt = None
        for b in path.glob("BAT*"):
            try:
                st = (b/"status").read_text().strip()
                res["raw"] = st
                batt = b
                if st == "Discharging": break
            except: pass

        if batt:
            try:
                v = float((batt/"voltage_now").read_text()) / 1e6
                res["v_str"] = f"{v:.2f} V"

                pf = batt/"power_now" if (batt/"power_now").exists() else batt/"power_avg"
                if pf.exists():
                    p_u = float(pf.read_text())
                    p = p_u / 1e3 # mW
                    res["w_str"] = f"{int(p)} mW" if p < 1000 else f"{p/1e3:.2f} W"
                    if v > 0: res["a_str"] = f"{(p/1e3)/v:.3f} A"
            except: pass

        rapl = Path("/sys/class/powercap/intel-rapl/intel-rapl:0/constraint_0_power_limit_uw")
        if rapl.exists():
            try: res["tdp"] = f"{float(rapl.read_text())/1e6:.0f} W"
            except: pass
        return res

def _draw_bar(pct, w=15):
    if pct<0: pct=0
    if pct>100: pct=100
    fill = int(w*pct/100)
    col = ansi.c_green() if pct<=60 else (ansi.c_yellow() if pct<=85 else ansi.c_red())
    return f"{col}{'█'*fill}{ansi.c_dim()}{'░'*(w-fill)}{ansi.c_reset()}"


# ==========================================================
# 3. FEATURE HANDLERS
# ==========================================================

def run_disk_check():
    if not shutil.which("smartctl"):
        ansi.print_brief_error("Butuh 'smartmontools'. (sudo dnf install smartmontools)")
        return 1

    ansi.print_system("STORAGE HEALTH (INTELLIGENT MODE)")

    # 1. DETECT DISKS (Filter zram/loop/sr0)
    # Gunakan lsblk -e7 untuk exclude loop device default (7)
    ls = _sh("lsblk -d -o NAME,MODEL,SIZE,TYPE,TRAN | grep disk")
    if not ls:
        print("Tidak ada disk fisik yang terdeteksi.")
        return 0

    need_sudo_alert = False

    for l in ls.splitlines():
        if "zram" in l or "loop" in l or "sr" in l: continue

        parts = l.split()
        dev = parts[0]
        model = " ".join(parts[1:])
        path = f"/dev/{dev}"

        print(f"\n{ansi.tag(dev, ansi.c_cyan())} : {model}")

        # S.M.A.R.T via subprocess
        # Get Percentage Used (Health %)
        res_A = _sh(f"sudo smartctl -A {path}")
        res_H = _sh(f"sudo smartctl -H {path}")

        health_pct = "Unknown"
        health_color = ansi.c_dim()
        writes_gb = 0.0
        has_writes = False
        hrs = "N/A"

        # -- PARSING INTELLIGENCE --

        # 1. Hitung Health % (NVMe Standard)
        # Mencari "Percentage Used: 2%" -> Health = 98%
        for ln in res_A.splitlines():
            # NVMe logic
            if "Percentage Used" in ln:
                try:
                    used = int(ln.split(":")[-1].replace("%","").strip())
                    val_h = 100 - used
                    health_pct = f"{val_h}%"
                    health_color = ansi.c_green() if val_h > 80 else ansi.c_yellow()
                except: pass

            # TBW parsing (NVMe)
            if "Data Units Written" in ln:
                # "Data Units Written: ... [2.35 TB]"
                try:
                    if "[" in ln:
                        raw = ln.split("[")[-1].replace("]","").split()
                        num = float(raw[0])
                        unit = raw[1]
                        if "TB" in unit: writes_gb = num * 1024
                        elif "GB" in unit: writes_gb = num
                        has_writes = True
                except: pass

            if "Power On Hours" in ln or "Power_On_Hours" in ln:
                try: hrs = ln.split(":")[-1].strip() if ":" in ln else ln.split()[-1]
                except: pass

        # Fallback SATA Health logic (Check Status PASS/FAIL)
        if health_pct == "Unknown":
            if "PASSED" in res_H:
                health_pct = "Good (Passed)" # Tidak bisa % pasti tanpa attribut spesifik
                health_color = ansi.c_green()
            elif "FAILED" in res_H:
                health_pct = "BAD (Failed)"
                health_color = ansi.c_red()

        # SATA Writes (LBA)
        if not has_writes and "Total_LBAs_Written" in res_A:
            for ln in res_A.splitlines():
                if "Total_LBAs_Written" in ln:
                    try:
                        lba = int(ln.split()[-1])
                        writes_gb = (lba * 512) / (1024**3)
                        has_writes = True
                    except: pass

        # DISPLAY
        print(f"  Health State  : {health_color}{health_pct}{ansi.c_reset()}")
        print(f"  Total Written : {f'{writes_gb:.2f} GB' if has_writes else 'N/A'}")
        print(f"  Power On      : {hrs}")

        # TRACKING LOGIC
        if has_writes:
            uniq = f"{dev}_{model.replace(' ','_')}"

            did_snap = snapshot_metric("disk", uniq, "tbw_gb", round(writes_gb, 2))
            diff_txt = get_comparison_text("disk", uniq, "tbw_gb", round(writes_gb, 2), "GB")

            if diff_txt:
                print(f"  {ansi.c_bold()}[ HISTORY LOG ]{ansi.c_reset()} {diff_txt}")

            if did_snap:
                print(f"    {ansi.c_dim()}✓ Snapshot hari ini disimpan.{ansi.c_reset()}")

        if not has_writes and "Permission denied" in res_A:
            need_sudo_alert = True

    if need_sudo_alert:
        print(f"\n{ansi.c_yellow()}[!] Gunakan 'sudo' agar Data Written terbaca oleh system.{ansi.c_reset()}")

    return 0

def run_battery_check():
    ansi.print_system("BATTERY CHECK & HISTORY")
    p = Path("/sys/class/power_supply")
    batteries = list(p.glob("BAT*"))

    if not batteries:
        print("Sensor baterai tidak ditemukan.")
        return 0

    for b in batteries:
        mn = (b/"model_name").read_text().strip() if (b/"model_name").exists() else b.name

        # Init
        path_f=b/"energy_full"; path_d=b/"energy_full_design"; unit="Wh"
        if not path_f.exists():
            path_f=b/"charge_full"; path_d=b/"charge_full_design"; unit="Ah"

        if not path_f.exists(): continue # Skip unreadable

        full = float(path_f.read_text()) / 1e6
        design = float(path_d.read_text()) / 1e6
        health = (full/design)*100
        cyc = (b/"cycle_count").read_text().strip() if (b/"cycle_count").exists() else "?"

        print(f"\n{ansi.tag(mn, ansi.c_cyan())}")
        col = ansi.c_green() if health>80 else (ansi.c_yellow() if health>60 else ansi.c_red())
        print(f"  Health (%)    : {col}{health:.2f}%{ansi.c_reset()} (Usable/Design)")
        print(f"  Capacity      : {full:.2f} / {design:.2f} {unit} | Cycles: {cyc}")

        # Consistent Key with HUD
        k_hlt="health_pct"; k_cap="capacity_full"
        s1 = snapshot_metric("battery", mn, k_hlt, round(health,2))
        s2 = snapshot_metric("battery", mn, k_cap, round(full,2))

        c1 = get_comparison_text("battery", mn, k_hlt, round(health,2), "%")
        c2 = get_comparison_text("battery", mn, k_cap, round(full,2), unit)

        if c1 or c2:
            print(f"  {ansi.c_bold()}[ COMPARISON ]{ansi.c_reset()}")
            print(f"  • Health Diff : {c1}")
            print(f"  • Cap. Diff   : {c2}")
            if s1 or s2: print(f"    {ansi.c_dim()}✓ Data saved.{ansi.c_reset()}")

    return 0


# ==========================================================
# 4. ADVANCED NET HANDLER (Interactive & Chip ID)
# ==========================================================

def get_detailed_wifi_name(iface: str) -> str:
    """Try to get pretty name via lspci or fallback."""
    # Method 1: Get bus info link
    # /sys/class/net/wlo1/device -> ../../../0000:03:00.0
    try:
        dev_link = Path(f"/sys/class/net/{iface}/device")
        if not dev_link.exists(): return ""

        # Realpath -> extract last part (PCI Address)
        pci_addr = os.path.basename(os.readlink(str(dev_link)))

        # Use lspci -s <addr> if available
        # Butuh: lspci (pciutils)
        if shutil.which("lspci"):
            # Output: 03:00.0 Network controller: Intel Corporation Wi-Fi 6 AX210 ...
            out = _sh(f"lspci -s {pci_addr} -vmm")
            # Parse 'Device: ...'
            for l in out.splitlines():
                if l.startswith("Device:"):
                    return l.split("Device:")[1].strip()
    except: pass

    # Method 2: Uevent / modalias
    try:
        with open(f"/sys/class/net/{iface}/device/uevent") as f:
            for l in f:
                if l.startswith("PCI_ID="): return f"PCI: {l.strip()}"
    except: pass

    # Method 3: Simple driver name
    try:
        d = Path(f"/sys/class/net/{iface}/device/driver")
        return os.path.basename(os.readlink(str(d)))
    except: return "Generic"

def run_net_diag():
    # 1. Ask Domain
    default = "google.com"

    # Input interaction
    print(f"\n{ansi.c_cyan()}Enter target domain/IP{ansi.c_reset()} [{default}]: ", end="", flush=True)
    try:
        user_in = input().strip()
    except KeyboardInterrupt:
        print(); return 0

    target = user_in if user_in else default

    ansi.print_system(f"NETWORK DIAGNOSTICS -> {target}")

    # 2. Interfaces Details
    info = psutil.net_if_addrs()
    stat = psutil.net_if_stats()

    print(f"\n{ansi.c_bold()}[ HARDWARE ID ]{ansi.c_reset()}")
    active = False
    for iface, addr_list in info.items():
        if iface == "lo": continue

        is_up = "DOWN"
        if iface in stat and stat[iface].isup: is_up="UP"

        ip = "-"
        for a in addr_list:
            if a.family == socket.AF_INET: ip = a.address

        # Deep Hardware Name
        hw_name = get_detailed_wifi_name(iface)
        hw_fmt = f"\n    ↳ {ansi.c_dim()}{hw_name}{ansi.c_reset()}" if hw_name else ""

        col = ansi.c_green() if is_up == "UP" else ansi.c_red()
        print(f"  • {ansi.c_cyan()}{iface:<8}{ansi.c_reset()} : {col}{is_up:<4}{ansi.c_reset()} | IP: {ip}{hw_fmt}")
        active = True

    if not active: print("  (No active interfaces)")

    # 3. Ping
    print(f"\n{ansi.c_bold()}[ QUALITY CHECK ]{ansi.c_reset()} Ping {target}...")
    try:
        # Check resolve first
        ip_res = socket.gethostbyname(target)

        res = _sh(f"ping -c 5 -i 0.2 {target}")
        times = []
        for l in res.splitlines():
            if "time=" in l:
                try: times.append(float(l.split("time=")[1].split()[0]))
                except: pass

        if times:
            avg = sum(times)/len(times)
            jit = max(times)-min(times)
            qc = "EXCELLENT"
            c = ansi.c_green()
            if jit > 10: qc="GOOD"; c=ansi.c_yellow()
            if jit > 50: qc="UNSTABLE"; c=ansi.c_red()

            print(f"  Target IP    : {ip_res}")
            print(f"  Avg Latency  : {avg:.1f} ms")
            print(f"  Jitter       : {jit:.1f} ms ({c}{qc}{ansi.c_reset()})")
        else:
            print(f"  {ansi.c_red()}Request Timed Out (RTO){ansi.c_reset()}")
    except Exception as e:
        print(f"  {ansi.c_red()}Error:{ansi.c_reset()} Cannot resolve or reach host. {e}")

    # 4. Resolve & Link
    print(f"\n{ansi.c_bold()}[ DNS CHECK ]{ansi.c_reset()}")
    print(f"  Propagation Check: {ansi.c_cyan()}https://dnschecker.org/#A/{target}{ansi.c_reset()}\n")

    return 0

def run_net_live():
    """Scrolling ping graph."""
    # Use default google here or ask
    target = "google.com"
    input_muter = ansi.MuteInputDuringWait()
    ansi.alt_screen_enter()
    ansi.cursor_hide()
    try:
        print(f"{ansi.c_bold()}{ansi.c_cyan()} REALTIME NET {ansi.c_reset()} -> {target}")
        print(f"{ansi.c_dim()}(Ctrl+C to Stop){ansi.c_reset()}\n")

        with input_muter:
            while True:
                out = _sh(f"ping -c 1 -W 1 {target}")
                ms = 0
                if "time=" in out:
                    try: ms = float(out.split("time=")[1].split()[0])
                    except: pass

                # Visual
                tm = datetime.datetime.now().strftime("%H:%M:%S")
                if ms > 0:
                    bars = int(ms / 5) # 1 bar per 5ms
                    if bars > 40: bars=40
                    col = ansi.c_green()
                    if ms > 50: col = ansi.c_yellow()
                    if ms > 150: col = ansi.c_red()

                    visual = f"{col}{'█' * bars}{ansi.c_reset()} {ms}ms"
                    print(f"{tm} | {visual}")
                else:
                    print(f"{tm} | {ansi.c_red()}TIMEOUT{ansi.c_reset()}")

                time.sleep(0.5)
    except: pass
    finally:
        ansi.cursor_show()
        ansi.alt_screen_exit()
    return 0


# ==========================================================
# 5. LIVE COCKPIT (Optimized I/O)
# ==========================================================

def run_live_cockpit():
    input_muter = ansi.MuteInputDuringWait()
    net = NetSpeedometer()
    net.update()
    ansi.alt_screen_enter()
    ansi.cursor_hide()

    # Throttle Timer untuk Snapshot
    last_snap_time = 0
    SNAP_INTERVAL = 600 # 10 Menit sekali coba snapshot

    try:
        with input_muter:
            while True:
                # Update Sensors
                cpu = psutil.cpu_percent()
                ram = psutil.virtual_memory()
                temp = SysScanner.get_thermal()
                pwr = SysScanner.get_power()
                rpm = SysScanner.get_fan()
                net.update()

                # Update UI Batt
                batt = psutil.sensors_battery()
                lvl = int(batt.percent) if batt else 0
                plug = batt.power_plugged if batt else False
                st_raw = pwr["raw"]

                st_txt = "DC (On Battery)"; c_bat = ansi.c_green()
                if plug:
                    if st_raw=="Full" or lvl==100: st_txt="AC (Full)"
                    elif st_raw=="Not charging": st_txt="AC (Smart Cut-off)"; c_bat=ansi.c_cyan()
                    else: st_txt=f"AC ({st_raw})"
                else:
                    if lvl < 20: c_bat = ansi.c_red()

                # --- PERIODIC SNAPSHOT CHECK (Every 10 min) ---
                if time.time() - last_snap_time > SNAP_INTERVAL:
                    if batt:
                        # Logic Battery Background Save
                        # Sama seperti run_battery_check tapi silent
                        try:
                            path = Path("/sys/class/power_supply")
                            bs = list(path.glob("BAT*"))
                            if bs:
                                bf = bs[0]
                                f_p = bf/"energy_full" if (bf/"energy_full").exists() else bf/"charge_full"
                                d_p = bf/"energy_full_design" if (bf/"energy_full_design").exists() else bf/"charge_full_design"
                                v_f = float(f_p.read_text())/1e6
                                v_d = float(d_p.read_text())/1e6
                                h = (v_f/v_d)*100
                                m = (bf/"model_name").read_text().strip()

                                # Simpan dgn keys konsisten
                                snapshot_metric("battery", m, "health_pct", round(h, 2))
                                snapshot_metric("battery", m, "capacity_full", round(v_f, 2))
                        except: pass
                    last_snap_time = time.time()

                # Render UI
                now = datetime.datetime.now().strftime("%A | %H:%M:%S")
                ansi.clear_screen()
                print(f"{ansi.c_cyan()}{ansi.c_bold()} SYSTEM COCKPIT {ansi.c_reset()}             {ansi.c_dim()}AI-Terminal{ansi.c_reset()}")
                print(f" {ansi.c_dim()}{now.center(35)}{ansi.c_reset()}")
                print("-" * 50)

                print(f"{ansi.c_bold()} CPU  :{ansi.c_reset()} {_draw_bar(cpu)} {cpu:>3.0f}%  {temp['cpu']}°C  {rpm}rpm")
                print(f"{ansi.c_bold()} RAM  :{ansi.c_reset()} {_draw_bar(ram.percent)} {ram.percent:>3.0f}%  {ram.used>>30}/{ram.total>>30}GB")
                print("-" * 50)

                print(f"{ansi.c_bold()} BAT  :{ansi.c_reset()} {c_bat}{lvl}%{ansi.c_reset()} [{st_txt}]")
                print(f"        {ansi.c_yellow()}{pwr['w_str']}{ansi.c_reset()} @ {pwr['v_str']} | {pwr['a_str']}")

                print(f"{ansi.c_bold()} LINK :{ansi.c_reset()} NVMe:{temp['ssd']}°C  WiFi:{temp['wifi']}°C")
                print(f"        ↓{net.fmt(net.rx)}  ↑{net.fmt(net.tx)}")

                print(f"\n{ansi.c_dim()}[Ctrl+C] Exit.{ansi.c_reset()}")

                time.sleep(1)

    except: pass
    finally:
        ansi.cursor_show()
        ansi.alt_screen_exit()
    return 0


# ==========================================================
# 6. HELPERS
# ==========================================================
class NetSpeedometer:
    def __init__(self): self.t0=time.time(); self.i0=psutil.net_io_counters(); self.rx=0; self.tx=0
    def update(self):
        t1=time.time(); i1=psutil.net_io_counters(); dt=t1-self.t0
        if dt>0: self.rx=(i1.bytes_recv-self.i0.bytes_recv)/dt; self.tx=(i1.bytes_sent-self.i0.bytes_sent)/dt
        self.t0=t1; self.i0=i1
    def fmt(self,b):
        for u in ['B','K','M','G']:
            if b<1024: return f"{b:.1f}{u}/s"
            b/=1024
        return f"{b:.1f}G/s"


# ==========================================================
# ENTRY POINT
# ==========================================================

def handle(argv: list[str], cfg: dict) -> int:
    # 0. Dependency Check at Entry (Graceful)
    if psutil is None:
        ansi.print_brief_error("Fitur Monitoring butuh library 'psutil'.")
        print("Silakan install: sudo dnf install python3-psutil")
        return 1

    mode = argv[0].lower().strip() if argv else "live"
    sub_arg = argv[1].lower().strip() if len(argv)>1 else ""

    if mode in ("live", "dashboard", "now"): return run_live_cockpit()
    if mode in ("disk", "storage"): return run_disk_check()
    if mode in ("batt", "battery"): return run_battery_check()
    if mode in ("net", "wifi"):
        return run_net_live() if sub_arg == "live" else run_net_diag()

    if mode == "help":
        print("AI Monitor:")
        print("  ai mon live      : Realtime Dashboard.")
        print("  ai mon batt      : Battery Health & History.")
        print("  ai mon disk      : SSD/NVMe Health & Usage.")
        print("  ai mon net       : Net Diagnostic (Interactive).")
        print("  ai mon net live  : Scrolling Ping.")
        return 0

    ansi.print_brief_error(f"Mode '{mode}' tidak ada. Coba: live, batt, disk, net")
    return 2