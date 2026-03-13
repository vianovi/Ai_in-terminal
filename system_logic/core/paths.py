from __future__ import annotations

from pathlib import Path

# ============================================================
# Primary app directory
# ============================================================

APP_DIR = Path.home() / ".config" / "ai-term"


# ============================================================
# Core persistent files
# ============================================================

CONFIG_PATH      = APP_DIR / "config.json"
MEMORY_PATH      = APP_DIR / "memory.json"
LAST_ERROR_PATH  = APP_DIR / "last_error.json"
RUN_PROFILE_PATH = APP_DIR / "run_profile.json"


# ============================================================
# Informational paths (used by status / diagnostics)
# ============================================================

LOGIC_PATH    = Path.home() / ".local" / "bin" / "ai-term"
FISH_DIR      = Path.home() / ".config" / "fish"
FISH_FUNCS_DIR = FISH_DIR / "functions"


# ============================================================
# MON (Monitoring System) paths
# ============================================================

MON_DATA_DIR         = APP_DIR / "mon_data"
MON_HISTORY_DB       = MON_DATA_DIR / "history.db"
MON_HISTORY_PATH     = APP_DIR / "mon_history.json"   # legacy JSON, kept for compat
MON_CONFIG_PATH      = APP_DIR / "mon_config.json"
MON_LOG_DIR          = APP_DIR / "mon_logs"
SENTINEL_LOG_PATH    = MON_LOG_DIR / "sentinel.log"
SENTINEL_LOG_ARCHIVE = MON_LOG_DIR / "archive"
MON_LOCK_DIR         = APP_DIR / "mon_locks"
MON_HISTORY_LOCK     = MON_LOCK_DIR / "history.lock"


# ============================================================
# Toolkit paths
# ============================================================

TOOLKIT_ROOT     = Path.home() / "Downloads" / "toolkit"
TOOLKIT_LOG_PATH = TOOLKIT_ROOT / "toolkit.log"
TOOLKIT_FAILED   = TOOLKIT_ROOT / "toolkit-failed.txt"

TOOLKIT_DIR_VIDEO   = TOOLKIT_ROOT / "video"
TOOLKIT_DIR_AUDIO   = TOOLKIT_ROOT / "audio"
TOOLKIT_DIR_IMAGE   = TOOLKIT_ROOT / "images"
TOOLKIT_DIR_FETCH   = TOOLKIT_ROOT / "files"