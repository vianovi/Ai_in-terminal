"""
AI Run - Storage & Profile Management.
Handles JSON profile loading, history logging, and template creation.
"""

import json
import datetime
from pathlib import Path
from typing import Tuple, List, Optional
from system_logic.terminal import ansi
from .const import PROFILE_FILE, HISTORY_FILE

# ==========================================================
# JSON5 SUPPORT (Optional)
# ==========================================================

HAS_JSON5 = False
try:
    import json5  # type: ignore
    HAS_JSON5 = True
except ImportError:
    pass


# ==========================================================
# TEMPLATE PAYLOAD
# ==========================================================

def _template_payload() -> dict:
    """Default template dengan contoh profile."""
    return {
        "version": 1,
        "profiles": [
            {
                "name": "check_env",
                "description": "Check basic dev tools.",
                "requires": {"commands": ["python3", "git"]},
                "steps": [
                    {"title": "Check Python", "cmd": "python3 --version"},
                    {"title": "Check Git", "cmd": "git --version"}
                ]
            }
        ]
    }


# ==========================================================
# FILE MANAGEMENT
# ==========================================================

def _create_template_if_missing() -> None:
    """
    Create template file if missing.

    Logic:
    1. Check if PROFILE_FILE exists
    2. If YES → Do nothing (use existing file)
    3. If NO → Create folder + file with template

    Safety:
    - Atomic file creation with 'x' mode
    - Race condition safe (FileExistsError handling)
    - Proper error reporting to user
    """
    # File already exists, use it
    if PROFILE_FILE.exists():
        return

    try:
        # Ensure parent directory exists
        PROFILE_FILE.parent.mkdir(parents=True, exist_ok=True)

        # Try exclusive create (atomic: fails if file exists)
        with open(PROFILE_FILE, "x", encoding="utf-8") as f:
            json.dump(_template_payload(), f, indent=2)

        # Success: inform user
        ansi.print_system("AI RUNBOOK (BOOTSTRAP)")
        print(f"Template created: {PROFILE_FILE}")

    except FileExistsError:
        # Race condition: another process created it
        # This is OK, just use the existing file
        pass

    except PermissionError as e:
        ansi.print_brief_error(f"Cannot create config file: Permission denied")
        ansi.print_info(f"Location: {PROFILE_FILE}")
        ansi.print_info(f"Fix: Check folder permissions for {PROFILE_FILE.parent}")

    except OSError as e:
        ansi.print_brief_error(f"Cannot create config file: {e}")
        ansi.print_info(f"Location: {PROFILE_FILE}")


def load_profiles() -> Tuple[dict, str]:
    """
    Load profiles from PROFILE_FILE.

    Logic:
    1. Ensure file exists (create template if needed)
    2. Read file content
    3. Parse JSON/JSON5
    4. Validate schema

    Returns:
        (data_dict, error_string)
        - If success: (data, "")
        - If error: ({}, "error message")
    """
    # Step 1: Ensure file exists
    _create_template_if_missing()

    # Step 2: Read file
    try:
        with open(PROFILE_FILE, "r", encoding="utf-8") as f:
            raw = f.read()
    except FileNotFoundError:
        # Should not happen after _create_template_if_missing()
        return {}, f"File not found: {PROFILE_FILE}"
    except PermissionError:
        return {}, f"Permission denied: {PROFILE_FILE}"
    except Exception as e:
        return {}, f"IO Error: {e}"

    # Step 3: Parse JSON/JSON5
    data = {}
    if HAS_JSON5:
        try:
            data = json5.loads(raw)
        except Exception as e:
            return {}, f"JSON5 Syntax Error: {e}"
    else:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            return {}, f"JSON Syntax Error (line {e.lineno}): {e.msg}"

    # Step 4: Validate schema
    if "profiles" not in data:
        return {}, "Invalid schema: Missing 'profiles' key in root object"

    if not isinstance(data.get("profiles"), list):
        return {}, "Invalid schema: 'profiles' must be a list"

    return data, ""


# ==========================================================
# HISTORY LOGGING
# ==========================================================

def log_history(
    profile_name: str,
    result: str,
    exit_code: int,
    step: Optional[int] = None,
    note: str = ""
) -> None:
    """
    Log execution history to HISTORY_FILE.

    Args:
        profile_name: Name of executed profile
        result: Result status (success, failed, cancelled, etc)
        exit_code: Process exit code
        step: Step number where error occurred (if any)
        note: Additional note (max 300 chars)

    Safety:
    - Creates parent directory if needed
    - Keeps last 50 entries (FIFO)
    - Non-blocking (does not crash on failure)
    - Reports errors to stderr
    """
    try:
        # Ensure parent directory exists
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)

        # Load existing history
        hist = []
        if HISTORY_FILE.exists():
            try:
                with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                    hist = json.load(f)
                if not isinstance(hist, list):
                    hist = []
            except (json.JSONDecodeError, OSError):
                # Corrupted history file, start fresh
                hist = []

        # Create new entry
        entry = {
            "time": datetime.datetime.now().isoformat(timespec="seconds"),
            "profile": profile_name,
            "result": result,
            "code": int(exit_code),
            "step": step,
            "note": str(note)[:300]  # Truncate to prevent huge logs
        }

        # Append and trim to last 50 entries
        hist.append(entry)
        if len(hist) > 50:
            hist = hist[-50:]

        # Write atomically
        tmp_file = HISTORY_FILE.with_suffix(".tmp")
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(hist, f, indent=2)

        # Atomic rename
        tmp_file.replace(HISTORY_FILE)

    except PermissionError as e:
        # Non-critical: log to stderr but don't crash
        import sys
        print(f"Warning: Cannot write history (permission denied): {HISTORY_FILE}", file=sys.stderr)

    except Exception as e:
        # Non-critical: log to stderr but don't crash
        import sys
        print(f"Warning: Failed to log history: {e}", file=sys.stderr)


def read_history(limit: int = 10) -> List[dict]:
    """
    Read execution history.

    Args:
        limit: Maximum number of entries to return (most recent)

    Returns:
        List of history entries (most recent last)
    """
    try:
        if not HISTORY_FILE.exists():
            return []

        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, list):
            return []

        # Return last N entries
        limit = max(1, int(limit))
        return data[-limit:]

    except (json.JSONDecodeError, OSError, ValueError):
        return []
