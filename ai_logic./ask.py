"""AI_IN-TERMINAL — ask
Version: 1.6 (2026-01-20)

Changelog (1.6)
- Front-layer output untuk oneshot dibuat lebih rapi, rame tapi tetap enak dibaca (emoji + wrap + persona label).
- API oneshot ditargetkan ringkas tanpa truncation (rewrite 1x bila kepanjangan).
- Chat divider pakai ansi.hr() supaya konsisten dengan lebar terminal.
- Legacy "hello/halo -> chat" dijadikan opt-in (ASK_MAGIC_HELLO=1).
- Tambah flag --plain untuk output polos (buat piping/scripting).

Notes
- Command ini terpisah dari "ai". Entry point: `ask ...`
- API persona: Sili Pinter 💍 (dewasa & nyaman). LOCAL persona: Sili AI 🌿 (teman lembut).
"""

from __future__ import annotations

import re
import os

from ai_logic.common import (
    backend_mode,
    force_single_paragraph,
    mem_context_text,
    record_last_error,
)
from ai_logic.ui.ansi import (
    ChatSpinner,
    Spinner,
    alt_screen_enter,
    alt_screen_exit,
    clear_screen,
    cursor_hide,
    cursor_show,
    flush_stdin,
    hr,
    wrap,
    print_info,
    print_warn,
    print_brief_error,
    print_meta_line,
    print_system,
    prompt_text,
    tag,
    c_bold,
    c_cyan,
    c_dim,
    c_green,
    c_yellow,
    c_reset,
    term_size,
)
from ai_logic.ui.prompts import (
    api_tokens_chat,
    api_tokens_oneshot,
    local_tokens_for_ask,
    local_tokens_for_chat,
    prompt_api_chat,
    prompt_api_oneshot,
    prompt_local_chat,
    prompt_local_oneshot,
)
from ai_logic.common import route_for, save_memory


# ============================================================
# UX helpers
# ============================================================

def _persona_label(backend: str) -> tuple[str, str]:
    """Return (label, emoji) for the current backend persona."""
    if backend == "api":
        return ("Sili Pinter", "💍")
    return ("Sili AI", "🌿")


def _format_answer_block(backend: str, answer: str) -> str:
    """Front-layer formatting for oneshot output."""
    cols, _ = term_size()
    label, emo = _persona_label(backend)
    header = f"{c_bold()}{emo} {label}:{c_reset()}"
    body = wrap((answer or "").strip(), width=min(cols, 110))
    return f"{header}\n{body}\n"


def _should_force_chat_on_hello(cfg: dict) -> bool:
    """Opt-in legacy: auto-enter chat when user asks 'hello/halo'."""
    try:
        v = str(cfg.get("ask_magic_hello") or "").strip().lower()
        if v in ("1", "true", "yes", "on"):
            return True
    except Exception:
        pass
    return False


def _maybe_compact_api_oneshot(cfg: dict, original_user: str, answer: str) -> str:
    """For API oneshot: ensure output stays concise *without truncating*.

    If the answer is long, do a second quick pass asking the API to rewrite
    into <= ~5 terminal lines (still complete), rather than cutting it.
    """
    if not answer:
        return answer
    # Heuristics: too many lines or too many chars => compact.
    lines = [ln for ln in str(answer).splitlines() if ln.strip()]
    if len(lines) <= 8 and len(answer) <= 900:
        return answer

    sys_prompt = (
        "Kamu adalah 'Sili Pinter' di terminal. "
        "Tulis ulang jawaban menjadi ringkas, dewasa, nyaman dibaca. "
        "Aturan: maksimal kira-kira 5 baris terminal, tapi jangan memotong langkah penting. "
        "Jika perlu, gunakan 1–3 bullet pendek. Jangan menambah fakta baru."
    )
    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": f"Pertanyaan:\n{original_user}\n\nJawaban awal:\n{answer}"},
    ]
    try:
        # small budget for rewrite
        compact = gemini_generate(cfg, messages, timeout=25, max_output_tokens=260)
        return (compact or answer).strip()
    except Exception:
        return answer


