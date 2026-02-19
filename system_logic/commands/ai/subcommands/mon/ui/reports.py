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

# NEW IMPORTS for net live dashboard
from .render import align_columns
from ..collectors.network import PingMonitor, collect_network_stats, collect_wifi_stats
from ..utils import col_by_ping, col_by_dbm, run_cmd


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
            print(f"  Health        : {ansi.c_yellow()}N/A ⚠ {ansi.c_reset()}  {ansi.c_dim()}(smartctl tidak memberi output){ansi.c_reset()}")
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
        ansi.print_brief_error("psutil belum terpasang – MON tidak bisa jalan tanpa ini.")
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


def run_net_live_dashboard(args: list[str]) -> int:
    """
    Live ping monitoring dengan WiFi stats lengkap.

    Usage: ai mon net live [target] [--interval N] [--window N]
    """
    try:
        import psutil
    except ImportError:
        ansi.print_brief_error("Fitur monitoring butuh library 'psutil'.")
        print("Install (Fedora): sudo dnf install python3-psutil")
        return 1

    import sys
    import time

    # Parse arguments
    target = "google.com"
    interval = 1.0
    window = 60

    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("--interval", "-i") and i + 1 < len(args):
            try:
                interval = max(0.2, min(5.0, float(args[i + 1])))
            except ValueError:
                pass
            i += 2
        elif arg in ("--window", "-w") and i + 1 < len(args):
            try:
                window = max(20, min(300, int(args[i + 1])))
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
    class _NetSpeed:
        def __init__(self):
            self.rx = 0.0
            self.tx = 0.0
            self.prev = None
            self.prev_time = time.time()

        def update(self):
            stats = collect_network_stats()
            if not stats.get('ok'):
                return
            curr_time = time.time()
            dt = curr_time - self.prev_time
            if dt <= 0:
                return
            rx = stats.get('bytes_recv', 0)
            tx = stats.get('bytes_sent', 0)
            if self.prev:
                self.rx = (rx - self.prev[0]) / dt
                self.tx = (tx - self.prev[1]) / dt
            self.prev = (rx, tx)
            self.prev_time = curr_time

    ns = _NetSpeed()

    # Alt screen
    ansi.alt_screen_enter()
    ansi.cursor_hide()
    input_muter = ansi.MuteInputDuringWait()

    try:
        with input_muter:
            while True:
                cols, _ = ansi.term_size()
                w = min(cols, 120)

                # Get stats
                stats = ping.get_stats()
                wifi = _get_full_wifi_stats()
                ns.update()

                # Build output
                out = []
                sep = "─" * w

                # Header
                now = time.strftime("%A | %H:%M:%S")
                hdr = f"{ansi.c_cyan()}{ansi.c_bold()}AI MON {ansi.c_dim()}•{ansi.c_reset()} {ansi.c_cyan()}{ansi.c_bold()}NET LIVE{ansi.c_reset()}"
                out.append(align_columns(hdr, f"{ansi.c_dim()}{now}{ansi.c_reset()}", w))
                out.append(sep)

                # Target
                ip = stats.get('resolved_ip', target)
                tgt = f"{ansi.c_bold()}TARGET{ansi.c_reset()}  {target}"
                if ip != target:
                    tgt += f" {ansi.c_dim()}({ip}){ansi.c_reset()}"
                out.append(tgt)

                # Config
                cfg = f"{ansi.c_bold()}CONFIG{ansi.c_reset()}  interval: {interval}s    window: {window} samples"
                out.append(cfg)
                out.append(sep)

                # WiFi stats
                if wifi.get('ok'):
                    ssid = wifi.get('ssid', '?')
                    sig = wifi.get('signal_dbm')
                    sig_s = f"{sig:.0f} dBm" if sig else "N/A"
                    sig_c = col_by_dbm(sig) if sig else ansi.c_dim()
                    ch = wifi.get('channel_info', '')
                    temp = wifi.get('temperature')
                    temp_s = f"{ansi.c_dim()}Temp: {temp}°C{ansi.c_reset()}" if temp else ""

                    # Line 1: SSID + Signal + Channel
                    w1 = f"{ansi.c_bold()}WiFi{ansi.c_reset()}    {ssid} {sig_c}({sig_s}){ansi.c_reset()}  {ch}"
                    out.append(align_columns(w1, temp_s, w))

                    # Line 2: Link speeds
                    tx_r = wifi.get('tx_bitrate', 'N/A')
                    rx_r = wifi.get('rx_bitrate', 'N/A')
                    qual = wifi.get('quality')
                    qual_s = f"{ansi.c_dim()}Quality: {qual}%{ansi.c_reset()}" if qual else ""
                    lnk = f"{ansi.c_bold()}Link{ansi.c_reset()}    TX {ansi.c_green()}{tx_r}{ansi.c_reset()}  RX {ansi.c_green()}{rx_r}{ansi.c_reset()}"
                    out.append(align_columns(lnk, qual_s, w))

                    # Line 3: Traffic
                    rx_bps = _format_bitrate(ns.rx * 8)
                    tx_bps = _format_bitrate(ns.tx * 8)
                    beacon = wifi.get('beacon_loss')
                    beacon_s = f"{ansi.c_dim()}Beacon loss: {beacon}{ansi.c_reset()}" if beacon is not None and beacon >= 0 else ""
                    trf = f"{ansi.c_bold()}Traffic{ansi.c_reset()} ↓ {ansi.c_cyan()}{rx_bps}{ansi.c_reset()}    ↑ {ansi.c_yellow()}{tx_bps}{ansi.c_reset()}"
                    out.append(align_columns(trf, beacon_s, w))

                    out.append(sep)

                # Ping stats
                last = stats.get('last_ping')
                avg = stats.get('avg_ping')
                jitter = stats.get('jitter')
                loss = stats.get('loss_pct', 0.0)

                last_s = f"{last:.0f}ms" if last else "TO"
                avg_s = f"{avg:.0f}ms" if avg else "N/A"
                jit_s = f"{jitter:.0f}ms" if jitter else "N/A"
                loss_s = f"{loss:.0f}%"

                last_c = col_by_ping(last) if last else ansi.c_red()
                avg_c = col_by_ping(avg) if avg else ansi.c_dim()

                if jitter and jitter < 10:
                    jit_c = ansi.c_green()
                elif jitter and jitter < 50:
                    jit_c = ansi.c_yellow()
                else:
                    jit_c = ansi.c_red()

                if loss < 1:
                    loss_c = ansi.c_green()
                elif loss < 5:
                    loss_c = ansi.c_yellow()
                else:
                    loss_c = ansi.c_red()

                ping_line = (
                    f"{ansi.c_bold()}PING{ansi.c_reset()}    "
                    f"LAST {last_c}{last_s}{ansi.c_reset()}    "
                    f"AVG {avg_c}{avg_s}{ansi.c_reset()}    "
                    f"JITTER {jit_c}{jit_s}{ansi.c_reset()}    "
                    f"LOSS {loss_c}{loss_s}{ansi.c_reset()}"
                )
                out.append(ping_line)
                out.append(sep)

                # Graph
                history = stats.get('history', [])
                graph_lines = _draw_ping_graph(history, w)
                out.extend(graph_lines)

                # Legend
                legend = (
                    f"{ansi.c_green()}●{ansi.c_reset()} 0-50ms  "
                    f"{ansi.c_cyan()}●{ansi.c_reset()} 50-100ms  "
                    f"{ansi.c_yellow()}●{ansi.c_reset()} 100-200ms  "
                    f"{ansi.c_red()}●{ansi.c_reset()} >200ms  "
                    f"{ansi.c_red()}{ansi.c_bold()}×{ansi.c_reset()} timeout"
                )
                out.append(f"Legend: {legend}")
                out.append(sep)

                # Footer
                tip = f"{ansi.c_dim()}Ctrl+C untuk keluar.   Tip: 'ai mon net {target}' untuk diagnosis (dns + wifi).{ansi.c_reset()}"
                out.append(tip)

                # Render
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


