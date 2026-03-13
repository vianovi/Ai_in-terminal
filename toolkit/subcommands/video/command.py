from __future__ import annotations

from pathlib import Path

from ..types import Result
from ..batch.command import _parse_common, DEFAULT_ROOT, SUBDIR, download_video
from .ui import render


def handle(argv, cfg) -> Result:
    # toolkit video <url> [--1080p|--best|--sub] [--to PATH] [--force]
    rest, to_dir, force = _parse_common(argv)

    mode = "best"
    filtered = []
    for a in rest:
        if a == "--1080p":
            mode = "1080p"
        elif a == "--best":
            mode = "best"
        elif a == "--sub":
            mode = "sub"
        else:
            filtered.append(a)

    if not filtered:
        return Result.fail("Usage: toolkit video <url> [--1080p|--best|--sub] [--to PATH] [--force]", exit_code=2, render=render)

    url = filtered[0]
    out_dir = to_dir if to_dir else (DEFAULT_ROOT / SUBDIR["video"])
    ok, out = download_video(url, Path(out_dir), force, mode)

    data = {"url": url, "out_dir": str(out_dir), "mode": mode, "ok": ok, "raw": out}
    if ok:
        return Result.success(data=data, exit_code=0, render=render)
    return Result.fail("Download failed.", exit_code=4, data=data, render=render)
