"""
GitX Core — Git Engine.
Subprocess helpers, repo detection, RepoSnapshot parsing.

Pakai system_logic.core.exec.run_argv() sebagai base runner.
Semua fungsi di sini read-only terhadap state — zero side effects.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any, Optional

from system_logic.core.exec import run_argv


# ==========================================================
# Low-level runner
# ==========================================================

def run_subprocess(
    cmd: list[str],
    cwd: Path,
    timeout: int = 8,
) -> tuple[int, str, str]:
    """
    Jalankan command subprocess dengan cwd dan timeout.
    Returns: (returncode, stdout, stderr) — semua di-strip.
    """
    try:
        p = subprocess.run(
            cmd,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
        return int(p.returncode), (p.stdout or "").strip(), (p.stderr or "").strip()
    except subprocess.TimeoutExpired:
        return 124, "", f"Timeout after {timeout}s"
    except Exception as e:
        return 1, "", f"{type(e).__name__}: {e}"


def run_git(
    args: list[str],
    cwd: Path,
    timeout: int = 8,
) -> tuple[int, str, str]:
    """
    Jalankan perintah git dengan --no-pager.
    Returns: (returncode, stdout, stderr).
    """
    return run_subprocess(["git", "--no-pager"] + args, cwd=cwd, timeout=timeout)


# ==========================================================
# Availability & repo detection
# ==========================================================

def ensure_git_exists() -> bool:
    """
    Cek apakah binary 'git' tersedia di PATH.
    Print error message jika tidak ada.
    Returns True jika tersedia.
    """
    from shutil import which
    from system_logic.terminal import ansi

    if which("git"):
        return True
    ansi.print_brief_error("Command 'git' tidak ditemukan.")
    print("Install (Fedora): sudo dnf install -y git")
    return False


def find_repo_root(cwd: Path) -> Optional[Path]:
    """
    Cari root repo git dari cwd ke atas.
    Coba via 'git rev-parse' dulu, fallback manual .git check.
    Returns Path ke root, atau None jika bukan repo git.
    """
    rc, out, _ = run_git(["rev-parse", "--show-toplevel"], cwd=cwd, timeout=4)
    if rc == 0 and out:
        return Path(out).resolve()

    # Fallback: manual search ke atas
    p = cwd.resolve()
    for parent in [p] + list(p.parents):
        if (parent / ".git").exists():
            return parent
    return None


# ==========================================================
# RepoSnapshot
# ==========================================================

class RepoSnapshot:
    """
    Snapshot kondisi repo git pada satu titik waktu.
    Semua field bersifat read-only setelah collect_snapshot() selesai.
    """

    def __init__(self) -> None:
        self.root: Optional[Path] = None
        self.branch: str = ""
        self.upstream: str = ""
        self.ahead: int = 0
        self.behind: int = 0
        self.staged: int = 0
        self.unstaged: int = 0
        self.untracked: int = 0
        self.is_dirty: bool = False
        self.head: str = ""
        self.last_commit: str = ""
        self.stash_count: int = 0
        self.remotes: list[tuple[str, str]] = []

    def to_json(self) -> dict[str, Any]:
        """Serialize snapshot ke dict (untuk --json output)."""
        return {
            "root": str(self.root) if self.root else "",
            "branch": self.branch,
            "upstream": self.upstream,
            "ahead": self.ahead,
            "behind": self.behind,
            "staged": self.staged,
            "unstaged": self.unstaged,
            "untracked": self.untracked,
            "dirty": self.is_dirty,
            "head": self.head,
            "last_commit": self.last_commit,
            "stash_count": self.stash_count,
            "remotes": [{"name": n, "url": u} for n, u in self.remotes],
        }


# ==========================================================
# Porcelain parser
# ==========================================================

def _parse_porcelain_status(
    porcelain: str,
) -> tuple[str, str, int, int, int, int, int]:
    """
    Parse output 'git status --porcelain=v1 -b'.
    Returns: (branch, upstream, ahead, behind, staged, unstaged, untracked)
    """
    branch = ""
    upstream = ""
    ahead = 0
    behind = 0
    staged = 0
    unstaged = 0
    untracked = 0

    lines = porcelain.splitlines()
    if not lines:
        return branch, upstream, ahead, behind, staged, unstaged, untracked

    hdr = lines[0].strip()
    if hdr.startswith("##"):
        h = hdr[2:].strip()
        head_part = h.split(" ", 1)[0]

        if "HEAD" in head_part:
            branch = "DETACHED"
        else:
            if "..." in head_part:
                branch, upstream = head_part.split("...", 1)
            else:
                branch = head_part

        m = re.search(r"\[(.*?)\]$", h)
        if m:
            flags = m.group(1)
            ma = re.search(r"ahead\s+(\d+)", flags)
            mb = re.search(r"behind\s+(\d+)", flags)
            if ma:
                ahead = int(ma.group(1))
            if mb:
                behind = int(mb.group(1))

    for ln in lines[1:]:
        if not ln:
            continue
        if ln.startswith("??"):
            untracked += 1
            continue
        if len(ln) >= 2:
            x = ln[0]
            y = ln[1]
            if x != " ":
                staged += 1
            if y != " ":
                unstaged += 1

    return branch, upstream, ahead, behind, staged, unstaged, untracked


# ==========================================================
# Snapshot collector
# ==========================================================

def collect_snapshot(cwd: Path) -> tuple[Optional[RepoSnapshot], str]:
    """
    Kumpulkan semua info repo menjadi satu RepoSnapshot.
    Returns: (snapshot, error_message)
    error_message kosong jika berhasil.
    """
    root = find_repo_root(cwd)
    if not root:
        return None, "Bukan repo git (tidak menemukan .git / rev-parse gagal)."

    snap = RepoSnapshot()
    snap.root = root

    # Status
    rc, out, err = run_git(["status", "--porcelain=v1", "-b"], cwd=root, timeout=5)
    if rc != 0:
        return None, f"Gagal membaca status git: {err or out or f'rc={rc}'}"

    b, up, a, bh, stg, uns, unt = _parse_porcelain_status(out)
    snap.branch    = b
    snap.upstream  = up
    snap.ahead     = a
    snap.behind    = bh
    snap.staged    = stg
    snap.unstaged  = uns
    snap.untracked = unt
    snap.is_dirty  = (stg + uns + unt) > 0

    # HEAD
    rc, out, _ = run_git(["rev-parse", "--short", "HEAD"], cwd=root, timeout=3)
    snap.head = out if rc == 0 else ""

    # Last commit
    rc, out, _ = run_git(
        ["log", "-1", "--pretty=format:%h • %s • %cr • %an"],
        cwd=root, timeout=4,
    )
    snap.last_commit = out if rc == 0 else ""

    # Stash count
    rc, out, _ = run_git(["stash", "list"], cwd=root, timeout=4)
    snap.stash_count = len(out.splitlines()) if (rc == 0 and out) else 0

    # Remotes
    rc, out, _ = run_git(["remote", "-v"], cwd=root, timeout=4)
    rem: dict[str, str] = {}
    if rc == 0 and out:
        for ln in out.splitlines():
            p = ln.split()
            if len(p) >= 2:
                rem.setdefault(p[0].strip(), p[1].strip())
    snap.remotes = [(k, v) for k, v in sorted(rem.items())]

    return snap, ""
