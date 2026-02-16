"""
MON Static Reports.
Battery report, Disk SMART, Network diagnostics, Sensors dump.

ADAPTED for new MON structure with SQLite storage.
"""

from __future__ import annotations
from typing import List, Optional
import os

from system_logic.terminal import ansi
from system_logic.core.paths import MON_HISTORY_DB
from system_logic.core.storage import (
    list_metric_dates_sql,
    get_metric_value_sql,
)

# Import from sibling modules
from ..storage import get_comparison_text, list_metric_dates
from ..utils import human_bytes


def run_battery_report(args: List[str]) -> int:
    """Generate battery health report dengan history."""
    from ..collectors.hardware import PowerReader

    ansi.print_system("BATTERY INTELLIGENCE (HISTORY + HEALTH)")

    pr = PowerReader()
    cap = pr.read_capacity_health()
    live = pr.read()

    if not cap.get('ok') and not live.get('ok'):
        print("Sensor baterai tidak ditemukan.")
        return 0

    model = cap.get("model") or live.get("batt_name") or "BAT"
    model_id = str(model)

    print(f"\n{ansi.tag(model_id, ansi.c_cyan())}")

    # Main health summary
    if cap.get('ok'):
        health = float(cap["health_pct"])
        full = float(cap["full"])
        design = float(cap["design"])
        unit = str(cap["unit"])
        cyc = str(cap.get("cycle") or "?")

        col = ansi.c_green() if health >= 80 else (ansi.c_yellow() if health >= 60 else ansi.c_red())
        print(f"  Health        : {col}{health:.2f}%{ansi.c_reset()}  {ansi.c_dim()}(usable vs design){ansi.c_reset()}")
        print(f"  Capacity      : {full:.2f}/{design:.2f} {unit}   Cycles: {cyc}")

        # Time travel
        print(f"  {ansi.c_bold()}[ TIME TRAVEL ]{ansi.c_reset()}")

        base_h = get_comparison_text("battery", model_id, "health_pct", round(health, 2), "%", window_months=30)
        base_c = get_comparison_text("battery", model_id, "capacity_full", round(full, 2), unit, window_months=30)

        print(f"  • Health      : {base_h if base_h else '-'}")
        print(f"  • Capacity    : {base_c if base_c else '-'}")

        # Tracking stats
        dates_h = list_metric_dates("battery", model_id, "health_pct")
        if dates_h:
            first = dates_h[0]
            last = dates_h[-1]
            print(f"  {ansi.c_dim()}Tracking: {len(dates_h)} snapshots ({first} -> {last}){ansi.c_reset()}")

    else:
        print(f"  {ansi.c_yellow()}Info:{ansi.c_reset()} kapasitas/design tidak tersedia di sysfs, hanya tampilkan live-power.")

    # Live power
    if live.get('ok'):
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


