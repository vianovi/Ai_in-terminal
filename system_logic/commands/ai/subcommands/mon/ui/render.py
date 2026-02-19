"""
MON UI Render Components.
Menangani visualisasi grafik batang (Bar Charts) dan grafik garis (Sparklines).
Fokus: Estetika (Color-coded) dan Kejelasan Informasi.
"""

import math
from typing import Optional, List, Dict

# Import library warna UI (Pastikan path ini sesuai dengan project-mu)
# Fallback ke path standar project jika path kustom tidak ditemukan
try:
    from system_logic.terminal import ansi
except ImportError:
    try:
        from ai_logic.ui import ansi
    except ImportError:
        # Fallback dummy class jika library ANSI benar-benar tidak ada (Anti-Crash)
        class ansi:
            @staticmethod
            def c_dim(): return ""
            @staticmethod
            def c_reset(): return ""
            @staticmethod
            def c_green(): return ""
            @staticmethod
            def c_yellow(): return ""
            @staticmethod
            def c_red(): return ""
            @staticmethod
            def c_cyan(): return ""


def draw_bar(pct: float, width: int = 14, limits: Optional[Dict[str, int]] = None) -> str:
    """
    Membuat bar chart text horisontal dengan pewarnaan dinamis.
    Style: [██████░░░] 60%
    """
    # 1. Clamp percentage 0-100
    val = max(0.0, min(100.0, float(pct or 0.0)))

    # 2. Hitung jumlah blok isi
    fill = int(width * val / 100.0)

    # 3. Tentukan batas threshold warna
    if limits:
        warn = limits.get('warn', 70)
        crit = limits.get('crit', 90)
    else:
        warn, crit = 70, 90

    # 4. Pilih warna
    if val <= warn:
        col = ansi.c_green()
    elif val <= crit:
        col = ansi.c_yellow()
    else:
        col = ansi.c_red()

    # 5. Render String
    # Menggunakan '█' agar terlihat solid/tinggi
    bar_filled = "█" * fill
    # Menggunakan '░' atau '·' sebagai track kosong yang samar
    bar_empty = "░" * (width - fill)

    return f"{col}{bar_filled}{ansi.c_dim()}{bar_empty}{ansi.c_reset()}"


def draw_sparkline(values: List[Optional[float]], width: int = 40, height: int = 1, limits: Optional[Dict[str, int]] = None) -> List[str]:
    """
    Generate ASCII/Unicode sparkline berwarna untuk data series.
    Fitur:
    - Handling 'None' sebagai Timeout ('x' Merah).
    - Auto-scaling tinggi grafik.
    - Pewarnaan per-titik data (bukan satu warna untuk seluruh grafik).
    """
    if not values:
        return [" " * width]

    # Ambil data sejumlah lebar grafik (dari yang paling baru)
    visible_data = values[-width:]

    # Kumpulkan nilai numerik yang valid (bukan None) untuk hitung skala
    valid_vals = [v for v in visible_data if v is not None]

    # Default values untuk display kosong
    current_lines = []

    # --- BLOCK CHARACTERS (Height levels 0-7) ---
    blocks = [" ", "▂", "▃", "▄", "▅", "▆", "▇", "█"]

    # Thresholds default untuk warna latency (ping ms)
    # Jika nilainya kecil = Bagus (Green), Besar = Jelek (Red)
    # Kebalikan dari RAM/CPU.
    # Kita asumsikan default use-case adalah Net Latency kecuali limit dikirim khusus.
    ping_mode = True
    if limits is None:
        limits = {'warn': 100, 'crit': 200}
    else:
        ping_mode = False # Jika user kirim limit sendiri, pakai logika value > limit = merah

    # Skala: Cari max value agar grafik tidak 'gepeng' atau 'terpotong'
    # Jika ping rendah stabil, scale max minimal 10ms agar noise kecil tidak terlihat besar
    local_min = min(valid_vals) if valid_vals else 0
    local_max = max(valid_vals) if valid_vals else 100
    scale_range = max(1.0, local_max - 0) # Base-0 scaling sering lebih mudah dibaca untuk ping

    line_str = ""

    for val in visible_data:
        # A) HANDLE TIMEOUT / DATA NULL
        if val is None:
            # Tanda 'x' tebal merah sesuai request
            line_str += f"{ansi.c_red()}×{ansi.c_reset()}"
            continue

        # B) PEWARNAAN DINAMIS (Per Point)
        if ping_mode:
            # Logic Ping: Makin kecil makin hijau
            if val < 50:
                color = ansi.c_green()   # Cepat (<50ms)
            elif val < 100:
                color = ansi.c_cyan()    # Normal (<100ms) - Biru muda
            elif val < limits['crit']:
                color = ansi.c_yellow()  # Agak lag
            else:
                color = ansi.c_red()     # Lag parah
        else:
            # Logic Umum (Load/RAM): Makin kecil makin aman
            if val < limits.get('warn', 70):
                color = ansi.c_green()
            elif val < limits.get('crit', 90):
                color = ansi.c_yellow()
            else:
                color = ansi.c_red()

        # C) KALKULASI TINGGI BLOK
        # Normalize 0..1 relative terhadap window max
        ratio = (val - 0) / scale_range
        # Clamp ratio
        ratio = max(0.0, min(1.0, ratio))

        block_idx = int(ratio * (len(blocks) - 1))
        char = blocks[block_idx]

        line_str += f"{color}{char}{ansi.c_reset()}"

    # Pad bagian kiri jika data belum memenuhi lebar layar
    padding = width - len(visible_data)
    if padding > 0:
        line_str = (" " * padding) + line_str

    # Return list (karena struktur lama me-return list of strings)
    return [line_str]


