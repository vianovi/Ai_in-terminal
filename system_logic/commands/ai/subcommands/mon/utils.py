"""
MON Utilities (Helper Functions).
Menangani eksekusi shell aman, formatting string, I/O file, dan ANSI helpers.
Digunakan oleh: Sentinel, Collectors, dan UI.
"""

import re
import shlex
import shutil
import subprocess
import datetime as _dt
from pathlib import Path
from typing import Optional, List, Tuple, Union

# Import UI colors dari framework utama
from system_logic.terminal import ansi

# Regex untuk menghapus kode warna ANSI (agar perhitungan panjang string akurat)
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


# ==========================================================
# 1. SHELL & EXECUTION (CRITICAL FOR SENTINEL)
# ==========================================================

def run_cmd(argv: List[str], timeout: int = 10) -> Tuple[int, str]:
    """
    Menjalankan perintah subprocess secara aman (shell=False).
    Digunakan oleh Sentinel untuk kill process dan Collectors untuk baca sensor.

    Returns:
        (returncode, stdout_stripped)
    """
    try:
        # Capture output, text mode, ignore stderr (kecuali debug)
        p = subprocess.run(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=timeout,
            check=False,
        )
        return int(p.returncode), (p.stdout or "").strip()
    except subprocess.TimeoutExpired:
        return 124, ""  # Standard timeout exit code
    except Exception:
        return 1, ""

def sh_cmd(cmd: str, timeout: int = 10) -> Tuple[int, str]:
    """
    Helper untuk string command. Otomatis split menggunakan shlex.
    Contoh: sh_cmd("kill -9 1234")
    """
    try:
        argv = shlex.split(cmd)
    except Exception:
        return 1, ""

    if not argv:
        return 1, ""

    return run_cmd(argv, timeout=timeout)


# ==========================================================
# 2. FILE I/O (SAFE READERS)
# ==========================================================

def read_text(p: Path) -> str:
    """Membaca isi file text (sysfs/procfs) dengan aman."""
    try:
        return p.read_text(encoding="utf-8", errors="replace").strip()
    except Exception:
        return ""

def read_int(p: Path) -> Optional[int]:
    """Membaca file dan mengonversi ke int (berguna untuk /sys/class/...)."""
    try:
        s = read_text(p)
        return int(s) if s else None
    except Exception:
        return None

def read_float(p: Path) -> Optional[float]:
    """Membaca file dan mengonversi ke float."""
    try:
        s = read_text(p)
        return float(s) if s else None
    except Exception:
        return None


# ==========================================================
# 3. STRING & UI FORMATTING
# ==========================================================

def vis_len(s: str) -> int:
    """Menghitung panjang string yang terlihat (mengabaikan kode warna ANSI)."""
    return len(_ANSI_RE.sub("", s))

def align_lr(left: str, right: str, width: int) -> str:
    """
    Format baris 'Task Manager': Teks Kiri ........ Teks Kanan.
    Memastikan padding pas sesuai lebar terminal.
    """
    w = max(20, int(width))
    l = vis_len(left)
    r = vis_len(right)

    # Jika terlalu panjang, potong bagian kiri
    if l + 1 + r >= w:
        keep = max(5, w - (r + 1))
        raw = _ANSI_RE.sub("", left)
        if len(raw) > keep:
            # Potong string mentah (tanpa ansi) lalu tambah ellipsis
            # Note: Ini simplifikasi, memotong string ber-ANSI rumit.
            # Kita asumsikan 'left' labelnya aman dipotong.
            left_clean = raw[: max(2, keep - 1)] + "…"
            return left_clean + " " + right
        return left + " " + right # Fallback

    padding = " " * (w - (l + r))
    return left + padding + right

def now_ts() -> str:
    """Timestamp untuk header dashboard."""
    return _dt.datetime.now().strftime("%A | %H:%M:%S")

def trim_name(s: str, maxlen: int) -> str:
    """Memotong nama proses/disk agar tidak merusak tabel."""
    s = (s or "").strip()
    maxlen = int(max(4, maxlen))
    if len(s) <= maxlen:
        return s
    return s[: max(2, maxlen - 1)] + "…"

def trim_tail(s: str, maxlen: int) -> str:
    """Memotong path (mountpoint) dengan menyisakan bagian akhir."""
    s = s or ""
    maxlen = int(max(8, maxlen))
    if len(s) <= maxlen:
        return s
    return "…" + s[-(maxlen - 1):]


# ==========================================================
# 4. MATH & VISUALIZATION
# ==========================================================

def clamp(n: float, lo: float, hi: float) -> float:
    """Membatasi nilai n di antara lo dan hi."""
    if n < lo: return lo
    if n > hi: return hi
    return n

def human_bytes(n: float) -> str:
    """Konversi bytes ke KB, MB, GB, TB."""
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    v = float(max(0.0, n))
    for u in units:
        if v < 1024.0:
            return f"{v:.1f}{u}"
        v /= 1024.0
    return f"{v:.1f}PB"

def human_rate_bps(bps: float) -> str:
    """Konversi bytes/sec."""
    return human_bytes(bps) + "/s"

def draw_bar(pct: float, width: int = 14) -> str:
    """
    Membuat bar chart text: [████░░░]
    Warna otomatis berubah (Green -> Yellow -> Red).
    """
    pct = clamp(float(pct), 0.0, 100.0)
    fill = int(width * pct / 100.0)

    # Default coloring logic (bisa di-override config nanti)
    if pct <= 60:
        col = ansi.c_green()
    elif pct <= 85:
        col = ansi.c_yellow()
    else:
        col = ansi.c_red()

    bar = "█" * fill
    empty = "░" * (width - fill)
    return f"{col}{bar}{ansi.c_dim()}{empty}{ansi.c_reset()}"


# ==========================================================
# 5. DEFAULT COLOR LOGIC (FALLBACK)
# ==========================================================
# Fungsi ini digunakan jika Config belum dimuat atau untuk display standar.

def col_by_ping(ms: Optional[float]) -> str:
    if ms is None: return ansi.c_dim()
    if ms <= 40: return ansi.c_green()
    if ms <= 120: return ansi.c_yellow()
    return ansi.c_red()

def col_by_dbm(dbm: Optional[float]) -> str:
    if dbm is None: return ansi.c_dim()
    if dbm >= -55: return ansi.c_green()
    if dbm >= -67: return ansi.c_yellow()
    return ansi.c_red()

def col_by_temp(v: Optional[float]) -> str:
    if v is None: return ansi.c_dim()
    if v < 70: return ansi.c_green()
    if v < 85: return ansi.c_yellow()
    return ansi.c_red()