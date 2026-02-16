from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from shutil import which
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

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
    DepBrief,
    LastErrorBrief,
    LocalProbe,
    PathsBrief,
    RegistryProbe,
    RoutingBrief,
    Snapshot,
)


def _find_repo_root(start: Path) -> str:
    p = start.resolve()
    for parent in [p] + list(p.parents):
        if (parent / ".git").exists():
            return str(parent)
    return ""


def _dep_check_import(mod: str) -> bool:
    try:
        __import__(mod)
        return True
    except Exception:
        return False


def _clipboard_bin() -> str:
    if which("wl-copy"):
        return "wl-copy"
    if which("xclip"):
        return "xclip"
    if which("xsel"):
        return "xsel"
    return ""


def _looks_conn_refused(msg: str) -> bool:
    s = (msg or "").lower()
    return ("connection refused" in s) or ("errno 111" in s) or ("refused" in s)


def _is_timeout(msg: str) -> bool:
    s = (msg or "").lower()
    return ("timed out" in s) or ("timeout" in s)


def _http_json_get(url: str, timeout_s: float) -> tuple[bool, dict, str, int]:
    t0 = time.perf_counter()
    try:
        req = Request(url=url, headers={"Accept": "application/json"}, method="GET")
        with urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            dt = int((time.perf_counter() - t0) * 1000)
            if not raw.strip():
                return True, {}, "", dt
            return True, json.loads(raw), "", dt
    except HTTPError as ex:
        dt = int((time.perf_counter() - t0) * 1000)
        return False, {}, f"HTTP {ex.code}", dt
    except URLError as ex:
        dt = int((time.perf_counter() - t0) * 1000)
        return False, {}, f"URLError: {getattr(ex, 'reason', ex)}", dt
    except Exception as ex:
        dt = int((time.perf_counter() - t0) * 1000)
        msg = f"{type(ex).__name__}: {ex}"
        if _is_timeout(msg):
            msg = "Timeout"
        return False, {}, msg, dt


def probe_local(cfg: dict) -> LocalProbe:
    host = (ollama_host(cfg) or "").strip().rstrip("/")
    if not host:
        return LocalProbe(
            state="ERROR",
            host="(unset)",
            latency_ms=0,
            models_count=0,
            selected_ok=False,
            detail="Host Ollama kosong (cek config: ollama.host).",
        )

    ok, data, err, dt = _http_json_get(f"{host}/api/tags", timeout_s=1.2)
    if not ok:
        if (err or "").strip().lower() == "timeout":
            return LocalProbe(
                state="SLOW",
                host=host,
                latency_ms=dt,
                models_count=0,
                selected_ok=False,
                detail="Respons lambat / timeout saat cek tags. Umum terjadi saat cold-start atau mesin lagi berat.",
            )
        if _looks_conn_refused(err):
            return LocalProbe(
                state="DOWN",
                host=host,
                latency_ms=dt,
                models_count=0,
                selected_ok=False,
                detail="Connection refused (service belum jalan / host/port salah).",
            )
        return LocalProbe(
            state="ERROR",
            host=host,
            latency_ms=dt,
            models_count=0,
            selected_ok=False,
            detail=err or "Gagal cek /api/tags",
        )

    models: list[str] = []
    try:
        for m in (data.get("models") or []):
            name = (m.get("name") or "").strip()
            if name:
                models.append(name)
    except Exception:
        models = []

    sel = {x.strip() for x in (local_model_for(cfg, "ask"), local_model_for(cfg, "cmd")) if x and x.strip()}
    available = set(models)
    selected_ok = (not sel) or sel.issubset(available)

    detail = "Server OK."
    if not models:
        detail = "Server terjangkau, tapi belum ada model terdeteksi (ollama list kosong)."

    return LocalProbe(
        state="READY",
        host=host,
        latency_ms=dt,
        models_count=len(models),
        selected_ok=selected_ok,
        detail=detail,
    )


def probe_api(cfg: dict) -> ApiProbe:
    prov = (api_provider(cfg) or "").strip().lower() or "gemini"
    active = api_active_model_raw(cfg) or ""
    if prov == "gemini":
        key, _env = gemini_key(cfg)
        ok, note, detail = validate_api_config(cfg)
        return ApiProbe(
            provider=prov,
            active_model=active,
            key_present=bool(key),
            config_ok=bool(ok),
            note=str(note or ""),
            detail=str(detail or ""),
            connection_tested=False,
        )
    # provider lain: shallow only
    return ApiProbe(
        provider=prov,
        active_model=active,
        key_present=False,
        config_ok=True,
        note="shallow",
        detail="Provider belum punya validator khusus di status.",
        connection_tested=False,
    )


