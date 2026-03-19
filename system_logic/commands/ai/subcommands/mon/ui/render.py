"""
MON UI Render Components.
Menangani visualisasi: bar charts, sparklines, ping graph, speed tracker.

Semua visual component yang dipakai oleh LEBIH DARI SATU modul UI
dikumpulkan di sini agar tidak ada duplikasi kode.

Functions:
    draw_bar()           — Colored horizontal bar chart
    draw_sparkline()     — ASCII/Unicode sparkline graph
    draw_ping_graph()    — Full ping graph dengan Y-axis (pindahan dari dashboard+reports)
    color_by_value()     — ANSI color berdasarkan nilai
    format_uptime()      — Format seconds → "2d 4h 12m"
    trim_string()        — Safe string truncation dengan ellipsis
    align_columns()      — Layout dua kolom kiri-kanan

Classes:
    NetSpeedTracker      — Track RX/TX speed (merge dari dashboard._NetSpeedTracker
                           dan reports._NetSpeed yang identik)
"""

from __future__ import annotations

import re
import time
from typing import Optional, List, Dict, Any

from system_logic.terminal import ansi
from ..collectors.network import collect_network_stats

# Regex ANSI untuk hitung visible length
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


# ==========================================================
# Bar chart
# ==========================================================

def draw_bar(
    pct: float,
    width: int = 14,
    limits: Optional[Dict[str, int]] = None,
) -> str:
    """
    Membuat bar chart text horisontal dengan pewarnaan dinamis.
    Style: ██████░░░

    Args:
        pct:    Nilai 0–100
        width:  Lebar bar dalam karakter
        limits: Dict opsional {"warn": int, "crit": int}
    """
    val  = max(0.0, min(100.0, float(pct or 0.0)))
    fill = int(width * val / 100.0)

    warn = limits.get("warn", 70) if limits else 70
    crit = limits.get("crit", 90) if limits else 90

    if val <= warn:
        col = ansi.c_green()
    elif val <= crit:
        col = ansi.c_yellow()
    else:
        col = ansi.c_red()

    return f"{col}{'█' * fill}{ansi.c_dim()}{'░' * (width - fill)}{ansi.c_reset()}"


# ==========================================================
# Sparkline
# ==========================================================

def draw_sparkline(
    values: List[Optional[float]],
    width: int = 40,
    height: int = 1,
    limits: Optional[Dict[str, int]] = None,
) -> List[str]:
    """
    Generate ASCII/Unicode sparkline berwarna untuk data series.

    Args:
        values:  List nilai (None = timeout/missing)
        width:   Lebar grafik dalam karakter
        limits:  {"warn": int, "crit": int} — None = ping mode (lower is better)
    """
    if not values:
        return [" " * width]

    visible_data = values[-width:]
    valid_vals   = [v for v in visible_data if v is not None]

    blocks    = [" ", "▂", "▃", "▄", "▅", "▆", "▇", "█"]
    ping_mode = limits is None

    if limits is None:
        limits = {"warn": 100, "crit": 200}

    local_max   = max(valid_vals) if valid_vals else 100
    scale_range = max(1.0, local_max)
    line_str    = ""

    for val in visible_data:
        if val is None:
            line_str += f"{ansi.c_red()}×{ansi.c_reset()}"
            continue

        if ping_mode:
            if val < 50:
                color = ansi.c_green()
            elif val < 100:
                color = ansi.c_cyan()
            elif val < limits["crit"]:
                color = ansi.c_yellow()
            else:
                color = ansi.c_red()
        else:
            if val < limits.get("warn", 70):
                color = ansi.c_green()
            elif val < limits.get("crit", 90):
                color = ansi.c_yellow()
            else:
                color = ansi.c_red()

        ratio     = max(0.0, min(1.0, val / scale_range))
        block_idx = int(ratio * (len(blocks) - 1))
        line_str += f"{color}{blocks[block_idx]}{ansi.c_reset()}"

    padding = width - len(visible_data)
    if padding > 0:
        line_str = (" " * padding) + line_str

    return [line_str]


# ==========================================================
# Ping graph — SINGLE SOURCE OF TRUTH
# (Sebelumnya duplikat di dashboard.py DAN reports.py)
# ==========================================================

