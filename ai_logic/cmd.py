from __future__ import annotations

import json
import os
import subprocess
from shutil import which

from ai_logic.common import mem_context_text, record_last_error, backend_mode, route_for
from ai_logic.ui.ansi import (
    Spinner,
    flush_stdin,
    print_brief_error,
    print_info,
    print_meta_line,
    prompt_text,
    tag,
    wrap,
    term_size,
    c_bold,
    c_green,
    c_red,
    c_reset,
    c_yellow,
)

from ai_logic.backends.api_gemini import generate as gemini_generate, validate_api_config, gemini_model_id
from ai_logic.backends.local_ollama import chat as ollama_chat, ollama_host, local_model_for
from ai_logic.ui.prompts import local_tokens_for_cmd


DENY_SUBSTRINGS = [
    "rm -rf /",
    " mkfs",
    "dd if=",
    ":(){:|:&};:",
    " shutdown",
    " reboot",
    " poweroff",
]


def _cmd_help() -> None:
    print("Usage:")
    print('  ./ai-term cmd "..."' )
    print("Flags:")
    print("  --help   Tampilkan help")


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

    system_prompt = (
        "Aku mengubah permintaan user menjadi SATU command Linux yang relevan dan aman.\n"
        "Aku WAJIB mengembalikan JSON valid saja dengan schema:\n"
        '{ "purpose": "tujuan singkat", "command": "satu baris", "risk": "risiko singkat" }\n'
        "- command wajib satu baris.\n"
        "- risk 1–3 kalimat.\n"
        f"- Direktori kerja saat ini: {pwd}\n"
    )

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
            print_info(f"{tag('SISTEM AI', c_yellow())} API tidak siap: {note}. Fallback ke LOCAL.")
            backend = "local"
        else:
            try:
                with Spinner("Aku lagi nyari command terbaik..."):
                    raw = gemini_generate(cfg, messages, timeout=70, max_output_tokens=420, json_schema=json_schema)
                flush_stdin()
                try:
                    purpose, cmd, risk = parse_obj(raw)
                except Exception:
                    raw2 = repair_json_once(raw, "api")
                    purpose, cmd, risk = parse_obj(raw2)
                return render_cmd_flow(cmd, risk, purpose)
            except KeyboardInterrupt:
                record_last_error("cmd", "api", "KeyboardInterrupt saat menunggu API (cmd).")
                flush_stdin()
                print_brief_error("Dibatalkan")
                return 130
            except Exception as ex:
                record_last_error("cmd", "api", str(ex))
                flush_stdin()
                if mode in ("auto", "api"):
                    print_info(f"{tag('SISTEM AI', c_yellow())} API error. Fallback ke LOCAL.")
                backend = "local"

    # LOCAL path
    try:
        host = ollama_host(cfg)
        m = local_model_for(cfg, "cmd") or "llama3.1:8b"
        tok = local_tokens_for_cmd()
        with Spinner("Aku lagi nyari command terbaik..."):
            raw = ollama_chat(host, m, messages, timeout=140, num_predict=tok)
        flush_stdin()
        try:
            purpose, cmd, risk = parse_obj(raw)
        except Exception:
            raw2 = repair_json_once(raw, "local")
            purpose, cmd, risk = parse_obj(raw2)
        return render_cmd_flow(cmd, risk, purpose)
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
    c = (cmd or "").strip()
    if not c or "\n" in c or "\r" in c:
        return True
    for bad in DENY_SUBSTRINGS:
        if bad in c:
            return True
    return False


def confirm(prompt: str) -> bool:
    ans = prompt_text(prompt).strip().lower()
    return ans == "y"


def run_command(cmd: str) -> int:
    p = subprocess.run(cmd, shell=True)
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


def render_cmd_flow(cmd: str, risk: str, purpose: str) -> int:
    cols, _ = term_size()

    if is_denied(cmd):
        print(f"{tag('BLOCKED', c_red())} Aku blokir perintah ini karena terdeteksi berbahaya.")
        return 3

    if purpose:
        print(f"{c_bold()}Tujuan:{c_reset()} {wrap(purpose, width=min(cols, 120))}")
    print(f"{c_bold()}Command:{c_reset()} {cmd}")
    print(f"{c_bold()}Risiko:{c_reset()} {wrap(risk or 'Risiko tidak dijelaskan.', width=min(cols, 120))}")

    if "sudo" in (cmd or "").split():
        if not confirm("🔐 Command ini pakai sudo. Tetap lanjut? [y/N]: "):
            if copy_to_clipboard(cmd):
                print_info("Command aku salin ke clipboard.")
            print(f"{tag('CANCEL', c_yellow())} Aku batalin.")
            return 0

    if confirm("⚠️  Jalankan command ini? [y/N]: "):
        rc = run_command(cmd)
        print(f"{tag('DONE', c_green())} Selesai. Exit code: {rc}")
        return rc

    if copy_to_clipboard(cmd):
        print_info("Command aku salin ke clipboard.")
    print(f"{tag('CANCEL', c_yellow())} Aku batalin. Command tidak dijalankan.")
    return 0
