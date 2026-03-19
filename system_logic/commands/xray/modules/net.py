"""
commands/xray/modules/net.py
=============================
Modul Network Inspector untuk command `xray`.

Aturan isolasi:
    BOLEH import dari luar xray/:
        - stdlib saja (socket, subprocess)
        - third-party: psutil, python-nmap, dnspython (opsional, ada fallback)
    UI via xray/ui.py saja.
    TIDAK BOLEH import dari luar xray/ selain stdlib/third-party.

Subcommands:
    scan    → port_scan(target)       Port scan + service detection
    monitor → monitor_traffic()       Traffic stats per interface
    arp     → arp_scan()              Device aktif di jaringan lokal
    trace   → traceroute(target)      Traceroute visual
    lookup  → dns_lookup(target)      DNS + hostname resolution

Semua fungsi return dict. Key '_error' menandakan gagal.

Exported:
    port_scan(target)       -> dict
    monitor_traffic()       -> dict
    arp_scan()              -> dict
    traceroute(target)      -> dict
    dns_lookup(target)      -> dict
"""
from __future__ import annotations

import socket
import subprocess
from typing import Any

from system_logic.commands.xray.ui import print_xray_error, print_xray_info


# ============================================================
# [1] Internal helpers
# ============================================================

def _err(msg: str) -> dict[str, Any]:
    """
    Buat dict error standar.

    Args:
        msg: Pesan error.
    """
    print_xray_error(msg)
    return {"_error": msg}


def _human_size(size_bytes: int) -> str:
    """
    Convert bytes ke format human readable.

    Args:
        size_bytes: Ukuran dalam bytes.
    """
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes //= 1024
    return f"{size_bytes:.1f} TB"


def _common_port_service(port: int) -> str:
    """
    Return nama service untuk common ports (fallback tanpa nmap).

    Args:
        port: Nomor port.
    """
    _SERVICES: dict[int, str] = {
        21: "FTP", 22: "SSH", 23: "Telnet", 25: "SMTP",
        53: "DNS", 80: "HTTP", 110: "POP3", 143: "IMAP",
        443: "HTTPS", 445: "SMB", 3306: "MySQL",
        3389: "RDP", 5432: "PostgreSQL",
        8080: "HTTP-Alt", 8443: "HTTPS-Alt",
    }
    return _SERVICES.get(port, "unknown")


# ============================================================
# [2] Port scan
# ============================================================

def port_scan(target: str) -> dict[str, Any]:
    """
    Scan port terbuka pada host target.

    Strategi:
        1. Gunakan python-nmap jika tersedia (full scan port 1-1024).
        2. Fallback: socket scan untuk 15 common ports.

    Args:
        target: Hostname atau IP address.

    Returns:
        Dict berisi list port terbuka dan service. Key '_error' jika gagal.
    """
    target = target.strip()
    if not target:
        return _err("Target tidak boleh kosong.")

    result: dict[str, Any] = {
        "_module"    : "net",
        "_subcommand": "scan",
        "target"     : target,
    }

    # --- Resolve hostname ---
    try:
        ip = socket.gethostbyname(target)
        result["resolved_ip"] = ip
    except Exception:
        result["resolved_ip"] = "Gagal resolve"
        ip = target

    # --- Strategi 1: nmap ---
    try:
        import nmap
        nm = nmap.PortScanner()
        nm.scan(ip, "1-1024", arguments="-sV --open -T4")

        open_ports: list[dict] = []
        for proto in nm[ip].all_protocols():
            for port in sorted(nm[ip][proto].keys()):
                info = nm[ip][proto][port]
                if info["state"] == "open":
                    open_ports.append({
                        "port"   : port,
                        "proto"  : proto,
                        "service": info.get("name", "unknown"),
                        "version": info.get("version", ""),
                        "state"  : "open",
                    })

        result["scan_method"] = "nmap (1-1024)"
        result["open_ports"]  = open_ports
        result["total_open"]  = len(open_ports)

    except ImportError:
        # --- Strategi 2: socket scan common ports ---
        print_xray_info(
            "python-nmap tidak tersedia — fallback ke socket scan (common ports)."
        )
        _COMMON_PORTS = [
            21, 22, 23, 25, 53, 80, 110, 143,
            443, 445, 3306, 3389, 5432, 8080, 8443,
        ]
        open_ports = []
        for port in _COMMON_PORTS:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1)
                if sock.connect_ex((ip, port)) == 0:
                    open_ports.append({
                        "port"   : port,
                        "proto"  : "tcp",
                        "service": _common_port_service(port),
                        "state"  : "open",
                    })
                sock.close()
            except Exception:
                pass

        result["scan_method"] = "socket fallback (common ports)"
        result["open_ports"]  = open_ports
        result["total_open"]  = len(open_ports)
        result["note"]        = (
            "Install python-nmap untuk full scan: pip install python-nmap"
        )

    except Exception as ex:
        result["error"] = f"Scan gagal: {ex}"

    return result


# ============================================================
# [3] Monitor traffic
# ============================================================

