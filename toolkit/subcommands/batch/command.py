from __future__ import annotations

import concurrent.futures as cf
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable, Iterable, Optional, Tuple

from ..types import Result
from .ui import render

DEFAULT_ROOT = Path.home() / "Downloads" / "toolkit"
SUBDIR = {
    "video": "video",
    "audio": "audio",
    "image": "images",
    "fetch": "files",
    "extract": "extract",
}


def _now_ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def _log(event: dict) -> None:
    DEFAULT_ROOT.mkdir(parents=True, exist_ok=True)
    log_path = DEFAULT_ROOT / "toolkit.log"
    event = {"ts": _now_ts(), **event}
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def _ensure_tool(name: str) -> None:
    if shutil.which(name) is None:
        raise RuntimeError(f"Dependency missing: {name}. Run: toolkit deps")


def _safe_mkdir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _parse_common(argv: list[str]) -> Tuple[list[str], Optional[Path], bool]:
    # returns (rest, to_dir, force)
    to_dir = None
    force = False
    rest: list[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--to" and i + 1 < len(argv):
            to_dir = Path(argv[i + 1]).expanduser()
            i += 2
            continue
        if a == "--force":
            force = True
            i += 1
            continue
        rest.append(a)
        i += 1
    return rest, to_dir, force


def _read_urls_file(p: Path) -> list[str]:
    lines = []
    for raw in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        lines.append(s)
    return lines


def _failed_path() -> Path:
    _safe_mkdir(DEFAULT_ROOT)
    return DEFAULT_ROOT / "toolkit-failed.txt"


def _read_failed() -> list[str]:
    fp = _failed_path()
    if not fp.exists():
        return []
    return _read_urls_file(fp)


def _write_failed(urls: list[str]) -> None:
    fp = _failed_path()
    _safe_mkdir(fp.parent)
    tmp = fp.with_suffix(".tmp")
    tmp.write_text("\n".join(urls) + ("\n" if urls else ""), encoding="utf-8")
    tmp.replace(fp)


def _dedupe_preserve(items: Iterable[str]) -> list[str]:
    seen = set()
    out = []
    for x in items:
        if x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


def _jobs_default() -> int:
    cpu = os.cpu_count() or 2
    return min(8, max(2, cpu))


def _run(cmd: list[str]) -> Tuple[int, str]:
    _log({"event": "run", "cmd": cmd})
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    out = (p.stdout or "").strip()
    if p.returncode != 0:
        _log({"event": "run_fail", "code": p.returncode, "out": out[:2000]})
    return p.returncode, out


def download_video(url: str, out_dir: Path, force: bool, mode: str) -> Tuple[bool, str]:
    _ensure_tool("yt-dlp")
    _safe_mkdir(out_dir)
    outtmpl = str(out_dir / "%(title).200B [%(id)s].%(ext)s")
    cmd = ["yt-dlp", url, "-o", outtmpl, "--no-playlist"]
    if not force:
        cmd += ["--no-overwrites"]
    if mode == "1080p":
        cmd += ["-f", "bv*[height<=1080]+ba/b[height<=1080]/best[height<=1080]/best"]
    elif mode == "best":
        cmd += ["-f", "bv*+ba/best"]
    elif mode == "sub":
        cmd += ["--write-subs", "--write-auto-subs", "--sub-langs", "all"]
    code, out = _run(cmd)
    return code == 0, out


def download_audio(url: str, out_dir: Path, force: bool, fmt: str) -> Tuple[bool, str]:
    _ensure_tool("yt-dlp")
    _safe_mkdir(out_dir)
    outtmpl = str(out_dir / "%(title).200B [%(id)s].%(ext)s")
    cmd = ["yt-dlp", url, "-o", outtmpl, "--no-playlist", "-x"]
    if fmt in ("mp3", "m4a"):
        cmd += ["--audio-format", fmt]
    if not force:
        cmd += ["--no-overwrites"]
    code, out = _run(cmd)
    return code == 0, out


def _sanitize_name(name: str) -> str:
    name = name.strip()
    name = re.sub(r"[^\w\.\-\(\)\[\]\s]+", "_", name, flags=re.UNICODE)
    name = re.sub(r"\s+", " ", name).strip()
    return name or "download"


def download_fetch(url: str, out_dir: Path, force: bool, name: Optional[str]) -> Tuple[bool, str]:
    _ensure_tool("curl")
    _safe_mkdir(out_dir)
    cmd = ["curl", "-L", "-C", "-", "--fail", "--show-error", "--silent"]
    cmd += ["--output-dir", str(out_dir)]
    if name:
        cmd += ["-o", _sanitize_name(name)]
    else:
        cmd += ["-O"]
    cmd.append(url)
    code, out = _run(cmd)
    return code == 0, out


def handle(argv, cfg) -> Result:
    # toolkit batch urls.txt --video|--audio|--image|--fetch [--to PATH] [--jobs N] [--force]
    rest, to_dir, force = _parse_common(argv)

    jobs = None
    mode = None  # video/audio/image/fetch
    i = 0
    filtered: list[str] = []
    while i < len(rest):
        a = rest[i]
        if a in ("--video", "--audio", "--image", "--fetch"):
            mode = a[2:]
            i += 1
            continue
        if a == "--jobs" and i + 1 < len(rest):
            try:
                jobs = max(1, int(rest[i + 1]))
            except Exception:
                return Result.fail("Invalid --jobs value", exit_code=2, render=render)
            i += 2
            continue
        filtered.append(a)
        i += 1

    if not filtered:
        return Result.fail("Usage: toolkit batch <urls.txt> --video|--audio|--image|--fetch", exit_code=2, render=render)
    if mode is None:
        return Result.fail("Pick one: --video / --audio / --image / --fetch", exit_code=2, render=render)

    in_path = Path(filtered[0]).expanduser()
    if not in_path.exists():
        return Result.fail(f"File not found: {in_path}", exit_code=2, render=render)

    urls = _read_urls_file(in_path)
    prev_failed = _read_failed()
    merged = _dedupe_preserve(urls + prev_failed)

    # output dir
    out_dir = to_dir if to_dir is not None else (DEFAULT_ROOT / SUBDIR[mode])

    total = len(merged)
    prev_failed_count = len(prev_failed)

    # auto parallel if >10
    if jobs is None:
        jobs = _jobs_default()
    parallel = total > 10

    # worker mapping (MVP defaults)
    if mode == "video":
        worker: Callable[[str], Tuple[bool, str]] = lambda u: download_video(u, out_dir, force, "best")
    elif mode == "audio":
        worker = lambda u: download_audio(u, out_dir, force, "m4a")
    else:
        worker = lambda u: download_fetch(u, out_dir, force, None)

    ok_count = 0
    failed_run: list[str] = []

    _log({"event": "batch_start", "mode": mode, "total": total, "prev_failed": prev_failed_count, "parallel": parallel, "jobs": jobs})

    def _one(u: str) -> Tuple[str, bool, str]:
        ok, out = worker(u)
        return u, ok, out

    if parallel:
        with cf.ThreadPoolExecutor(max_workers=jobs) as ex:
            futs = [ex.submit(_one, u) for u in merged]
            for fut in cf.as_completed(futs):
                u, ok, out = fut.result()
                if ok:
                    ok_count += 1
                else:
                    failed_run.append(u)
    else:
        for u in merged:
            u2, ok, out = _one(u)
            if ok:
                ok_count += 1
            else:
                failed_run.append(u2)

    # persist failed (only current failures)
    new_failed = _dedupe_preserve(failed_run)
    _write_failed(new_failed)

    exit_code = 0 if not failed_run else 4
    data = {
        "mode": mode,
        "input_file": str(in_path),
        "out_dir": str(out_dir),
        "total": total,
        "prev_failed": prev_failed_count,
        "ok": ok_count,
        "failed": len(failed_run),
        "failed_file": str(_failed_path()),
        "parallel": parallel,
        "jobs": jobs,
    }

    if exit_code == 0:
        return Result.success(data=data, exit_code=0, render=render)
    return Result.fail("Some items failed (see failed file).", exit_code=exit_code, data=data, render=render)
