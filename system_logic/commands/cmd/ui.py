from __future__ import annotations

from system_logic.terminal.ansi import (
    print_info,
    print_warn,
    tag,
    wrap,
    term_size,
    hr,
    c_bold,
    c_cyan,
    c_dim,
    c_green,
    c_red,
    c_reset,
    c_yellow,
)


def persona_label(backend: str) -> tuple[str, str]:
    return ("Sili Pinter", "💍") if backend == "api" else ("Sili AI", "🌿")


def print_help() -> None:
    cols, _ = term_size()
    h: list[str] = []
    h.append(f"{tag('SILI', c_cyan())} {tag('CMD', c_yellow())} {c_dim()}•{c_reset()} bikin 1 command yang aman + siap dieksekusi")
    h.append("")
    h.append(wrap("📌 Cara pakai:", width=min(cols, 100)))
    h.append(wrap("  cmd \"apa yang kamu mau\"", width=min(cols, 100)))
    h.append("")
    h.append(wrap("🎛️  Flags:", width=min(cols, 100)))
    h.append(wrap("  --help, -h   Tampilkan help", width=min(cols, 100)))
    h.append("")
    h.append(wrap("🧠 Output:", width=min(cols, 100)))
    h.append(wrap("  - Tujuan (singkat)", width=min(cols, 100)))
    h.append(wrap("  - Command (1 baris)", width=min(cols, 100)))
    h.append(wrap("  - Risiko (1–3 kalimat)", width=min(cols, 100)))
    h.append("")
    h.append(wrap("✅ Aksi cepat setelah itu: Run / Copy / Edit / Cancel", width=min(cols, 100)))
    print("\n".join(h))


def render_blocks(cmd: str, risk: str, purpose: str, backend: str) -> None:
    cols, _ = term_size()
    w = min(cols, 110)
    label, emo = persona_label(backend)
    print(f"{c_bold()}{emo} {label}:{c_reset()} {c_dim()}(hasil generate){c_reset()}")
    print(f"{c_dim()}{hr(width=min(cols, 60))}{c_reset()}")

    if purpose:
        print(f"{c_bold()}🎯 Tujuan:{c_reset()} {wrap(purpose, width=w)}")

    print(f"{c_bold()}🧾 Command:{c_reset()}")
    print(wrap(cmd or "(empty)", width=w))

    print(f"{c_bold()}⚠️  Risiko:{c_reset()} {wrap(risk or 'Risiko tidak dijelaskan.', width=w)}")
