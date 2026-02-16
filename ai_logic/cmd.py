"""AI_IN-TERMINAL — cmd
Version: 1.6 (2026-01-20)

Changelog (1.6)
- UI/UX diperbaiki: help lebih rapi + emoji, output dibungkus (wrap) termasuk command.
- Confirm flow ditingkatkan: pilihan Run / Copy / Edit / Cancel.
- Konfirmasi menerima y/ya/yes dan n/no.
- Sudo detection dibuat lebih aman (word-boundary), tetap tanpa paksa.
- Persona tetap: API = Sili Pinter 💍, LOCAL = Sili AI 🌿.

Notes
- Command ini TERPISAH dari `ai`. Entry point: `cmd ...`
- Output didesain "rame tapi enak" (emoji secukupnya) dan tetap aman.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from shutil import which

from ai_logic.common import (
    mem_context_text,
    record_last_error,
    backend_mode,
    route_for,
    DENY_SUBSTRINGS as COMMON_DENY_SUBSTRINGS,
    evaluate_command,
    run_shell_command,
)
from ai_logic.ui.ansi import (
    Spinner,
    flush_stdin,
    print_brief_error,
    print_info,
    print_warn,
    print_meta_line,
    prompt_text,
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

from ai_logic.backends.api_gemini import (
    generate as gemini_generate,
    validate_api_config,
    gemini_model_id,
)
from ai_logic.backends.local_ollama import (
    chat as ollama_chat,
    ollama_host,
    local_model_for,
)
from ai_logic.ui.prompts import (
    local_tokens_for_cmd,
    prompt_cmd_api,
    prompt_cmd_local,
)


# Centralized deny-list (compat alias; single source of truth in ai_logic.core.safety)
DENY_SUBSTRINGS = list(COMMON_DENY_SUBSTRINGS)

_RE_WORD_SUDO = re.compile(r"(^|\s)sudo(\s|$)")


def _persona_label(backend: str) -> tuple[str, str]:
    return ("Sili Pinter", "💍") if backend == "api" else ("Sili AI", "🌿")


def _cmd_help() -> None:
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


def handle(argv: list[str], cfg: dict) -> int:
    if not argv or argv[0] in ("-h", "--help", "help"):
        _cmd_help()
        return 2

    text = " ".join(argv).strip()
    if not text:
        _cmd_help()
        return 2

    mode = backend_mode(cfg)
    backend, route = route_for(cfg, "cmd")
    model = gemini_model_id(cfg) if backend == "api" else (local_model_for(cfg, "cmd") or "(unset)")
    print_meta_line("CMD", route, model)

    pwd = os.getcwd()
    mem_ctx = mem_context_text(cfg).strip()

    system_prompt = prompt_cmd_api(pwd) if backend == "api" else prompt_cmd_local(pwd)

    messages = [{"role": "system", "content": system_prompt}]
    if mem_ctx:
        messages.append({"role": "system", "content": mem_ctx})
    messages.append({"role": "user", "content": text})

    json_schema = {
        "type": "object",
        "properties": {
            "purpose": {"type": "string"},
            "command": {"type": "string"},
            "risk": {"type": "string"},
        },
        "required": ["purpose", "command", "risk"],
    }

    def parse_obj(raw: str) -> tuple[str, str, str]:
        obj = json.loads(raw)
        return (
            str(obj.get("purpose") or "").strip(),
            str(obj.get("command") or "").strip(),
            str(obj.get("risk") or "").strip(),
        )

    def repair_json_once(raw: str, backend_name: str) -> str:
        fixer = [
            {"role": "system", "content": "Kamu HARUS mengeluarkan JSON valid saja. Tanpa teks lain."},
            {"role": "user", "content": f"Perbaiki output ini menjadi JSON valid sesuai schema (jangan ubah makna):\n{raw}"},
        ]
        if backend_name == "api":
            return gemini_generate(cfg, fixer, timeout=30, max_output_tokens=220, json_schema=json_schema)
        host = ollama_host(cfg)
        m = local_model_for(cfg, "cmd") or "llama3.1:8b"
        return ollama_chat(host, m, fixer, timeout=60, num_predict=72)

    flush_stdin()

    # API path
    if backend == "api":
        ok, note, detail = validate_api_config(cfg)
        if not ok:
            record_last_error("cmd", "api", f"{note}\n{detail}")
            print_info(f"{tag('SISTEM AI', c_yellow())} API tidak siap: {note}. Aku fallback ke LOCAL 🌿")
            backend = "local"
        else:
            try:
                with Spinner("Aku lagi nyari command terbaik... 💡"):
                    raw = gemini_generate(cfg, messages, timeout=70, max_output_tokens=420, json_schema=json_schema)
                flush_stdin()
                try:
                    purpose, cmd, risk = parse_obj(raw)
                except Exception:
                    raw2 = repair_json_once(raw, "api")
                    purpose, cmd, risk = parse_obj(raw2)
                return render_cmd_flow(cmd, risk, purpose, cfg, backend="api")
            except KeyboardInterrupt:
                record_last_error("cmd", "api", "KeyboardInterrupt saat menunggu API (cmd).")
                flush_stdin()
                print_brief_error("Dibatalkan")
                return 130
            except Exception as ex:
                record_last_error("cmd", "api", str(ex))
                flush_stdin()
                if mode in ("auto", "api"):
                    print_warn(f"{tag('SISTEM AI', c_yellow())} API error. Aku pindah ke LOCAL dulu ya 🌿")
                backend = "local"

    # LOCAL path
    try:
        host = ollama_host(cfg)
        m = local_model_for(cfg, "cmd") or "llama3.1:8b"
        tok = local_tokens_for_cmd()
        with Spinner("Aku lagi nyari command terbaik... 🌿"):
            raw = ollama_chat(host, m, messages, timeout=140, num_predict=tok)
        flush_stdin()
        try:
            purpose, cmd, risk = parse_obj(raw)
        except Exception:
            raw2 = repair_json_once(raw, "local")
            purpose, cmd, risk = parse_obj(raw2)
        return render_cmd_flow(cmd, risk, purpose, cfg, backend="local")
    except KeyboardInterrupt:
        record_last_error("cmd", "local", "KeyboardInterrupt saat menunggu LOCAL (cmd).")
        flush_stdin()
        print_brief_error("Dibatalkan")
        return 130
    except Exception as ex:
        record_last_error("cmd", "local", str(ex))
        flush_stdin()
        print_brief_error("Mode lokal juga gagal")
        return 2


def is_denied(cmd: str) -> bool:
    return bool(evaluate_command(cmd).blocked)


def _confirm_yn(prompt: str, default_no: bool = True) -> bool:
    ans = prompt_text(prompt).strip().lower()
    if not ans:
        return not default_no
    if ans in ("y", "yes", "ya"):
        return True
    if ans in ("n", "no", "tidak"):
        return False
    # unknown -> treat as no
    return False


def run_command(cmd: str, cfg: dict | None = None) -> int:
    p = run_shell_command(cmd, cfg=cfg or {})
    return int(p.returncode)


def copy_to_clipboard(text: str) -> bool:
    if not (text or "").strip():
        return False
    wl = which("wl-copy")
    if wl:
        try:
            p = subprocess.Popen([wl], stdin=subprocess.PIPE)
            p.communicate(text.encode("utf-8"))
            return p.returncode == 0
        except Exception:
            return False
    xclip = which("xclip")
    if xclip:
        try:
            p = subprocess.Popen([xclip, "-selection", "clipboard"], stdin=subprocess.PIPE)
            p.communicate(text.encode("utf-8"))
            return p.returncode == 0
        except Exception:
            return False
    xsel = which("xsel")
    if xsel:
        try:
            p = subprocess.Popen([xsel, "--clipboard", "--input"], stdin=subprocess.PIPE)
            p.communicate(text.encode("utf-8"))
            return p.returncode == 0
        except Exception:
            return False
    return False


def _render_blocks(cmd: str, risk: str, purpose: str, backend: str) -> None:
    cols, _ = term_size()
    w = min(cols, 110)
    label, emo = _persona_label(backend)
    print(f"{c_bold()}{emo} {label}:{c_reset()} {c_dim()}(hasil generate){c_reset()}")
    print(f"{c_dim()}{hr(width=min(cols, 60))}{c_reset()}")

    if purpose:
        print(f"{c_bold()}🎯 Tujuan:{c_reset()} {wrap(purpose, width=w)}")

    print(f"{c_bold()}🧾 Command:{c_reset()}")
    print(wrap(cmd or "(empty)", width=w))

    print(f"{c_bold()}⚠️  Risiko:{c_reset()} {wrap(risk or 'Risiko tidak dijelaskan.', width=w)}")


def _choose_action() -> str:
    ans = prompt_text("\nPilih aksi: [r]un  [c]opy  [e]dit  [n]cancel  (default n): ").strip().lower()
    if not ans:
        return "n"
    if ans in ("r", "run"):
        return "r"
    if ans in ("c", "copy"):
        return "c"
    if ans in ("e", "edit"):
        return "e"
    return "n"


def render_cmd_flow(cmd: str, risk: str, purpose: str, cfg: dict | None = None, backend: str = "local") -> int:
    cmd = (cmd or "").strip()
    risk = (risk or "").strip()
    purpose = (purpose or "").strip()

    if is_denied(cmd):
        print(f"{tag('BLOCKED', c_red())} 🚫 Aku blokir perintah ini karena terdeteksi berbahaya.")
        return 3

    _render_blocks(cmd, risk, purpose, backend=backend)

    # sudo gating (soft) — but do not be noisy for non-sudo commands
    if _RE_WORD_SUDO.search(cmd or ""):
        ok = _confirm_yn("🔐 Ini pakai sudo. Lanjut tetap aman? [y/ya/yes untuk lanjut, selain itu batal]: ")
        if not ok:
            if copy_to_clipboard(cmd):
                print_info("📋 Command aku salin ke clipboard.")
            print(f"{tag('CANCEL', c_yellow())} 😌 Oke, aku batalin.")
            return 0

    # action loop (at most 2 edits to keep it snappy)
    for _ in range(3):
        action = _choose_action()

        if action == "c":
            if copy_to_clipboard(cmd):
                print_info("📋 Command aku salin ke clipboard.")
            else:
                print_warn("📋 Aku gagal copy (wl-copy/xclip/xsel tidak ada?).")
            print(f"{tag('DONE', c_green())} ✨ Beres.")
            return 0

        if action == "e":
            new_cmd = prompt_text("✏️  Edit command (kosong = batal edit): ").rstrip("\n")
            if new_cmd.strip():
                cmd = new_cmd.strip()
                if is_denied(cmd):
                    print(f"{tag('BLOCKED', c_red())} 🚫 Edit ini terdeteksi berbahaya. Aku batal pakai command itu.")
                else:
                    print_info("✅ Command diupdate.")
                    _render_blocks(cmd, risk, purpose, backend=backend)
            continue

        if action == "r":
            if not _confirm_yn("⚡ Jalankan command ini sekarang? [y/ya/yes untuk run]: "):
                print(f"{tag('CANCEL', c_yellow())} Oke, gak jadi run.")
                continue
            rc = run_command(cmd, cfg=cfg)
            if rc == 0:
                print(f"{tag('DONE', c_green())} ✅ Selesai. Exit code: {rc}")
            else:
                print(f"{tag('WARN', c_yellow())} ⚠️  Selesai tapi exit code: {rc}")
                print(wrap("Kalau ini tidak sesuai, coba cek output di atas atau jalankan lagi dengan opsi verbose.", width=min(term_size()[0], 110)))
            return rc

        # cancel
        if copy_to_clipboard(cmd):
            print_info("📋 Command aku salin ke clipboard.")
        print(f"{tag('CANCEL', c_yellow())} 😌 Aku batalin. Command tidak dijalankan.")
        return 0

    print(f"{tag('CANCEL', c_yellow())} Timeout pilihan aksi.")
    return 0
