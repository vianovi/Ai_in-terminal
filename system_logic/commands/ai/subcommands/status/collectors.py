from __future__ import annotations

import json
import time
from datetime import datetime
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

from system_logic.core.paths import (
    CONFIG_PATH,
    MEMORY_PATH,
    MON_HISTORY_PATH,
    RUN_PROFILE_PATH,
    LAST_ERROR_PATH,
    LOGIC_PATH,
)
from system_logic.core.routing import backend_mode, api_provider, api_active_model_raw
from system_logic.core.last_error import read_last_error
from system_logic.backends.local_ollama import ollama_host, local_model_for
from system_logic.backends.api_gemini import gemini_key, validate_api_config

from .models import (
    ApiProbe,
    LastErrorBrief,
    LocalProbe,
    PathsBrief,
    RegistryProbe,
    RoutingBrief,
    Snapshot,
)


def _path_str(p) -> str:
    try:
        return str(p)
    except Exception:
        return repr(p)


def _http_json(url: str, timeout: float = 2.0) -> tuple[bool, dict, str, int]:
    t0 = time.time()
    try:
        req = Request(url=url, headers={"Accept": "application/json"}, method="GET")
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            ms = int((time.time() - t0) * 1000)
            if not raw.strip():
                return True, {}, "", ms
            return True, json.loads(raw), "", ms
    except HTTPError as ex:
        ms = int((time.time() - t0) * 1000)
        return False, {}, f"HTTP {ex.code}", ms
    except URLError as ex:
        ms = int((time.time() - t0) * 1000)
        return False, {}, f"URLError: {ex.reason}", ms
    except Exception as ex:
        ms = int((time.time() - t0) * 1000)
        return False, {}, f"{type(ex).__name__}: {ex}", ms


def probe_local(cfg: dict) -> LocalProbe:
    host = ollama_host(cfg).rstrip("/")
    ok, _data, err, ms = _http_json(f"{host}/api/tags", timeout=2.0)

    state = "READY"
    detail = "reachable"
    if not ok:
        low = (err or "").lower()
        if "timed out" in low or "timeout" in low:
            state = "SLOW"
            detail = err
        else:
            state = "DOWN"
            detail = err

    return LocalProbe(
        state=state,  # type: ignore
        host=host,
        latency_ms=ms,
        detail=detail,
        model_ask=str(local_model_for(cfg, "ask") or ""),
        model_cmd=str(local_model_for(cfg, "cmd") or ""),
    )


def probe_api(cfg: dict) -> ApiProbe:
    prov = api_provider(cfg) or "gemini"
    active = api_active_model_raw(cfg) or ""
    key = gemini_key(cfg) if prov == "gemini" else ""
    ok, note, detail = validate_api_config(cfg) if prov == "gemini" else (True, "provider", "")

    return ApiProbe(
        provider=prov,
        key_present=bool(key),
        config_ok=bool(ok),
        note=str(note or ""),
        detail=str(detail or ""),
        active_model=str(active),
    )


def probe_registry() -> RegistryProbe:
    try:
        from system_logic.commands.ai.subcommands import registry
        cmds = registry.get_all_commands()
        for c in cmds:
            mod = registry.get_module(c)
            if not hasattr(mod, "handle"):
                return RegistryProbe(False, f"'{c}' missing handle()")
        return RegistryProbe(True, f"{len(cmds)} cmds OK")
    except Exception as ex:
        return RegistryProbe(False, f"{type(ex).__name__}: {ex}")


def probe_last_error() -> LastErrorBrief:
    le = read_last_error()
    if not le:
        return LastErrorBrief(False)
    return LastErrorBrief(
        True,
        time=str(le.get("time") or ""),
        stage=str(le.get("stage") or ""),
        backend=str(le.get("backend") or ""),
        detail=str(le.get("detail") or ""),
    )


def collect_snapshot(cfg: dict) -> Snapshot:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    routing = RoutingBrief(
        backend_mode=backend_mode(cfg),
        api_provider=api_provider(cfg),
        api_active_model=api_active_model_raw(cfg),
    )
    local = probe_local(cfg)
    api = probe_api(cfg)
    reg = probe_registry()
    le = probe_last_error()
    paths = PathsBrief(
        logic=_path_str(LOGIC_PATH),
        config=_path_str(CONFIG_PATH),
        memory=_path_str(MEMORY_PATH),
        mon_history=_path_str(MON_HISTORY_PATH),
        run_profile=_path_str(RUN_PROFILE_PATH),
        last_error=_path_str(LAST_ERROR_PATH),
    )
    return Snapshot(routing=routing, local=local, api=api, registry=reg, last_error=le, paths=paths, timestamp=ts)
