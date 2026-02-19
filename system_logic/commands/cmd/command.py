"""
commands/cmd/command.py
=======================
Core handler untuk `cmd` command.

Entry point:
    handle(argv, cfg) -> int

Alur eksekusi:
    1. Parse argv  → jika help/kosong, tampilkan help, return 0.
    2. Resolve backend (api | local) via route_info().
    3. Build system prompt + messages (1 system message, consolidated).
    4. Generate:
         a. API (Gemini)  — pakai json_schema untuk enforce JSON output.
         b. LOCAL (Ollama) — pakai _parse_model_output() sebagai parser.
         c. Fallback api → local jika API tidak siap atau error.
    5. Parse hasil → CmdResult.
    6. Guard kosong: tolak jika command string kosong.
    7. Guard safety: tolak jika command blocked oleh policy.
    8. Guard needs_confirm: minta konfirmasi eksplisit jika command berisiko.
    9. Action loop: run / copy / edit / cancel.

Bug yang difix vs versi lama:
    [C-01] evaluate_command() dipanggil dengan kwargs 'deny_substrings'
           yang tidak ada di safety.py aktual → TypeError. FIXED.
    [C-02] SafetyDecision.denied tidak ada; field aktual adalah .blocked
           → AttributeError saat runtime. FIXED.
    [C-03] run_shell_command(cfg, cmd) — argumen terbalik dan return type
           adalah CompletedProcess, bukan tuple (code, out) → TypeError. FIXED.
    [C-04] _parse_blocks tidak handle JSON padahal prompt wajib JSON output
           → command selalu salah parse. FIXED: API pakai json_schema,
           LOCAL pakai _parse_model_output() dengan JSON-first strategy.
    [C-05] Tidak ada guard jika command kosong setelah parse → execute
           string kosong ke shell. FIXED.
    [C-06] messages pakai 2× role='system' → beberapa model error.
           FIXED: digabung ke 1 system message.
    [C-07] _force_single_paragraph didefinisikan tapi tidak pernah dipanggil
           → dead code. FIXED: diganti _sanitize_text() yang aktif dipakai.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass

from system_logic.core.routing import route_info
from system_logic.core.memory import mem_context_text
from system_logic.core.last_error import record_last_error
from system_logic.core.safety import evaluate_command, SafetyPolicy
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
    JSON_SCHEMA,
    prompt_cmd_api,
    prompt_cmd_local,
)
from system_logic.commands.cmd import ui as cmd_ui

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
# Data model
# ============================================================

@dataclass
class CmdResult:
    """Hasil generate dari model: tiga field utama yang ditampilkan ke user."""
    purpose: str
    command: str
    risk: str


# ============================================================
# Internal helpers
# ============================================================

def _sanitize_text(text: str) -> str:
    """
    Normalisasi teks multi-baris menjadi satu paragraf bersih.
    Dipakai untuk membersihkan field 'purpose' dan 'risk' dari output model.
    """
    s = re.sub(r"\s*\n+\s*", " ", (text or "").strip())
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip()


def _strip_md_fence(text: str) -> str:
    """
    Hapus markdown code fence (```json ... ``` atau ``` ... ```) jika ada.
    Beberapa model lokal membungkus JSON dalam fence meski dilarang oleh prompt.
    """
    return re.sub(
        r"^```(?:json)?\s*|\s*```$",
        "",
        text.strip(),
        flags=re.MULTILINE,
    ).strip()


def _parse_model_output(raw: str) -> CmdResult:
    """
    Parse output mentah dari model lokal (Ollama) menjadi CmdResult.

    Strategi berurutan:
        1. JSON parse      — ekspektasi utama, prompt sudah mewajibkan JSON.
        2. Regex heading   — fallback jika model tidak patuh format JSON.
        3. First-line      — last resort; ambil baris pertama sebagai command.

    Catatan: Untuk backend API (Gemini), fungsi ini TIDAK dipanggil karena
    Gemini dipaksa output JSON valid via parameter json_schema.
    Output Gemini di-parse langsung via json.loads() di _generate_api().

    Returns:
        CmdResult. Field 'command' bisa kosong string jika semua strategi gagal.
        Caller wajib cek kosong sebelum lanjut.
    """
    text = (raw or "").strip()
    if not text:
        return CmdResult(purpose="", command="", risk="")

    # --- Strategi 1: JSON parse ---
    try:
        data = json.loads(_strip_md_fence(text))
        if isinstance(data, dict):
            return CmdResult(
                purpose=_sanitize_text(str(data.get("purpose", ""))),
                command=str(data.get("command", "")).strip(),
                risk=_sanitize_text(str(data.get("risk", ""))),
            )
    except (json.JSONDecodeError, ValueError):
        pass

    # --- Strategi 2: Regex heading ---
    normalized = text.replace("\r\n", "\n")

    def _grab(label: str) -> str:
        """Ekstrak nilai setelah label heading (misal 'Tujuan: ...')."""
        m = re.search(
            rf"(?is)\b{label}\b\s*:\s*(.+?)(?=\n\s*\w[\w\s]*\s*:|\Z)",
            normalized,
        )
        return _sanitize_text(m.group(1)) if m else ""

    purpose = _grab(r"Tujuan|Goal|Purpose")
    command = _grab(r"Command|Perintah").strip()
    risk    = _grab(r"Risiko|Risk")

    if command:
        return CmdResult(purpose=purpose, command=command, risk=risk)

    # --- Strategi 3: First non-empty line ---
    lines = [ln.strip() for ln in normalized.splitlines() if ln.strip()]
    return CmdResult(purpose="", command=lines[0] if lines else "", risk="")


def _build_messages(sys_prompt: str, mem_ctx: str, user_text: str) -> list[dict]:
    """
    Bangun daftar pesan untuk dikirim ke model.

    Memory context digabung ke dalam system prompt (bukan pesan terpisah)
    agar kompatibel dengan model yang hanya support 1 system message.

    Args:
        sys_prompt : System prompt utama (dari prompt_cmd_api/local).
        mem_ctx    : Memory context string, boleh kosong.
        user_text  : Input dari user.

    Returns:
        List of dict dengan keys 'role' dan 'content'.
    """
    full_system = sys_prompt
    if mem_ctx:
        full_system = f"{sys_prompt}\n\nMEMORY CONTEXT:\n{mem_ctx}"

    return [
        {"role": "system", "content": full_system},
        {"role": "user",   "content": user_text},
    ]


# ============================================================
# Internal: Generate wrappers
# ============================================================

def _generate_api(cfg: dict, messages: list[dict]) -> str:
    """
    Generate via Gemini API dengan json_schema untuk enforce JSON output.

    Raises:
        Exception: Diteruskan ke caller untuk di-handle (fallback ke local).

    Returns:
        String JSON yang sudah divalidasi mengandung key yang dibutuhkan.
    """
    raw = gemini_generate(
        cfg,
        messages,
        timeout=70,
        max_output_tokens=380,
        json_schema=JSON_SCHEMA,
    )
    # Gemini dengan json_schema dijamin return valid JSON, tapi kita tetap
    # validasi minimal agar tidak ada surprises.
    data = json.loads(raw)
    if not isinstance(data, dict) or "command" not in data:
        raise ValueError(f"Gemini output tidak sesuai schema: {raw[:200]}")
    return raw


def _generate_local(cfg: dict, messages: list[dict]) -> str:
    """
    Generate via Ollama lokal.

    Raises:
        Exception: Diteruskan ke caller.

    Returns:
        Raw string output dari model (belum di-parse).
    """
    host  = ollama_host(cfg)
    model = local_model_for(cfg, "cmd") or "llama3.1:8b"
    return ollama_chat(host, model, messages, timeout=260, num_predict=420)


# ============================================================
# Internal: Safety helpers
# ============================================================

def _check_safety(command: str) -> tuple[bool, bool]:
    """
    Evaluasi command via safety policy default.

    Returns:
        Tuple (blocked, needs_confirm).
        blocked      : True jika command harus ditolak sepenuhnya.
        needs_confirm: True jika command berisiko dan butuh konfirmasi user.
    """
    decision = evaluate_command(command, policy=SafetyPolicy())
    return decision.blocked, decision.needs_confirm


def _confirm_risky(command: str, flags: tuple) -> bool:
    """
    Minta konfirmasi eksplisit dari user untuk command berisiko.

    Returns:
        True jika user setuju lanjut, False jika menolak.
    """
    flag_str = ", ".join(flags) if flags else "risky"
    print_system(f"⚠️  Command ini terdeteksi berisiko [{flag_str}].")
    print_system(f"   {command}")
    try:
        flush_stdin()
        ans = prompt_text("Lanjutkan? [y/N] › ").strip().lower()
        return ans in ("y", "yes", "ya")
    except KeyboardInterrupt:
        return False


# ============================================================
# Entry point
# ============================================================

def handle(argv: list[str], cfg: dict) -> int:
    """
    Entry point utama untuk command `cmd`.

    Args:
        argv : Argumen setelah nama command (list of strings).
        cfg  : Config dict dari framework.

    Returns:
        Exit code: 0 = sukses, 2 = error/ditolak, 130 = Ctrl+C.
    """
    # --- Guard: help atau input kosong ---
    if not argv or argv[0] in ("-h", "--help", "-help"):
        cmd_ui.print_help()
        return 0

    user_text = " ".join(argv).strip()
    if not user_text:
        cmd_ui.print_help()
        return 0

    # --- Routing ---
    info    = route_info(cfg, "cmd")
    backend = info.backend   # 'api' atau 'local'

    model_display = (
        gemini_model_id(cfg)
        if backend == "api"
        else (local_model_for(cfg, "cmd") or "(unset)")
    )
    print_meta_line("CMD", info.label, model_display)

    # --- Build prompt & messages ---
    mem_ctx    = mem_context_text(cfg).strip()
    pwd        = os.getcwd()
    sys_prompt = prompt_cmd_api(pwd) if backend == "api" else prompt_cmd_local(pwd)
    messages   = _build_messages(sys_prompt, mem_ctx, user_text)

    # --------------------------------------------------------
    # Generate
    # --------------------------------------------------------
    raw_output   = ""
    used_backend = backend

    if backend == "api":
        # Validasi config API sebelum mencoba generate
        ok, note, _detail = validate_api_config(cfg)
        if not ok:
            record_last_error("cmd_generate", "api", f"{note}\n{_detail}")
            print_system(f"⚠️  API tidak siap: {note}. Fallback ke LOCAL.")
            used_backend = "local"
        else:
            try:
                with Spinner("Sili Pinter 💍 lagi nyusun command yang aman..."):
                    raw_output = _generate_api(cfg, messages)
                used_backend = "api"
            except KeyboardInterrupt:
                record_last_error(
                    "cmd_generate", "api",
                    "KeyboardInterrupt saat generate cmd (api).",
                )
                flush_stdin()
                print_brief_error("Dibatalkan")
                return 130
            except Exception as ex:
                record_last_error("cmd_generate", "api", str(ex))
                flush_stdin()
                print_system("😿 API error. Fallback ke LOCAL.")
                used_backend = "local"

    if used_backend == "local":
        try:
            with Spinner("Sili AI 🌿 lagi nyusun command yang aman..."):
                raw_output = _generate_local(cfg, messages)
        except KeyboardInterrupt:
            record_last_error(
                "cmd_generate", "local",
                "KeyboardInterrupt saat generate cmd (local).",
            )
            flush_stdin()
            print_brief_error("Dibatalkan")
            return 130
        except Exception as ex:
            record_last_error("cmd_generate", "local", str(ex))
            flush_stdin()
            print_brief_error("Mode lokal juga gagal. Cek Ollama service.")
            return 2

    # --------------------------------------------------------
    # Parse output
    # --------------------------------------------------------
    if used_backend == "api":
        # API sudah enforce JSON via json_schema, parse langsung
        try:
            data = json.loads(raw_output)
            res = CmdResult(
                purpose=_sanitize_text(str(data.get("purpose", ""))),
                command=str(data.get("command", "")).strip(),
                risk=_sanitize_text(str(data.get("risk", ""))),
            )
        except (json.JSONDecodeError, ValueError):
            # Fallback jika terjadi hal tidak terduga
            res = _parse_model_output(raw_output)
    else:
        # Ollama: pakai parser heuristik karena tidak support json_schema
        res = _parse_model_output(raw_output)

    # --------------------------------------------------------
    # Guard: command kosong
    # --------------------------------------------------------
    if not res.command:
        print_brief_error(
            "Generate berhasil tapi command kosong. "
            "Coba pertanyaan yang lebih spesifik."
        )
        return 2

    # Render hasil ke terminal
    cmd_ui.render_blocks(res.purpose, res.command, res.risk, backend=used_backend)

    # --------------------------------------------------------
    # Guard: safety check
    # --------------------------------------------------------
    blocked, needs_confirm = _check_safety(res.command)

    if blocked:
        print_brief_error(
            "Command terdeteksi berbahaya dan ditolak oleh safety policy."
        )
        return 2

    if needs_confirm:
        decision_obj = evaluate_command(res.command, policy=SafetyPolicy())
        allowed = _confirm_risky(res.command, decision_obj.flags)
        if not allowed:
            print_system("Dibatalkan oleh user.")
            return 0

    # --------------------------------------------------------
    # Action loop
    # --------------------------------------------------------
    while True:
        try:
            flush_stdin()
            choice = prompt_text("\nAksi [run/copy/edit/cancel] › ").strip().lower()
        except KeyboardInterrupt:
            print_brief_error("Dibatalkan")
            return 130

        # --- run ---
        if choice in ("", "run", "r"):
            try:
                result: subprocess.CompletedProcess = run_shell_command(
                    res.command, cfg=cfg
                )
                return result.returncode
            except KeyboardInterrupt:
                print_brief_error("Dibatalkan")
                return 130
            except Exception as ex:
                record_last_error("cmd_exec", "shell", str(ex))
                print_brief_error(f"Gagal menjalankan command: {ex}")
                return 2

        # --- copy ---
        if choice in ("copy", "c"):
            print(res.command)
            return 0

        # --- edit ---
        if choice in ("edit", "e"):
            try:
                flush_stdin()
                edited = prompt_text("Edit command › ").strip()
            except KeyboardInterrupt:
                print_brief_error("Dibatalkan")
                return 130

            if not edited:
                print_system("Tidak ada perubahan.")
                continue

            res.command = edited
            cmd_ui.render_blocks(
                res.purpose, res.command, res.risk, backend=used_backend
            )

            # Re-evaluate safety setelah edit
            blocked, needs_confirm = _check_safety(res.command)
            if blocked:
                print_brief_error(
                    "Command hasil edit terdeteksi berbahaya dan ditolak."
                )
                return 2
            if needs_confirm:
                decision_obj = evaluate_command(res.command, policy=SafetyPolicy())
                allowed = _confirm_risky(res.command, decision_obj.flags)
                if not allowed:
                    print_system("Dibatalkan oleh user.")
                    return 0

            continue

        # --- cancel ---
        if choice in ("cancel", "x", "q", "quit", "exit"):
            return 0

        print_system("Pilihan tidak dikenal. Ketik: run / copy / edit / cancel")