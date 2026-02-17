"""
GitX Actions — Repo (Non-AI).
Semua aksi git yang aman: branches, log, sync, wip, clean, open.

Prinsip:
- Semua aksi destruktif wajib konfirmasi via ui.render.confirm()
- Tidak ada shell=True, semua argv-based
- Tidak ada business logic UI di sini — delegasi ke ui/render.py
"""

from __future__ import annotations

import time
from pathlib import Path

from system_logic.terminal import ansi

from ..core.git import run_git, collect_snapshot
from ..ui.render import wrap, print_section, confirm


# ==========================================================
# action_branches
# ==========================================================

def action_branches(root: Path) -> int:
    """Tampilkan list branch dengan info upstream & last commit."""
    print_section("BRANCHES")
    rc, out, err = run_git(["branch", "-vv"], cwd=root, timeout=8)
    if rc != 0:
        ansi.print_brief_error(err or "Gagal membaca branch list.")
        return 2
    print(out)
    return 0


# ==========================================================
# action_log
# ==========================================================

def action_log(root: Path, n: int) -> int:
    """Tampilkan git log singkat sebanyak n entry."""
    n = max(1, min(int(n), 200))
    print_section(f"LOG (last {n})")
    rc, out, err = run_git(
        ["log", f"-n{n}", "--oneline", "--decorate"],
        cwd=root, timeout=10,
    )
    if rc != 0:
        ansi.print_brief_error(err or "Gagal membaca log.")
        return 2
    print(out)
    return 0


# ==========================================================
# action_open
# ==========================================================

def action_open(root: Path) -> int:
    """Print command untuk membuka repo di VS Code."""
    from shutil import which

    print_section("OPEN WORKSPACE")
    if which("code"):
        print(wrap(f'- VS Code: code "{root}" --verbose'))
        print(wrap("- Jalankan command di atas manual."))
        return 0
    print(wrap("- VS Code CLI 'code' tidak terdeteksi."))
    print("Install (Fedora): sudo dnf install -y code")
    return 0


# ==========================================================
# action_clean
# ==========================================================

def action_clean(root: Path, apply: bool) -> int:
    """
    Preview atau eksekusi git clean untuk untracked files.
    Tanpa --apply: hanya preview (aman).
    Dengan --apply: konfirmasi dulu, baru eksekusi.
    """
    print_section("CLEAN (UNTRACKED)")

    rc, out, err = run_git(["clean", "-nd"], cwd=root, timeout=12)
    if rc != 0:
        ansi.print_brief_error(err or "Gagal preview git clean.")
        return 2

    if not out:
        print(wrap(
            f"{ansi.tag('INFO', ansi.c_cyan())} "
            "Tidak ada untracked file/dir yang akan dihapus."
        ))
        return 0

    print(wrap(f"{ansi.tag('INFO', ansi.c_cyan())} Preview (tidak dieksekusi):"))
    print(out)

    if not apply:
        print(wrap(f"{ansi.tag('INFO', ansi.c_cyan())} Untuk eksekusi: ai gitx clean --apply"))
        return 0

    print(wrap(
        f"{ansi.tag('WARN', ansi.c_yellow())} "
        "Ini akan menghapus UNTRACKED file/dir secara permanen."
    ))
    if not confirm(f"{ansi.c_yellow()}Lanjutkan git clean -fd ? [y/N] {ansi.c_reset()}"):
        print(wrap("Dibatalkan."))
        return 0

    rc, out2, err2 = run_git(["clean", "-fd"], cwd=root, timeout=30)
    if rc != 0:
        ansi.print_brief_error(err2 or "Gagal menjalankan git clean -fd.")
        return 2

    print(wrap(f"{ansi.c_green()}OK.{ansi.c_reset()} Untracked dibersihkan."))
    if out2:
        print(out2)
    return 0


# ==========================================================
# action_wip
# ==========================================================

