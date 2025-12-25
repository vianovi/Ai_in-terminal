from __future__ import annotations

import json
import os
import sys

from ai_logic.common import (
    Spinner,
    api_generate,
    api_model_display,
    api_tokens_oneshot,
    backend_mode,
    c_bold,
    c_green,
    c_red,
    c_reset,
    c_yellow,
    confirm,
    copy_to_clipboard,
    flush_stdin,
    is_denied,
    local_model_for,
    local_tokens_for_cmd,
    mem_context_text,
    ollama_chat,
    ollama_host,
    print_brief_error,
    print_info,
    print_meta_line,
    record_last_error,
    route_for,
    run_command,
    tag,
    validate_api_config,
    wrap,
)


def mode_cmd(text: str, cfg: dict) -> int:
    mode = backend_mode(cfg)
    backend, route = route_for(cfg, "cmd")
    model = api_model_display(cfg) if backend == "api" else (local_model_for(cfg, "cmd") or "(unset)")
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
        "additionalProperties": False,
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
            return api_generate(cfg, fixer, timeout=30, max_output_tokens=220, json_schema=json_schema)
        host = ollama_host(cfg)
        m = local_model_for(cfg, "cmd") or "llama3.1:8b"
        return ollama_chat(host, m, fixer, timeout=60, num_predict=72)

    flush_stdin()

    # API path
    if backend == "api":
        ok, note, detail = validate_api_config(cfg)
        if not ok:
            record_last_error("cmd", "api", f"{note}\n{detail}")
            if mode == "api":
                print_brief_error(note)
            backend = "local"
        else:
            try:
                with Spinner("Aku lagi nyari command terbaik..."):
                    raw = api_generate(cfg, messages, timeout=70, max_output_tokens=420, json_schema=json_schema)
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
                print_info(f"{tag('AUTO', c_yellow())} -> {tag('LOCAL', c_green())} • {local_model_for(cfg,'cmd') or '(unset)'}")
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

def render_cmd_flow(cmd: str, risk: str, purpose: str) -> int:
    cols = 120

    if is_denied(cmd):
        sys.stdout.write(f"{tag('BLOCKED', c_red())} Aku blokir perintah ini karena terdeteksi berbahaya.\n")
        sys.stdout.flush()
        return 3

    if purpose:
        sys.stdout.write(f"{c_bold()}Tujuan:{c_reset()} {wrap(purpose, width=min(cols, 120))}\n")
    sys.stdout.write(f"{c_bold()}Command:{c_reset()} {cmd}\n")
    sys.stdout.write(f"{c_bold()}Risiko:{c_reset()} {wrap(risk or 'Risiko tidak dijelaskan.', width=min(cols, 120))}\n")
    sys.stdout.flush()

    if "sudo" in cmd.split():
        if not confirm("🔐 Command ini pakai sudo. Tetap lanjut? [y/N]: "):
            if copy_to_clipboard(cmd):
                sys.stdout.write(f"{tag('INFO', c_yellow())} Command aku salin ke clipboard.\n")
            sys.stdout.write(f"{tag('CANCEL', c_yellow())} Aku batalin.\n")
            sys.stdout.flush()
            return 0

    if confirm("⚠️  Jalankan command ini? [y/N]: "):
        rc = run_command(cmd)
        sys.stdout.write(f"{tag('DONE', c_green())} Selesai. Exit code: {rc}\n")
        sys.stdout.flush()
        return rc

    if copy_to_clipboard(cmd):
        sys.stdout.write(f"{tag('INFO', c_yellow())} Command aku salin ke clipboard.\n")
    sys.stdout.write(f"{tag('CANCEL', c_yellow())} Aku batalin. Command tidak dijalankan.\n")
    sys.stdout.flush()
    return 0

def handle_cmd(argv: list[str], cfg: dict) -> int:
    text = " ".join(argv).strip()
    return mode_cmd(text, cfg)
