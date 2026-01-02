"""
GitX — Git Cockpit (ANSI-only, Safe Actions, Optional AI via API).

Tambahan penting:
- ai gitx summary            : AI buat ringkasan PR/changes (markdown).
- ai gitx chat / ai          : Chat mode (gantikan AI Advisor one-shot).
- ai gitx halo sili          : alias masuk chat mode (kata setelah "halo" jadi salam awal).

Prinsip:
- Semua eksekusi berisiko wajib konfirmasi.
- Chat mode: AI hanya mengusulkan command git, GitX yang eksekusi (jika user setuju).
- AI mode selalu pakai API (tidak peduli backend_mode di config).

Usage ringkas:
  ai gitx
  ai gitx status [--json]
  ai gitx branches
  ai gitx log [N]
  ai gitx sync [--rebase]
  ai gitx wip [msg...]
  ai gitx wip --commit [msg...]
  ai gitx clean
  ai gitx clean --apply
  ai gitx open
  ai gitx summary
  ai gitx chat
  ai gitx ai
  ai gitx halo sili
  ai gitx aicm [--apply] [--amend]
  ai gitx help
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

from ai_logic.ui import ansi

# Common (single source of truth)
try:
    from ai_logic.common import (
        backend_mode,
        api_provider,
        api_active_model_raw,
        force_single_paragraph,
        normalize_model_id,
        DENY_SUBSTRINGS,
    )
except Exception:
    def backend_mode(cfg: dict) -> str:
        return str(cfg.get("backend_mode") or "auto").strip().lower()

    def api_provider(cfg: dict) -> str:
        return str((cfg.get("api") or {}).get("provider") or "gemini").strip().lower()

    def api_active_model_raw(cfg: dict) -> str:
        return str((cfg.get("api") or {}).get("active_model") or "").strip()

    def force_single_paragraph(text: str) -> str:
        return re.sub(r"\s+", " ", (text or "").strip()).strip()

    def normalize_model_id(model: str) -> str:
        m = (model or "").strip()
        return m[7:] if m.startswith("models/") else m

    DENY_SUBSTRINGS = [
        "rm -rf /", " mkfs", "dd if=", ":(){:|:&};:", " shutdown", " reboot", " poweroff"
    ]


# ==========================================================
# Subprocess helpers
# ==========================================================

def _run(cmd: list[str], cwd: Path, timeout: int = 8) -> tuple[int, str, str]:
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

def _git(args: list[str], cwd: Path, timeout: int = 8) -> tuple[int, str, str]:
    return _run(["git", "--no-pager"] + args, cwd=cwd, timeout=timeout)

def _ensure_git_exists() -> bool:
    from shutil import which
    if which("git"):
        return True
    ansi.print_brief_error("Command 'git' tidak ditemukan.")
    print("Install (Fedora): sudo dnf install -y git")
    return False

def _repo_root(cwd: Path) -> Optional[Path]:
    rc, out, _ = _git(["rev-parse", "--show-toplevel"], cwd=cwd, timeout=4)
    if rc == 0 and out:
        return Path(out).resolve()
    p = cwd.resolve()
    for parent in [p] + list(p.parents):
        if (parent / ".git").exists():
            return parent
    return None


# ==========================================================
# UI helpers (ansi-only, clean)
# ==========================================================

def _wrap(s: str) -> str:
    cols, _ = ansi.term_size()
    return ansi.wrap(s, width=min(cols, 120))

def _h(title: str) -> None:
    print(f"\n{ansi.tag(title, ansi.c_cyan())}")

def _kv(k: str, v: str) -> None:
    cols, _ = ansi.term_size()
    print(ansi.wrap(f"- {k:<16}: {v}", width=min(cols, 120)))

def _confirm(prompt: str) -> bool:
    try:
        ans = ansi.prompt_text(prompt).strip().lower()
        return ans == "y"
    except KeyboardInterrupt:
        print("")
        return False

def _print_header(cfg: dict) -> None:
    mode = backend_mode(cfg).upper()
    prov = api_provider(cfg).upper()
    model = normalize_model_id(api_active_model_raw(cfg) or "-")
    ansi.print_system("GITX (REPO INTELLIGENCE MODE)")
    print(_wrap(f"{ansi.tag('INFO', ansi.c_cyan())} Mode: {mode} | API: {prov} | Model: {model}"))
    print(_wrap(f"{ansi.tag('INFO', ansi.c_cyan())} Tip: `ai gitx help` untuk daftar aksi aman."))


# ==========================================================
# Snapshot parsing
# ==========================================================

class RepoSnapshot:
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

def _parse_porcelain_status(porcelain: str) -> tuple[str, str, int, int, int, int, int]:
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

def _collect_snapshot(cwd: Path) -> tuple[Optional[RepoSnapshot], str]:
    root = _repo_root(cwd)
    if not root:
        return None, "Bukan repo git (tidak menemukan .git / rev-parse gagal)."

    snap = RepoSnapshot()
    snap.root = root

    rc, out, err = _git(["status", "--porcelain=v1", "-b"], cwd=root, timeout=5)
    if rc != 0:
        return None, f"Gagal membaca status git: {err or out or f'rc={rc}'}"

    b, up, a, bh, stg, uns, unt = _parse_porcelain_status(out)
    snap.branch = b
    snap.upstream = up
    snap.ahead = a
    snap.behind = bh
    snap.staged = stg
    snap.unstaged = uns
    snap.untracked = unt
    snap.is_dirty = (stg + uns + unt) > 0

    rc, out, _ = _git(["rev-parse", "--short", "HEAD"], cwd=root, timeout=3)
    snap.head = out if rc == 0 else ""

    rc, out, _ = _git(["log", "-1", "--pretty=format:%h • %s • %cr • %an"], cwd=root, timeout=4)
    snap.last_commit = out if rc == 0 else ""

    rc, out, _ = _git(["stash", "list"], cwd=root, timeout=4)
    snap.stash_count = len(out.splitlines()) if (rc == 0 and out) else 0

    rc, out, _ = _git(["remote", "-v"], cwd=root, timeout=4)
    rem: dict[str, str] = {}
    if rc == 0 and out:
        for ln in out.splitlines():
            p = ln.split()
            if len(p) >= 2:
                name = p[0].strip()
                url = p[1].strip()
                rem.setdefault(name, url)
    snap.remotes = [(k, v) for k, v in sorted(rem.items())]
    return snap, ""


# ==========================================================
# Rendering
# ==========================================================

def _print_status(snap: RepoSnapshot, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(snap.to_json(), indent=2))
        return

    _h("1) Repo & Branch")
    _kv("Repo root", str(snap.root))
    _kv("Branch", snap.branch or "-")
    _kv("Upstream", snap.upstream or "(belum set)")
    _kv("Ahead/Behind", f"{snap.ahead} / {snap.behind}")
    _kv("HEAD", snap.head or "-")
    if snap.last_commit:
        _kv("Last commit", snap.last_commit)

    _h("2) Worktree")
    dirty_txt = f"{ansi.c_yellow()}DIRTY{ansi.c_reset()}" if snap.is_dirty else f"{ansi.c_green()}CLEAN{ansi.c_reset()}"
    _kv("State", dirty_txt)
    _kv("Staged", str(snap.staged))
    _kv("Unstaged", str(snap.unstaged))
    _kv("Untracked", str(snap.untracked))
    _kv("Stash", str(snap.stash_count))

    _h("3) Remotes")
    if not snap.remotes:
        print(_wrap("- (tidak ada remote)"))
    else:
        for n, u in snap.remotes:
            _kv(n, u)

    _h("4) Quick actions (aman)")
    print(_wrap("- ai gitx sync            : Fetch + pull aman (ff-only default)."))
    print(_wrap("- ai gitx wip \"pesan\"      : Stash WIP (include untracked)."))
    print(_wrap("- ai gitx clean           : Preview untracked deletion (no apply)."))
    print(_wrap("- ai gitx summary         : AI buat ringkasan PR/changes."))
    print(_wrap("- ai gitx chat            : Mode chat GitX (AI usul command, optional exec)."))
    print(_wrap("- ai gitx aicm            : AI Commit Message (API-only)."))
    print(_wrap(f"{ansi.tag('INFO', ansi.c_cyan())} GitX tidak mengeksekusi aksi destruktif tanpa konfirmasi."))


# ==========================================================
# Actions (non-AI)
# ==========================================================

def action_branches(root: Path) -> int:
    _h("BRANCHES")
    rc, out, err = _git(["branch", "-vv"], cwd=root, timeout=8)
    if rc != 0:
        ansi.print_brief_error(err or "Gagal membaca branch list.")
        return 2
    print(out)
    return 0

def action_log(root: Path, n: int) -> int:
    n = max(1, min(int(n), 200))
    _h(f"LOG (last {n})")
    rc, out, err = _git(["log", f"-n{n}", "--oneline", "--decorate"], cwd=root, timeout=10)
    if rc != 0:
        ansi.print_brief_error(err or "Gagal membaca log.")
        return 2
    print(out)
    return 0

def action_open(root: Path) -> int:
    from shutil import which
    _h("OPEN WORKSPACE")
    if which("code"):
        print(_wrap(f"- VS Code: code \"{root}\" --verbose"))
        print(_wrap("- Jalankan command di atas manual."))
        return 0
    print(_wrap("- VS Code CLI 'code' tidak terdeteksi."))
    print("Install (Fedora): sudo dnf install -y code")
    return 0

def action_clean(root: Path, apply: bool) -> int:
    _h("CLEAN (UNTRACKED)")

    rc, out, err = _git(["clean", "-nd"], cwd=root, timeout=12)
    if rc != 0:
        ansi.print_brief_error(err or "Gagal preview git clean.")
        return 2

    if not out:
        print(_wrap(f"{ansi.tag('INFO', ansi.c_cyan())} Tidak ada untracked file/dir yang akan dihapus."))
        return 0

    print(_wrap(f"{ansi.tag('INFO', ansi.c_cyan())} Preview (tidak dieksekusi):"))
    print(out)

    if not apply:
        print(_wrap(f"{ansi.tag('INFO', ansi.c_cyan())} Untuk eksekusi: ai gitx clean --apply"))
        return 0

    print(_wrap(f"{ansi.tag('WARN', ansi.c_yellow())} Ini akan menghapus UNTRACKED file/dir permanen."))
    if not _confirm(f"{ansi.c_yellow()}Lanjutkan git clean -fd ? [y/N] {ansi.c_reset()}"):
        print(_wrap("Dibatalkan."))
        return 0

    rc, out2, err2 = _git(["clean", "-fd"], cwd=root, timeout=30)
    if rc != 0:
        ansi.print_brief_error(err2 or "Gagal menjalankan git clean -fd.")
        return 2

    print(_wrap(f"{ansi.c_green()}OK.{ansi.c_reset()} Untracked dibersihkan."))
    if out2:
        print(out2)
    return 0

def action_wip(root: Path, msg: str, do_commit: bool) -> int:
    _h("WIP")

    snap, err = _collect_snapshot(root)
    if not snap:
        ansi.print_brief_error(err)
        return 2
    if not snap.is_dirty:
        print(_wrap(f"{ansi.tag('INFO', ansi.c_cyan())} Repo sudah clean. Tidak ada yang perlu di-WIP."))
        return 0

    msg = (msg or "work-in-progress").strip()

    if do_commit:
        print(_wrap(f"{ansi.tag('WARN', ansi.c_yellow())} Mode COMMIT akan membuat commit baru di branch sekarang."))
        if not _confirm(f"{ansi.c_yellow()}Lanjutkan WIP commit? [y/N] {ansi.c_reset()}"):
            print(_wrap("Dibatalkan."))
            return 0

        rc, _, err = _git(["add", "-A"], cwd=root, timeout=20)
        if rc != 0:
            ansi.print_brief_error(err or "Gagal git add -A")
            return 2

        rc, out, err = _git(["commit", "-m", f"WIP: {msg}"], cwd=root, timeout=40)
        if rc != 0:
            ansi.print_brief_error(err or out or "Gagal commit.")
            return 2

        print(_wrap(f"{ansi.c_green()}OK.{ansi.c_reset()} WIP commit dibuat."))
        print(out)
        return 0

    print(_wrap(f"{ansi.tag('INFO', ansi.c_cyan())} Mode STASH (aman): menyimpan perubahan tanpa ubah history."))
    if not _confirm(f"{ansi.c_yellow()}Stash semua perubahan (include untracked)? [y/N] {ansi.c_reset()}"):
        print(_wrap("Dibatalkan."))
        return 0

    rc, out, err = _git(["stash", "push", "-u", "-m", msg], cwd=root, timeout=40)
    if rc != 0:
        ansi.print_brief_error(err or out or "Gagal stash.")
        return 2

    print(_wrap(f"{ansi.c_green()}OK.{ansi.c_reset()} Disimpan ke stash."))
    print(out)
    return 0

def action_sync(root: Path, rebase: bool) -> int:
    _h("SYNC")

    snap, err = _collect_snapshot(root)
    if not snap:
        ansi.print_brief_error(err)
        return 2

    if not snap.upstream:
        print(_wrap(f"{ansi.tag('INFO', ansi.c_cyan())} Upstream belum diset untuk branch ini."))
        print(_wrap("Saran: set upstream dulu, mis: git push -u origin <branch>"))
        return 2

    if snap.is_dirty:
        print(_wrap(f"{ansi.tag('WARN', ansi.c_yellow())} Repo sedang DIRTY."))
        print(_wrap("Saran: ai gitx wip \"pesan\" dulu, baru sync."))
        if not _confirm(f"{ansi.c_yellow()}Tetap lanjut fetch/pull? [y/N] {ansi.c_reset()}"):
            print(_wrap("Dibatalkan."))
            return 0

    print(_wrap("• git fetch --prune ..."))
    t0 = time.time()
    rc, _, errf = _git(["fetch", "--prune"], cwd=root, timeout=60)
    dt = time.time() - t0
    if rc != 0:
        ansi.print_brief_error(errf or "Fetch gagal.")
        print(_wrap("Hint: cek koneksi / credential remote."))
        return 2
    print(_wrap(f"{ansi.c_green()}OK.{ansi.c_reset()} ({dt:.2f}s)"))

    if rebase:
        cmd = ["pull", "--rebase", "--autostash"]
        print(_wrap("• git pull --rebase --autostash ..."))
    else:
        cmd = ["pull", "--ff-only"]
        print(_wrap("• git pull --ff-only ..."))

    t0 = time.time()
    rc, out, errp = _git(cmd, cwd=root, timeout=120)
    dt = time.time() - t0
    if rc != 0:
        msg = (errp or out or "Pull gagal.")[:800]
        ansi.print_brief_error(msg)
        if "ff-only" in " ".join(cmd) and ("diverg" in msg.lower() or "fast-forward" in msg.lower()):
            print(_wrap(f"{ansi.tag('INFO', ansi.c_cyan())} ff-only gagal biasanya karena branch divergen."))
            print(_wrap("Saran aman: coba `ai gitx sync --rebase` atau review manual sebelum merge."))
        return 2

    print(_wrap(f"{ansi.c_green()}OK.{ansi.c_reset()} ({dt:.2f}s)"))
    if out:
        print(out)
    return 0


# ==========================================================
# AI utilities (API-only)
# ==========================================================

def _api_ready(cfg: dict) -> tuple[bool, str]:
    prov = api_provider(cfg).lower().strip()
    if prov == "gemini":
        try:
            from ai_logic.backends.api_gemini import validate_api_config  # type: ignore
            ok, note, _detail = validate_api_config(cfg)
            return bool(ok), (note or "OK")
        except Exception as e:
            return False, f"Wrapper gemini tidak siap: {type(e).__name__}"
    return False, f"Provider '{prov}' belum didukung untuk AI GitX."

def _api_generate(cfg: dict, system: str, user: str, timeout: int = 30, max_out: int = 900) -> tuple[bool, str, str]:
    prov = api_provider(cfg).lower().strip()
    if prov == "gemini":
        try:
            from ai_logic.backends.api_gemini import generate as gemini_generate  # type: ignore
            msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
            t0 = time.time()
            out = gemini_generate(cfg, msgs, timeout=timeout, max_output_tokens=max_out)
            dt = time.time() - t0
            txt = (out or "").strip()
            if not txt:
                return False, f"API menjawab kosong ({dt:.2f}s).", ""
            return True, f"OK ({dt:.2f}s)", txt
        except Exception as e:
            return False, f"API error: {type(e).__name__}: {e}", ""
    return False, f"Provider '{prov}' belum didukung.", ""

def _extract_json_object(text: str) -> Optional[dict]:
    """
    Coba ambil JSON object dari output AI.
    Toleran terhadap code fence.
    """
    s = (text or "").strip()
    if "```" in s:
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", s, flags=re.DOTALL | re.IGNORECASE)
        if m:
            s = m.group(1).strip()

    # fallback: cari {...} terluar
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

def _is_shell_unsafe(cmd: str) -> bool:
    """
    Blok metakarakter shell dan deny substrings.
    """
    c = (cmd or "").strip()
    if not c:
        return True
    if "\n" in c or "\r" in c:
        return True
    low = c.lower()
    for bad in DENY_SUBSTRINGS:
        if bad.strip().lower() in low:
            return True
    # metacharacters berbahaya; kita mau exec tanpa shell
    if re.search(r"[;&|<>`$]", c):
        return True
    return False

def _parse_git_cmd(cmd: str) -> Optional[list[str]]:
    """
    Konversi "git ..." menjadi argv list aman untuk subprocess (tanpa shell=True).
    """
    c = (cmd or "").strip()
    if not c.startswith("git "):
        return None
    if _is_shell_unsafe(c):
        return None
    try:
        parts = shlex.split(c)
        if not parts or parts[0] != "git":
            return None
        return parts
    except Exception:
        return None


# ----------------------------------------------------------
# AI: Summary (PR description / changelog)
# ----------------------------------------------------------

def _git_upstream_ref(root: Path) -> str:
    rc, out, _ = _git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], cwd=root, timeout=4)
    return out.strip() if rc == 0 and out else ""

def _git_merge_base(root: Path, upstream: str) -> str:
    if not upstream:
        return ""
    rc, out, _ = _git(["merge-base", "HEAD", upstream], cwd=root, timeout=6)
    return out.strip() if rc == 0 and out else ""

def action_ai_summary(root: Path, cfg: dict) -> int:
    _h("AI SUMMARY (PR/CHANGELOG)")

    ok, note = _api_ready(cfg)
    upstream = _git_upstream_ref(root)
    base = _git_merge_base(root, upstream) if upstream else ""

    # snapshot
    _, st, _ = _git(["status", "-sb"], cwd=root, timeout=5)

    if base:
        _, log_out, _ = _git(["log", "--oneline", "--decorate", f"{base}..HEAD"], cwd=root, timeout=10)
        _, stat_out, _ = _git(["diff", "--stat", f"{base}..HEAD"], cwd=root, timeout=12)
        _, name_out, _ = _git(["diff", "--name-only", f"{base}..HEAD"], cwd=root, timeout=12)
        scope_note = f"Base: merge-base(HEAD, {upstream})"
    else:
        # fallback (tanpa upstream)
        _, log_out, _ = _git(["log", "-n", "20", "--oneline", "--decorate"], cwd=root, timeout=10)
        _, stat_out, _ = _git(["diff", "--stat"], cwd=root, timeout=12)
        _, name_out, _ = _git(["diff", "--name-only"], cwd=root, timeout=12)
        scope_note = "Base: (no upstream) — using working tree diff + last 20 commits"

    stat_out = "\n".join(stat_out.splitlines()[:60])
    name_out = "\n".join(name_out.splitlines()[:80])
    log_out = "\n".join(log_out.splitlines()[:80])

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
        print(_wrap(f"{ansi.tag('INFO', ansi.c_cyan())} API belum siap: {note}"))
        print(_wrap("Aku print prompt siap-tempel ke `ask` (fallback):"))
        print("")
        print(system + "\n" + user_prompt)
        return 0

    with ansi.Spinner("AI lagi bikin summary"):
        gok, meta, txt = _api_generate(cfg, system, user_prompt, timeout=35, max_out=900)

    if not gok:
        print(_wrap(f"{ansi.tag('INFO', ansi.c_cyan())} AI tidak bisa dipanggil: {meta}"))
        print(_wrap("Fallback prompt siap-tempel:"))
        print("")
        print(system + "\n" + user_prompt)
        return 0

    print(_wrap(f"{ansi.tag('INFO', ansi.c_cyan())} AI via API: {meta}"))
    print(txt)
    return 0


# ----------------------------------------------------------
# AI: Commit message (tetap ada)
# ----------------------------------------------------------

def _build_commitmsg_prompt(root: Path) -> tuple[str, bool]:
    _, st, _ = _git(["status", "-sb"], cwd=root, timeout=5)

    rc, cached_stat, _ = _git(["diff", "--cached", "--stat"], cwd=root, timeout=12)
    has_staged = bool(cached_stat.strip()) and (rc == 0)

    if has_staged:
        _, cached_name, _ = _git(["diff", "--cached", "--name-only"], cwd=root, timeout=12)
        _, cached_patch, _ = _git(["diff", "--cached"], cwd=root, timeout=16)
        cached_patch_short = "\n".join((cached_patch.splitlines() if cached_patch else [])[:220])
        return (
            "===== SNAPSHOT =====\n"
            f"$ git status -sb\n{st}\n\n"
            f"$ git diff --cached --stat\n{cached_stat}\n\n"
            f"$ git diff --cached --name-only\n{cached_name}\n\n"
            f"$ git diff --cached (truncated)\n{cached_patch_short}\n"
            "====================\n",
            True,
        )

    _, wt_stat, _ = _git(["diff", "--stat"], cwd=root, timeout=12)
    _, wt_name, _ = _git(["diff", "--name-only"], cwd=root, timeout=12)
    _, wt_patch, _ = _git(["diff"], cwd=root, timeout=16)
    wt_patch_short = "\n".join((wt_patch.splitlines() if wt_patch else [])[:220])

    return (
        "===== SNAPSHOT =====\n"
        f"$ git status -sb\n{st}\n\n"
        f"$ git diff --stat\n{wt_stat}\n\n"
        f"$ git diff --name-only\n{wt_name}\n\n"
        f"$ git diff (truncated)\n{wt_patch_short}\n"
        "====================\n",
        False,
    )

def _parse_commit_choices(text: str) -> list[str]:
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

def action_ai_commit_msg(root: Path, cfg: dict, apply: bool, amend: bool) -> int:
    _h("AI COMMIT MESSAGE (API-ONLY)")

    ok, note = _api_ready(cfg)
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
        print(_wrap(f"{ansi.tag('INFO', ansi.c_cyan())} API belum siap: {note}"))
        print(_wrap("Aku print prompt siap-tempel ke `ask` (fallback):"))
        print("")
        print(system + "\n" + prompt)
        return 0

    with ansi.Spinner("AI lagi bikin commit message"):
        gok, meta, txt = _api_generate(cfg, system, prompt, timeout=30, max_out=500)

    if not gok:
        print(_wrap(f"{ansi.tag('INFO', ansi.c_cyan())} AI tidak bisa dipanggil: {meta}"))
        print(_wrap("Fallback prompt siap-tempel:"))
        print("")
        print(system + "\n" + prompt)
        return 0

    txt = txt.strip()
    print(_wrap(f"{ansi.tag('INFO', ansi.c_cyan())} AI via API: {meta}"))

    choices = _parse_commit_choices(txt)
    if not choices:
        print(txt)
        print(_wrap(f"{ansi.tag('INFO', ansi.c_cyan())} Tidak bisa mem-parsing kandidat otomatis. Copy manual."))
        return 0

    print(_wrap("Kandidat commit message:"))
    for i, c in enumerate(choices, 1):
        print(_wrap(f"  {i}. {c}"))

    if not apply:
        if not has_staged:
            print(_wrap(f"{ansi.tag('INFO', ansi.c_cyan())} Catatan: belum ada staged changes. Stage dulu biar commit rapi."))
        print(_wrap(f"{ansi.tag('INFO', ansi.c_cyan())} Untuk auto-commit: ai gitx aicm --apply"))
        return 0

    if not has_staged:
        print(_wrap(f"{ansi.tag('INFO', ansi.c_cyan())} Aku tidak auto-commit karena belum ada staged changes."))
        print(_wrap("Saran: stage dulu (git add -p / git add <file>), lalu ulang `ai gitx aicm --apply`."))
        return 0

    try:
        sel = ansi.prompt_text(f"{ansi.c_yellow()}Pilih nomor (1-{len(choices)}) atau ketik message sendiri: {ansi.c_reset()}").strip()
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

    msg = force_single_paragraph(msg)
    if not msg:
        print(_wrap("Message kosong. Dibatalkan."))
        return 0

    verb = "AMEND" if amend else "COMMIT"
    print(_wrap(f"{ansi.tag('WARN', ansi.c_yellow())} Akan menjalankan git commit dengan message:"))
    print(_wrap(f"  \"{msg}\""))
    if not _confirm(f"{ansi.c_yellow()}Lanjutkan {verb}? [y/N] {ansi.c_reset()}"):
        print(_wrap("Dibatalkan."))
        return 0

    cmd = ["commit", "-m", msg] if not amend else ["commit", "--amend", "-m", msg]
    rc, out, err = _git(cmd, cwd=root, timeout=120)
    if rc != 0:
        ansi.print_brief_error(err or out or "Commit gagal.")
        return 2

    print(_wrap(f"{ansi.c_green()}OK.{ansi.c_reset()} Commit sukses."))
    if out:
        print(out)
    return 0


# ----------------------------------------------------------
# AI: Chat mode (gantikan AI Advisor)
# ----------------------------------------------------------

def _chat_snapshot(root: Path) -> str:
    _, st, _ = _git(["status", "-sb"], cwd=root, timeout=5)
    _, br, _ = _git(["branch", "--show-current"], cwd=root, timeout=4)
    _, last, _ = _git(["log", "-n", "8", "--oneline", "--decorate"], cwd=root, timeout=10)
    _, diff_stat, _ = _git(["diff", "--stat"], cwd=root, timeout=12)
    _, diff_cached_stat, _ = _git(["diff", "--cached", "--stat"], cwd=root, timeout=12)

    diff_stat = "\n".join(diff_stat.splitlines()[:40])
    diff_cached_stat = "\n".join(diff_cached_stat.splitlines()[:40])

    return (
        "===== REPO STATE =====\n"
        f"$ git status -sb\n{st}\n\n"
        f"$ git branch --show-current\n{br}\n\n"
        f"$ git log -n 8 --oneline --decorate\n{last}\n\n"
        f"$ git diff --stat\n{diff_stat}\n\n"
        f"$ git diff --cached --stat\n{diff_cached_stat}\n"
        "======================\n"
    )

def _ai_chat_system() -> str:
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

def _render_ai_plan(plan: dict) -> list[str]:
    summary = str(plan.get("summary") or "").strip()
    notes = plan.get("notes") if isinstance(plan.get("notes"), list) else []
    cmds = plan.get("commands") if isinstance(plan.get("commands"), list) else []

    if summary:
        print(_wrap(f"{ansi.tag('AI', ansi.c_green())} {summary}"))

    if notes:
        print(_wrap(f"{ansi.tag('NOTES', ansi.c_cyan())}"))
        for n in notes[:8]:
            print(_wrap(f"- {n}"))

    valid_cmds: list[str] = []
    if cmds:
        print(_wrap(f"{ansi.tag('PLAN', ansi.c_cyan())} Command yang disarankan:"))
        for i, item in enumerate(cmds[:8], 1):
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            cmd = str(item.get("cmd") or "").strip()
            risk = str(item.get("risk") or "low").strip().lower()

            if not cmd.startswith("git "):
                continue

            icon = "✅"
            col = ansi.c_green()
            if risk == "medium":
                icon = "⚠️"
                col = ansi.c_yellow()
            if risk == "high":
                icon = "🧨"
                col = ansi.c_red()

            if title:
                print(_wrap(f"  {i}. {col}{icon} {title}{ansi.c_reset()}"))
            else:
                print(_wrap(f"  {i}. {col}{icon} Command{ansi.c_reset()}"))
            print(_wrap(f"     $ {cmd}"))
            valid_cmds.append(cmd)

    return valid_cmds

def _execute_git_commands(root: Path, cmds: list[str]) -> int:
    if not cmds:
        return 0

    print(_wrap(f"{ansi.tag('INFO', ansi.c_cyan())} Eksekusi hanya dilakukan untuk command `git ...` yang aman (tanpa shell)."))
    if not _confirm(f"{ansi.c_yellow()}Eksekusi semua command di atas? [y/N] {ansi.c_reset()}"):
        # per-command selection
        for i, c in enumerate(cmds, 1):
            if not _confirm(f"{ansi.c_yellow()}Run #{i}? [y/N] {ansi.c_reset()}"):
                continue
            argv = _parse_git_cmd(c)
            if not argv:
                print(_wrap(f"{ansi.tag('INFO', ansi.c_cyan())} Skip (command tidak aman untuk auto-exec): {c}"))
                continue
            rc, out, err = _run(argv, cwd=root, timeout=120)
            if rc != 0:
                ansi.print_brief_error(err or out or f"Command gagal rc={rc}")
                return 2
            if out:
                print(out)
        return 0

    # run all sequential
    for c in cmds:
        argv = _parse_git_cmd(c)
        if not argv:
            print(_wrap(f"{ansi.tag('INFO', ansi.c_cyan())} Skip (command tidak aman untuk auto-exec): {c}"))
            continue
        print(_wrap(f"{ansi.tag('RUN', ansi.c_yellow())} {c}"))
        rc, out, err = _run(argv, cwd=root, timeout=120)
        if rc != 0:
            ansi.print_brief_error(err or out or f"Command gagal rc={rc}")
            return 2
        if out:
            print(out)
    return 0

def action_ai_chat(root: Path, cfg: dict, initial: str) -> int:
    _h("GITX CHAT MODE")

    ok, note = _api_ready(cfg)
    if not ok:
        print(_wrap(f"{ansi.tag('INFO', ansi.c_cyan())} API belum siap untuk Chat GitX: {note}"))
        print(_wrap("Saran: set env var key (GEMINI_API_KEY) lalu coba lagi."))
        return 2

    # Banner (SYSTEM, bukan AI)
    print(_wrap(f"{ansi.tag('SISTEM AI', ansi.c_yellow())} GitX Chat aktif. Ketik 'exit' untuk keluar."))
    print(_wrap(f"{ansi.tag('INFO', ansi.c_cyan())} Aku akan usulkan command git, lalu kamu putuskan mau dieksekusi atau tidak."))

    if initial:
        print(_wrap(f"{ansi.tag('YOU', ansi.c_cyan())} {initial}"))

    system = _ai_chat_system()

    def run_turn(user_text: str) -> int:
        snap = _chat_snapshot(root)
        user_prompt = snap + "\nUSER REQUEST:\n" + user_text.strip()

        with ansi.Spinner("AI lagi mikir (gitx chat)"):
            gok, meta, txt = _api_generate(cfg, system, user_prompt, timeout=35, max_out=900)

        if not gok:
            print(_wrap(f"{ansi.tag('INFO', ansi.c_cyan())} AI gagal dipanggil: {meta}"))
            return 2

        plan = _extract_json_object(txt)
        if not plan:
            # fallback: print raw, tapi tetap tidak auto-exec
            print(_wrap(f"{ansi.tag('INFO', ansi.c_cyan())} Output AI tidak valid JSON. Aku tampilkan raw:"))
            print(txt)
            return 0

        print(_wrap(f"{ansi.tag('INFO', ansi.c_cyan())} AI via API: {meta}"))
        cmds = _render_ai_plan(plan)

        if not cmds:
            print(_wrap(f"{ansi.tag('INFO', ansi.c_cyan())} Tidak ada command yang bisa dieksekusi."))
            return 0

        # Kalau ada command high risk, paksa konfirmasi ekstra
        high_risk = False
        for c in cmds:
            if any(x in c for x in ("reset --hard", "clean -fd", "push --force", "rebase", "checkout --force")):
                high_risk = True
                break
        if high_risk:
            print(_wrap(f"{ansi.tag('WARN', ansi.c_yellow())} Ada command berisiko tinggi. Aku sarankan eksekusi manual atau pastikan backup."))

        # Exec decision
        if _confirm(f"{ansi.c_yellow()}Mau eksekusi command yang disarankan? [y/N] {ansi.c_reset()}"):
            return _execute_git_commands(root, cmds)

        print(_wrap(f"{ansi.tag('INFO', ansi.c_cyan())} Skip eksekusi. Kamu bisa copy/paste manual kalau mau."))
        return 0

    # initial turn (optional)
    if initial.strip():
        _ = run_turn(initial)

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
            print(_wrap("Contoh:"))
            print(_wrap("- tampilkan graph perubahan"))
            print(_wrap("- stage semua perubahan yang relevan"))
            print(_wrap("- buat branch baru feature/x lalu pindah"))
            print(_wrap("- squash commit terakhir jadi 1"))
            continue

        _ = run_turn(user_text)

    print(_wrap(f"{ansi.tag('INFO', ansi.c_cyan())} GitX Chat selesai."))
    return 0


# ==========================================================
# Entry point
# ==========================================================

def handle(argv: list[str], cfg: dict) -> int:
    if not _ensure_git_exists():
        return 2

    cwd = Path.cwd()
    root = _repo_root(cwd)
    if not root:
        ansi.print_brief_error("GitX hanya berjalan di dalam repo git.")
        print(_wrap("Saran: cd ke folder repo kamu, lalu jalankan lagi."))
        return 2

    _print_header(cfg)

    # flags
    as_json = "--json" in argv

    # normalize args
    raw_args = [a for a in argv if a and not a.startswith("--")]
    action = (raw_args[0].lower().strip() if raw_args else "status")
    rest = raw_args[1:] if len(raw_args) > 1 else []

    if action in ("help", "-h", "--help"):
        ansi.print_info("GitX — Git cockpit + safe helpers")
        print("  ai gitx                      : Status ringkas (default).")
        print("  ai gitx status [--json]       : Status ringkas / snapshot JSON.")
        print("  ai gitx branches              : Branch list.")
        print("  ai gitx log [N]               : Log singkat (default 12).")
        print("  ai gitx sync [--rebase]       : Fetch + pull aman.")
        print("  ai gitx wip [msg]             : Stash WIP (include untracked).")
        print("  ai gitx wip --commit [msg]    : WIP commit.")
        print("  ai gitx clean                 : Preview git clean -nd.")
        print("  ai gitx clean --apply         : Execute git clean -fd (confirm).")
        print("  ai gitx open                  : Print command buka repo di VS Code.")
        print("  ai gitx summary               : AI buat PR/changes summary (API-only).")
        print("  ai gitx chat | ai             : Chat mode GitX (API-only).")
        print("  ai gitx halo <text>           : Masuk chat mode dengan salam awal.")
        print("  ai gitx aicm [--apply] [--amend] : AI Commit Message (API-only).")
        return 0

    # status
    if action in ("status", "st", ""):
        snap, err = _collect_snapshot(root)
        if not snap:
            ansi.print_brief_error(err)
            return 2
        _print_status(snap, as_json=as_json)
        return 0

    if action in ("branches", "branch", "br"):
        return action_branches(root)

    if action == "log":
        n = 12
        if rest and rest[0].isdigit():
            n = int(rest[0])
        return action_log(root, n)

    if action == "sync":
        rebase = "--rebase" in argv
        return action_sync(root, rebase=rebase)

    if action == "wip":
        do_commit = "--commit" in argv
        msg = " ".join(rest).strip() if rest else ""
        return action_wip(root, msg=msg, do_commit=do_commit)

    if action == "clean":
        apply = "--apply" in argv
        return action_clean(root, apply=apply)

    if action == "open":
        return action_open(root)

    # AI summary
    if action in ("summary", "sum", "pr"):
        return action_ai_summary(root, cfg)

    # AI commit message
    if action in ("aicm", "commit-msg", "commitmsg", "cm"):
        apply = "--apply" in argv
        amend = "--amend" in argv
        return action_ai_commit_msg(root, cfg, apply=apply, amend=amend)

    # Chat mode (replace advisor)
    if action in ("chat", "ai"):
        initial = " ".join(rest).strip()
        return action_ai_chat(root, cfg, initial=initial)

    if action == "halo":
        initial = ("halo " + " ".join(rest)).strip()
        return action_ai_chat(root, cfg, initial=initial)

    # Fallback: kalau user ngetik sesuatu yang bukan action valid,
    # anggap sebagai masuk chat dengan initial message = seluruh argv.
    # Ini bikin "ai gitx tampilkan graph" langsung masuk chat.
    initial = " ".join(raw_args).strip()
    return action_ai_chat(root, cfg, initial=initial)
