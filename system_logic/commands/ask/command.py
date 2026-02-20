"""
commands/ask/command.py
=======================
Core handler untuk `ask` command.

Entry point:
    handle(argv, cfg) -> int

Mode yang tersedia:
    Oneshot  : ask "pertanyaan"   — satu jawaban, langsung keluar.
    Chat     : ask --chat          — sesi interaktif di alt-screen.
    Plain    : flag --plain        — output tanpa ANSI, cocok untuk piping.

Alur oneshot:
    1. Parse argv → help / plain / user_text.
    2. Resolve backend (api | local) via route_info().
    3. Build 1 consolidated system message + user message.
    4. Generate via API (Gemini) atau LOCAL (Ollama), dengan fallback.
       Jika fallback: REBUILD messages dengan prompt yang sesuai backend baru.
    5. Compact jawaban API jika terlalu panjang (opsional).
    6. Render hasil.

Alur chat:
    1. Buka alt-screen, tampilkan header & greeting persona.
    2. Loop: baca input → generate → tampilkan jawaban → ulangi.
    3. Jika API error → fallback ke LOCAL, update system prompt di history[0].
    4. Di akhir sesi: simpan ringkasan memori via Gemini jika ada.
    5. Tutup alt-screen, tampilkan goodbye.

Bug yang difix vs versi lama:
    [A-01] messages pakai 2× role='system' (sys_prompt + mem_ctx terpisah)
           → beberapa model error. FIXED: digabung ke 1 system message.
    [A-02] Saat _oneshot fallback api→local, messages[0] masih pakai
           prompt_api_oneshot() → model lokal terima instruksi persona API.
           FIXED: messages di-rebuild dengan prompt yang tepat saat fallback.
    [A-03] _chat_loop: history[0] reassignment partial (hanya sys_prompt,
           mem_ctx di history[1] tidak ikut update).
           FIXED: dengan consolidated messages, cukup update history[0].
    [A-04] mode in ("auto","api") check di except block _oneshot — redundant.
           FIXED: kondisi disederhanakan.
    [A-05] local_tokens_for_cmd() misplaced di ask/prompts.py.
           FIXED: dihapus dari prompts.py (tidak relevan untuk ask).
"""
from __future__ import annotations

import os
import re

from system_logic.core.routing import backend_mode, route_info
from system_logic.core.memory import mem_context_text, save_memory
from system_logic.core.last_error import record_last_error

from system_logic.terminal.ansi import (
    ChatSpinner,
    Spinner,
    alt_screen_enter,
    alt_screen_exit,
    cursor_hide,
    cursor_show,
    flush_stdin,
    print_brief_error,
    print_meta_line,
    print_system,
    prompt_text,
)

from system_logic.commands.ask.prompts import (
    api_tokens_chat,
    api_tokens_oneshot,
    local_tokens_for_ask,
    local_tokens_for_chat,
    prompt_api_chat,
    prompt_api_oneshot,
    prompt_local_chat,
    prompt_local_oneshot,
)
from system_logic.commands.ask import ui as ask_ui

from system_logic.backends.local_ollama import (
    chat as ollama_chat,
    ollama_host,
    local_model_for,
)
from system_logic.backends.api_gemini import (
    generate as gemini_generate,
    validate_api_config,
    gemini_model_id,
)


# ============================================================
# Internal helpers
# ============================================================

def _sanitize_paragraph(text: str) -> str:
    """
    Normalisasi teks multi-baris menjadi satu paragraf bersih.
    Dipakai untuk membersihkan output model lokal di mode oneshot.
    """
    s = re.sub(r"\s*\n+\s*", " ", (text or "").strip())
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip()


def _full_system(sys_prompt: str, mem_ctx: str) -> str:
    """
    Gabungkan sys_prompt dan mem_ctx menjadi satu string system content.
    Dipakai oleh _build_messages, _build_history, dan fallback history[0].

    Args:
        sys_prompt : System prompt utama.
        mem_ctx    : Memory context string, boleh kosong.

    Returns:
        String gabungan siap dipakai sebagai content system message.
    """
    if mem_ctx:
        return f"{sys_prompt}\n\nMEMORY CONTEXT:\n{mem_ctx}"
    return sys_prompt


