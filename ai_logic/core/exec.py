"""AI_IN-TERMINAL — ai_logic.core.exec
Version: 1.5 (2026-01-04)

Changelog (1.5)
- Centralized shell selection (fish-first for Silvia's environment).
- Provided helpers for running shell commands consistently.

Safety
- This module does NOT bypass SafetyPolicy; higher layers should gate execution.
"""

from __future__ import annotations

import os
import subprocess
from shutil import which
from typing import Sequence


def resolve_shell_executable(cfg: dict) -> str:
    """Resolve shell executable path based on config.

    Supported cfg["exec"]["shell"] values:
    - "fish" (preferred), "bash", "sh"
    - absolute path ("/usr/bin/fish")
    - anything else -> fish if available, else bash, else sh
    """
    pref = str(cfg.get("exec", {}).get("shell") or "fish").strip()

    if pref.startswith("/") and os.path.exists(pref):
        return pref

    key = pref.lower()
    if key in ("fish", "bash", "sh"):
        p = which(key)
        if p:
            return p

    # Fallback order
    for k in ("fish", "bash", "sh"):
        p = which(k)
        if p:
            return p

    # Absolute last resort (very rare)
    return "/bin/sh"


def run_shell_command(cmd: str, *, cfg: dict | None = None, check: bool = False, env: dict | None = None) -> subprocess.CompletedProcess:
    """Run a command string via selected shell.

    Notes
    - Uses shell=True with explicit executable to ensure consistent semantics.
    - For argv-based execution, prefer subprocess.run([...]) in callers.
    """
    c = (cmd or "").strip()
    shell_exe = resolve_shell_executable(cfg or {})
    return subprocess.run(
        c,
        shell=True,
        executable=shell_exe,
        check=check,
        env=env,
    )


def run_argv(argv: Sequence[str], *, check: bool = False, env: dict | None = None) -> subprocess.CompletedProcess:
    """Run argv without invoking a shell (preferred when possible)."""
    return subprocess.run(list(argv), check=check, env=env)
