from __future__ import annotations

from pathlib import Path

from ..types import Result
from ..batch.command import _parse_common, DEFAULT_ROOT, SUBDIR, download_fetch
from .ui import render


def handle(argv, cfg) -> Result:
    # toolkit image <url> [--name X] [--to PATH] [--force]
    rest, to_dir, force = _parse_common(argv)

    name = None
    filtered = []
    i = 0
    while i < len(rest):
        a = rest[i]
        if a == "--name" and i + 1 < len(rest):
            name = rest[i + 1]
            i += 2
            continue
        filtered.append(a)
        i += 1

    if not filtered:
        return Result.fail("Usage: toolkit image <url> [--name X] [--to PATH] [--force]", exit_code=2, render=render)

    url = filtered[0]
    out_dir = to_dir if to_dir else (DEFAULT_ROOT / SUBDIR["image"])
    ok, out = download_fetch(url, Path(out_dir), force, name)

    data = {"url": url, "out_dir": str(out_dir), "name": name, "ok": ok, "raw": out}
    if ok:
        return Result.success(data=data, exit_code=0, render=render)
    return Result.fail("Image download failed.", exit_code=4, data=data, render=render)
