from __future__ import annotations

import datetime as _dt
import json
import os
import re
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


# ============================================================
# Path penting (single place)
# ============================================================

APP_DIR = Path.home() / ".config" / "ai-term"
CONFIG_PATH = APP_DIR / "config.json"
MEMORY_PATH = APP_DIR / "memory.json"
LAST_ERROR_PATH = APP_DIR / "last_error.json"
MON_HISTORY_PATH = APP_DIR / "mon_history.json"

# Informational paths (untuk status)
LOGIC_PATH = Path.home() / ".local" / "bin" / "ai-term"
FISH_DIR = Path.home() / ".config" / "fish"
FISH_FUNCS_DIR = FISH_DIR / "functions"

SUPPORTED_API_MODELS = {
    "gemini": ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash", "gemini-2.0-flash-lite"],
    "openai": ["gpt-4o-mini", "gpt-4.1-mini"],
}

DENY_SUBSTRINGS = [
    "rm -rf /",
    " mkfs",
    "dd if=",
    ":(){:|:&};:",
    " shutdown",
    " reboot",
    " poweroff",
]


# ============================================================
# Config / memory / last error
# ============================================================

def load_config() -> dict:
    if not CONFIG_PATH.exists():
        APP_DIR.mkdir(parents=True, exist_ok=True)
        default = {
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
        }
        CONFIG_PATH.write_text(json.dumps(default, indent=2), encoding="utf-8")
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def record_last_error(stage: str, backend: str, detail: str) -> None:
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "time": _dt.datetime.now().isoformat(timespec="seconds"),
            "stage": stage,
            "backend": backend,
            "detail": (detail or "")[:6000],
        }
        LAST_ERROR_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception:
        pass


def read_last_error() -> dict | None:
    try:
        if not LAST_ERROR_PATH.exists():
            return None
        d = json.loads(LAST_ERROR_PATH.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def load_memory(cfg: dict) -> dict:
    if not cfg.get("memory", {}).get("enabled", True):
        return {"enabled": False, "summary": ""}
    if not MEMORY_PATH.exists():
        return {"enabled": True, "summary": ""}
    try:
        d = json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
        if not isinstance(d, dict):
            return {"enabled": True, "summary": ""}
        d.setdefault("enabled", True)
        d.setdefault("summary", "")
        return d
    except Exception:
        return {"enabled": True, "summary": ""}


def save_memory(summary: str, cfg: dict) -> None:
    if not cfg.get("memory", {}).get("enabled", True):
        return
    max_chars = int(cfg.get("memory", {}).get("max_chars", 900))
    summary = (summary or "").strip()[:max_chars]
    APP_DIR.mkdir(parents=True, exist_ok=True)
    MEMORY_PATH.write_text(json.dumps({"enabled": True, "summary": summary}, indent=2), encoding="utf-8")


def mem_context_text(cfg: dict) -> str:
    mem = load_memory(cfg)
    if not mem.get("enabled", True):
        return ""
    s = str(mem.get("summary") or "").strip()
    return f"Konteks singkat terakhir (agar aku nyambung): {s}" if s else ""


# ============================================================
# Backend routing (konsisten dan terpusat)
# ============================================================

def backend_mode(cfg: dict) -> str:
    return (cfg.get("backend_mode") or "local").strip().lower()


def api_provider(cfg: dict) -> str:
    return (cfg.get("api", {}).get("provider") or "gemini").strip().lower()


def api_active_model_raw(cfg: dict) -> str:
    return str(cfg.get("api", {}).get("active_model") or "").strip()


def route_for(cfg: dict, want: str) -> tuple[str, str]:
    """
    Return: (backend, route_label)
      backend: "local" | "api"
    want: "ask" | "cmd"
    """
    from ai_logic.ui.ansi import tag, c_yellow, c_green, c_dim, c_reset

    mode = backend_mode(cfg)

    def api_label() -> str:
        prov = (api_provider(cfg) or "api").strip().upper()
        raw_model = api_active_model_raw(cfg) or "(unset)"
        return f"{tag('API', c_yellow())} -> {tag(prov, c_green())} • {raw_model}"

    def local_label() -> str:
        from ai_logic.backends.local_ollama import local_model_for
        return f"{tag('LOCAL', c_green())} • {local_model_for(cfg, want) or '(unset)'}"

    if mode == "local":
        return "local", local_label()

    if mode == "auto":
        try:
            from ai_logic.backends.api_gemini import validate_api_config
            ok, _note, _detail = validate_api_config(cfg)
        except Exception:
            ok = False
        if ok:
            return "api", f"{tag('AUTO', c_yellow())} -> {api_label()}"
        return "local", f"{tag('AUTO', c_yellow())} -> {local_label()}"

    # mode == "api"
    return "api", api_label()


# ============================================================
# HTTP JSON helper (dipakai oleh backend wrappers)
# ============================================================

def http_json(method: str, url: str, payload: dict | None = None, timeout: int = 60, headers: dict | None = None) -> dict:
    data = None
    hdrs = {"Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")

    req = Request(url=url, data=data, method=method, headers=hdrs)
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw.strip() else {}
    except HTTPError as ex:
        body = ex.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {ex.code}: {body}") from ex
    except TimeoutError as ex:
        raise RuntimeError("Timeout (server terlalu lama menjawab).") from ex
    except URLError as ex:
        raise RuntimeError(f"URLError: {ex.reason}") from ex


def looks_like_conn_refused(err: Exception) -> bool:
    s = str(err).lower()
    return ("errno 111" in s) or ("connection refused" in s)


def normalize_model_id(model: str) -> str:
    m = (model or "").strip()
    return m[7:] if m.startswith("models/") else m


def force_single_paragraph(text: str) -> str:
    s = re.sub(r"\s*\n+\s*", " ", (text or "").strip())
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip()