def _build_messages(sys_prompt: str, mem_ctx: str, user_text: str) -> list[dict]:
    """
    Bangun daftar pesan untuk dikirim ke model.

    Memory context digabung ke dalam system prompt (bukan pesan terpisah)
    agar kompatibel dengan model yang hanya support 1 system message.

    Args:
        sys_prompt : System prompt utama (dari prompt_api_* / prompt_local_*).
        mem_ctx    : Memory context string, boleh kosong.
        user_text  : Pesan dari user.

    Returns:
        List of dict dengan keys 'role' dan 'content'.
    """
    return [
        {"role": "system", "content": _full_system(sys_prompt, mem_ctx)},
        {"role": "user",   "content": user_text},
    ]


def _build_history(sys_prompt: str, mem_ctx: str) -> list[dict]:
    """
    Bangun history awal untuk chat mode (hanya system message).
    User message akan di-append per turn di dalam chat loop.

    Args:
        sys_prompt : System prompt utama.
        mem_ctx    : Memory context string, boleh kosong.

    Returns:
        List berisi 1 system message.
    """
    return [{"role": "system", "content": _full_system(sys_prompt, mem_ctx)}]


def _compact_api_answer(cfg: dict, original_user: str, answer: str) -> str:
    """
    Opsional: ringkas jawaban API yang terlalu panjang agar nyaman di terminal.
    Hanya dilakukan jika jawaban > 8 baris atau > 900 karakter.
    Jika compact gagal, kembalikan jawaban asli (safe fallback).

    Args:
        cfg           : Config dict.
        original_user : Pertanyaan asal user (konteks untuk ringkasan).
        answer        : Jawaban mentah dari model.

    Returns:
        Jawaban ringkas atau jawaban asli jika compact tidak perlu / gagal.
    """
    if not answer:
        return answer

    lines = [ln for ln in str(answer).splitlines() if ln.strip()]
    if len(lines) <= 8 and len(answer) <= 900:
        return answer  # sudah cukup ringkas, tidak perlu compact

    sys_prompt = (
        "Kamu adalah 'Sili Pinter 💍' di terminal. "
        "Tulis ulang jawaban menjadi ringkas, dewasa, nyaman dibaca. "
        "Aturan: maksimal kira-kira 5 baris terminal, "
        "tapi jangan memotong langkah penting. "
        "Jika perlu, gunakan 1–3 bullet pendek. Jangan menambah fakta baru."
    )
    messages = [
        {"role": "system", "content": sys_prompt},
        {
            "role": "user",
            "content": f"Pertanyaan:\n{original_user}\n\nJawaban awal:\n{answer}",
        },
    ]
    try:
        compact = gemini_generate(cfg, messages, timeout=25, max_output_tokens=260)
        return (compact or answer).strip()
    except Exception:
        return answer  # fallback ke jawaban asli jika compact error


def _render_oneshot(
    cfg: dict,
    user_text: str,
    backend: str,
    answer: str,
    plain: bool,
) -> None:
    """
    Render jawaban oneshot ke terminal.
    Jika backend API dan jawaban panjang, coba compact dulu.

    Args:
        cfg       : Config dict (untuk _compact_api_answer).
        user_text : Pertanyaan asli user.
        backend   : 'api' atau 'local'.
        answer    : Jawaban dari model.
        plain     : Jika True, print polos tanpa ANSI.
    """
    ans = (answer or "").strip()

    if backend == "api":
        ans = _compact_api_answer(cfg, original_user=user_text, answer=ans)

    if plain:
        print(ans)
        return

    print(ask_ui.format_answer_block(backend, ans), end="")


# ============================================================
# Entry point
# ============================================================

