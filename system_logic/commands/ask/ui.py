from __future__ import annotations

from system_logic.terminal.ansi import (
    hr,
    wrap,
    print_info,
    tag,
    c_bold,
    c_cyan,
    c_dim,
    c_yellow,
    c_reset,
    term_size,
)


def persona_label(backend: str) -> tuple[str, str]:
    if backend == "api":
        return ("Sili Pinter", "💍")
    return ("Sili AI", "🌿")


def format_answer_block(backend: str, answer: str) -> str:
    cols, _ = term_size()
    label, emo = persona_label(backend)
    header = f"{c_bold()}{emo} {label}:{c_reset()}"
    body = wrap((answer or "").strip(), width=min(cols, 110))
    return f"{header}\n{body}\n"


def print_help() -> None:
    cols, _ = term_size()
    h = []
    h.append(f"{tag('SILI', c_cyan())} {tag('ASK', c_yellow())} {c_dim()}•{c_reset()} ngobrol/oneshot di terminal")
    h.append("")
    h.append(wrap("📌 Cara pakai:", width=min(cols, 100)))
    h.append(wrap("  ask \"pertanyaanmu\"", width=min(cols, 100)))
    h.append(wrap("  ask --chat", width=min(cols, 100)))
    h.append("")
    h.append(wrap("🎛️  Flags:", width=min(cols, 100)))
    h.append(wrap("  --chat, -c   Masuk mode chat (alt-screen)", width=min(cols, 100)))
    h.append(wrap("  --plain      Output polos (tanpa header/emoji) — cocok buat piping", width=min(cols, 100)))
    h.append(wrap("  --help, -h   Tampilkan help", width=min(cols, 100)))
    h.append("")
    h.append(wrap("💡 Tips cepat:", width=min(cols, 100)))
    h.append(wrap("  - Mode API = 'Sili Pinter' (hangat & dewasa). Mode LOCAL = 'Sili AI' (teman lembut).", width=min(cols, 100)))
    h.append(wrap("  - Keluar dari chat: ketik 'exit' atau 'keluar' (Ctrl+C juga bisa).", width=min(cols, 100)))
    print("\n".join(h))


def print_chat_turn(label: str, backend: str, answer: str, plain: bool) -> None:
    cols, _ = term_size()
    if plain:
        print(f"{label}: {answer}\n")
        return
    print(f"{c_dim()}{hr(width=min(cols, 56))}{c_reset()}")
    _, emo = persona_label(backend)
    print(f"{c_bold()}{emo} {label}:{c_reset()} {wrap(answer, width=min(cols, 110))}\n")


def print_chat_header(mode: str, current_route: str, plain: bool) -> None:
    from system_logic.terminal.ansi import clear_screen
    clear_screen()
    if plain:
        print(f"SILI CHAT • {current_route}\n")
        return
    print(f"{tag('SILI CHAT', c_cyan())} {tag(mode.upper(), c_yellow())} {c_dim()}•{c_reset()} {current_route}")
    print(f"{c_dim()}⌨️  exit/keluar untuk menutup sesi • Ctrl+C juga bisa{c_reset()}")
    print(f"{c_dim()}{'—' * 2} tips: jaga chat tetap jelas; kalau butuh ringkas, bilang 'ringkas dong'{c_reset()}\n")


def print_generated_block_header(backend: str) -> None:
    cols, _ = term_size()
    label, emo = persona_label(backend)
    print(f"{c_bold()}{emo} {label}:{c_reset()} {c_dim()}(hasil generate){c_reset()}")
    print(f"{c_dim()}{hr(width=min(cols, 60))}{c_reset()}")


def print_root_help_hint() -> None:
    print_info("Coba: ./ai-term ask --help")