def _get_full_wifi_stats() -> dict:
    """Get comprehensive WiFi stats via iw commands."""
    result = {
        'ok': False,
        'ssid': None,
        'signal_dbm': None,
        'tx_bitrate': 'N/A',
        'rx_bitrate': 'N/A',
        'channel_info': '',
        'quality': None,
        'temperature': None,
        'beacon_loss': None
    }

    # Get interface
    rc, out = run_cmd(["iw", "dev"], timeout=2)
    iface = None
    if rc == 0:
        for line in out.split('\n'):
            if 'Interface' in line:
                parts = line.strip().split()
                if len(parts) >= 2:
                    iface = parts[1]
                    break

    if not iface:
        return result

    result['ok'] = True

    # Get link info (signal, SSID)
    rc, out = run_cmd(["iw", "dev", iface, "link"], timeout=2)
    if rc == 0:
        for line in out.split('\n'):
            line = line.strip()
            if line.startswith('SSID:'):
                result['ssid'] = line.split(':', 1)[1].strip()
            elif 'signal:' in line:
                parts = line.split()
                for i, part in enumerate(parts):
                    if part == 'signal:' and i + 1 < len(parts):
                        try:
                            result['signal_dbm'] = float(parts[i + 1])
                        except ValueError:
                            pass

    # Get station dump (TX/RX bitrate, beacon loss)
    rc, out = run_cmd(["iw", "dev", iface, "station", "dump"], timeout=2)
    if rc == 0:
        for line in out.split('\n'):
            line = line.strip()
            if line.startswith('rx bitrate:'):
                result['rx_bitrate'] = line.split(':', 1)[1].strip()
            elif line.startswith('tx bitrate:'):
                result['tx_bitrate'] = line.split(':', 1)[1].strip()
            elif line.startswith('beacon loss:'):
                try:
                    result['beacon_loss'] = int(line.split(':', 1)[1].strip())
                except ValueError:
                    pass

    # Get channel info
    rc, out = run_cmd(["iw", "dev", iface, "info"], timeout=2)
    if rc == 0:
        channel = None
        freq = None
        width = None
        for line in out.split('\n'):
            line = line.strip()
            if line.startswith('channel'):
                parts = line.split()
                if len(parts) >= 2:
                    channel = parts[1]
            elif 'MHz' in line and 'width:' in line:
                if 'width:' in line:
                    width_part = line.split('width:', 1)[1].strip()
                    width = width_part.split()[0] + 'MHz'
                if '(' in line and 'MHz)' in line:
                    freq_str = line.split('(')[1].split('MHz')[0].strip()
                    try:
                        freq_mhz = int(freq_str)
                        band = "5GHz" if freq_mhz >= 5000 else "2.4GHz"
                        freq = band
                    except ValueError:
                        pass

        if channel and freq:
            result['channel_info'] = f"{ansi.c_dim()}{freq} Ch{channel}{ansi.c_reset()}"
            if width:
                result['channel_info'] += f"{ansi.c_dim()} {width}{ansi.c_reset()}"

    # Get quality from /proc/net/wireless
    try:
        with open('/proc/net/wireless', 'r') as f:
            lines = f.readlines()
            for line in lines:
                if iface in line:
                    parts = line.split()
                    if len(parts) >= 3:
                        qual_str = parts[2].rstrip('.')
                        try:
                            qual = float(qual_str)
                            result['quality'] = int((qual / 70.0) * 100)
                        except ValueError:
                            pass
    except Exception:
        pass

    # Get WiFi temperature (iwlwifi sensor)
    try:
        import psutil
        temps = psutil.sensors_temperatures()
        for key in temps:
            if 'iwl' in key.lower() or 'ath' in key.lower():
                entries = temps[key]
                if entries:
                    result['temperature'] = int(entries[0].current)
                    break
    except Exception:
        pass

    return result