def handle(argv: list[str], cfg: dict) -> int:
    """
    Entry point utama untuk command `ask`.

    Args:
        argv : Argumen setelah nama command.
        cfg  : Config dict dari framework.

    Returns:
        Exit code: 0 = sukses, 2 = error, 130 = Ctrl+C.
    """
    if not argv or argv[0] in ("-h", "--help", "-help"):
        ask_ui.print_help()
        return 0

    chat_mode = False
    plain     = False
    rest: list[str] = []

    for a in argv:
        if a in ("--chat", "-c"):
            chat_mode = True
        elif a == "--plain":
            plain = True
        else:
            rest.append(a)

    if chat_mode:
        return _chat_loop(cfg, plain=plain)

    text = " ".join(rest).strip()
    if not text:
        ask_ui.print_help()
        return 0

    # Magic hello → chat (opsional, harus diaktifkan via env var)
    if text.lower() in ("hello", "halo") and (
        os.environ.get("ASK_MAGIC_HELLO", "").strip().lower() in ("1", "true", "yes", "on")
    ):
        return _chat_loop(cfg, plain=plain)

    return _oneshot(cfg, text, plain=plain)


# ============================================================
# Oneshot
# ============================================================

def _oneshot(cfg: dict, text: str, plain: bool = False) -> int:
    """
    Mode oneshot: satu pertanyaan, satu jawaban, langsung keluar.

    Args:
        cfg   : Config dict.
        text  : Pertanyaan dari user.
        plain : Jika True, output polos.

    Returns:
        Exit code.
    """
    info    = route_info(cfg, "ask")
    backend = info.backend  # 'api' atau 'local'

    model_display = (
        gemini_model_id(cfg)
        if backend == "api"
        else (local_model_for(cfg, "ask") or "(unset)")
    )
    if not plain:
        print_meta_line("ASK", info.label, model_display)

    mem_ctx = mem_context_text(cfg).strip()

    # --------------------------------------------------------
    # API path
    # --------------------------------------------------------
    if backend == "api":
        ok, note, _detail = validate_api_config(cfg)
        if not ok:
            record_last_error("ask_oneshot", "api", f"{note}\n{_detail}")
            if not plain:
                print_system(f"⚠️  API tidak siap: {note}. Fallback ke LOCAL.")
            backend = "local"
        else:
            messages = _build_messages(prompt_api_oneshot(), mem_ctx, text)
            flush_stdin()
            try:
                with Spinner("Sili Pinter 💍 lagi nyusun jawaban..."):
                    ans = gemini_generate(
                        cfg, messages, timeout=60, max_output_tokens=api_tokens_oneshot()
                    )
                flush_stdin()
                _render_oneshot(cfg, user_text=text, backend="api", answer=ans, plain=plain)
                return 0
            except KeyboardInterrupt:
                record_last_error(
                    "ask_oneshot", "api", "KeyboardInterrupt saat menunggu API."
                )
                flush_stdin()
                print_brief_error("Dibatalkan")
                return 130
            except Exception as ex:
                record_last_error("ask_oneshot", "api", str(ex))
                flush_stdin()
                if not plain:
                    print_system("😿 API error. Fallback ke LOCAL.")
                backend = "local"

    # --------------------------------------------------------
    # LOCAL path (primer atau fallback dari api)
    # Rebuild messages dengan prompt lokal yang tepat.
    # --------------------------------------------------------
    messages = _build_messages(prompt_local_oneshot(), mem_ctx, text)
    flush_stdin()
    try:
        host  = ollama_host(cfg)
        model = local_model_for(cfg, "ask") or "llama3.1:8b"
        tok   = local_tokens_for_ask(text)
        with Spinner("Sili AI 🌿 lagi nyusun jawaban..."):
            ans = ollama_chat(host, model, messages, timeout=220, num_predict=tok)
        flush_stdin()
        _render_oneshot(
            cfg,
            user_text=text,
            backend="local",
            answer=_sanitize_paragraph(ans),
            plain=plain,
        )
        return 0
    except KeyboardInterrupt:
        record_last_error(
            "ask_oneshot", "local", "KeyboardInterrupt saat menunggu LOCAL."
        )
        flush_stdin()
        print_brief_error("Dibatalkan")
        return 130
    except Exception as ex:
        record_last_error("ask_oneshot", "local", str(ex))
        flush_stdin()
        print_brief_error("Mode lokal juga gagal. Cek Ollama service.")
        return 2


