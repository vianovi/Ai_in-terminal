from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RouteInfo:
    backend: str               # "local" | "api"
    mode: str                  # "local" | "api" | "auto"
    provider: str = ""         # e.g. "gemini" | "ollama"
    model: str = ""            # raw model id
    note: str = ""             # e.g. "auto_api" / "auto_local" / fallback notes

    @property
    def label(self) -> str:
        """
        Human-friendly route label untuk meta line/UI.
        Format konsisten dan tidak bergantung field lain.
        """
        prov = (self.provider or "").strip()
        mdl = (self.model or "").strip()

        base = ""
        if self.backend == "api":
            base = "API"
            if prov:
                base += f"({prov})"
            if mdl:
                base += f" • {mdl}"
        else:
            base = "LOCAL"
            if mdl:
                base += f" • {mdl}"

        if self.mode == "auto":
            # note bisa "auto_api" / "auto_local" / dll
            tag = self.note or "auto"
            base = f"AUTO→{base} ({tag})"

        return base


def backend_mode(cfg: dict) -> str:
    return (cfg.get("backend_mode") or "local").strip().lower()


def api_provider(cfg: dict) -> str:
    return (cfg.get("api", {}).get("provider") or "gemini").strip().lower()


def api_active_model_raw(cfg: dict) -> str:
    return str(cfg.get("api", {}).get("active_model") or "").strip()


def route_info(cfg: dict, want: str) -> RouteInfo:
    """
    Resolve route (local/api) for a given intent (ask/cmd/ai).
    want dipakai untuk memilih model lokal yang beda antara ask/cmd.
    """
    mode = backend_mode(cfg)

    if mode == "local":
        from system_logic.backends.local_ollama import local_model_for
        return RouteInfo(
            backend="local",
            mode=mode,
            provider="ollama",
            model=str(local_model_for(cfg, want) or ""),
        )

    if mode == "api":
        prov = api_provider(cfg) or "api"
        return RouteInfo(
            backend="api",
            mode=mode,
            provider=prov,
            model=api_active_model_raw(cfg) or "",
        )

    # auto
    prov = api_provider(cfg) or "api"
    ok = False
    try:
        if prov == "gemini":
            from system_logic.backends.api_gemini import validate_api_config
            ok, _note, _detail = validate_api_config(cfg)
        else:
            ok = True
    except Exception:
        ok = False

    if ok:
        return RouteInfo(
            backend="api",
            mode="auto",
            provider=prov,
            model=api_active_model_raw(cfg) or "",
            note="auto_api",
        )

    from system_logic.backends.local_ollama import local_model_for
    return RouteInfo(
        backend="local",
        mode="auto",
        provider="ollama",
        model=str(local_model_for(cfg, want) or ""),
        note="auto_local",
    )
