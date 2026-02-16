import json
import datetime
from pathlib import Path
from typing import Tuple, List, Optional
from system_logic.terminal import ansi
from .const import PROFILE_FILE, HISTORY_FILE

HAS_JSON5 = False
try:
    import json5 # type: ignore
    HAS_JSON5 = True
except ImportError:
    HAS_JSON5 = False

def _template_payload() -> dict:
    return {
        "version": 1,
        "profiles": [
            {
                "name": "check_env",
                "description": "Check basic dev tools.",
                "requires": {"commands": ["python3", "git"]},
                "steps": [{"title": "Check Python", "cmd": "python3 --version"}]
            }
        ]
    }

def _create_template_if_missing() -> None:
    if PROFILE_FILE.exists(): return
    try:
        PROFILE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(PROFILE_FILE, "w", encoding="utf-8") as f:
            json.dump(_template_payload(), f, indent=2)
        ansi.print_system("AI RUNBOOK (BOOTSTRAP)")
        print(f"Template created: {PROFILE_FILE}")
    except Exception: pass

def load_profiles() -> Tuple[dict, str]:
    _create_template_if_missing()
    try:
        with open(PROFILE_FILE, "r", encoding="utf-8") as f:
            raw = f.read()
    except Exception as e:
        return {}, f"IO Error: {e}"

    data = {}
    if HAS_JSON5:
        try:
            data = json5.loads(raw)
        except Exception as e:
            return {}, f"JSON5 Syntax: {e}"
    else:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            return {}, f"JSON Error: {e}"

    if "profiles" not in data or not isinstance(data.get("profiles"), list):
        return {}, "Schema invalid: Root must have 'profiles' list."
    return data, ""

def log_history(profile_name: str, result: str, exit_code: int, step: Optional[int] = None, note: str = "") -> None:
    try:
        PROFILE_FILE.parent.mkdir(parents=True, exist_ok=True)
        hist = []
        if HISTORY_FILE.exists():
            with open(HISTORY_FILE, "r") as f:
                hist = json.load(f)

        entry = {
            "time": datetime.datetime.now().isoformat(timespec="seconds"),
            "profile": profile_name,
            "result": result,
            "code": int(exit_code),
            "step": step,
            "note": note[:300]
        }
        hist.append(entry)
        if len(hist) > 50: hist = hist[-50:]

        with open(HISTORY_FILE, "w") as f:
            json.dump(hist, f, indent=2)
    except: pass

def read_history(limit: int = 10) -> List[dict]:
    try:
        if not HISTORY_FILE.exists(): return []
        with open(HISTORY_FILE, "r") as f:
            d = json.load(f)
            return d[-max(1, int(limit)):] if isinstance(d, list) else []
    except: return []