def _format_bitrate(bps: float) -> str:
    """Format bitrate ke bps/Kbps/Mbps sesuai ukuran."""
    if bps < 1000:
        return f"{bps:.0f} bps"
    elif bps < 1_000_000:
        return f"{bps/1000:.1f} Kbps"
    else:
        return f"{bps/1_000_000:.1f} Mbps"


def _draw_ping_graph(history: list, width: int) -> list[str]:
    """Draw full-width ping graph dengan Y-axis dan colors."""
    if not history:
        return [f"{ansi.c_dim()}Waiting for data...{ansi.c_reset()}"]

    # Calculate visible data
    graph_width = width - 10  # Reserve for Y-axis labels
    visible = history[-graph_width:] if len(history) > graph_width else history

    # Get valid values for scaling
    valid = [v for v in visible if v is not None]
    if not valid:
        return [f"{ansi.c_dim()}No valid pings yet...{ansi.c_reset()}"]

    max_val = max(valid)

    # Dynamic scale
    if max_val < 50:
        scale_max = 50
    elif max_val < 100:
        scale_max = 100
    elif max_val < 200:
        scale_max = 200
    else:
        scale_max = int((max_val + 99) // 100 * 100)

    # Y-axis levels
    levels = [scale_max, scale_max*3//4, scale_max//2, scale_max//4, 0]
    graph_height = 6

    lines = []

    # Draw graph rows
    for row in range(graph_height):
        # Y-axis label
        level_idx = row * len(levels) // graph_height
        if level_idx < len(levels):
            label = f"{levels[level_idx]:>4.0f}ms"
        else:
            label = "     "

        # Line character
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
                # Timeout marker
                row_str += f"{ansi.c_red()}{ansi.c_bold()}×{ansi.c_reset()}"
            else:
                # Calculate position
                ratio = val / scale_max if scale_max > 0 else 0
                ratio = max(0.0, min(1.0, ratio))
                point_row = int((1.0 - ratio) * (graph_height - 1))

                if point_row == row:
                    # Color by value
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
    mid_space = max(0, (graph_width - 30) // 2)
    time_label = " " * 7 + "◄" + "─" * mid_space + " TIME (oldest ← newest) " + "─" * mid_space + "►"
    lines.append(time_label[:width])

    return lines


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