def run_disk_report(args: List[str]) -> int:
    """Generate disk SMART report dengan TBW/health history."""
    use_sudo = "--sudo" in args or "--deep" in args

    ansi.print_system("STORAGE HEALTH (INTELLIGENT MODE)")

    if not shutil.which("smartctl"):
        ansi.print_brief_error("Butuh 'smartmontools'. Install: sudo dnf install smartmontools")
        return 1

    # Get disk list
    import shutil
    disks = _lsblk_disks()

    if not disks:
        print("Tidak ada disk fisik yang terdeteksi.")
        return 0

    if not use_sudo and os.geteuid() != 0:
        print(f"{ansi.c_yellow()}Info:{ansi.c_reset()} beberapa metrik (TBW/TBR/detail health) mungkin butuh sudo.")
        print(f"  Jalankan: {ansi.c_cyan()}sudo ai mon disk{ansi.c_reset()}  (untuk data lengkap)")

    for d in disks:
        name = d.get("name", "?")
        model = (d.get("model") or "").strip() or "-"
        size = d.get("size") or "-"
        tran = d.get("tran") or "-"
        dev = f"/dev/{name}"

        print(f"\n{ansi.tag(name, ansi.c_cyan())} {ansi.c_dim()}({size}, {tran}){ansi.c_reset()}")
        print(f"  Model         : {model}")

        # Run smartctl
        cmdA = f"smartctl -A {dev}"
        cmdH = f"smartctl -H {dev}"
        cmdI = f"smartctl -i {dev}"

        import subprocess

        def _sh(cmd: str) -> tuple[int, str]:
            try:
                p = subprocess.run(
                    cmd.split(),
                    capture_output=True,
                    text=True,
                    timeout=8
                )
                return p.returncode, p.stdout
            except Exception:
                return 1, ""

        codeA, outA = _sh(cmdA)
        codeH, outH = _sh(cmdH)
        codeI, outI = _sh(cmdI)

        need_priv = ("permission denied" in (outA or "").lower()) or codeA != 0
        if need_priv and use_sudo and os.geteuid() != 0:
            codeA, outA = _sh(f"sudo {cmdA}")
            codeH, outH = _sh(f"sudo {cmdH}")
            codeI, outI = _sh(f"sudo {cmdI}")

        if not outA and not outH:
            print(f"  Health        : {ansi.c_yellow()}N/A ⚠{ansi.c_reset()}  {ansi.c_dim()}(smartctl tidak memberi output){ansi.c_reset()}")
            continue

        parsed = _parse_smart_health(outA, outH)

        hc = parsed["health_color"]
        print(f"  Health        : {hc}{parsed['health_text']}{ansi.c_reset()}")
        print(f"  Temperature   : {parsed.get('temp_str') or 'N/A'}")
        print(f"  Power On      : {parsed.get('power_on') or 'N/A'}")

        tbw = parsed.get("tbw_gb") if parsed.get("has_tbw") else None
        tbr = parsed.get("tbr_gb") if parsed.get("has_tbr") else None

        if isinstance(tbw, (int, float)):
            print(f"  Total Written : {float(tbw):.2f} GB")
        else:
            print(f"  Total Written : {ansi.c_dim()}N/A{ansi.c_reset()}")

        if isinstance(tbr, (int, float)):
            print(f"  Total Read    : {float(tbr):.2f} GB")

    return 0


def run_network_diag(args: List[str]) -> int:
    """Network diagnostics: ping, DNS, WiFi."""
    from ..collectors.network import WiFiReader

    target = args[0] if args else "google.com"
    target = target.strip() or "google.com"

    ansi.print_system(f"NETWORK DIAGNOSTICS -> {target}")

    # WiFi
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

    # Ping test
    print(f"\n{ansi.c_bold()}[ CONNECTION QUALITY ]{ansi.c_reset()}")

    import subprocess
    try:
        p = subprocess.run(
            ["ping", "-c", "5", "-i", "0.2", target],
            capture_output=True,
            text=True,
            timeout=6
        )
        code, out = p.returncode, p.stdout
    except Exception:
        code, out = 1, ""

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
        print(f"  Result        : {ansi.c_red()}TIMEOUT/RTO ❌{ansi.c_reset()}")

    return 0


