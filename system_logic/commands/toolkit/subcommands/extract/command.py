from __future__ import annotations

import shutil
from pathlib import Path

from ..types import Result
from ..batch.command import _parse_common, DEFAULT_ROOT, SUBDIR, _run, _safe_mkdir
from .ui import render


def handle(argv, cfg) -> Result:
    # toolkit extract <archive> [--to PATH]
    rest, to_dir, force = _parse_common(argv)

    if not rest:
        return Result.fail("Usage: toolkit extract <archive> [--to PATH]", exit_code=2, render=render)

    arc = Path(rest[0]).expanduser()
    if not arc.exists():
        return Result.fail(f"File not found: {arc}", exit_code=2, render=render)

    if to_dir is None:
        out_dir = DEFAULT_ROOT / SUBDIR["extract"] / arc.stem
    else:
        out_dir = Path(to_dir).expanduser()

    _safe_mkdir(out_dir)

    name = arc.name.lower()
    cmd = None

    if name.endswith(".zip"):
        if shutil.which("unzip") is None:
            return Result.fail("Dependency missing: unzip. Run: toolkit deps", exit_code=3, render=render)
        cmd = ["unzip", "-o" if force else "-n", str(arc), "-d", str(out_dir)]
    elif any(name.endswith(x) for x in (".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz")):
        if shutil.which("tar") is None:
            return Result.fail("Dependency missing: tar. Run: toolkit deps", exit_code=3, render=render)
        cmd = ["tar", "-xf", str(arc), "-C", str(out_dir)]
    elif name.endswith(".7z"):
        if shutil.which("7z") is None:
            return Result.fail("Dependency missing: p7zip (7z). Run: toolkit deps", exit_code=3, render=render)
        cmd = ["7z", "x", str(arc), f"-o{out_dir}"]
        if not force:
            cmd += ["-aos"]
    elif name.endswith(".rar"):
        if shutil.which("unrar") is not None:
            cmd = ["unrar", "x", "-o+" if force else "-o-", str(arc), str(out_dir)]
        elif shutil.which("7z") is not None:
            cmd = ["7z", "x", str(arc), f"-o{out_dir}"]
            if not force:
                cmd += ["-aos"]
        else:
            return Result.fail("Dependency missing: unrar or 7z. Run: toolkit deps", exit_code=3, render=render)
    else:
        return Result.fail("Unsupported archive format.", exit_code=2, render=render)

    code, out = _run(cmd)
    ok = code == 0
    data = {"archive": str(arc), "out_dir": str(out_dir), "ok": ok, "raw": out}
    if ok:
        return Result.success(data=data, exit_code=0, render=render)
    return Result.fail("Extract failed.", exit_code=4, data=data, render=render)