def draw_ping_graph(
    history: List[Optional[float]],
    width: int = 80,
) -> List[str]:
    """
    Draw ping graph dengan Y-axis labels dan full width.
    Ini adalah SATU-SATUNYA definisi fungsi ini — tidak ada duplikat.

    Sebelumnya ada di:
        - ui/dashboard.py  (_draw_ping_graph)
        - ui/reports.py    (_draw_ping_graph)

    Args:
        history: List ping values (None = timeout)
        width:   Total lebar area render

    Returns:
        List of strings (lines) untuk di-print/append ke output.
    """
    if not history:
        return [f"{ansi.c_dim()}Waiting for data...{ansi.c_reset()}"]

    graph_width = max(10, width - 10)  # Reserve 10 chars untuk Y-axis
    visible     = history[-graph_width:] if len(history) > graph_width else history

    valid = [v for v in visible if v is not None]
    if not valid:
        return [f"{ansi.c_dim()}No valid pings yet...{ansi.c_reset()}"]

    max_val = max(valid)

    # Round scale ke angka yang rapi
    if max_val < 50:
        scale_max = 50
    elif max_val < 100:
        scale_max = 100
    elif max_val < 200:
        scale_max = 200
    elif max_val < 500:
        scale_max = 500
    else:
        scale_max = int((max_val + 99) // 100 * 100)

    levels       = [scale_max, scale_max * 3 // 4, scale_max // 2, scale_max // 4, 0]
    graph_height = 6
    lines        = []

    for row in range(graph_height):
        level_idx = row * len(levels) // graph_height
        label     = f"{levels[level_idx]:>5.0f}ms" if level_idx < len(levels) else "      "
        line_char = "┤" if row == 0 else ("└" if row == graph_height - 1 else "│")

        row_str = f"{label} {line_char}"

        for val in visible:
            if val is None:
                row_str += f"{ansi.c_red()}×{ansi.c_reset()}"
            else:
                ratio     = (val / scale_max) if scale_max > 0 else 0
                ratio     = max(0.0, min(1.0, ratio))
                point_row = int((1.0 - ratio) * (graph_height - 1))

                if point_row == row:
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
    lines.append("   0ms " + "└" + "─" * graph_width)

    # Time label
    mid = max(0, (graph_width - 35) // 2)
    lines.append(
        " " * 7
        + f"◄{'─' * mid} TIME (oldest ← newest) {'─' * mid}►"
    )

    return lines


# ==========================================================
# NetSpeedTracker — SINGLE SOURCE OF TRUTH
# (Merge dari dashboard.NetSpeedometer DAN reports._NetSpeed
#  yang logicnya identik)
# ==========================================================

class NetSpeedTracker:
    """
    Track RX/TX network speed (bytes/sec).

    Ini adalah merge dari dua class yang sebelumnya identik:
        - ui/dashboard.py  → NetSpeedometer / _NetSpeedTracker
        - ui/reports.py    → _NetSpeed (inner class)

    Usage:
        tracker = NetSpeedTracker()
        tracker.update()
        print(tracker.rx, tracker.tx)  # bytes/sec
    """

    def __init__(self, iface: Optional[str] = None) -> None:
        self.iface      = iface
        self.rx         = 0.0   # bytes/sec received
        self.tx         = 0.0   # bytes/sec sent
        self._prev:     Optional[Dict[str, int]] = None
        self._prev_time = time.time()

    def update(self) -> None:
        """Baca network stats terbaru dan hitung speed."""
        stats = collect_network_stats(iface=self.iface)
        if not stats.get("ok"):
            return

        curr_time = time.time()
        dt        = curr_time - self._prev_time
        if dt <= 0:
            return

        curr_rx = int(stats.get("bytes_recv", 0))
        curr_tx = int(stats.get("bytes_sent", 0))

        if self._prev:
            self.rx = (curr_rx - self._prev["rx"]) / dt
            self.tx = (curr_tx - self._prev["tx"]) / dt

        self._prev      = {"rx": curr_rx, "tx": curr_tx}
        self._prev_time = curr_time


# ==========================================================
# Color helpers
# ==========================================================

def color_by_value(value: Optional[float], thresholds: dict) -> str:
    """Return ANSI color berdasarkan nilai vs thresholds."""
    if value is None:
        return ansi.c_dim()

    low  = thresholds.get("low",  0)
    warn = thresholds.get("warn", 70)
    crit = thresholds.get("crit", 90)

    if value <= low:
        return ansi.c_dim()
    elif value <= warn:
        return ansi.c_green()
    elif value <= crit:
        return ansi.c_yellow()
    else:
        return ansi.c_red()


# ==========================================================
# String formatting helpers
# ==========================================================

def format_uptime(seconds: Optional[int]) -> str:
    """Format uptime seconds → '2d 4h 12m'."""
    if seconds is None:
        return "n/a"
    days  = seconds // 86400
    hours = (seconds % 86400) // 3600
    mins  = (seconds % 3600) // 60

    parts = []
    if days  > 0:              parts.append(f"{days}d")
    if hours > 0:              parts.append(f"{hours}h")
    if mins  > 0 or not parts: parts.append(f"{mins}m")
    return " ".join(parts[:2])


def trim_string(s: str, maxlen: int, position: str = "end") -> str:
    """Safe string trimmer dengan ellipsis."""
    s       = s or ""
    cut_len = max(1, maxlen - 1)
    if len(s) <= maxlen:
        return s
    if position == "start":
        return "…" + s[-cut_len:]
    elif position == "middle":
        half = cut_len // 2
        return s[:half] + "…" + s[-(cut_len - half):]
    return s[:cut_len] + "…"


def align_columns(
    left:      str,
    right:     str,
    width:     int,
    fill_char: str = " ",
) -> str:
    """
    Layout dua kolom kiri-kanan yang rapi,
    memperhitungkan ANSI escape codes saat menghitung panjang.
    """
    left_vis  = len(_ANSI_RE.sub("", left))
    right_vis = len(_ANSI_RE.sub("", right))
    available = width - left_vis - right_vis

    if available < 1:
        return left + " " + right

    return left + (fill_char * available) + right