def run_sensors_dump() -> int:
    """Dump semua sensor yang bisa dibaca (discovery mode)."""
    ansi.print_system("SENSORS INVENTORY (FULL DISCOVERY MODE)")

    try:
        import psutil
    except ImportError:
        ansi.print_brief_error("psutil belum terpasang — MON tidak bisa jalan tanpa ini.")
        print("Install Fedora: sudo dnf install python3-psutil")
        return 1

    # Temperature keys
    print(f"\n{ansi.c_bold()}[ TEMPERATURE KEYS ]{ansi.c_reset()}")
    try:
        temps = psutil.sensors_temperatures(fahrenheit=False) or {}
        keys = sorted(list(temps.keys()))
        if not keys:
            print("  (tidak ada / tidak didukung)")
        else:
            print(f"  Keys: {', '.join(keys)}")
            for k in keys:
                entries = temps.get(k, [])
                print(f"\n  {ansi.c_cyan()}{k}{ansi.c_reset()}")
                if not entries:
                    print("    (no entries)")
                    continue
                for e in entries[:16]:
                    lab = (getattr(e, "label", "") or "-").strip()
                    cur = getattr(e, "current", None)
                    hi = getattr(e, "high", None)
                    cr = getattr(e, "critical", None)
                    print(f"    - {lab:<18} cur={cur}°C  high={hi}  crit={cr}")
    except Exception:
        print("  (error reading temperatures)")

    # Fan keys
    print(f"\n{ansi.c_bold()}[ FAN KEYS ]{ansi.c_reset()}")
    try:
        fans = psutil.sensors_fans() or {}
        fkeys = sorted(list(fans.keys()))
        if not fkeys:
            print("  (tidak ada / tidak didukung)")
        else:
            print(f"  Keys: {', '.join(fkeys)}")
            for k in fkeys:
                entries = fans.get(k, [])
                print(f"\n  {ansi.c_cyan()}{k}{ansi.c_reset()}")
                if not entries:
                    print("    (no entries)")
                    continue
                for e in entries[:16]:
                    lab = (getattr(e, "label", "") or "-").strip()
                    cur = getattr(e, "current", None)
                    print(f"    - {lab:<18} rpm={cur}")
    except Exception:
        print("  (error reading fans)")

    # Battery
    print(f"\n{ansi.c_bold()}[ BATTERY ]{ansi.c_reset()}")
    try:
        b = psutil.sensors_battery()
        if b:
            print(f"  Percent: {getattr(b, 'percent', 0)}%")
            print(f"  Plugged: {getattr(b, 'power_plugged', False)}")
            secs = getattr(b, 'secsleft', None)
            if secs and secs > 0:
                h = secs // 3600
                m = (secs % 3600) // 60
                print(f"  Time left: {h}h {m}m")
        else:
            print("  (no battery)")
    except Exception:
        print("  (error reading battery)")

    print(f"\n{ansi.c_dim()}Tip:{ansi.c_reset()} HUD paling berguna: CPU package, NVMe composite, watt/volt/amp, swap, disk active%+R/W")

    return 0


# Helper functions
def _lsblk_disks() -> list[dict[str, str]]:
    """Get disk list via lsblk."""
    import shutil
    import subprocess
    import json

    if not shutil.which("lsblk"):
        return []

    try:
        p = subprocess.run(
            ["lsblk", "-J", "-d", "-o", "NAME,MODEL,SIZE,TYPE,TRAN,ROTA"],
            capture_output=True,
            text=True,
            timeout=3
        )
        if p.returncode != 0 or not p.stdout:
            return []

        d = json.loads(p.stdout)
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


def _parse_smart_health(smart_a: str, smart_h: str) -> dict:
    """Parse smartctl output."""
    import os
    out: dict = {
        "health_text": "Unknown",
        "health_color": ansi.c_dim(),
        "health_pct": None,
        "temp_c": None,
        "temp_str": "N/A",
        "power_on": "N/A",
        "tbw_gb": None,
        "tbr_gb": None,
        "has_tbw": False,
        "has_tbr": False,
    }

    # Parse temperature
    for ln in (smart_a or "").splitlines():
        s = ln.strip()
        if s.startswith("Temperature:") and "Celsius" in s:
            try:
                toks = s.split()
                for tok in toks:
                    if tok.lstrip("-").isdigit():
                        out["temp_c"] = float(tok)
                        out["temp_str"] = f"{int(float(tok))}°C"
                        break
            except Exception:
                pass

    # Parse health from -H output
    if "PASSED" in (smart_h or ""):
        out["health_text"] = "PASSED"
        out["health_color"] = ansi.c_green()
    elif "FAILED" in (smart_h or ""):
        out["health_text"] = "FAILED"
        out["health_color"] = ansi.c_red()

    return out