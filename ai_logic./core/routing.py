"""AI_IN-TERMINAL — ai_logic.core.routing
Version: 1.5 (2026-01-04)

Changelog (1.5)
- Introduced RouteInfo (data-first routing; no UI formatting).
- Preserved legacy semantics of backend_mode/api_provider/model selection.

Notes
- UI formatting is handled by ai_logic.ui.ansi.
- route_for() compatibility wrapper remains in ai_logic.common.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RouteInfo:
    backend: str               # "local" | "api"
    mode: str                  # "local" | "api" | "auto"
    provider: str = ""         # e.g. "gemini"
    model: str = ""            # raw model id
    note: str = ""             # e.g. "AUTO->API" / fallback notes


def backend_mode(cfg: dict) -> str:
    return (cfg.get("backend_mode") or "local").strip().lower()


def api_provider(cfg: dict) -> str:
    return (cfg.get("api", {}).get("provider") or "gemini").strip().lower()


def api_active_model_raw(cfg: dict) -> str:
    return str(cfg.get("api", {}).get("active_model") or "").strip()


def route_info(cfg: dict, want: str) -> RouteInfo:
    """Resolve route (local/api) for a given intent (ask/cmd)."""
    mode = backend_mode(cfg)

    if mode == "local":
        from ai_logic.backends.local_ollama import local_model_for
        return RouteInfo(backend="local", mode=mode, provider="ollama", model=str(local_model_for(cfg, want) or ""))

    if mode == "api":
        prov = api_provider(cfg) or "api"
        return RouteInfo(backend="api", mode=mode, provider=prov, model=api_active_model_raw(cfg) or "")

    # auto
    prov = api_provider(cfg) or "api"
    ok = False
    try:
        if prov == "gemini":
            from ai_logic.backends.api_gemini import validate_api_config
            ok, _note, _detail = validate_api_config(cfg)
        else:
            # For future providers: assume configured means "ok".
            ok = True
    except Exception:
        ok = False

    if ok:
        return RouteInfo(backend="api", mode="auto", provider=prov, model=api_active_model_raw(cfg) or "", note="auto_api")

    from ai_logic.backends.local_ollama import local_model_for
    return RouteInfo(backend="local", mode="auto", provider="ollama", model=str(local_model_for(cfg, want) or ""), note="auto_local")
