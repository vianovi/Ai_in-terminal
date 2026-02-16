import re
from pathlib import Path

# Try common import, fallback if standalone
try:
    from system_logic.core import RUN_PROFILE_PATH, DENY_SUBSTRINGS, RISKY_PATTERNS
except ImportError:
    RUN_PROFILE_PATH = Path.cwd() / "workspace" / "run_profile.json"
    DENY_SUBSTRINGS = ["rm -rf /", "mkfs", "dd if=", "shutdown", "reboot", "poweroff"]
    RISKY_PATTERNS = [r"\brm\s+-rf\b", r"\bdd\s+if=", r"\bmkfs(\.\w+)?\b"]

def _resolve_profile_file(path_obj: Path) -> Path:
    p = Path(path_obj)
    if p.suffix.lower() in (".json", ".json5"):
        return p
    return p / "run_profile.json"

PROFILE_FILE = _resolve_profile_file(RUN_PROFILE_PATH)
HISTORY_FILE = PROFILE_FILE.parent / "run_history.json"

RISKY_REGEX = None
if RISKY_PATTERNS:
    RISKY_REGEX = re.compile("|".join(f"(?:{p})" for p in RISKY_PATTERNS), re.IGNORECASE)

BAD_CONTROL_REGEX = re.compile(r"[\r\n]")
VAR_PATTERN = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")