from __future__ import annotations

import shutil
import subprocess
from typing import Dict, List, Tuple

from ..types import Result
from .ui import render


def _version(cmd: list[str]) -> str:
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True).strip()
        return out.splitlines()[0][:200]
    except Exception:
        return "unknown"


def handle(argv, cfg) -> Result:
    tools: List[Tuple[str, List[str]]] = [
        ("yt-dlp", ["yt-dlp", "--version"]),
        ("ffmpeg", ["ffmpeg", "-version"]),
        ("curl", ["curl", "--version"]),
        ("tar", ["tar", "--version"]),
        ("unzip", ["unzip", "-v"]),
        ("7z", ["7z", "i"]),
        ("exiftool", ["exiftool", "-ver"]),
        ("magick", ["magick", "-version"]),
        ("unrar", ["unrar"]),
        ("jq", ["jq", "--version"]),
        ("rg", ["rg", "--version"]),
    ]

    status: Dict[str, dict] = {}
    missing = []
    for name, vcmd in tools:
        path = shutil.which(name)
        ok = path is not None
        ver = _version(vcmd) if ok else ""
        status[name] = {"ok": ok, "path": path or "", "version": ver}
        if not ok and name in ("yt-dlp", "ffmpeg", "curl"):
            missing.append(name)

    # CI-friendly: missing core deps -> exit 3
    exit_code = 0 if not missing else 3
    ok = exit_code == 0
    data = {"status": status, "missing_core": missing}
    return Result.success(data=data, exit_code=exit_code, render=render) if ok else Result.fail(
        msg=f"Missing core deps: {', '.join(missing)}",
        exit_code=exit_code,
        data=data,
        render=render,
    )
