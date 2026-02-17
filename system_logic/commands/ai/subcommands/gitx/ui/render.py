"""
GitX UI — Render Layer.
Semua fungsi display, formatting, konfirmasi interaktif,
dan rendering output AI plan.

Tidak ada business logic di sini — murni presentasi.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from system_logic.terminal import ansi
from system_logic.core.utils import normalize_model_id

from ..core.git import run_git, RepoSnapshot


# ==========================================================
# Config helpers (baca langsung dari cfg dict)
# ==========================================================

def _backend_mode(cfg: dict) -> str:
    return str(cfg.get("backend_mode") or "auto").strip().lower()

def _api_provider(cfg: dict) -> str:
    return str((cfg.get("api") or {}).get("provider") or "gemini").strip().lower()

def _active_model(cfg: dict) -> str:
    return str((cfg.get("api") or {}).get("active_model") or "").strip()


# ==========================================================
# Base formatting
# ==========================================================

def wrap(s: str) -> str:
    """Wrap string ke lebar terminal (max 120)."""
    cols, _ = ansi.term_size()
    return ansi.wrap(s, width=min(cols, 120))


def print_section(title: str) -> None:
    """Print section header dengan tag cyan."""
    print(f"\n{ansi.tag(title, ansi.c_cyan())}")


def print_kv(k: str, v: str) -> None:
    """Print key-value pair dengan format rapi."""
    cols, _ = ansi.term_size()
    print(ansi.wrap(f"- {k:<16}: {v}", width=min(cols, 120)))


def confirm(prompt: str) -> bool:
    """
    Prompt konfirmasi interaktif.
    Returns True hanya jika user ketik 'y'.
    """
    try:
        ans = ansi.prompt_text(prompt).strip().lower()
        return ans == "y"
    except KeyboardInterrupt:
        print("")
        return False


# ==========================================================
# Header
# ==========================================================

def print_header(cfg: dict) -> None:
    """Print header GitX dengan info mode/provider/model."""
    mode  = _backend_mode(cfg).upper()
    prov  = _api_provider(cfg).upper()
    model = normalize_model_id(_active_model(cfg) or "-")

    ansi.print_system("GITX (REPO INTELLIGENCE MODE)")
    print(wrap(f"{ansi.tag('INFO', ansi.c_cyan())} Mode: {mode} | API: {prov} | Model: {model}"))
    print(wrap(f"{ansi.tag('INFO', ansi.c_cyan())} Tip: `ai gitx help` untuk daftar aksi aman."))


# ==========================================================
# Status display
# ==========================================================

def print_status(snap: RepoSnapshot, as_json: bool = False) -> None:
    """
    Render RepoSnapshot ke terminal.
    Jika as_json=True, output JSON mentah.
    """
    import json

    if as_json:
        print(json.dumps(snap.to_json(), indent=2))
        return

    print_section("1) Repo & Branch")
    print_kv("Repo root", str(snap.root))
    print_kv("Branch",    snap.branch or "-")
    print_kv("Upstream",  snap.upstream or "(belum set)")
    print_kv("Ahead/Behind", f"{snap.ahead} / {snap.behind}")
    print_kv("HEAD", snap.head or "-")
    if snap.last_commit:
        print_kv("Last commit", snap.last_commit)

    print_section("2) Worktree")
    dirty_txt = (
        f"{ansi.c_yellow()}DIRTY{ansi.c_reset()}"
        if snap.is_dirty
        else f"{ansi.c_green()}CLEAN{ansi.c_reset()}"
    )
    print_kv("State",     dirty_txt)
    print_kv("Staged",    str(snap.staged))
    print_kv("Unstaged",  str(snap.unstaged))
    print_kv("Untracked", str(snap.untracked))
    print_kv("Stash",     str(snap.stash_count))

    print_section("3) Remotes")
    if not snap.remotes:
        print(wrap("- (tidak ada remote)"))
    else:
        for n, u in snap.remotes:
            print_kv(n, u)

    print_section("4) Quick actions (aman)")
    print(wrap("- ai gitx sync            : Fetch + pull aman (ff-only default)."))
    print(wrap("- ai gitx wip \"pesan\"      : Stash WIP (include untracked)."))
    print(wrap("- ai gitx clean           : Preview untracked deletion (no apply)."))
    print(wrap("- ai gitx summary         : AI buat ringkasan PR/changes."))
    print(wrap("- ai gitx chat            : Mode chat GitX (AI usul command, optional exec)."))
    print(wrap("- ai gitx aicm            : AI Commit Message (API-only)."))
    print(wrap(
        f"{ansi.tag('INFO', ansi.c_cyan())} "
        "GitX tidak mengeksekusi aksi destruktif tanpa konfirmasi."
    ))


# ==========================================================
# AI plan renderer
# ==========================================================

def render_ai_plan(plan: dict) -> list[str]:
    """
    Render rencana AI (dict hasil parse JSON) ke terminal.
    Returns list command git valid yang bisa dieksekusi.

    Format plan yang diharapkan:
    {
        "summary": "...",
        "commands": [{"title":"...", "cmd":"git ...", "risk":"low|medium|high"}],
        "notes": ["..."]
    }
    """
    summary = str(plan.get("summary") or "").strip()
    notes   = plan.get("notes")   if isinstance(plan.get("notes"),   list) else []
    cmds    = plan.get("commands") if isinstance(plan.get("commands"), list) else []

    if summary:
        print(wrap(f"{ansi.tag('AI', ansi.c_green())} {summary}"))

    if notes:
        print(wrap(f"{ansi.tag('NOTES', ansi.c_cyan())}"))
        for n in notes[:8]:
            print(wrap(f"- {n}"))

    valid_cmds: list[str] = []

    if cmds:
        print(wrap(f"{ansi.tag('PLAN', ansi.c_cyan())} Command yang disarankan:"))
        for i, item in enumerate(cmds[:8], 1):
            if not isinstance(item, dict):
                continue

            title = str(item.get("title") or "").strip()
            cmd   = str(item.get("cmd")   or "").strip()
            risk  = str(item.get("risk")  or "low").strip().lower()

            # Hanya terima command yang diawali 'git '
            if not cmd.startswith("git "):
                continue

            icon = "✅"
            col  = ansi.c_green()
            if risk == "medium":
                icon = "⚠️"
                col  = ansi.c_yellow()
            if risk == "high":
                icon = "🧨"
                col  = ansi.c_red()

            label = title if title else "Command"
            print(wrap(f"  {i}. {col}{icon} {label}{ansi.c_reset()}"))
            print(wrap(f"     $ {cmd}"))
            valid_cmds.append(cmd)

    return valid_cmds


# ==========================================================
# Command executor (UI-layer: confirm + run)
# ==========================================================

def execute_git_commands(root: Path, cmds: list[str]) -> int:
    """
    Eksekusi list git command setelah konfirmasi user.
    Hanya menerima command yang lolos safety check.

    Flow:
    1. Konfirmasi bulk (run all?)
    2. Jika tidak → per-command selection
    3. Setiap command di-parse & divalidasi sebelum dieksekusi
    """
    from system_logic.core.safety import is_denied
    import shlex

    if not cmds:
        return 0

    def _parse_safe(c: str) -> Optional[list[str]]:
        """Parse 'git ...' string menjadi argv list. None jika tidak aman."""
        stripped = (c or "").strip()
        if not stripped.startswith("git "):
            return None
        if is_denied(stripped):
            return None
        try:
            parts = shlex.split(stripped)
            if not parts or parts[0] != "git":
                return None
            return parts
        except Exception:
            return None

    print(wrap(
        f"{ansi.tag('INFO', ansi.c_cyan())} "
        "Eksekusi hanya dilakukan untuk command `git ...` yang aman (tanpa shell)."
    ))

    if not confirm(f"{ansi.c_yellow()}Eksekusi semua command di atas? [y/N] {ansi.c_reset()}"):
        # Per-command selection
        for i, c in enumerate(cmds, 1):
            if not confirm(f"{ansi.c_yellow()}Run #{i} `{c}`? [y/N] {ansi.c_reset()}"):
                continue
            argv = _parse_safe(c)
            if not argv:
                print(wrap(
                    f"{ansi.tag('INFO', ansi.c_cyan())} "
                    f"Skip (command tidak aman untuk auto-exec): {c}"
                ))
                continue
            rc, out, err = run_git(argv[1:], cwd=root, timeout=120)
            if rc != 0:
                ansi.print_brief_error(err or out or f"Command gagal rc={rc}")
                return 2
            if out:
                print(out)
        return 0

    # Run all sequential
    for c in cmds:
        argv = _parse_safe(c)
        if not argv:
            print(wrap(
                f"{ansi.tag('INFO', ansi.c_cyan())} "
                f"Skip (command tidak aman untuk auto-exec): {c}"
            ))
            continue
        print(wrap(f"{ansi.tag('RUN', ansi.c_yellow())} {c}"))
        rc, out, err = run_git(argv[1:], cwd=root, timeout=120)
        if rc != 0:
            ansi.print_brief_error(err or out or f"Command gagal rc={rc}")
            return 2
        if out:
            print(out)

    return 0
