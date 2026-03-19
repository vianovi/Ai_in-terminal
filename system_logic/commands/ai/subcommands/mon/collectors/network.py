"""
MON Network Collector.
Membaca: Network I/O, WiFi signal, Ping monitoring.

Classes:
    WiFiReader   — Static wrapper untuk collect_wifi_stats() (pindahan dari dashboard.py)
    PingMonitor  — Background threading ping monitor

Functions:
    collect_network_stats()  — RX/TX bytes per interface
    collect_wifi_stats()     — SSID, signal strength via iw
"""

from __future__ import annotations

import socket
import threading
import time
from collections import deque
from typing import Dict, Any, Optional, List

from ..utils import run_cmd

try:
    import psutil  # type: ignore
except ImportError:
    psutil = None


# ==========================================================
# Network I/O
# ==========================================================

def collect_network_stats(iface: Optional[str] = None) -> Dict[str, Any]:
    """
    Membaca statistik network I/O.

    Args:
        iface: Interface spesifik (None = total semua interface)
    """
    if not psutil:
        return {"ok": False, "error": "psutil not available"}

    try:
        io_counters = psutil.net_io_counters(pernic=True)

        if iface:
            if iface in io_counters:
                stats = io_counters[iface]
                return {
                    "ok":           True,
                    "iface":        iface,
                    "bytes_sent":   stats.bytes_sent,
                    "bytes_recv":   stats.bytes_recv,
                    "packets_sent": stats.packets_sent,
                    "packets_recv": stats.packets_recv,
                    "errin":        stats.errin,
                    "errout":       stats.errout,
                    "dropin":       stats.dropin,
                    "dropout":      stats.dropout,
                }
            return {"ok": False, "error": f"Interface '{iface}' not found"}

        # Aggregate all interfaces
        total_sent = sum(s.bytes_sent for s in io_counters.values())
        total_recv = sum(s.bytes_recv for s in io_counters.values())
        return {
            "ok":         True,
            "iface":      "all",
            "bytes_sent": total_sent,
            "bytes_recv": total_recv,
        }

    except Exception as e:
        return {"ok": False, "error": str(e)}


# ==========================================================
# WiFi stats
# ==========================================================

def collect_wifi_stats(iface: Optional[str] = None) -> Dict[str, Any]:
    """
    Membaca WiFi signal strength menggunakan iw.

    Args:
        iface: Interface WiFi (auto-detect jika None)
    """
    # Auto-detect wireless interface
    if not iface:
        rc, out = run_cmd(["iw", "dev"], timeout=2)
        if rc == 0:
            for line in out.split("\n"):
                if "Interface" in line:
                    parts = line.strip().split()
                    if len(parts) >= 2:
                        iface = parts[1]
                        break

    if not iface:
        return {"ok": False, "error": "No wireless interface found"}

    rc, out = run_cmd(["iw", "dev", iface, "link"], timeout=2)
    if rc != 0:
        return {"ok": False, "error": "Failed to read WiFi stats"}

    ssid:       Optional[str]   = None
    signal_dbm: Optional[float] = None
    rx_bitrate: str = "N/A"
    tx_bitrate: str = "N/A"

    for line in out.split("\n"):
        line = line.strip()
        if line.startswith("SSID:"):
            ssid = line.split(":", 1)[1].strip()
        elif "signal:" in line:
            parts = line.split()
            for i, part in enumerate(parts):
                if part == "signal:" and i + 1 < len(parts):
                    try:
                        signal_dbm = float(parts[i + 1])
                    except ValueError:
                        pass

    # TX/RX bitrate via station dump
    rc2, out2 = run_cmd(["iw", "dev", iface, "station", "dump"], timeout=2)
    if rc2 == 0:
        for line in out2.split("\n"):
            line = line.strip()
            if line.startswith("rx bitrate:"):
                rx_bitrate = line.split(":", 1)[1].strip()
            elif line.startswith("tx bitrate:"):
                tx_bitrate = line.split(":", 1)[1].strip()

    return {
        "ok":         True,
        "iface":      iface,
        "ssid":       ssid,
        "signal_dbm": signal_dbm,
        "rx_bitrate": rx_bitrate,
        "tx_bitrate": tx_bitrate,
    }


