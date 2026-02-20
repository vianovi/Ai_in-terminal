"""
commands/ask/ui.py
==================
Display & rendering layer untuk `ask` command.

Exported (dipanggil dari command.py):
    persona_label(backend)                              -> tuple[str, str]
    format_answer_block(backend, answer)                -> str
    print_help()                                        -> None
    print_chat_turn(label, backend, answer, plain)      -> None
    print_chat_header(mode, current_route, plain)       -> None

Exported (dipanggil dari modul eksternal, e.g. bridge.py):
    print_generated_block_header(backend)               -> None
    print_root_help_hint()                              -> None
"""
from __future__ import annotations

from system_logic.terminal.ansi import (
    c_bold,
    c_cyan,
    c_dim,
    c_reset,
    c_yellow,
    hr,
    print_info,
    tag,
    term_size,
    wrap,
)


# ============================================================
# Persona resolver
# ============================================================

def persona_label(backend: str) -> tuple[str, str]:
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
# Answer rendering
# ============================================================

def format_answer_block(backend: str, answer: str) -> str:
    """
    Format jawaban oneshot menjadi blok siap print dengan header persona.

    Args:
        backend : 'api' atau 'local'.
        answer  : Teks jawaban dari model.

    Returns:
        String terformat dengan newline trailing siap di-print.
    """
    cols, _ = term_size()
    label, emo = persona_label(backend)
    header = f"{c_bold()}{emo} {label}:{c_reset()}"
    body   = wrap((answer or "").strip(), width=min(cols, 110))
    return f"{header}\n{body}\n"


# ============================================================
# Help
# ============================================================

def print_help() -> None:
    """Tampilkan halaman bantuan untuk command `ask`."""
    cols, _ = term_size()
    w = min(cols, 100)
    lines: list[str] = [
        (
            f"{tag('SILI', c_cyan())} {tag('ASK', c_yellow())} "
            f"{c_dim()}•{c_reset()} ngobrol/oneshot di terminal"
        ),
        "",
        wrap("📌 Cara pakai:", width=w),
        wrap('  ask "pertanyaanmu"', width=w),
        wrap("  ask --chat", width=w),
        "",
        wrap("🎛️  Flags:", width=w),
        wrap("  --chat, -c   Masuk mode chat (alt-screen)", width=w),
        wrap("  --plain      Output polos (tanpa header/emoji) — cocok buat piping", width=w),
        wrap("  --help, -h   Tampilkan halaman ini", width=w),
        "",
        wrap("💡 Tips cepat:", width=w),
        wrap(
            "  Mode API = 'Sili Pinter 💍' (hangat & dewasa). "
            "Mode LOCAL = 'Sili AI 🌿' (teman lembut).",
            width=w,
        ),
        wrap(
            "  Keluar dari chat: ketik 'exit' atau 'keluar' (Ctrl+C juga bisa).",
            width=w,
        ),
    ]
    print("\n".join(lines))


# ============================================================
# Chat display
# ============================================================

def print_chat_turn(label: str, backend: str, answer: str, plain: bool) -> None:
    """
    Render satu turn jawaban di dalam chat loop.

    Args:
        label   : Nama persona (e.g. 'Sili Pinter').
        backend : 'api' atau 'local' — untuk emoji.
        answer  : Teks jawaban dari model.
        plain   : Jika True, output polos tanpa ANSI.
    """
    cols, _ = term_size()
    if plain:
        print(f"{label}: {answer}\n")
        return
    _, emo = persona_label(backend)
    print(f"{c_dim()}{hr(width=min(cols, 56))}{c_reset()}")
    print(f"{c_bold()}{emo} {label}:{c_reset()} {wrap(answer, width=min(cols, 110))}\n")


def print_chat_header(mode: str, current_route: str, plain: bool) -> None:
    """
    Render header di awal sesi chat (setelah clear screen).

    Args:
        mode         : Mode backend string (e.g. 'auto', 'api', 'local').
        current_route: Label route dari RouteInfo.label.
        plain        : Jika True, output polos.
    """
    from system_logic.terminal.ansi import clear_screen
    clear_screen()
    if plain:
        print(f"SILI CHAT • {current_route}\n")
        return
    print(
        f"{tag('SILI CHAT', c_cyan())} {tag(mode.upper(), c_yellow())} "
        f"{c_dim()}•{c_reset()} {current_route}"
    )
    print(f"{c_dim()}⌨️  exit/keluar untuk menutup sesi • Ctrl+C juga bisa{c_reset()}")
    print(
        f"{c_dim()}{'—' * 2} tips: jaga chat tetap jelas; "
        f"kalau butuh ringkas, bilang 'ringkas dong'{c_reset()}\n"
    )


# ============================================================
# Utility display (dipanggil dari modul eksternal / bridge)
# ============================================================

def print_generated_block_header(backend: str) -> None:
    """
    Render header blok 'hasil generate' — dipakai oleh modul eksternal
    (e.g. app/bridge.py) untuk menampilkan header sebelum blok output.

    Args:
        backend: 'api' atau 'local'.
    """
    cols, _ = term_size()
    label, emo = persona_label(backend)
    print(f"{c_bold()}{emo} {label}:{c_reset()} {c_dim()}(hasil generate){c_reset()}")
    print(f"{c_dim()}{hr(width=min(cols, 60))}{c_reset()}")


def print_root_help_hint() -> None:
    """
    Tampilkan hint singkat ke user saat command tidak dikenal.
    Dipakai oleh modul eksternal (e.g. app/bridge.py).
    """
    print_info("Coba: ./ai-term ask --help")