from __future__ import annotations

from pathlib import Path

from ..types import Result
from ..batch.command import _parse_common, DEFAULT_ROOT, SUBDIR, download_audio
from .ui import render


def handle(argv, cfg) -> Result:
    # toolkit audio <url> [--mp3|--m4a] [--to PATH] [--force]
    rest, to_dir, force = _parse_common(argv)

    fmt = "m4a"
    filtered = []
    for a in rest:
        if a == "--mp3":
            fmt = "mp3"
        elif a == "--m4a":
            fmt = "m4a"
        else:
            filtered.append(a)

    if not filtered:
        return Result.fail("Usage: toolkit audio <url> [--mp3|--m4a] [--to PATH] [--force]", exit_code=2, render=render)

    url = filtered[0]
    out_dir = to_dir if to_dir else (DEFAULT_ROOT / SUBDIR["audio"])
    ok, out = download_audio(url, Path(out_dir), force, fmt)

    data = {"url": url, "out_dir": str(out_dir), "format": fmt, "ok": ok, "raw": out}
    if ok:
        return Result.success(data=data, exit_code=0, render=render)
    return Result.fail("Audio extraction failed.", exit_code=4, data=data, render=render)
