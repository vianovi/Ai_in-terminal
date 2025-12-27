from __future__ import annotations

import re

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

from ai_logic.backends.local_ollama import chat as ollama_chat, ollama_host, local_model_for
from ai_logic.backends.api_gemini import generate as gemini_generate, validate_api_config, gemini_model_id


def _ask_help() -> None:
    print("Usage:")
    print('  ./ai-term ask "..."' )
    print("  ./ai-term ask --chat")
    print("Flags:")
    print("  --chat   Masuk mode chat (alt-screen)")
    print("  --help   Tampilkan help")


def handle(argv: list[str], cfg: dict) -> int:
    if not argv or argv[0] in ("-h", "--help", "help"):
        _ask_help()
        return 2

    chat_mode = False
    rest: list[str] = []
    for a in argv:
        if a in ("--chat", "-c"):
            chat_mode = True
        else:
            rest.append(a)

    if chat_mode:
        return _chat_loop(rest, cfg)

    text = " ".join(rest).strip()
    if not text:
        _ask_help()
        return 2

    # Legacy convenience: "hello/halo" masuk chat
    if text.strip().lower() in ("hello", "halo"):
        return _chat_loop([], cfg)

    return _oneshot(text, cfg)


def _oneshot(text: str, cfg: dict) -> int:
    mode = backend_mode(cfg)
    backend, route = route_for(cfg, "ask")
    model = gemini_model_id(cfg) if backend == "api" else (local_model_for(cfg, "ask") or "(unset)")
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
                with Spinner("Aku lagi nyusun jawaban..."):
                    ans = gemini_generate(cfg, messages, timeout=60, max_output_tokens=api_tokens_oneshot())
                flush_stdin()
                print(ans)
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
                    print_system("API error. Aku fallback ke LOCAL.")
                backend = "local"

    # LOCAL path
    try:
        host = ollama_host(cfg)
        m = local_model_for(cfg, "ask") or "llama3.1:8b"
        tok = local_tokens_for_ask(text)
        with Spinner("Aku lagi nyusun jawaban..."):
            ans = ollama_chat(host, m, messages, timeout=220, num_predict=tok)
        flush_stdin()
        print(force_single_paragraph(ans))
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


def _chat_loop(argv: list[str], cfg: dict) -> int:
    mode = backend_mode(cfg)
    backend, route = route_for(cfg, "ask")

    def persona_for(backend_now: str) -> tuple[str, str, str]:
        if backend_now == "api":
            return ("Sili Pinter", "Hai, mau ngobrol apa?", prompt_api_chat())
        return ("Sili AI", "Hai, cerita aja. Aku dengerin.", prompt_local_chat())

    def print_header(current_route: str) -> None:
        clear_screen()
        print(f"{tag('SILI CHAT', c_cyan())} {tag(mode.upper(), c_yellow())} {c_dim()}•{c_reset()} {current_route}")
        print(f"{c_dim()}Ketik \"exit\" / \"keluar\" untuk menutup sesi.{c_reset()}")
        print(f"{c_dim()}(Ctrl+C juga bisa untuk keluar){c_reset()}\n")

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
                    print_system("API tidak siap. Aku pindah ke LOCAL dulu ya.")
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
                        print(f"{c_dim()}────────────────────────────────────────{c_reset()}")
                        print(f"{c_bold()}{label}:{c_reset()} {answer}\n")
                        continue
                    except KeyboardInterrupt:
                        record_last_error("ask_chat", "api", "KeyboardInterrupt saat menunggu API (chat).")
                        exit_requested = True
                        interrupted = True
                        persona_at_exit = "api"
                        break
                    except Exception as ex:
                        record_last_error("ask_chat", "api", str(ex))
                        print_system("API error. Aku pindah ke LOCAL dulu ya.")
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
                print(f"{c_dim()}────────────────────────────────────────{c_reset()}")
                print(f"{c_bold()}{label}:{c_reset()} {answer}\n")
            except KeyboardInterrupt:
                record_last_error("ask_chat", "local", "KeyboardInterrupt saat menunggu LOCAL (chat).")
                exit_requested = True
                interrupted = True
                persona_at_exit = "local"
                break
            except Exception as ex:
                record_last_error("ask_chat", "local", str(ex))
                print_brief_error("LOCAL gagal jawab. Cek ai status.")

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
                                    "- Tulis 3–5 kalimat Bahasa Indonesia yang lengkap.\n"
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
