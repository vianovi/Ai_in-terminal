"""AI_IN-TERMINAL — ai_logic.core.config
Version: 1.5 (2026-01-04)

Changelog (1.5)
- Added deep default-merge for config (backward compatible).
- Added minimal migration fields for exec shell + ui toggles.
- Uses atomic writes when creating a new config.

Guarantees
- load_config() never returns missing core keys.
- Existing user config values are preserved.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from ai_logic.core.paths import APP_DIR, CONFIG_PATH
from ai_logic.core.storage import atomic_write_json, ensure_dir, read_json_safe


DEFAULT_CONFIG: dict[str, Any] = {
    "backend_mode": "auto",  # "local" | "api" | "auto"
    "ollama": {
        "host": "http://localhost:11434",
        "active_model_ask": "llama3.1:8b",
        "active_model_cmd": "llama3.1:8b",
    },
    "api": {
        "provider": "gemini",
        "active_model": "gemini-2.5-flash",  # boleh juga "models/gemini-2.5-flash"
        "openai_api_key_env": "OPENAI_API_KEY",
        "gemini_api_key_env": "GEMINI_API_KEY",
    },
    "memory": {
        "enabled": True,
        "max_chars": 900,
    },
    # Execution preferences (new in 1.5)
    "exec": {
        # "fish" is recommended for Silvia's environment. Supported: fish|bash|sh|/absolute/path
        "shell": "fish",
    },
    # UI preferences (new in 1.5)
    "ui": {
        "no_emoji": False,
    },
}


def _deep_merge(dst: dict[str, Any], src: Mapping[str, Any]) -> dict[str, Any]:
    """Merge src into dst recursively, without overwriting existing scalars."""
    for k, v in src.items():
        if k not in dst:
            dst[k] = deepcopy(v)
            continue
        if isinstance(dst.get(k), dict) and isinstance(v, Mapping):
            _deep_merge(dst[k], v)  # type: ignore[arg-type]
    return dst


def load_config() -> dict:
    """Load config with backward-compatible defaults merged in."""
    ensure_dir(APP_DIR)

    if not CONFIG_PATH.exists():
        # First run: create a fresh config atomically.
        atomic_write_json(CONFIG_PATH, DEFAULT_CONFIG)
        return deepcopy(DEFAULT_CONFIG)

    raw = read_json_safe(CONFIG_PATH, default={})
    cfg: dict[str, Any] = raw if isinstance(raw, dict) else {}

    merged = deepcopy(cfg)
    _deep_merge(merged, DEFAULT_CONFIG)
    return merged
