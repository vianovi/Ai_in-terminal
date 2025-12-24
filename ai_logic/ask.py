from __future__ import annotations

import re
import sys

from ai_logic.common import (
    ChatSpinner,
    Spinner,
    alt_screen_enter,
    alt_screen_exit,
    api_generate,
    api_model_display,
    api_provider,
    api_tokens_chat,
    api_tokens_oneshot,
    backend_mode,
    c_bold,
    c_cyan,
    c_dim,
    c_green,
    c_reset,
    clear_screen,
    cursor_hide,
    cursor_show,
    flush_stdin,
    force_single_paragraph,
    gemini_generate,  # dipakai untuk ringkasan? (akan diganti via api_generate)
    load_config,
    local_model_for,
    local_tokens_for_ask,
    local_tokens_for_chat,
    mem_context_text,
    ollama_chat,
    ollama_host,
    print_brief_error,
    print_info,
    print_meta_line,
    prompt_api_oneshot,
    prompt_local_oneshot,
    prompt_text,
    record_last_error,
    route_for,
    save_memory,
    tag,
    validate_api_config,
    wrap,
)

# NOTE: gemini_generate tidak dipakai langsung; ringkasan pakai api_generate (provider-agnostic).


def mode_ask(text: str, cfg: dict) -> int:
    t = text.strip().lower()
    if t in ("hello", "halo"):
        return ask_chat_loop(cfg)

    mode = backend_mode(cfg)
    backend, route = route_for(cfg, "ask")
    model = api_model_display(cfg) if backend == "api" else (local_model_for(cfg, "ask") or "(unset)")
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
            if mode == "api":
                print_brief_error(note)
            backend = "local"
        else:
            try:
                with Spinner("Aku lagi nyusun jawaban..."):
                    ans = api_generate(cfg, messages, timeout=60, max_output_tokens=api_tokens_oneshot())
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
                    print_info(f"{tag('AUTO', '33m')} -> {tag('LOCAL', '32m')} • {local_model_for(cfg,'ask') or '(unset)'}")
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