def _render_oneshot(cfg: dict, user_text: str, backend: str, answer: str, plain: bool) -> None:
    """Render oneshot answer with persona-aware UI.

    API: if terlalu panjang, lakukan 1 pass rewrite (ringkas tapi tidak dipotong).
    LOCAL: keep it simple.
    """
    ans = (answer or "").strip()
    if backend == "api":
        ans = _maybe_compact_api_oneshot(cfg, original_user=user_text, answer=ans)
    if plain:
        print(ans)
        return
    print(_format_answer_block(backend, ans), end="")


def _print_chat_turn(label: str, backend: str, answer: str, plain: bool) -> None:
    cols, _ = term_size()
    if plain:
        print(f"{label}: {answer}\n")
        return
    # Slightly shorter divider to avoid 'ngantuk'
    print(f"{c_dim()}{hr(width=min(cols, 56))}{c_reset()}")
    # Add a tiny persona emoji for vibe
    _, emo = _persona_label(backend)
    print(f"{c_bold()}{emo} {label}:{c_reset()} {wrap(answer, width=min(cols, 110))}\n")

from ai_logic.backends.local_ollama import chat as ollama_chat, ollama_host, local_model_for
from ai_logic.backends.api_gemini import generate as gemini_generate, validate_api_config, gemini_model_id


def _ask_help() -> None:
    # Front-facing help (ramai tapi tetap scan-able)
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


def handle(argv: list[str], cfg: dict) -> int:
    if not argv or argv[0] in ("-h", "--help", "-help"):
        _ask_help()
        return 2

    chat_mode = False
    plain = False
    rest: list[str] = []
    for a in argv:
        if a in ("--chat", "-c"):
            chat_mode = True
        elif a == "--plain":
            plain = True
        else:
            rest.append(a)

    if chat_mode:
        return _chat_loop(rest, cfg, plain=plain)

    text = " ".join(rest).strip()
    if not text:
        _ask_help()
        return 2

    # Legacy convenience (opt-in): "hello/halo" -> chat.
    # Default OFF supaya tidak bikin kaget. Enable via env: ASK_MAGIC_HELLO=1
    if (text.strip().lower() in ("hello", "halo")
            and (os.environ.get('ASK_MAGIC_HELLO', '').strip().lower() in ('1','true','yes','on'))):
        return _chat_loop([], cfg, plain=plain)

    return _oneshot(text, cfg, plain=plain)


def _oneshot(text: str, cfg: dict, plain: bool = False) -> int:
    mode = backend_mode(cfg)
    backend, route = route_for(cfg, "ask")
    model = gemini_model_id(cfg) if backend == "api" else (local_model_for(cfg, "ask") or "(unset)")
    if not plain:
        print_meta_line("ASK", route, model)

    mem_ctx = mem_context_text(cfg).strip()
    sys_prompt = prompt_api_oneshot() if backend == "api" else prompt_local_oneshot()

    messages = [{"role": "system", "content": sys_prompt}]
    if mem_ctx:
        messages.append({"role": "system", "content": mem_ctx})
    messages.append({"role": "user", "content": text})

    flush_stdin()

    # API path
    if backend == "api":
        ok, note, detail = validate_api_config(cfg)
        if not ok:
            record_last_error("ask_oneshot", "api", f"{note}\n{detail}")
            print_system(f"API tidak siap: {note}. Aku fallback ke LOCAL.")
            backend = "local"
        else:
            try:
                with Spinner("Aku lagi nyusun jawaban... ✨"):
                    ans = gemini_generate(cfg, messages, timeout=60, max_output_tokens=api_tokens_oneshot())
                flush_stdin()
                _render_oneshot(cfg, user_text=text, backend="api", answer=ans, plain=plain)
                return 0
            except KeyboardInterrupt:
                record_last_error("ask_oneshot", "api", "KeyboardInterrupt saat menunggu API.")
                flush_stdin()
                print_brief_error("Dibatalkan")
                return 130
            except Exception as ex:
                record_last_error("ask_oneshot", "api", str(ex))
                flush_stdin()
                if mode in ("auto", "api"):
                    if not plain:
                        print_system("😿 API error. Aku pindah ke LOCAL dulu ya.")
                backend = "local"

    # LOCAL path
    try:
        host = ollama_host(cfg)
        m = local_model_for(cfg, "ask") or "llama3.1:8b"
        tok = local_tokens_for_ask(text)
        with Spinner("Aku lagi nyusun jawaban... 🌿"):
            ans = ollama_chat(host, m, messages, timeout=220, num_predict=tok)
        flush_stdin()
        _render_oneshot(cfg, user_text=text, backend="local", answer=force_single_paragraph(ans), plain=plain)
        return 0
    except KeyboardInterrupt:
        record_last_error("ask_oneshot", "local", "KeyboardInterrupt saat menunggu LOCAL.")
        flush_stdin()
        print_brief_error("Dibatalkan")
        return 130
    except Exception as ex:
        record_last_error("ask_oneshot", "local", str(ex))
        flush_stdin()
        print_brief_error("Mode lokal juga gagal")
        return 2