def monitor_traffic() -> dict[str, Any]:
    """
    Monitor statistik traffic jaringan via psutil.

    Data yang dikumpulkan:
        - Stats per interface: bytes sent/recv, packets, errors
        - Active connections (ESTABLISHED, limit 20)
        - Total jumlah koneksi

    Returns:
        Dict berisi interface stats dan active connections.
    """
    try:
        import psutil
    except ImportError:
        return _err(
            "Library 'psutil' tidak terinstall. "
            "Jalankan: pip install psutil"
        )

    result: dict[str, Any] = {
        "_module"    : "net",
        "_subcommand": "monitor",
    }

    # --- Interface stats ---
    try:
        net_io     = psutil.net_io_counters(pernic=True)
        interfaces = {}
        for iface, stats in net_io.items():
            interfaces[iface] = {
                "bytes_sent"  : _human_size(stats.bytes_sent),
                "bytes_recv"  : _human_size(stats.bytes_recv),
                "packets_sent": stats.packets_sent,
                "packets_recv": stats.packets_recv,
                "errors_in"   : stats.errin,
                "errors_out"  : stats.errout,
            }
        result["interfaces"] = interfaces
    except Exception as ex:
        result["interfaces_error"] = str(ex)

    # --- Active connections ---
    try:
        connections = psutil.net_connections(kind="inet")
        active = [
            {
                "local" : f"{c.laddr.ip}:{c.laddr.port}" if c.laddr else "N/A",
                "remote": f"{c.raddr.ip}:{c.raddr.port}" if c.raddr else "N/A",
                "status": c.status,
                "pid"   : c.pid or "N/A",
            }
            for c in connections
            if c.status == "ESTABLISHED"
        ][:20]  # Limit 20

        result["active_connections"] = active
        result["total_connections"]  = len(connections)
    except Exception as ex:
        result["connections_error"] = str(ex)

    return result


# ============================================================
# [4] ARP scan
# ============================================================

def arp_scan() -> dict[str, Any]:
    """
    Deteksi device aktif di jaringan lokal via ARP table.
    Menggunakan command 'arp -n' yang tersedia di Linux.

    Returns:
        Dict berisi list device dengan IP dan MAC address.
    """
    result: dict[str, Any] = {
        "_module"    : "net",
        "_subcommand": "arp",
    }

    try:
        proc = subprocess.run(
            ["arp", "-n"],
            capture_output=True, text=True, timeout=10,
        )

        if proc.returncode == 0:
            devices: list[dict] = []
            lines = proc.stdout.strip().splitlines()

            for line in lines[1:]:  # Skip header
                parts = line.split()
                if len(parts) >= 3:
                    devices.append({
                        "ip"       : parts[0],
                        "mac"      : parts[2] if parts[2] != "(incomplete)" else "N/A",
                        "interface": parts[-1] if len(parts) > 3 else "N/A",
                    })

            result["devices"]       = devices
            result["total_devices"] = len(devices)
            result["scan_method"]   = "arp table"
        else:
            result["error"] = "arp command gagal"

    except FileNotFoundError:
        result["error"]      = "arp command tidak tersedia"
        result["suggestion"] = "Jalankan: sudo dnf install net-tools"
    except Exception as ex:
        result["error"] = str(ex)

    return result


# ============================================================
# [5] Traceroute
# ============================================================

def traceroute(target: str) -> dict[str, Any]:
    """
    Traceroute ke target — tampilkan jalur paket ke tujuan.
    Menggunakan command 'traceroute' sistem Linux.

    Args:
        target: Hostname atau IP address.

    Returns:
        Dict berisi hop-by-hop route. Key '_error' jika gagal.
    """
    target = target.strip()
    if not target:
        return _err("Target tidak boleh kosong.")

    result: dict[str, Any] = {
        "_module"    : "net",
        "_subcommand": "trace",
        "target"     : target,
    }

    try:
        proc = subprocess.run(
            ["traceroute", "-n", "-m", "20", target],
            capture_output=True, text=True, timeout=30,
        )

        if proc.stdout:
            hops = [
                line.strip()
                for line in proc.stdout.strip().splitlines()[1:]  # Skip header
                if line.strip()
            ]
            result["hops"]       = hops
            result["total_hops"] = len(hops)
        else:
            result["error"] = proc.stderr or "Traceroute tidak menghasilkan output"

    except FileNotFoundError:
        result["error"]      = "traceroute tidak terinstall"
        result["suggestion"] = "Jalankan: sudo dnf install traceroute"
    except subprocess.TimeoutExpired:
        result["error"] = "Traceroute timeout (>30 detik)"
    except Exception as ex:
        result["error"] = str(ex)

    return result


# ============================================================
# [6] DNS Lookup
# ============================================================

def dns_lookup(target: str) -> dict[str, Any]:
    """
    DNS dan hostname lookup untuk domain atau IP.

    Data yang dikumpulkan:
        - IP yang di-resolve
        - Hostname (reverse lookup)
        - DNS records: A, AAAA, MX, NS, TXT (via dnspython jika tersedia)

    Args:
        target: Domain atau IP address.

    Returns:
        Dict berisi DNS info. Key '_error' jika gagal.
    """
    target = target.strip()
    if not target:
        return _err("Target tidak boleh kosong.")

    result: dict[str, Any] = {
        "_module"    : "net",
        "_subcommand": "lookup",
        "target"     : target,
    }

    # --- Resolve & reverse ---
    try:
        ip = socket.gethostbyname(target)
        result["resolved_ip"] = ip
    except Exception:
        result["resolved_ip"] = "Gagal resolve"

    try:
        hostname, _, _ = socket.gethostbyaddr(result.get("resolved_ip", target))
        result["hostname"] = hostname
    except Exception:
        result["hostname"] = "N/A"

    # --- DNS Records via dnspython ---
    try:
        import dns.resolver
        for rtype in ("A", "AAAA", "MX", "NS", "TXT"):
            try:
                answers = dns.resolver.resolve(target, rtype, lifetime=5)
                result[f"dns_{rtype}"] = [str(r) for r in answers]
            except Exception:
                result[f"dns_{rtype}"] = []

    except ImportError:
        result["dns_note"] = (
            "dnspython tidak terinstall — DNS records tidak tersedia. "
            "Jalankan: pip install dnspython"
        )

    return result
