from __future__ import annotations

import os
import re
from dataclasses import dataclass

from system_logic.core.routing import backend_mode, route_info
from system_logic.core.memory import mem_context_text
from system_logic.core.last_error import record_last_error
from system_logic.core.safety import evaluate_command, DEFAULT_DENY_SUBSTRINGS
from system_logic.core.exec import run_shell_command

from system_logic.terminal.ansi import (
    Spinner,
    flush_stdin,
    print_brief_error,
    print_meta_line,
    print_system,
    prompt_text,
)

from system_logic.commands.cmd.prompts import (
    prompt_cmd_api,
    prompt_cmd_local,
)

from system_logic.commands.cmd import ui as cmd_ui

from system_logic.backends.local_ollama import chat as ollama_chat, ollama_host, local_model_for
from system_logic.backends.api_gemini import generate as gemini_generate, validate_api_config, gemini_model_id


@dataclass
class CmdResult:
    purpose: str
    command: str
    risk: str


def _force_single_paragraph(text: str) -> str:
    s = re.sub(r"\s*\n+\s*", " ", (text or "").strip())
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip()


def _parse_blocks(text: str) -> CmdResult:
    """
    Parser longgar untuk output model:
    cari tiga bagian: Tujuan / Command / Risiko.
    Fallback: kalau tidak ketemu, command = baris non-empty pertama.
    """
    raw = (text or "").strip()
    if not raw:
        return CmdResult(purpose="", command="", risk="")

    # normalize headings
    t = raw.replace("\r\n", "\n")

    # pattern heading blocks
    def grab(label: str) -> str:
        m = re.search(rf"(?is)\b{label}\b\s*:\s*(.+?)(?=\n\s*\w+\s*:|\Z)", t)
        return (m.group(1).strip() if m else "")

    purpose = grab("Tujuan|Goal|Purpose")
    command = grab("Command|Perintah")
    risk = grab("Risiko|Risk")

    if not command:
        # fallback: first non-empty line that looks like a shell command
        lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
        command = lines[0] if lines else ""

    return CmdResult(purpose=purpose, command=command, risk=risk)


def handle(argv: list[str], cfg: dict) -> int:
    if not argv or argv[0] in ("-h", "--help", "-help"):
        cmd_ui.print_help()
        return 2

    user_text = " ".join(argv).strip()
    if not user_text:
        cmd_ui.print_help()
        return 2

    mode = backend_mode(cfg)
    info = route_info(cfg, "cmd")
    backend = info.backend
    route = info.label

    model = gemini_model_id(cfg) if backend == "api" else (local_model_for(cfg, "cmd") or "(unset)")
    print_meta_line("CMD", route, model)

    mem_ctx = mem_context_text(cfg).strip()

    pwd = os.getcwd()
    sys_prompt = prompt_cmd_api(pwd) if backend == "api" else prompt_cmd_local(pwd)

    messages = [{"role": "system", "content": sys_prompt}]
    if mem_ctx:
        messages.append({"role": "system", "content": mem_ctx})
    messages.append({"role": "user", "content": user_text})

    flush_stdin()

    # === Generate via API (if selected/auto) ===
    generated = ""
    used_backend = backend

    if backend == "api":
        ok, note, detail = validate_api_config(cfg)
        if not ok:
            record_last_error("cmd_generate", "api", f"{note}\n{detail}")
            print_system(f"API tidak siap: {note}. Aku fallback ke LOCAL.")
            used_backend = "local"
        else:
            try:
                with Spinner("Aku lagi nyusun command yang aman... ✨"):
                    generated = gemini_generate(cfg, messages, timeout=70, max_output_tokens=380)
                used_backend = "api"
            except KeyboardInterrupt:
                record_last_error("cmd_generate", "api", "KeyboardInterrupt saat generate cmd (api).")
                flush_stdin()
                print_brief_error("Dibatalkan")
                return 130
            except Exception as ex:
                record_last_error("cmd_generate", "api", str(ex))
                flush_stdin()
                if mode in ("auto", "api"):
                    print_system("😿 API error. Aku pindah ke LOCAL dulu ya.")
                used_backend = "local"

    # === Generate via LOCAL ===
    if used_backend == "local":
        try:
            host = ollama_host(cfg)
            m = local_model_for(cfg, "cmd") or "llama3.1:8b"
            with Spinner("Aku lagi nyusun command yang aman... 🌿"):
                generated = ollama_chat(host, m, messages, timeout=260, num_predict=420)
        except KeyboardInterrupt:
            record_last_error("cmd_generate", "local", "KeyboardInterrupt saat generate cmd (local).")
            flush_stdin()
            print_brief_error("Dibatalkan")
            return 130
        except Exception as ex:
            record_last_error("cmd_generate", "local", str(ex))
            flush_stdin()
            print_brief_error("Mode lokal juga gagal")
            return 2

    flush_stdin()

    res = _parse_blocks(generated)
    cmd_ui.render_blocks(res.command, res.risk, res.purpose, backend=used_backend)

    # Safety check
    decision = evaluate_command(
        res.command,
        deny_substrings=DEFAULT_DENY_SUBSTRINGS,
        risky_patterns=None,
    )

    if decision.denied:
        print_brief_error("Command terdeteksi berbahaya dan ditolak.")
        return 2

    # Action loop
    while True:
        try:
            flush_stdin()
            choice = prompt_text("\nAksi [run/copy/edit/cancel] › ").strip().lower()
        except KeyboardInterrupt:
            print_brief_error("Dibatalkan")
            return 130

        if choice in ("", "run", "r"):
            # Execute
            try:
                code, out = run_shell_command(cfg, res.command)
                if out:
                    print(out)
                return int(code)
            except KeyboardInterrupt:
                print_brief_error("Dibatalkan")
                return 130
            except Exception as ex:
                record_last_error("cmd_exec", "shell", str(ex))
                print_brief_error("Gagal menjalankan command")
                return 2

        if choice in ("copy", "c"):
            # Print only the command for easy copy
            print(res.command)
            return 0

        if choice in ("edit", "e"):
            try:
                flush_stdin()
                edited = prompt_text("Edit command › ").strip()
            except KeyboardInterrupt:
                print_brief_error("Dibatalkan")
                return 130
            if edited:
                res.command = edited
                cmd_ui.render_blocks(res.command, res.risk, res.purpose, backend=used_backend)
                # re-evaluate
                decision = evaluate_command(
                    res.command,
                    deny_substrings=DEFAULT_DENY_SUBSTRINGS,
                    risky_patterns=None,
                )
                if decision.denied:
                    print_brief_error("Command edit terdeteksi berbahaya dan ditolak.")
                    return 2
            continue

        if choice in ("cancel", "x", "q", "quit", "exit"):
            return 0

        print_system("Pilihan tidak dikenal. Pilih: run / copy / edit / cancel")