def _chat_loop(argv: list[str], cfg: dict, plain: bool = False) -> int:
    mode = backend_mode(cfg)
    backend, route = route_for(cfg, "ask")

    def persona_for(backend_now: str) -> tuple[str, str, str]:
        if backend_now == "api":
            return ("Sili Pinter", "Hai sayang, mau ngobrol apa? 💍", prompt_api_chat())
        return ("Sili AI", "Haii~ cerita aja ya. Aku dengerin 🌿", prompt_local_chat())

    def print_header(current_route: str) -> None:
        clear_screen()
        if plain:
            print(f"SILI CHAT • {current_route}\n")
            return
        print(f"{tag('SILI CHAT', c_cyan())} {tag(mode.upper(), c_yellow())} {c_dim()}•{c_reset()} {current_route}")
        print(f"{c_dim()}⌨️  exit/keluar untuk menutup sesi • Ctrl+C juga bisa{c_reset()}")
        print(f"{c_dim()}{'—' * 2} tips: jaga chat tetap jelas; kalau butuh ringkas, bilang 'ringkas dong'{c_reset()}\n")

    mem_ctx = mem_context_text(cfg).strip()
    label, greeting, sys_prompt = persona_for(backend)

    history: list[dict] = [{"role": "system", "content": sys_prompt}]
    if mem_ctx:
        history.append({"role": "system", "content": mem_ctx})

    exit_requested = False
    interrupted = False
    persona_at_exit = "api" if backend == "api" else "local"

    used_api_at_least_once = False

    alt_screen_enter()
    cursor_hide()
    try:
        print_header(route)
        if not plain:
            print(f"{c_bold()}{label}:{c_reset()} {greeting}\n")

        while True:
            try:
                flush_stdin()
                user = prompt_text(f"{c_cyan()}User{c_reset()} {c_dim()}›{c_reset()} ").strip()
            except KeyboardInterrupt:
                exit_requested = True
                interrupted = True
                persona_at_exit = "api" if backend == "api" else "local"
                break

            if not user:
                continue

            if user.lower() in ("exit", "keluar", "quit"):
                exit_requested = True
                interrupted = False
                persona_at_exit = "api" if backend == "api" else "local"
                break

            history.append({"role": "user", "content": user})

            # API path
            if backend == "api":
                ok, note, detail = validate_api_config(cfg)
                if not ok:
                    record_last_error("ask_chat", "api", f"{note}\n{detail}")
                    if not plain:
                        print_system("🔄 API tidak siap. Aku pindah ke LOCAL dulu ya.")
                    backend = "local"
                    route = f"{tag('AUTO', c_yellow())} -> {tag('LOCAL', c_green())} • {local_model_for(cfg, 'ask') or '(unset)'}"
                    label, greeting, sys_prompt = persona_for("local")
                    history[0] = {"role": "system", "content": sys_prompt}
                else:
                    try:
                        with ChatSpinner("Aku lagi mikir..."):
                            answer = gemini_generate(cfg, history, timeout=90, max_output_tokens=api_tokens_chat())
                        flush_stdin()
                        used_api_at_least_once = True
                        history.append({"role": "assistant", "content": answer})
                        _print_chat_turn(label, backend="api", answer=answer, plain=plain)
                        continue
                    except KeyboardInterrupt:
                        record_last_error("ask_chat", "api", "KeyboardInterrupt saat menunggu API (chat).")
                        exit_requested = True
                        interrupted = True
                        persona_at_exit = "api"
                        break
                    except Exception as ex:
                        record_last_error("ask_chat", "api", str(ex))
                        if not plain:
                            print_system("😿 API error. Aku pindah ke LOCAL dulu ya.")
                        backend = "local"
                        route = f"{tag('AUTO', c_yellow())} -> {tag('LOCAL', c_green())} • {local_model_for(cfg, 'ask') or '(unset)'}"
                        label, greeting, sys_prompt = persona_for("local")
                        history[0] = {"role": "system", "content": sys_prompt}

            # LOCAL path
            try:
                host = ollama_host(cfg)
                m = local_model_for(cfg, "ask") or "llama3.1:8b"
                tok = local_tokens_for_chat(user)
                with ChatSpinner("Aku lagi mikir..."):
                    answer = ollama_chat(host, m, history, timeout=260, num_predict=tok)
                flush_stdin()
                history.append({"role": "assistant", "content": answer})
                _print_chat_turn(label, backend="local", answer=answer, plain=plain)
            except KeyboardInterrupt:
                record_last_error("ask_chat", "local", "KeyboardInterrupt saat menunggu LOCAL (chat).")
                exit_requested = True
                interrupted = True
                persona_at_exit = "local"
                break
            except Exception as ex:
                record_last_error("ask_chat", "local", str(ex))
                if not plain:
                    print_brief_error("LOCAL gagal jawab. Cek ai status.")
                else:
                    print("LOCAL gagal jawab.")

        # Memory ringkas (API chat only) — opsional
        if used_api_at_least_once and mode in ("api", "auto"):
            ok, _, _ = validate_api_config(cfg)
            if ok:
                try:
                    convo = []
                    for m in history[-18:]:
                        r = m.get("role")
                        c = str(m.get("content") or "").strip()
                        if not c:
                            continue
                        if r == "user":
                            convo.append(f"User: {c}")
                        elif r == "assistant":
                            convo.append(f"Asisten: {c}")
                    convo_text = "\n".join(convo).strip()

                    if len(convo_text) >= 200 and len(convo) >= 4:
                        summarizer = [
                            {
                                "role": "system",
                                "content": (
                                    "Ringkas percakapan ini untuk memori singkat.\n"
                                    "- kalimat Bahasa Indonesia yang lengkap.\n"
                                    "- Sebutkan topik utama + preferensi/keputusan user bila ada.\n"
                                    "- Jangan simpan credential/rahasia.\n"
                                    "- Jangan terlalu singkat (minimal ~20 kata)."
                                ),
                            },
                            {"role": "user", "content": convo_text},
                        ]
                        summary = gemini_generate(cfg, summarizer, timeout=35, max_output_tokens=220)
                        summary = re.sub(r"(AIza[0-9A-Za-z\-_]+|sk-[0-9A-Za-z]+)", "[REDACTED]", summary).strip()
                        if len(summary.split()) >= 10:
                            if summary and summary[-1] not in ".!?":
                                summary += "."
                            save_memory(summary, cfg)
                except Exception as ex:
                    record_last_error("memory_summary", "api", str(ex))

    finally:
        cursor_show()
        alt_screen_exit()

    if exit_requested:
        from ai_logic.ui.ansi import print_goodbye
        print_goodbye("api" if persona_at_exit == "api" else "local", interrupted)

    return 0
