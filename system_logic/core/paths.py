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

# ============================================================
# MON (Monitoring System) paths
# ============================================================
# Tambahkan di bawah existing paths

# MON data directory (untuk SQLite database)
MON_DATA_DIR = APP_DIR / "mon_data"

# MON history database (SQLite untuk performance)
MON_HISTORY_DB = MON_DATA_DIR / "history.db"

# MON Sentinel logs directory
MON_LOG_DIR = APP_DIR / "mon_logs"
SENTINEL_LOG_PATH = MON_LOG_DIR / "sentinel.log"
SENTINEL_LOG_ARCHIVE = MON_LOG_DIR / "archive"

# MON config (tetap JSON, jarang diubah)
MON_CONFIG_PATH = APP_DIR / "mon_config.json"

# Lock files untuk prevent concurrent access
MON_LOCK_DIR = APP_DIR / "mon_locks"
MON_HISTORY_LOCK = MON_LOCK_DIR / "history.lock"

"""
=== ADDITION TO system_logic/core/paths.py ===
Append these lines to the EXISTING paths.py file
"""

from pathlib import Path

# MON Framework paths (ADD THESE)
MON_DATA_DIR = APP_DIR / "mon_data"
MON_HISTORY_DB = MON_DATA_DIR / "history.db"
MON_HISTORY_PATH = APP_DIR / "mon_history.json"  # Legacy
MON_HISTORY_LOCK = APP_DIR / "mon_locks" / "history.lock"
MON_CONFIG_PATH = APP_DIR / "mon_config.json"
MON_LOG_DIR = APP_DIR / "mon_logs"
SENTINEL_LOG_PATH = MON_LOG_DIR / "sentinel.log"
SENTINEL_LOG_ARCHIVE = MON_LOG_DIR / "archive"
MON_LOCK_DIR = APP_DIR / "mon_locks"