def color_by_value(value: Optional[float], thresholds: dict) -> str:
    """
    Return ANSI color code berdasarkan nilai.
    Berguna untuk mewarnai teks angka (misal: "Ping: 200ms").
    """
    if value is None:
        return ansi.c_dim()

    # Threshold default (Logika Standard: High is Bad)
    low = thresholds.get('low', 0)
    warn = thresholds.get('warn', 70)
    crit = thresholds.get('crit', 90)

    if value <= low:
        return ansi.c_dim()
    elif value <= warn:
        return ansi.c_green()
    elif value <= crit:
        return ansi.c_yellow()
    else:
        return ansi.c_red()


def format_uptime(seconds: Optional[int]) -> str:
    """Format uptime seconds ke string pendek: '2d 4h 12m'."""
    if seconds is None:
        return "n/a"

    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    mins = (seconds % 3600) // 60

    parts = []
    if days > 0: parts.append(f"{days}d")
    if hours > 0: parts.append(f"{hours}h")
    if mins > 0 or not parts: parts.append(f"{mins}m")

    return " ".join(parts[:2]) # Ambil 2 komponen terbesar saja agar ringkas


def trim_string(s: str, maxlen: int, position: str = "end") -> str:
    """Safe string trimmer (Ellipsis handler)."""
    s = s or ""
    if len(s) <= maxlen:
        return s

    ellipsis = "…"
    cut_len = max(1, maxlen - 1)

    if position == "start":
        return ellipsis + s[-cut_len:]
    elif position == "middle":
        half = cut_len // 2
        return s[:half] + ellipsis + s[-(cut_len - half):]
    else: # end
        return s[:cut_len] + ellipsis


def align_columns(left: str, right: str, width: int, fill_char: str = " ") -> str:
    """
    Membuat layout dua kolom (kiri-kanan) yang rapi
    meskipun ada kode warna ANSI yang tidak terlihat.
    """
    import re
    # Regex untuk membuang kode warna saat menghitung panjang string visible
    ansi_re = re.compile(r"\x1b\[[0-9;]*m")

    left_vis = len(ansi_re.sub("", left))
    right_vis = len(ansi_re.sub("", right))

    available = width - left_vis - right_vis
    if available < 1:
        # Jika sempit, korbankan teks kiri (truncate)
        # Note: Ini logika sederhana, truncate string berwarna itu kompleks.
        # Kita potong teks 'display' secara kasar untuk safety.
        return left + " " + right

    return left + (fill_char * available) + right