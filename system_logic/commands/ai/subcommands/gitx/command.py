"""
GitX Command Router.
Entry point yang dipanggil oleh subcommands/registry.py.

Routing argv → action yang tepat.
Semua business logic didelegasikan ke:
- actions/repo.py (non-AI)
- actions/ai.py   (AI features)
- ui/render.py    (display)
"""

from __future__ import annotations

from pathlib import Path

from system_logic.terminal import ansi

from .core.git import ensure_git_exists, find_repo_root, collect_snapshot
from .ui.render import wrap, print_header, print_status
from .actions.repo import (
    action_branches,
    action_log,
    action_open,
    action_clean,
    action_wip,
    action_sync,
)
from .actions.ai import (
    action_ai_summary,
    action_ai_commit_msg,
    action_ai_chat,
)


def handle(argv: list[str], cfg: dict) -> int:
    """
    Main entry point untuk 'ai gitx'.
    Dipanggil oleh registry.dispatch().

    Args:
        argv : argument setelah 'ai gitx'
        cfg  : global config dict dari load_config()

    Returns:
        Exit code (0=sukses, 1=error, 2=usage error)

    Supported commands:
        status [--json]
        branches
        log [N]
        sync [--rebase]
        wip [msg] [--commit]
        clean [--apply]
        open
        summary
        aicm [--apply] [--amend]
        chat / ai
        halo <text>
        help
    """
    # Guard: pastikan git tersedia
    if not ensure_git_exists():
        return 2

    # Guard: pastikan berada di dalam repo git
    cwd  = Path.cwd()
    root = find_repo_root(cwd)
    if not root:
        ansi.print_brief_error("GitX hanya berjalan di dalam repo git.")
        print(wrap("Saran: cd ke folder repo kamu, lalu jalankan lagi."))
        return 2

    # Print header (mode/provider/model info)
    print_header(cfg)

    # Flag parsing
    as_json = "--json"   in argv
    rebase  = "--rebase" in argv
    apply   = "--apply"  in argv
    amend   = "--amend"  in argv
    commit  = "--commit" in argv

    # Normalize: ambil args yang bukan flag (--)
    raw_args = [a for a in argv if a and not a.startswith("--")]
    action   = raw_args[0].lower().strip() if raw_args else "status"
    rest     = raw_args[1:] if len(raw_args) > 1 else []

    # ──────────────────────────────────────────
    # HELP
    # ──────────────────────────────────────────
    if action in ("help", "-h", "--help"):
        return _print_help()

    # ──────────────────────────────────────────
    # STATUS (default)
    # ──────────────────────────────────────────
    if action in ("status", "st", ""):
        snap, err = collect_snapshot(root)
        if not snap:
            ansi.print_brief_error(err)
            return 2
        print_status(snap, as_json=as_json)
        return 0

    # ──────────────────────────────────────────
    # BRANCHES
    # ──────────────────────────────────────────
    if action in ("branches", "branch", "br"):
        return action_branches(root)

    # ──────────────────────────────────────────
    # LOG
    # ──────────────────────────────────────────
    if action == "log":
        n = 12
        if rest and rest[0].isdigit():
            n = int(rest[0])
        return action_log(root, n)

    # ──────────────────────────────────────────
    # SYNC
    # ──────────────────────────────────────────
    if action == "sync":
        return action_sync(root, rebase=rebase)

    # ──────────────────────────────────────────
    # WIP
    # ──────────────────────────────────────────
    if action == "wip":
        msg = " ".join(rest).strip() if rest else ""
        return action_wip(root, msg=msg, do_commit=commit)

    # ──────────────────────────────────────────
    # CLEAN
    # ──────────────────────────────────────────
    if action == "clean":
        return action_clean(root, apply=apply)

    # ──────────────────────────────────────────
    # OPEN
    # ──────────────────────────────────────────
    if action == "open":
        return action_open(root)

    # ──────────────────────────────────────────
    # AI SUMMARY
    # ──────────────────────────────────────────
    if action in ("summary", "sum", "pr"):
        return action_ai_summary(root, cfg)

    # ──────────────────────────────────────────
    # AI COMMIT MESSAGE
    # ──────────────────────────────────────────
    if action in ("aicm", "commit-msg", "commitmsg", "cm"):
        return action_ai_commit_msg(root, cfg, apply=apply, amend=amend)

    # ──────────────────────────────────────────
    # AI CHAT
    # ──────────────────────────────────────────
    if action in ("chat", "ai"):
        initial = " ".join(rest).strip()
        return action_ai_chat(root, cfg, initial=initial)

    if action == "halo":
        initial = ("halo " + " ".join(rest)).strip()
        return action_ai_chat(root, cfg, initial=initial)

    # ──────────────────────────────────────────
    # FALLBACK: unknown input → masuk chat mode
    # Contoh: "ai gitx tampilkan graph" → chat
    # ──────────────────────────────────────────
    initial = " ".join(raw_args).strip()
    return action_ai_chat(root, cfg, initial=initial)


# ==========================================================
# Help text
# ==========================================================

def _print_help() -> int:
    """Print help text lengkap."""
    ansi.print_info("GitX — Git Cockpit + AI Assistant")
    print()
    print(f"{ansi.c_bold()}USAGE{ansi.c_reset()}")
    print("  ai gitx                          : Status ringkas (default).")
    print("  ai gitx status [--json]          : Status lengkap / snapshot JSON.")
    print("  ai gitx branches                 : Branch list.")
    print("  ai gitx log [N]                  : Log singkat (default 12).")
    print("  ai gitx sync [--rebase]          : Fetch + pull aman.")
    print("  ai gitx wip [msg]                : Stash WIP (include untracked).")
    print("  ai gitx wip --commit [msg]       : WIP commit.")
    print("  ai gitx clean                    : Preview git clean -nd.")
    print("  ai gitx clean --apply            : Execute git clean -fd (confirm).")
    print("  ai gitx open                     : Print command buka repo di VS Code.")
    print()
    print(f"{ansi.c_bold()}AI FEATURES (API-only){ansi.c_reset()}")
    print("  ai gitx summary                  : AI buat PR/changes summary.")
    print("  ai gitx chat | ai                : Chat mode GitX (AI usul command).")
    print("  ai gitx halo <text>              : Masuk chat mode dengan salam awal.")
    print("  ai gitx aicm [--apply] [--amend] : AI Commit Message.")
    print()
    print(f"{ansi.c_bold()}NOTES{ansi.c_reset()}")
    print("  - GitX tidak eksekusi aksi destruktif tanpa konfirmasi.")
    print("  - AI features membutuhkan API key (GEMINI_API_KEY).")
    print("  - Input bebas selain command di atas → masuk chat mode otomatis.")
    return 0
