"""
MON Constants - Path & Config Locations.
Menggunakan centralized path management dari system_logic.core.paths
"""

from system_logic.core.paths import (
    MON_HISTORY_DB,
    MON_CONFIG_PATH,
    SENTINEL_LOG_PATH,
    MON_HISTORY_LOCK,
)

# Backwards compatibility exports
MON_HISTORY_PATH = MON_HISTORY_DB  # Sekarang pointing ke SQLite DB
MON_CONFIG_FILE = MON_CONFIG_PATH
SENTINEL_LOG_FILE = SENTINEL_LOG_PATH
HISTORY_LOCK_FILE = MON_HISTORY_LOCK

__all__ = [
    "MON_HISTORY_PATH",
    "MON_CONFIG_FILE",
    "SENTINEL_LOG_FILE",
    "HISTORY_LOCK_FILE",
]