def action_wip(root: Path, msg: str, do_commit: bool) -> int:
    """
    Simpan work-in-progress.
    do_commit=False : git stash push -u (aman, reversible)
    do_commit=True  : git add -A + git commit (ubah history)
    """
    print_section("WIP")

    snap, err = collect_snapshot(root)
    if not snap:
        ansi.print_brief_error(err)
        return 2

    if not snap.is_dirty:
        print(wrap(
            f"{ansi.tag('INFO', ansi.c_cyan())} "
            "Repo sudah clean. Tidak ada yang perlu di-WIP."
        ))
        return 0

    msg = (msg or "work-in-progress").strip()

    if do_commit:
        print(wrap(
            f"{ansi.tag('WARN', ansi.c_yellow())} "
            "Mode COMMIT akan membuat commit baru di branch sekarang."
        ))
        if not confirm(f"{ansi.c_yellow()}Lanjutkan WIP commit? [y/N] {ansi.c_reset()}"):
            print(wrap("Dibatalkan."))
            return 0

        rc, _, err = run_git(["add", "-A"], cwd=root, timeout=20)
        if rc != 0:
            ansi.print_brief_error(err or "Gagal git add -A")
            return 2

        rc, out, err = run_git(["commit", "-m", f"WIP: {msg}"], cwd=root, timeout=40)
        if rc != 0:
            ansi.print_brief_error(err or out or "Gagal commit.")
            return 2

        print(wrap(f"{ansi.c_green()}OK.{ansi.c_reset()} WIP commit dibuat."))
        print(out)
        return 0

    # Default: stash (aman)
    print(wrap(
        f"{ansi.tag('INFO', ansi.c_cyan())} "
        "Mode STASH (aman): menyimpan perubahan tanpa ubah history."
    ))
    if not confirm(
        f"{ansi.c_yellow()}Stash semua perubahan (include untracked)? [y/N] {ansi.c_reset()}"
    ):
        print(wrap("Dibatalkan."))
        return 0

    rc, out, err = run_git(["stash", "push", "-u", "-m", msg], cwd=root, timeout=40)
    if rc != 0:
        ansi.print_brief_error(err or out or "Gagal stash.")
        return 2

    print(wrap(f"{ansi.c_green()}OK.{ansi.c_reset()} Disimpan ke stash."))
    print(out)
    return 0


# ==========================================================
# action_sync
# ==========================================================

def action_sync(root: Path, rebase: bool) -> int:
    """
    Fetch + pull aman.
    Default: --ff-only (tidak bisa jika divergen).
    --rebase: pull --rebase --autostash (lebih fleksibel).
    """
    print_section("SYNC")

    snap, err = collect_snapshot(root)
    if not snap:
        ansi.print_brief_error(err)
        return 2

    if not snap.upstream:
        print(wrap(
            f"{ansi.tag('INFO', ansi.c_cyan())} "
            "Upstream belum diset untuk branch ini."
        ))
        print(wrap("Saran: set upstream dulu, mis: git push -u origin <branch>"))
        return 2

    if snap.is_dirty:
        print(wrap(f"{ansi.tag('WARN', ansi.c_yellow())} Repo sedang DIRTY."))
        print(wrap('Saran: ai gitx wip "pesan" dulu, baru sync.'))
        if not confirm(f"{ansi.c_yellow()}Tetap lanjut fetch/pull? [y/N] {ansi.c_reset()}"):
            print(wrap("Dibatalkan."))
            return 0

    # Fetch
    print(wrap("• git fetch --prune ..."))
    t0 = time.time()
    rc, _, errf = run_git(["fetch", "--prune"], cwd=root, timeout=60)
    dt = time.time() - t0
    if rc != 0:
        ansi.print_brief_error(errf or "Fetch gagal.")
        print(wrap("Hint: cek koneksi / credential remote."))
        return 2
    print(wrap(f"{ansi.c_green()}OK.{ansi.c_reset()} ({dt:.2f}s)"))

    # Pull
    if rebase:
        cmd = ["pull", "--rebase", "--autostash"]
        print(wrap("• git pull --rebase --autostash ..."))
    else:
        cmd = ["pull", "--ff-only"]
        print(wrap("• git pull --ff-only ..."))

    t0 = time.time()
    rc, out, errp = run_git(cmd, cwd=root, timeout=120)
    dt = time.time() - t0

    if rc != 0:
        msg = (errp or out or "Pull gagal.")[:800]
        ansi.print_brief_error(msg)
        if "ff-only" in " ".join(cmd) and (
            "diverg" in msg.lower() or "fast-forward" in msg.lower()
        ):
            print(wrap(
                f"{ansi.tag('INFO', ansi.c_cyan())} "
                "ff-only gagal biasanya karena branch divergen."
            ))
            print(wrap(
                "Saran aman: coba `ai gitx sync --rebase` "
                "atau review manual sebelum merge."
            ))
        return 2

    print(wrap(f"{ansi.c_green()}OK.{ansi.c_reset()} ({dt:.2f}s)"))
    if out:
        print(out)
    return 0