# ============================================================
# Chat loop
# ============================================================

def _chat_loop(cfg: dict, plain: bool = False) -> int:
    """
    Mode chat interaktif dengan alt-screen.

    Flow:
        - Buka alt-screen, render header.
        - Loop: baca input → generate → tampilkan → ulangi.
        - Fallback api→local jika API error (update history[0]).
        - Di akhir: simpan ringkasan memori jika ada konten yang cukup.
        - Tutup alt-screen, tampilkan goodbye.

    Args:
        cfg   : Config dict.
        plain : Jika True, output polos.

    Returns:
        Exit code (selalu 0).
    """
    mode    = backend_mode(cfg)   # dipakai untuk print_chat_header & memory guard
    info    = route_info(cfg, "ask")
    backend = info.backend
    route   = info.label

    def _persona_for(b: str) -> tuple[str, str, str]:
        """Kembalikan (label, greeting, sys_prompt) berdasarkan backend."""
        if b == "api":
            return ("Sili Pinter", "Hai sayang, mau ngobrol apa? 💍", prompt_api_chat())
        return ("Sili AI", "Haii~ cerita aja ya. Aku dengerin 🌿", prompt_local_chat())

    mem_ctx              = mem_context_text(cfg).strip()
    label, greeting, sys_prompt = _persona_for(backend)
    history              = _build_history(sys_prompt, mem_ctx)

    exit_requested       = False
    interrupted          = False
    persona_at_exit      = backend
    used_api_at_least_once = False

    alt_screen_enter()
    cursor_hide()
    try:
        ask_ui.print_chat_header(mode, route, plain=plain)
        if not plain:
            print(f"{greeting}\n")

        while True:
            # --- Baca input user ---
            try:
                flush_stdin()
                user = prompt_text("User › ").strip()
            except KeyboardInterrupt:
                exit_requested = True
                interrupted    = True
                persona_at_exit = backend
                break

            if not user:
                continue

            if user.lower() in ("exit", "keluar", "quit"):
                exit_requested  = True
                interrupted     = False
                persona_at_exit = backend
                break

            history.append({"role": "user", "content": user})

            # ------------------------------------------------
            # API path
            # ------------------------------------------------
            if backend == "api":
                ok, note, _detail = validate_api_config(cfg)
                if not ok:
                    record_last_error("ask_chat", "api", f"{note}\n{_detail}")
                    if not plain:
                        print_system("🔄 API tidak siap. Fallback ke LOCAL.")
                    # Fallback: ganti backend + update system prompt di history
                    backend  = "local"
                    route    = f"AUTO→LOCAL • {local_model_for(cfg, 'ask') or '(unset)'}"
                    label, greeting, sys_prompt = _persona_for("local")
                    history[0] = {"role": "system", "content": _full_system(sys_prompt, mem_ctx)}
                    # Lanjut ke LOCAL path di bawah (tidak continue)
                else:
                    try:
                        with ChatSpinner("Sili Pinter 💍 lagi mikir..."):
                            answer = gemini_generate(
                                cfg, history, timeout=90, max_output_tokens=api_tokens_chat()
                            )
                        flush_stdin()
                        used_api_at_least_once = True
                        history.append({"role": "assistant", "content": answer})
                        ask_ui.print_chat_turn(
                            label, backend="api", answer=answer, plain=plain
                        )
                        continue
                    except KeyboardInterrupt:
                        record_last_error(
                            "ask_chat", "api",
                            "KeyboardInterrupt saat menunggu API (chat).",
                        )
                        exit_requested  = True
                        interrupted     = True
                        persona_at_exit = "api"
                        break
                    except Exception as ex:
                        record_last_error("ask_chat", "api", str(ex))
                        if not plain:
                            print_system("😿 API error. Fallback ke LOCAL.")
                        # Fallback: ganti backend + update system prompt di history
                        backend  = "local"
                        route    = f"AUTO→LOCAL • {local_model_for(cfg, 'ask') or '(unset)'}"
                        label, greeting, sys_prompt = _persona_for("local")
                        history[0] = {"role": "system", "content": _full_system(sys_prompt, mem_ctx)}
                        # Lanjut ke LOCAL path di bawah

            # ------------------------------------------------
            # LOCAL path (primer atau fallback dari api)
            # ------------------------------------------------
            try:
                host  = ollama_host(cfg)
                model = local_model_for(cfg, "ask") or "llama3.1:8b"
                tok   = local_tokens_for_chat(user)
                with ChatSpinner("Sili AI 🌿 lagi mikir..."):
                    answer = ollama_chat(
                        host, model, history, timeout=260, num_predict=tok
                    )
                flush_stdin()
                history.append({"role": "assistant", "content": answer})
                ask_ui.print_chat_turn(
                    label, backend="local", answer=answer, plain=plain
                )
            except KeyboardInterrupt:
                record_last_error(
                    "ask_chat", "local",
                    "KeyboardInterrupt saat menunggu LOCAL (chat).",
                )
                exit_requested  = True
                interrupted     = True
                persona_at_exit = "local"
                break
            except Exception as ex:
                record_last_error("ask_chat", "local", str(ex))
                if not plain:
                    print_brief_error("LOCAL gagal jawab. Cek ai status.")
                else:
                    print("LOCAL gagal jawab.")

        # --------------------------------------------------------
        # Memory summary (API only, setelah sesi selesai)
        # --------------------------------------------------------
        _try_save_memory(cfg, mode, history, used_api_at_least_once)

    finally:
        cursor_show()
        alt_screen_exit()

    if exit_requested:
        from system_logic.terminal.ansi import print_goodbye
        print_goodbye(persona_at_exit, interrupted)

    return 0