# ==========================================================
# WiFiReader — static wrapper (pindahan dari dashboard.py)
# Dibutuhkan oleh reports.py
# ==========================================================

class WiFiReader:
    """
    Static wrapper untuk collect_wifi_stats().
    Dipindah dari ui/dashboard.py ke sini agar
    collectors menjadi satu-satunya sumber data network.

    Usage:
        w = WiFiReader.read()
        print(w.get('ssid'))
    """

    @staticmethod
    def read(iface: Optional[str] = None) -> Dict[str, Any]:
        """
        Get WiFi stats. Returns dict dengan keys:
            ok, iface, ssid, signal_dbm, rx_bitrate, tx_bitrate
        """
        return collect_wifi_stats(iface=iface)


# ==========================================================
# PingMonitor — background threading ping
# ==========================================================

class PingMonitor:
    """
    Background ping monitor menggunakan threading.
    Menyimpan history ping times untuk graph dan statistik.

    DNS Optimization: Resolve hostname HANYA SEKALI di awal.
    """

    def __init__(self, target: str = "8.8.8.8", window: int = 60):
        self.target   = target
        self.window   = window
        self.history:  deque = deque(maxlen=window)
        self.running   = False
        self.thread:   Optional[threading.Thread] = None
        self.last_ping: Optional[float] = None
        self.avg_ping:  Optional[float] = None
        self.jitter:    Optional[float] = None
        self.loss_count  = 0
        self.total_count = 0

        # DNS Optimization: Resolve ONCE at initialization
        self.resolved_ip: Optional[str] = None
        try:
            self.resolved_ip = socket.gethostbyname(target)
        except socket.gaierror:
            self.resolved_ip = target  # Fallback: assume already an IP

    def start(self) -> None:
        """Start background ping monitoring."""
        if self.running:
            return
        self.running = True
        self.thread  = threading.Thread(target=self._ping_loop, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        """Stop background monitoring."""
        self.running = False
        if self.thread:
            self.thread.join(timeout=2)

    def _ping_loop(self) -> None:
        """Internal ping loop."""
        while self.running:
            ping_ms = self._single_ping()

            self.total_count += 1
            if ping_ms is None:
                self.loss_count += 1
                self.history.append(None)
            else:
                self.last_ping = ping_ms
                self.history.append(ping_ms)

            valid_pings = [p for p in self.history if p is not None]
            if valid_pings:
                self.avg_ping = sum(valid_pings) / len(valid_pings)
                if len(valid_pings) > 1:
                    diffs = [
                        abs(valid_pings[i] - valid_pings[i - 1])
                        for i in range(1, len(valid_pings))
                    ]
                    self.jitter = sum(diffs) / len(diffs) if diffs else 0.0

            time.sleep(1)

    def _single_ping(self) -> Optional[float]:
        """
        Execute single ping, return RTT in milliseconds.
        Uses resolved IP — no repeated DNS lookups.
        Returns None on failure.
        """
        try:
            start = time.perf_counter()
            sock  = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2.0)
            try:
                sock.connect((self.resolved_ip, 80))
                return (time.perf_counter() - start) * 1000
            finally:
                sock.close()
        except Exception:
            return None

    def get_stats(self) -> Dict[str, Any]:
        """Get current ping statistics."""
        loss_pct = 0.0
        if self.total_count > 0:
            loss_pct = (self.loss_count / self.total_count) * 100

        return {
            "target":      self.target,
            "resolved_ip": self.resolved_ip,
            "last_ping":   self.last_ping,
            "avg_ping":    self.avg_ping,
            "jitter":      self.jitter,
            "loss_pct":    loss_pct,
            "history":     list(self.history),
            "total_count": self.total_count,
            "loss_count":  self.loss_count,
        }