from __future__ import annotations

from pathlib import Path

# ============================================================
# Primary app directory
# ============================================================

APP_DIR = Path.home() / ".config" / "ai-term"


# ============================================================
# Core persistent files
# ============================================================

CONFIG_PATH = APP_DIR / "config.json"
MEMORY_PATH = APP_DIR / "memory.json"
LAST_ERROR_PATH = APP_DIR / "last_error.json"
MON_HISTORY_PATH = APP_DIR / "mon_history.json"
RUN_PROFILE_PATH = APP_DIR / "run_profile.json"


# ============================================================
# Informational paths (used by status / diagnostics)
# ============================================================

LOGIC_PATH = Path.home() / ".local" / "bin" / "ai-term"
FISH_DIR = Path.home() / ".config" / "fish"
FISH_FUNCS_DIR = FISH_DIR / "functions"
