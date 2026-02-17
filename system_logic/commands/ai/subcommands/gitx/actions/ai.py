"""
GitX Actions — AI Features (API-only).
Summary PR, AI commit message, Chat mode.

Semua fitur di file ini membutuhkan API key yang valid.
Fallback ke print prompt jika API tidak siap.

Import chain:
- system_logic.core.utils  → normalize_model_id
- system_logic.core.safety → is_denied (untuk filter command AI)
- system_logic.terminal    → ansi
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Optional

from system_logic.terminal import ansi
from system_logic.core.utils import normalize_model_id

from ..core.git import run_git
from ..ui.render import wrap, print_section, confirm, render_ai_plan, execute_git_commands


# ==========================================================
# Config helpers
# ==========================================================

def _api_provider(cfg: dict) -> str:
    return str((cfg.get("api") or {}).get("provider") or "gemini").strip().lower()

def _active_model_raw(cfg: dict) -> str:
    return str((cfg.get("api") or {}).get("active_model") or "").strip()


# ==========================================================
# API helpers
# ==========================================================

def _api_ready(cfg: dict) -> tuple[bool, str]:
    """
    Cek apakah API provider siap digunakan.
    Returns: (ok, note_message)
    """
    prov = _api_provider(cfg)
    if prov == "gemini":
        try:
            from ai_logic.backends.api_gemini import validate_api_config  # type: ignore
            ok, note, _detail = validate_api_config(cfg)
            return bool(ok), (note or "OK")
        except Exception as e:
            return False, f"Wrapper gemini tidak siap: {type(e).__name__}"
    return False, f"Provider '{prov}' belum didukung untuk AI GitX."


def _api_generate(
    cfg: dict,
    system: str,
    user: str,
    timeout: int = 30,
    max_out: int = 900,
) -> tuple[bool, str, str]:
    """
    Panggil API generate dan return (ok, meta, text).
    meta berisi timing atau error message.
    """
    prov = _api_provider(cfg)
    if prov == "gemini":
        try:
            from ai_logic.backends.api_gemini import generate as gemini_generate  # type: ignore
            msgs = [
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ]
            t0  = time.time()
            out = gemini_generate(cfg, msgs, timeout=timeout, max_output_tokens=max_out)
            dt  = time.time() - t0
            txt = (out or "").strip()
            if not txt:
                return False, f"API menjawab kosong ({dt:.2f}s).", ""
            return True, f"OK ({dt:.2f}s)", txt
        except Exception as e:
            return False, f"API error: {type(e).__name__}: {e}", ""
    return False, f"Provider '{prov}' belum didukung.", ""


def _extract_json_object(text: str) -> Optional[dict]:
    """
    Ambil JSON object dari output AI.
    Toleran terhadap code fence markdown.
    """
    s = (text or "").strip()

    # Coba strip code fence dulu
    if "```" in s:
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", s, flags=re.DOTALL | re.IGNORECASE)
        if m:
            s = m.group(1).strip()

    # Fallback: cari {...} terluar
    if not s.startswith("{"):
        i = s.find("{")
        j = s.rfind("}")
        if i >= 0 and j > i:
            s = s[i : j + 1].strip()

    try:
        d = json.loads(s)
        return d if isinstance(d, dict) else None
    except Exception:
        return None


# ==========================================================
# Git context builders
# ==========================================================

def _git_upstream_ref(root: Path) -> str:
    rc, out, _ = run_git(
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
        cwd=root, timeout=4,
    )
    return out.strip() if rc == 0 and out else ""


def _git_merge_base(root: Path, upstream: str) -> str:
    if not upstream:
        return ""
    rc, out, _ = run_git(["merge-base", "HEAD", upstream], cwd=root, timeout=6)
    return out.strip() if rc == 0 and out else ""


def _chat_snapshot(root: Path) -> str:
    """Buat snapshot singkat repo untuk context AI chat."""
    _, st,             _ = run_git(["status", "-sb"],                                cwd=root, timeout=5)
    _, br,             _ = run_git(["branch", "--show-current"],                     cwd=root, timeout=4)
    _, last,           _ = run_git(["log", "-n", "8", "--oneline", "--decorate"],    cwd=root, timeout=10)
    _, diff_stat,      _ = run_git(["diff", "--stat"],                               cwd=root, timeout=12)
    _, diff_cached,    _ = run_git(["diff", "--cached", "--stat"],                   cwd=root, timeout=12)

    diff_stat   = "\n".join(diff_stat.splitlines()[:40])
    diff_cached = "\n".join(diff_cached.splitlines()[:40])

    return (
        "===== REPO STATE =====\n"
        f"$ git status -sb\n{st}\n\n"
        f"$ git branch --show-current\n{br}\n\n"
        f"$ git log -n 8 --oneline --decorate\n{last}\n\n"
        f"$ git diff --stat\n{diff_stat}\n\n"
        f"$ git diff --cached --stat\n{diff_cached}\n"
        "======================\n"
    )


# ==========================================================
# action_ai_summary
# ==========================================================

def action_ai_summary(root: Path, cfg: dict) -> int:
    """
    Generate PR description / changelog summary menggunakan AI.
    Scope: commits dari merge-base ke HEAD (vs upstream).
    Fallback: print prompt siap-tempel jika API tidak siap.
    """
    print_section("AI SUMMARY (PR/CHANGELOG)")

    ok, note     = _api_ready(cfg)
    upstream     = _git_upstream_ref(root)
    base         = _git_merge_base(root, upstream) if upstream else ""

    _, st, _     = run_git(["status", "-sb"], cwd=root, timeout=5)

    if base:
        _, log_out,  _ = run_git(["log",  "--oneline", "--decorate", f"{base}..HEAD"], cwd=root, timeout=10)
        _, stat_out, _ = run_git(["diff", "--stat",    f"{base}..HEAD"],               cwd=root, timeout=12)
        _, name_out, _ = run_git(["diff", "--name-only", f"{base}..HEAD"],             cwd=root, timeout=12)
        scope_note     = f"Base: merge-base(HEAD, {upstream})"
    else:
        _, log_out,  _ = run_git(["log",  "-n", "20", "--oneline", "--decorate"],      cwd=root, timeout=10)
        _, stat_out, _ = run_git(["diff", "--stat"],                                   cwd=root, timeout=12)
        _, name_out, _ = run_git(["diff", "--name-only"],                              cwd=root, timeout=12)
        scope_note     = "Base: (no upstream) — using working tree diff + last 20 commits"

    stat_out = "\n".join(stat_out.splitlines()[:60])
    name_out = "\n".join(name_out.splitlines()[:80])
    log_out  = "\n".join(log_out.splitlines()[:80])

    user_prompt = (
        "===== SNAPSHOT =====\n"
        f"$ git status -sb\n{st}\n\n"
        f"{scope_note}\n\n"
        f"$ git log (scope)\n{log_out}\n\n"
        f"$ git diff --stat (scope)\n{stat_out}\n\n"
        f"$ git diff --name-only (scope)\n{name_out}\n"
        "====================\n"
        "Tolong buat ringkasan PR/changes dalam format markdown yang siap ditempel.\n"
    )

    system = (
        "Kamu adalah assistant untuk menulis PR description dan changelog yang rapi.\n"
        "Aturan:\n"
        "- Jangan mengarang perubahan yang tidak ada.\n"
        "- Output markdown dengan struktur:\n"
        "  ## Summary\n"
        "  ## Changes\n"
        "  ## Risks / Notes\n"
        "  ## Testing\n"
        "- Isi ringkas tapi jelas.\n"
    )

    if not ok:
        print(wrap(f"{ansi.tag('INFO', ansi.c_cyan())} API belum siap: {note}"))
        print(wrap("Aku print prompt siap-tempel ke `ask` (fallback):"))
        print("")
        print(system + "\n" + user_prompt)
        return 0

    with ansi.Spinner("AI lagi bikin summary"):
        gok, meta, txt = _api_generate(cfg, system, user_prompt, timeout=35, max_out=900)

    if not gok:
        print(wrap(f"{ansi.tag('INFO', ansi.c_cyan())} AI tidak bisa dipanggil: {meta}"))
        print(wrap("Fallback prompt siap-tempel:"))
        print("")
        print(system + "\n" + user_prompt)
        return 0

    print(wrap(f"{ansi.tag('INFO', ansi.c_cyan())} AI via API: {meta}"))
    print(txt)
    return 0


# ==========================================================
# action_ai_commit_msg
# ==========================================================

def _build_commitmsg_prompt(root: Path) -> tuple[str, bool]:
    """
    Buat prompt untuk AI commit message.
    Returns: (prompt_string, has_staged)
    Prioritas: staged diff → working tree diff.
    """
    _, st, _ = run_git(["status", "-sb"], cwd=root, timeout=5)

    rc, cached_stat, _ = run_git(["diff", "--cached", "--stat"], cwd=root, timeout=12)
    has_staged = bool(cached_stat.strip()) and (rc == 0)

    if has_staged:
        _, cached_name,  _ = run_git(["diff", "--cached", "--name-only"], cwd=root, timeout=12)
        _, cached_patch, _ = run_git(["diff", "--cached"],                cwd=root, timeout=16)
        patch_short = "\n".join((cached_patch.splitlines() if cached_patch else [])[:220])
        return (
            "===== SNAPSHOT =====\n"
            f"$ git status -sb\n{st}\n\n"
            f"$ git diff --cached --stat\n{cached_stat}\n\n"
            f"$ git diff --cached --name-only\n{cached_name}\n\n"
            f"$ git diff --cached (truncated)\n{patch_short}\n"
            "====================\n"
        ), True

    _, wt_stat,  _ = run_git(["diff", "--stat"],       cwd=root, timeout=12)
    _, wt_name,  _ = run_git(["diff", "--name-only"],  cwd=root, timeout=12)
    _, wt_patch, _ = run_git(["diff"],                 cwd=root, timeout=16)
    patch_short    = "\n".join((wt_patch.splitlines() if wt_patch else [])[:220])

    return (
        "===== SNAPSHOT =====\n"
        f"$ git status -sb\n{st}\n\n"
        f"$ git diff --stat\n{wt_stat}\n\n"
        f"$ git diff --name-only\n{wt_name}\n\n"
        f"$ git diff (truncated)\n{patch_short}\n"
        "====================\n"
    ), False


def _parse_commit_choices(text: str) -> list[str]:
    """Parse output AI menjadi list commit message candidates."""
    choices: list[str] = []
    for ln in (text or "").splitlines():
        s = ln.strip()
        if not s:
            continue
        s = re.sub(r"^\s*[-*]\s*", "", s)
        s = re.sub(r"^\s*\d+[\).\s]+", "", s)
        s = s.strip()
        if len(s) < 4 or len(s) > 120:
            continue
        choices.append(s)
        if len(choices) >= 6:
            break
    out: list[str] = []
    for c in choices:
        if c not in out:
            out.append(c)
    return out[:6]


def action_ai_commit_msg(
    root: Path,
    cfg: dict,
    apply: bool,
    amend: bool,
) -> int:
    """
    Generate kandidat commit message menggunakan AI.
    --apply : pilih dan langsung commit
    --amend : gunakan --amend saat commit
    """
    print_section("AI COMMIT MESSAGE (API-ONLY)")

    ok, note       = _api_ready(cfg)
    prompt, has_staged = _build_commitmsg_prompt(root)

    system = (
        "Kamu adalah assistant untuk membuat commit message yang bagus.\n"
        "Aturan:\n"
        "- Berikan 5 kandidat commit message.\n"
        "- Utamakan Conventional Commits bila relevan.\n"
        "- Message singkat, jelas, tidak lebay.\n"
        "- Output: hanya list message, 1 per baris.\n"
    )

    if not ok:
        print(wrap(f"{ansi.tag('INFO', ansi.c_cyan())} API belum siap: {note}"))
        print(wrap("Aku print prompt siap-tempel ke `ask` (fallback):"))
        print("")
        print(system + "\n" + prompt)
        return 0

    with ansi.Spinner("AI lagi bikin commit message"):
        gok, meta, txt = _api_generate(cfg, system, prompt, timeout=30, max_out=500)

    if not gok:
        print(wrap(f"{ansi.tag('INFO', ansi.c_cyan())} AI tidak bisa dipanggil: {meta}"))
        print(wrap("Fallback prompt siap-tempel:"))
        print("")
        print(system + "\n" + prompt)
        return 0

    txt = txt.strip()
    print(wrap(f"{ansi.tag('INFO', ansi.c_cyan())} AI via API: {meta}"))

    choices = _parse_commit_choices(txt)
    if not choices:
        print(txt)
        print(wrap(
            f"{ansi.tag('INFO', ansi.c_cyan())} "
            "Tidak bisa mem-parsing kandidat otomatis. Copy manual."
        ))
        return 0

    print(wrap("Kandidat commit message:"))
    for i, c in enumerate(choices, 1):
        print(wrap(f"  {i}. {c}"))

    if not apply:
        if not has_staged:
            print(wrap(
                f"{ansi.tag('INFO', ansi.c_cyan())} "
                "Catatan: belum ada staged changes. Stage dulu biar commit rapi."
            ))
        print(wrap(f"{ansi.tag('INFO', ansi.c_cyan())} Untuk auto-commit: ai gitx aicm --apply"))
        return 0

    if not has_staged:
        print(wrap(
            f"{ansi.tag('INFO', ansi.c_cyan())} "
            "Aku tidak auto-commit karena belum ada staged changes."
        ))
        print(wrap(
            "Saran: stage dulu (git add -p / git add <file>), "
            "lalu ulang `ai gitx aicm --apply`."
        ))
        return 0

    try:
        sel = ansi.prompt_text(
            f"{ansi.c_yellow()}Pilih nomor (1-{len(choices)}) atau ketik message sendiri: "
            f"{ansi.c_reset()}"
        ).strip()
    except KeyboardInterrupt:
        print("")
        return 0

    msg = ""
    if sel.isdigit():
        idx = int(sel)
        if 1 <= idx <= len(choices):
            msg = choices[idx - 1]
    else:
        msg = sel.strip()

    # Normalisasi: collapse whitespace
    msg = re.sub(r"\s+", " ", msg.strip()).strip()
    if not msg:
        print(wrap("Message kosong. Dibatalkan."))
        return 0

    verb = "AMEND" if amend else "COMMIT"
    print(wrap(f"{ansi.tag('WARN', ansi.c_yellow())} Akan menjalankan git commit dengan message:"))
    print(wrap(f'  "{msg}"'))
    if not confirm(f"{ansi.c_yellow()}Lanjutkan {verb}? [y/N] {ansi.c_reset()}"):
        print(wrap("Dibatalkan."))
        return 0

    cmd = ["commit", "-m", msg] if not amend else ["commit", "--amend", "-m", msg]
    rc, out, err = run_git(cmd, cwd=root, timeout=120)
    if rc != 0:
        ansi.print_brief_error(err or out or "Commit gagal.")
        return 2

    print(wrap(f"{ansi.c_green()}OK.{ansi.c_reset()} Commit sukses."))
    if out:
        print(out)
    return 0


# ==========================================================
# action_ai_chat
# ==========================================================

def _ai_chat_system() -> str:
    """System prompt untuk AI chat mode."""
    return (
        "Kamu adalah assistant Git untuk repo lokal.\n"
        "Tugas: jawab permintaan user dengan rencana + command git yang aman.\n"
        "Aturan ketat:\n"
        "- Hanya boleh mengusulkan command yang diawali 'git '.\n"
        "- Jangan usulkan command shell non-git.\n"
        "- Jangan gunakan operator shell seperti &&, ;, |, >, <.\n"
        "- Output WAJIB format JSON (tanpa code fence):\n"
        "{\n"
        '  "summary": "1-2 kalimat ringkas",\n'
        '  "commands": [\n'
        '    {"title":"...", "cmd":"git ...", "risk":"low|medium|high"}\n'
        "  ],\n"
        '  "notes": ["...","..."]\n'
        "}\n"
        "Risk guidance:\n"
        "- low: read-only / safe (status, log, diff)\n"
        "- medium: write but reversible (add, stash)\n"
        "- high: potentially destructive (reset --hard, clean -fd, rebase, force push)\n"
    )


def action_ai_chat(root: Path, cfg: dict, initial: str) -> int:
    """
    Chat mode interaktif dengan AI Git assistant.
    AI mengusulkan command git, user memutuskan eksekusi atau tidak.
    initial: pesan pertama opsional (dari 'ai gitx halo ...')
    """
    print_section("GITX CHAT MODE")

    ok, note = _api_ready(cfg)
    if not ok:
        print(wrap(f"{ansi.tag('INFO', ansi.c_cyan())} API belum siap untuk Chat GitX: {note}"))
        print(wrap("Saran: set env var key (GEMINI_API_KEY) lalu coba lagi."))
        return 2

    print(wrap(
        f"{ansi.tag('SISTEM AI', ansi.c_yellow())} "
        "GitX Chat aktif. Ketik 'exit' untuk keluar."
    ))
    print(wrap(
        f"{ansi.tag('INFO', ansi.c_cyan())} "
        "Aku akan usulkan command git, lalu kamu putuskan mau dieksekusi atau tidak."
    ))

    if initial:
        print(wrap(f"{ansi.tag('YOU', ansi.c_cyan())} {initial}"))

    system = _ai_chat_system()

    def run_turn(user_text: str) -> int:
        snap        = _chat_snapshot(root)
        user_prompt = snap + "\nUSER REQUEST:\n" + user_text.strip()

        with ansi.Spinner("AI lagi mikir (gitx chat)"):
            gok, meta, txt = _api_generate(cfg, system, user_prompt, timeout=35, max_out=900)

        if not gok:
            print(wrap(f"{ansi.tag('INFO', ansi.c_cyan())} AI gagal dipanggil: {meta}"))
            return 2

        plan = _extract_json_object(txt)
        if not plan:
            print(wrap(
                f"{ansi.tag('INFO', ansi.c_cyan())} "
                "Output AI tidak valid JSON. Aku tampilkan raw:"
            ))
            print(txt)
            return 0

        print(wrap(f"{ansi.tag('INFO', ansi.c_cyan())} AI via API: {meta}"))
        cmds = render_ai_plan(plan)

        if not cmds:
            print(wrap(f"{ansi.tag('INFO', ansi.c_cyan())} Tidak ada command yang bisa dieksekusi."))
            return 0

        # Extra warning untuk high-risk commands
        high_risk_keywords = (
            "reset --hard", "clean -fd", "push --force",
            "rebase", "checkout --force",
        )
        if any(kw in c for c in cmds for kw in high_risk_keywords):
            print(wrap(
                f"{ansi.tag('WARN', ansi.c_yellow())} "
                "Ada command berisiko tinggi. Pastikan backup sebelum lanjut."
            ))

        if confirm(f"{ansi.c_yellow()}Mau eksekusi command yang disarankan? [y/N] {ansi.c_reset()}"):
            return execute_git_commands(root, cmds)

        print(wrap(
            f"{ansi.tag('INFO', ansi.c_cyan())} "
            "Skip eksekusi. Kamu bisa copy/paste manual kalau mau."
        ))
        return 0

    # Initial turn (opsional)
    if initial.strip():
        run_turn(initial)

    # REPL loop
    while True:
        try:
            user_text = ansi.prompt_text("> ").strip()
        except KeyboardInterrupt:
            print("")
            break

        if not user_text:
            continue

        low = user_text.lower().strip()
        if low in ("exit", "quit", "q"):
            break
        if low in ("help", "?"):
            print(wrap("Contoh:"))
            print(wrap("- tampilkan graph perubahan"))
            print(wrap("- stage semua perubahan yang relevan"))
            print(wrap("- buat branch baru feature/x lalu pindah"))
            print(wrap("- squash commit terakhir jadi 1"))
            continue

        run_turn(user_text)

    print(wrap(f"{ansi.tag('INFO', ansi.c_cyan())} GitX Chat selesai."))
    return 0