def ask_chat_loop(cfg: dict) -> int:
    mode = backend_mode(cfg)               # "local" | "api" | "auto"
    backend, route = route_for(cfg, "ask") # awal routing sesuai mode

    def persona_for(backend_now: str) -> tuple[str, str, str, str]:
        if backend_now == "api":
            label = "Sili Pinter 💖"
            greeting = "Iyahh, aku Sili si pintar. Mau ngobrol apa nih, sayang?"
            goodbye = "Ih kok udahan? Aku tungguin kamu balik lagi ya, sayang."
            system_prompt = (
                "ROLE: Kamu adalah 'Sili Pinter', asisten AI berbasis API yang hidup di terminal.\n"
                "GAYA: Hangat, romantis, perhatian seperti pasangan dewasa. Panggil user dengan 'sayang'.\n"
                "BAHASA: Bahasa Indonesia natural santai.\n"
                "ATURAN: Jangan mengarang fakta. Kalau tidak yakin, bilang tidak yakin.\n"
                "CATATAN: Mode chat santai; boleh agak panjang, tapi jangan bertele-tele."
            )
            return label, greeting, goodbye, system_prompt

        label = "Sili AI 🤖"
        greeting = "Hai! Aku Sili AI. Cerita aja, aku dengerin."
        goodbye = "Sip, sesi chat-nya aku tutup dulu ya. Sampai ketemu lagi."
        system_prompt = (
            "ROLE: Kamu adalah 'Sili AI', asisten AI lokal (Ollama) di terminal.\n"
            "GAYA: Ramah, jelas, praktis.\n"
            "BAHASA: Bahasa Indonesia.\n"
            "ATURAN: Jangan mengarang fakta. Kalau tidak yakin, bilang tidak yakin.\n"
            "CATATAN: Aturan halus: usahakan 3–4 baris kalau memungkinkan."
        )
        return label, greeting, goodbye, system_prompt

    def print_header(current_route: str) -> None:
        cols, _ = (120, 24)
        _ = cols
        clear_screen()
        sys.stdout.write(
            f"{tag('SILI CHAT', '36m')} {tag(mode.upper(), '33m')} {c_dim()}•{c_reset()} {current_route}\n"
        )
        sys.stdout.write(f"{c_dim()}Ketik \"exit\" / \"keluar\" untuk menutup sesi.{c_reset()}\n")
        sys.stdout.write(f"{c_dim()}(Ctrl+C juga bisa untuk keluar){c_reset()}\n\n")
        sys.stdout.flush()

    mem_ctx = mem_context_text(cfg).strip()
    label, greeting, goodbye, sys_prompt = persona_for(backend)

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
        sys.stdout.write(f"{c_bold()}{label}:{c_reset()} {greeting}\n\n")
        sys.stdout.flush()

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

            # ----------------
            # API path
            # ----------------
            if backend == "api":
                ok, note, detail = validate_api_config(cfg)
                if not ok:
                    record_last_error("ask_chat", "api", f"{note}\n{detail}")
                    sys.stdout.write(
                        f"{tag('SISTEM AI', '33m')} API lagi tidak siap. Aku pindah ke LOCAL dulu ya.\n"
                        f"{c_dim()}(cek detail: ai \"status\"){c_reset()}\n\n"
                    )
                    sys.stdout.flush()

                    backend = "local"
                    route = f"{tag('AUTO', '33m')} -> {tag('LOCAL', '32m')} • {local_model_for(cfg, 'ask') or '(unset)'}"
                    label, greeting, goodbye, sys_prompt = persona_for("local")
                    history[0] = {"role": "system", "content": sys_prompt}
                    print_header(route)
                else:
                    try:
                        with ChatSpinner("Aku lagi mikir..."):
                            answer = api_generate(cfg, history, timeout=90, max_output_tokens=api_tokens_chat())
                        flush_stdin()
                        used_api_at_least_once = True
                        history.append({"role": "assistant", "content": answer})

                        sys.stdout.write(f"{c_dim()}────────────────────────────────────────{c_reset()}\n")
                        sys.stdout.write(f"{c_bold()}{label}:{c_reset()} {answer}\n\n")
                        sys.stdout.flush()
                        continue
                    except KeyboardInterrupt:
                        record_last_error("ask_chat", "api", "KeyboardInterrupt saat menunggu API (chat).")
                        exit_requested = True
                        interrupted = True
                        persona_at_exit = "api"
                        break
                    except Exception as ex:
                        record_last_error("ask_chat", "api", str(ex))
                        sys.stdout.write(
                            f"{tag('SISTEM AI', '33m')} API error. Aku pindah ke LOCAL dulu ya.\n"
                            f"{c_dim()}(cek detail: ai \"status\"){c_reset()}\n\n"
                        )
                        sys.stdout.flush()

                        backend = "local"
                        route = f"{tag('AUTO', '33m')} -> {tag('LOCAL', '32m')} • {local_model_for(cfg, 'ask') or '(unset)'}"
                        label, greeting, goodbye, sys_prompt = persona_for("local")
                        history[0] = {"role": "system", "content": sys_prompt}
                        print_header(route)

            # ----------------
            # LOCAL path
            # ----------------
            try:
                host = ollama_host(cfg)
                m = local_model_for(cfg, "ask") or "llama3.1:8b"
                tok = local_tokens_for_chat(user)

                with ChatSpinner("Aku lagi mikir..."):
                    answer = ollama_chat(host, m, history, timeout=260, num_predict=tok)

                flush_stdin()
                history.append({"role": "assistant", "content": answer})

                sys.stdout.write(f"{c_dim()}────────────────────────────────────────{c_reset()}\n")
                sys.stdout.write(f"{c_bold()}{label}:{c_reset()} {answer}\n\n")
                sys.stdout.flush()

            except KeyboardInterrupt:
                record_last_error("ask_chat", "local", "KeyboardInterrupt saat menunggu LOCAL (chat).")
                exit_requested = True
                interrupted = True
                persona_at_exit = "local"
                break
            except Exception as ex:
                record_last_error("ask_chat", "local", str(ex))
                sys.stdout.write(f"{tag('LOCAL', '31m')} Aku gagal jawab dari mode lokal.\n")
                sys.stdout.write(f"{c_dim()}Cek detail lewat: ai \"status\"{c_reset()}\n\n")
                sys.stdout.flush()

        if exit_requested:
            label2, _g, bye2, _sp = persona_for("api" if persona_at_exit == "api" else "local")
            sys.stdout.write("\n")
            sys.stdout.write(f"{c_bold()}{label2}:{c_reset()} {bye2}\n")
            sys.stdout.flush()

        # Memory ringkas: hanya kalau API sempat dipakai
        if used_api_at_least_once and mode in ("api", "auto"):
            ok, _, _ = validate_api_config(cfg)
            if ok:
                try:
                    convo: list[str] = []
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
                                    "- Minimal ~20 kata."
                                ),
                            },
                            {"role": "user", "content": convo_text},
                        ]

                        summary = api_generate(cfg, summarizer, timeout=35, max_output_tokens=220)
                        summary = re.sub(r"(AIza[0-9A-Za-z\-_]+|sk-[0-9A-Za-z]+)", "[REDACTED]", summary).strip()

                        if len(summary.split()) >= 10:
                            save_memory(summary, cfg)
                        else:
                            record_last_error("memory_summary", "api", f"Ringkasan terlalu pendek: {summary!r}")

                except Exception as ex:
                    record_last_error("memory_summary", "api", str(ex))

    finally:
        cursor_show()
        alt_screen_exit()

    if exit_requested:
        from ai_logic.common import _print_goodbye
        _print_goodbye("api" if persona_at_exit == "api" else "local", interrupted)

    return 0
