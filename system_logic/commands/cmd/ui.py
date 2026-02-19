"""
commands/cmd/ui.py
==================
Display & rendering layer untuk `cmd` command.

Exported:
    print_help()                                          -> None
    render_blocks(purpose, command, risk, backend)        -> None

Internal (tidak diexport):
    _persona(backend)  -> tuple[str, str]
"""
from __future__ import annotations

from system_logic.terminal.ansi import (
    c_bold,
    c_cyan,
    c_dim,
    c_reset,
    c_yellow,
    hr,
    tag,
    term_size,
    wrap,
)


# ============================================================
# Internal: Persona resolver
# ============================================================

def _persona(backend: str) -> tuple[str, str]:
    """
    Kembalikan (nama, emoji) berdasarkan backend aktif.

    Args:
        backend: 'api' atau 'local' (nilai lain fallback ke local).

    Returns:
        Tuple (nama_display, emoji).
            'api'   -> ('Sili Pinter', '💍')
            'local' -> ('Sili AI',     '🌿')
    """
    if backend == "api":
        return ("Sili Pinter", "💍")
    return ("Sili AI", "🌿")


# ============================================================
# Public: Help
# ============================================================

def print_help() -> None:
    """Tampilkan halaman bantuan untuk command `cmd`."""
    cols, _ = term_size()
    w = min(cols, 100)

    lines: list[str] = [
        (
            f"{tag('SILI', c_cyan())} {tag('CMD', c_yellow())} "
            f"{c_dim()}•{c_reset()} "
            "Generate 1 command Linux yang aman + siap dieksekusi"
        ),
        "",
        wrap("📌 Cara pakai:", width=w),
        wrap('  cmd "apa yang kamu mau lakukan"', width=w),
        "",
        wrap("🎛️  Flags:", width=w),
        wrap("  --help, -h   Tampilkan halaman ini", width=w),
        "",
        wrap("🧠 Output yang dihasilkan:", width=w),
        wrap("  Tujuan   — penjelasan singkat apa yang dilakukan command", width=w),
        wrap("  Command  — satu baris siap pakai", width=w),
        wrap("  Risiko   — 1–3 kalimat tentang efek samping / bahaya", width=w),
        "",
        wrap("✅ Aksi setelah generate:", width=w),
        wrap("  run  / r  — Langsung jalankan di shell", width=w),
        wrap("  copy / c  — Print command untuk dicopy", width=w),
        wrap("  edit / e  — Edit command sebelum dijalankan", width=w),
        wrap("  cancel    — Batalkan tanpa melakukan apapun", width=w),
    ]
    print("\n".join(lines))


# ============================================================
# Public: Result renderer
# ============================================================

def render_blocks(purpose: str, command: str, risk: str, backend: str) -> None:
    """
    Render hasil generate ke terminal dalam format blok yang rapi.

    Args:
        purpose : Tujuan / penjelasan command (dari model).
        command : Command satu baris yang siap dijalankan (dari model).
        risk    : Penjelasan risiko singkat (dari model).
        backend : 'api' atau 'local' — menentukan persona yang ditampilkan.
    """
    cols, _ = term_size()
    w       = min(cols, 110)
    name, emo = _persona(backend)

    print(f"\n{c_bold()}{emo} {name}:{c_reset()} {c_dim()}(hasil generate){c_reset()}")
    print(f"{c_dim()}{hr(width=min(cols, 60))}{c_reset()}")

    if purpose:
        print(f"{c_bold()}🎯 Tujuan  :{c_reset()} {wrap(purpose, width=w)}")

    print(f"{c_bold()}🧾 Command :{c_reset()}")
    print(f"   {wrap(command if command else '(kosong — lihat pesan error di atas)', width=w)}")

    risk_text = risk if risk else "Risiko tidak dijelaskan."
    print(f"{c_bold()}⚠️  Risiko  :{c_reset()} {wrap(risk_text, width=w)}")