# ============================================================
# Memory summary helper
# ============================================================

def _try_save_memory(
    cfg: dict,
    mode: str,
    history: list[dict],
    used_api: bool,
) -> None:
    """
    Ringkas percakapan dan simpan ke memori via Gemini API.
    Hanya dijalankan jika:
        - API pernah dipakai dalam sesi ini (used_api=True).
        - Mode adalah 'api' atau 'auto'.
        - Konten percakapan cukup panjang (>= 200 char, >= 4 turns).
        - API masih valid saat akhir sesi.

    Credential (API key, secret) di-redact sebelum disimpan.

    Args:
        cfg     : Config dict.
        mode    : Backend mode string ('api', 'local', 'auto').
        history : Full conversation history list.
        used_api: Apakah API pernah berhasil dipakai dalam sesi ini.
    """
    if not used_api or mode not in ("api", "auto"):
        return

    ok, _, _ = validate_api_config(cfg)
    if not ok:
        return

    try:
        convo: list[str] = []
        for msg in history[-18:]:
            role    = msg.get("role")
            content = str(msg.get("content") or "").strip()
            if not content:
                continue
            if role == "user":
                convo.append(f"User: {content}")
            elif role == "assistant":
                convo.append(f"Asisten: {content}")

        convo_text = "\n".join(convo).strip()
        if len(convo_text) < 200 or len(convo) < 4:
            return  # konten terlalu pendek untuk diringkas

        summarizer = [
            {
                "role": "system",
                "content": (
                    "Ringkas percakapan ini untuk memori singkat.\n"
                    "- Tulis dalam kalimat Bahasa Indonesia yang lengkap.\n"
                    "- Sebutkan topik utama + preferensi/keputusan user bila ada.\n"
                    "- Jangan simpan credential atau informasi rahasia.\n"
                    "- Jangan terlalu singkat (minimal ~20 kata)."
                ),
            },
            {"role": "user", "content": convo_text},
        ]
        summary = gemini_generate(cfg, summarizer, timeout=35, max_output_tokens=220)

        # Redact credential patterns
        summary = re.sub(
            r"(AIza[0-9A-Za-z\-_]+|sk-[0-9A-Za-z]+)",
            "[REDACTED]",
            summary,
        ).strip()

        if len(summary.split()) < 10:
            return  # ringkasan terlalu pendek, tidak berguna

        if summary and summary[-1] not in ".!?":
            summary += "."

        save_memory(summary, cfg)

    except Exception as ex:
        record_last_error("memory_summary", "api", str(ex))