def probe_registry() -> RegistryProbe:
    try:
        from system_logic.commands.ai.subcommands import registry
        cmds = registry.get_all_commands()
        missing: list[str] = []
        for name in cmds:
            mod = registry.get_module(name)
            if not mod or not hasattr(mod, "handle"):
                missing.append(name)
        if missing:
            return RegistryProbe(False, "Module tanpa handle(): " + ", ".join(missing))
        return RegistryProbe(True, f"OK. Commands: {', '.join(cmds)}")
    except Exception as ex:
        return RegistryProbe(False, f"Gagal import/cek registry: {type(ex).__name__}")


def probe_last_error() -> LastErrorBrief:
    le = read_last_error()
    if not le:
        return LastErrorBrief(False)
    detail = str(le.get("detail") or "")
    first = (detail.splitlines()[0] if detail else "")[:180]
    return LastErrorBrief(
        True,
        time=str(le.get("time") or ""),
        stage=str(le.get("stage") or ""),
        backend=str(le.get("backend") or ""),
        summary=first,
    )


def build_conclusion(cfg: dict, local: LocalProbe, api: ApiProbe, reg: RegistryProbe) -> tuple[list[str], list[str]]:
    mode = (backend_mode(cfg) or "").strip().lower()
    prov = (api_provider(cfg) or "").strip().upper()
    api_model = api_active_model_raw(cfg) or "(unset)"

    diagnosis: list[str] = []
    next_actions: list[str] = []

    # LOCAL
    if local.state == "READY":
        diagnosis.append("LOCAL siap (cek cepat /api/tags).")
        if local.models_count > 0 and not local.selected_ok:
            diagnosis.append("Ada mismatch model config vs model tersedia.")
            next_actions.append("Perbaiki active_model_ask/cmd agar cocok dengan `ollama list`.")
        else:
            next_actions.append("Kalau respons chat terasa berat, jalankan deep test LOCAL (Action [1]).")
    elif local.state == "SLOW":
        diagnosis.append("LOCAL terjangkau tapi lambat (cold-start / beban CPU/RAM).")
        next_actions.append("Jalankan deep test LOCAL untuk ukur inference (Action [1]).")
        next_actions.append("Pertimbangkan model lebih ringan untuk ask/cmd.")
    elif local.state == "DOWN":
        diagnosis.append("LOCAL tidak bisa diakses (connection refused).")
        next_actions.append("Pastikan `ollama serve` jalan & host/port benar (default 11434).")
    else:
        diagnosis.append("LOCAL error (lihat detail di bagian LOCAL).")
        next_actions.append("Cek ollama.host di config dan konektivitas.")

    # API (shallow only)
    if api.config_ok and api.key_present:
        diagnosis.append(f"API config terlihat siap (shallow) ({prov}/{api_model}).")
        next_actions.append("Kalau perlu verifikasi koneksi, jalankan deep test API (Action [2]).")
    else:
        diagnosis.append("API belum siap (key/config).")
        if mode in ("api", "auto"):
            next_actions.append("Perbaiki env var API key/provider/model bila ingin API aktif stabil.")

    # ROUTING note
    if mode == "local":
        diagnosis.append("Routing: Mode LOCAL → selalu pakai LOCAL.")
    elif mode == "api":
        diagnosis.append("Routing: Mode API → pakai API dulu, fallback LOCAL bila error.")
    else:
        diagnosis.append("Routing: Mode AUTO → pakai API jika siap, fallback LOCAL jika tidak siap/error.")

    # Registry
    if not reg.ok:
        diagnosis.append("Ada masalah registry/router (smoke check).")
        next_actions.append("Perbaiki subcommand yang missing handle() / import error.")

    return diagnosis, next_actions[:4]


def collect_snapshot(cfg: dict) -> Snapshot:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    workspace = _find_repo_root(Path(__file__))

    routing = RoutingBrief(
        backend_mode=backend_mode(cfg),
        api_provider=api_provider(cfg),
        api_active_model=api_active_model_raw(cfg),
        local_ask=str(local_model_for(cfg, "ask") or ""),
        local_cmd=str(local_model_for(cfg, "cmd") or ""),
    )
    paths = PathsBrief(
        logic=str(LOGIC_PATH),
        config=str(CONFIG_PATH),
        memory=str(MEMORY_PATH),
        mon_history=str(MON_HISTORY_PATH),
        run_profile=str(RUN_PROFILE_PATH),
        last_error=str(LAST_ERROR_PATH),
    )
    deps = DepBrief(
        psutil=_dep_check_import("psutil"),
        json5=_dep_check_import("json5"),
        prompt_toolkit=_dep_check_import("prompt_toolkit"),
        clipboard=_clipboard_bin(),
    )
    local = probe_local(cfg)
    api = probe_api(cfg)
    reg = probe_registry()
    le = probe_last_error()
    diagnosis, next_actions = build_conclusion(cfg, local, api, reg)

    return Snapshot(
        timestamp=ts,
        workspace=workspace,
        routing=routing,
        paths=paths,
        deps=deps,
        local=local,
        api=api,
        registry=reg,
        conclusion=diagnosis,
        next_actions=next_actions,
        last_error=le,
    )
