"""
AI Run - Constants & Path Management.
Single source of truth untuk path locations.
"""

import re
from pathlib import Path

# ==========================================================
# PATH MANAGEMENT (Primary Source)
# ==========================================================

try:
    from system_logic.core.paths import RUN_PROFILE_PATH, DENY_SUBSTRINGS, RISKY_PATTERNS
except ImportError:
    # Fallback: Predictable user config location (jika standalone mode)
    _APP_DIR = Path.home() / ".config" / "ai-term"
    RUN_PROFILE_PATH = _APP_DIR / "run_profile.json"
    DENY_SUBSTRINGS = ["rm -rf /", "mkfs", "dd if=", "shutdown", "reboot", "poweroff"]
    RISKY_PATTERNS = [r"\brm\s+-rf\b", r"\bdd\s+if=", r"\bmkfs(\.\w+)?\b"]

# Direct usage - NO resolver, NO magic
PROFILE_FILE = RUN_PROFILE_PATH
HISTORY_FILE = PROFILE_FILE.parent / "run_history.json"


# ==========================================================
# SAFETY REGEX COMPILATION
# ==========================================================

RISKY_REGEX = None
if RISKY_PATTERNS:
    RISKY_REGEX = re.compile("|".join(f"(?:{p})" for p in RISKY_PATTERNS), re.IGNORECASE)

BAD_CONTROL_REGEX = re.compile(r"[\r\n]")
VAR_PATTERN = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")