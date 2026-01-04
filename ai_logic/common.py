"""AI_IN-TERMINAL — ai_logic.common
Version: 1.5 (2026-01-04)

Changelog (1.5)
- Centralized core concerns into ai_logic.core (paths/config/storage/memory/safety/routing/exec).
- Added atomic JSON writes for config/memory/last_error.
- Introduced data-first routing via RouteInfo + route_info().
- Preserved legacy API surface (function names + constants) for backward compatibility.

Compatibility
- Existing imports from ai_logic.common should keep working.
- route_for() remains available, but formatting is delegated to ai_logic.ui.ansi.
"""

from __future__ import annotations

import json
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ai_logic.core.paths import (
    APP_DIR,
    CONFIG_PATH,
    MEMORY_PATH,
    LAST_ERROR_PATH,
    MON_HISTORY_PATH,
    RUN_PROFILE_PATH,
    LOGIC_PATH,
    FISH_DIR,
    FISH_FUNCS_DIR,
)
from ai_logic.core.config import load_config
from ai_logic.core.last_error import record_last_error, read_last_error
from ai_logic.core.memory import load_memory, save_memory, mem_context_text
from ai_logic.core.routing import (
    RouteInfo,
    backend_mode,
    api_provider,
    api_active_model_raw,
    route_info,
)
from ai_logic.core.safety import (
    SafetyPolicy,
    SafetyDecision,
    DEFAULT_DENY_SUBSTRINGS,
    DEFAULT_RISKY_PATTERNS,
    default_policy,
    extend_policy,
    evaluate_command,
    is_denied,
    is_risky,
)
from ai_logic.core.exec import resolve_shell_executable, run_shell_command, run_argv


# ============================================================
# API models supported (informational)
# ============================================================

SUPPORTED_API_MODELS = {
    "gemini": ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash", "gemini-2.0-flash-lite"],
    "openai": ["gpt-4o-mini", "gpt-4.1-mini"],
}


# ============================================================
# Backward-compatible aliases (legacy names)
# ============================================================

# Legacy deny list name used by multiple modules.
DENY_SUBSTRINGS = list(DEFAULT_DENY_SUBSTRINGS)

# A few modules historically used these patterns; expose them centrally.
RISKY_PATTERNS = list(DEFAULT_RISKY_PATTERNS)


# ============================================================
# Routing legacy wrapper (kept for compatibility)
# ============================================================

def route_for(cfg: dict, want: str) -> tuple[str, str]:
    """Legacy wrapper returning (backend, route_label).

    Notes
    - Backend selection is data-first via route_info().
    - UI formatting is delegated to ai_logic.ui.ansi.

    want: "ask" | "cmd"
    """
    info = route_info(cfg, want)

    # UI formatting is intentionally imported lazily to avoid hard coupling.
    from ai_logic.ui.ansi import format_route_label

    return info.backend, format_route_label(info)


# ============================================================
# HTTP JSON helper (used by backend wrappers)
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
