"""AI_IN-TERMINAL — ai_logic.core.memory
Version: 1.5 (2026-01-04)

Changelog (1.5)
- Standardized memory read/write with atomic persistence.
- Preserved legacy JSON shape: {"enabled": bool, "summary": str}.

Notes
- Memory is intentionally lightweight (single summary string).
"""

from __future__ import annotations

from typing import Any

from ai_logic.core.paths import APP_DIR, MEMORY_PATH
from ai_logic.core.storage import atomic_write_json, ensure_dir, read_json_safe


def load_memory(cfg: dict) -> dict:
    if not cfg.get("memory", {}).get("enabled", True):
        return {"enabled": False, "summary": ""}

    raw = read_json_safe(MEMORY_PATH, default=None)
    if raw is None:
        return {"enabled": True, "summary": ""}
    if not isinstance(raw, dict):
        return {"enabled": True, "summary": ""}

    d: dict[str, Any] = dict(raw)
    d.setdefault("enabled", True)
    d.setdefault("summary", "")
    return {"enabled": bool(d.get("enabled", True)), "summary": str(d.get("summary") or "")}


def save_memory(summary: str, cfg: dict) -> None:
    if not cfg.get("memory", {}).get("enabled", True):
        return

    max_chars = int(cfg.get("memory", {}).get("max_chars", 900))
    s = (summary or "").strip()[:max_chars]

    ensure_dir(APP_DIR)
    atomic_write_json(MEMORY_PATH, {"enabled": True, "summary": s})


def mem_context_text(cfg: dict) -> str:
    mem = load_memory(cfg)
    if not mem.get("enabled", True):
        return ""
    s = str(mem.get("summary") or "").strip()
    return f"Konteks singkat terakhir (agar aku nyambung): {s}" if s else ""
