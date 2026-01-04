"""AI_IN-TERMINAL — ai_logic.core.last_error
Version: 1.5 (2026-01-04)

Changelog (1.5)
- Writes last_error.json atomically.
- Preserves legacy schema.
"""

from __future__ import annotations

import datetime as _dt

from ai_logic.core.paths import APP_DIR, LAST_ERROR_PATH
from ai_logic.core.storage import atomic_write_json, ensure_dir, read_json_safe


def record_last_error(stage: str, backend: str, detail: str) -> None:
    try:
        ensure_dir(APP_DIR)
        payload = {
            "time": _dt.datetime.now().isoformat(timespec="seconds"),
            "stage": stage,
            "backend": backend,
            "detail": (detail or "")[:6000],
        }
        atomic_write_json(LAST_ERROR_PATH, payload)
    except Exception:
        # Do not crash the main flow due to logging.
        pass


def read_last_error() -> dict | None:
    try:
        raw = read_json_safe(LAST_ERROR_PATH, default=None)
        return raw if isinstance(raw, dict) else None
    except Exception:
        return None
