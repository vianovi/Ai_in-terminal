"""Check backend status, connectivity, and configs (ANSI-only, intelligent).

Subcommand:
  ai status [--info] [--last-error] [--deep]

Design goals:
- Default mode: cepat, tidak bikin "false error".
- Ollama timeout/lambat => SLOW ⏳ (bukan NOT READY ❌).
- Deep mode: uji inference LOCAL lebih dalam + tampilkan jawaban + latency.
- API self-test: opsional, user yang memutuskan (hemat kuota).
- Smoke check registry: selalu tampil di ai status (bukan command terpisah).
"""

from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from shutil import which
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# === IMPORT LAMA TETAP AMAN KARENA PAKAI ABSOLUTE PATH ===
from ai_logic.common import (
    CONFIG_PATH,
    MEMORY_PATH,
    MON_HISTORY_PATH,
    RUN_PROFILE_PATH,
    LAST_ERROR_PATH,
    LOGIC_PATH,
    FISH_DIR,
    FISH_FUNCS_DIR,
    api_active_model_raw,
    api_provider,
    backend_mode,
    read_last_error,
)
from ai_logic.ui.ansi import (
    prompt_text,
    tag,
    wrap,
    term_size,
    print_system,
    print_info,
    c_cyan,
    c_dim,
    c_reset,
    c_green,
    c_yellow,
    c_red,
)

from ai_logic.backends.local_ollama import ollama_host, local_model_for
from ai_logic.backends.api_gemini import (
    gemini_key,
    validate_api_config,
    generate as gemini_generate,
)


# ---------------------------------------------------------
# Entry point
# ---------------------------------------------------------


def handle(argv: list[str], cfg: dict) -> int:
    """
    Subcommand: ai status [flags]
    Flags:
      --last-error / last-error : Tampilkan error log terakhir.
      --info / info             : Tampilkan info mode singkat (akurat).
      --deep / deep             : Deep check LOCAL (inference + latency + output).
      (default)                 : Full status cepat + self-test opsional API.
    """
    args = [a.strip() for a in (argv or []) if a.strip()]
    if any(x in args for x in ("--last-error", "last-error")):
        return run_last_error(cfg)

    if any(x in args for x in ("--info", "info")):
        return run_info(cfg)

    deep = any(x in args for x in ("--deep", "deep"))
    if deep:
        return run_deep(cfg)

    return run_status(cfg)


# ---------------------------------------------------------
# Helpers: paths, formatting, deps
# ---------------------------------------------------------


def _find_repo_root(start: Path) -> Path | None:
    p = start.resolve()
    for parent in [p] + list(p.parents):
        if (parent / ".git").exists():
            return parent
    return None


def _fmt_path(p: Path) -> str:
    try:
        if p.is_symlink():
            return f"{p} -> {p.resolve()}"
        return str(p)
    except Exception:
        return str(p)


def _h(title: str) -> None:
    print(f"\n{tag(title, c_cyan())}")


def _kv(cols: int, k: str, v: str) -> None:
    key_w = 18
    line = f"- {k:<{key_w}}: {v}"
    print(wrap(line, width=min(cols, 120)))


def _dep_check_import(mod_name: str) -> bool:
    try:
        __import__(mod_name)
        return True
    except Exception:
        return False


def _dnf_hint_for(mod_name: str) -> str:
    mapping = {
        "psutil": "sudo dnf install -y python3-psutil",
        "json5": "sudo dnf install -y python3-json5",
        "prompt_toolkit": "sudo dnf install -y python3-prompt-toolkit",
    }
    return mapping.get(mod_name, "")


def _safe_read_text(p: Path, limit: int = 200_000) -> str:
    try:
        if not p.exists():
            return ""
        s = p.read_text(encoding="utf-8", errors="replace")
        return s[:limit]
    except Exception:
        return ""


def _try_parse_json(text: str) -> tuple[bool, str]:
    if not text.strip():
        return False, "kosong"
    try:
        json.loads(text)
        return True, "OK"
    except Exception as ex:
        return False, f"JSON error: {type(ex).__name__}"


def _try_parse_json5(text: str) -> tuple[bool, str]:
    if not text.strip():
        return False, "kosong"
    try:
        import json5  # type: ignore
    except Exception:
        return _try_parse_json(text)
    try:
        json5.loads(text)
        return True, "OK (json5)"
    except Exception as ex:
        return False, f"JSON5 error: {type(ex).__name__}"


# ---------------------------------------------------------
# HTTP JSON + Ollama probes
# ---------------------------------------------------------


def _looks_conn_refused(msg: str) -> bool:
    s = (msg or "").lower()
    return ("connection refused" in s) or ("errno 111" in s) or ("refused" in s)


def _is_timeout_msg(msg: str) -> bool:
    s = (msg or "").lower()
    return ("timed out" in s) or ("timeout" in s)


def _http_json(method: str, url: str, payload: dict | None, timeout_s: float) -> tuple[bool, dict, str]:
    data = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")

    req = Request(url=url, data=data, method=method, headers=headers)
    try:
        with urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            if not raw.strip():
                return True, {}, ""
            try:
                return True, json.loads(raw), ""
            except Exception:
                return False, {}, "Invalid JSON from server."
    except HTTPError as ex:
        try:
            body = ex.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        return False, {}, f"HTTP {ex.code}: {body[:350]}"
    except URLError as ex:
        return False, {}, f"URLError: {getattr(ex, 'reason', ex)}"
    except TimeoutError:
        return False, {}, "Timeout"
    except Exception as ex:
        msg = f"{type(ex).__name__}: {ex}"
        # urlopen timeout sering muncul sebagai socket.timeout/OSError dengan pesan "timed out"
        if _is_timeout_msg(msg):
            return False, {}, "Timeout"
        return False, {}, msg


@dataclass
class LocalProbe:
    state: str  # READY | SLOW | DOWN | ERROR
    host: str
    latency_ms: int
    models: list[str]
    selected_ok: bool
    detail: str


def _probe_ollama_fast(cfg: dict, timeout_s: float = 1.2) -> LocalProbe:
    host = (ollama_host(cfg) or "").strip().rstrip("/")
    if not host:
        return LocalProbe(
            state="ERROR",
            host="(unset)",
            latency_ms=0,
            models=[],
            selected_ok=False,
            detail="Host Ollama kosong (cek config: ollama.host).",
        )

    t0 = time.perf_counter()
    ok, data, err = _http_json("GET", f"{host}/api/tags", None, timeout_s=timeout_s)
    dt = int((time.perf_counter() - t0) * 1000)

    if not ok:
        if err.strip().lower() == "timeout":
            return LocalProbe(
                state="SLOW",
                host=host,
                latency_ms=dt,
                models=[],
                selected_ok=False,
                detail=f"Respons lambat / timeout saat cek tags (>{timeout_s:.1f}s). Ini sering terjadi saat cold-start atau mesin lagi berat.",
            )
        if _looks_conn_refused(err):
            return LocalProbe(
                state="DOWN",
                host=host,
                latency_ms=dt,
                models=[],
                selected_ok=False,
                detail="Connection refused (service belum jalan / host/port salah).",
            )
        return LocalProbe(
            state="ERROR",
            host=host,
            latency_ms=dt,
            models=[],
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

    sel = {x.strip() for x in (local_model_for(cfg, "ask"), local_model_for(cfg, "cmd")) if x.strip()}
    available = set(models)
    selected_ok = (not sel) or sel.issubset(available)

    if not models:
        return LocalProbe(
            state="READY",
            host=host,
            latency_ms=dt,
            models=[],
            selected_ok=selected_ok,
            detail="Server terjangkau, tapi belum ada model terdeteksi (ollama list kosong).",
        )

    return LocalProbe(
        state="READY",
        host=host,
        latency_ms=dt,
        models=models,
        selected_ok=selected_ok,
        detail=f"Server OK, model terdeteksi: {len(models)}",
    )


@dataclass
class LocalDeep:
    reachable: bool
    inference_ok: bool
    used_model: str
    latency_ms: int
    answer: str
    detail: str


def _deep_inference_test(cfg: dict, host: str, model: str, timeout_s: float = 12.0) -> LocalDeep:
    """
    Tes inference /api/chat minimal:
    - instruksi super singkat
    - ukur latency
    - tampilkan jawaban
    """
    host = (host or "").strip().rstrip("/")
    if not host:
        return LocalDeep(False, False, model, 0, "", "Host kosong.")

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Balas 1 kata saja: OK"}],
        "stream": False,
        "options": {"num_predict": 8, "num_ctx": 256},
    }

    t0 = time.perf_counter()
    ok, data, err = _http_json("POST", f"{host}/api/chat", payload, timeout_s=timeout_s)
    dt = int((time.perf_counter() - t0) * 1000)

    if not ok:
        if err.strip().lower() == "timeout":
            return LocalDeep(
                reachable=True,
                inference_ok=False,
                used_model=model,
                latency_ms=dt,
                answer="",
                detail=f"SLOW ⏳ Inference timeout (>{timeout_s:.1f}s). Model kemungkinan cold-start / terlalu berat / CPU penuh.",
            )
        if _looks_conn_refused(err):
            return LocalDeep(False, False, model, dt, "", "NOT READY ❌ Connection refused.")
        return LocalDeep(False, False, model, dt, "", f"ERROR ❌ {err}")

    try:
        msg = data.get("message") or {}
        content = (msg.get("content") or "").strip()
    except Exception:
        content = ""

    if content:
        return LocalDeep(True, True, model, dt, content, "READY ✅ Inference merespons.")
    return LocalDeep(True, False, model, dt, "", "Reachable, tapi output kosong/format tidak dikenali.")


def _status_badge(state: str) -> str:
    st = (state or "").upper()
    if st == "READY":
        return f"{c_green()}READY ✅{c_reset()}"
    if st == "SLOW":
        return f"{c_yellow()}SLOW ⏳{c_reset()}"
    if st == "DOWN":
        return f"{c_red()}NOT READY ❌{c_reset()}"
    if st == "ERROR":
        return f"{c_red()}ERROR ❌{c_reset()}"
    return f"{c_dim()}{st}{c_reset()}"


# ---------------------------------------------------------
# Smoke check (registry/router) - always included
# ---------------------------------------------------------


def _smoke_registry() -> tuple[bool, str]:
    """
    Aman:
    - import registry
    - pastikan COMMANDS ada dan setiap module punya handle()
    Tanpa menjalankan command.
    """
    try:
        from . import registry  # type: ignore
    except Exception as ex:
        return False, f"Gagal import registry: {type(ex).__name__}"

    try:
        cmds = registry.get_all_commands()
        if not cmds:
            return False, "COMMANDS kosong."
        missing: list[str] = []
        for name in cmds:
            mod = registry.get_module(name)
            if not mod or not hasattr(mod, "handle"):
                missing.append(name)
        if missing:
            return False, f"Module tanpa handle(): {', '.join(missing)}"
        return True, f"OK. Commands: {', '.join(cmds)}"
    except Exception as ex:
        return False, f"Smoke error: {type(ex).__name__}: {ex}"


# ---------------------------------------------------------
# Public actions
# ---------------------------------------------------------


def run_info(cfg: dict) -> int:
    """
    Info singkat yang AKURAT:
    - Jika mode LOCAL: tampilkan model local ask/cmd (bukan model API).
    - Jika mode API/AUTO: tampilkan provider + model API + local fallback model.
    """
    cols, _ = term_size()
    mode = backend_mode(cfg).strip().lower()
    prov = api_provider(cfg).strip().upper()
    api_model = api_active_model_raw(cfg) or "(unset)"
    l_ask = local_model_for(cfg, "ask") or "(unset)"
    l_cmd = local_model_for(cfg, "cmd") or "(unset)"

    if mode == "local":
        s = f"{tag('INFO', c_cyan())} Mode: LOCAL | Local ask: {l_ask} | Local cmd: {l_cmd}"
    elif mode == "api":
        s = f"{tag('INFO', c_cyan())} Mode: API | API: {prov} | Model: {api_model} | Local fallback: ask={l_ask}, cmd={l_cmd}"
    else:  # auto
        s = f"{tag('INFO', c_cyan())} Mode: AUTO | API: {prov}/{api_model} | Local fallback: ask={l_ask}, cmd={l_cmd}"

    print(wrap(s, width=min(cols, 120)))
    return 0


def run_last_error(cfg: dict) -> int:
    cols, _ = term_size()
    last = read_last_error()
    if not last:
        print(wrap("Tidak ada error terakhir tercatat.", width=min(cols, 120)))
        return 0
    print(wrap(f"{tag('LAST ERROR', c_cyan())} {last.get('time','')}", width=min(cols, 120)))
    print(wrap(f"stage  : {last.get('stage','')}", width=min(cols, 120)))
    print(wrap(f"backend: {last.get('backend','')}", width=min(cols, 120)))
    print(wrap(str(last.get("detail", ""))[:6000], width=min(cols, 120)))
    return 0


def run_status(cfg: dict) -> int:
    cols, _ = term_size()

    # Header paling atas
    print_system("STATUS CENTER (INTELLIGENT MODE)")

    # 0) Workspace
    repo_root = _find_repo_root(Path(__file__))
    workspace = repo_root

    _h("1) Lokasi kerja & file penting")
    _kv(cols, "Workspace", str(workspace) if workspace else "(tidak terdeteksi otomatis)")
    _kv(cols, "Logic", _fmt_path(LOGIC_PATH))
    _kv(cols, "Config", _fmt_path(CONFIG_PATH))
    _kv(cols, "Memory", _fmt_path(MEMORY_PATH))
    _kv(cols, "Mon history", _fmt_path(MON_HISTORY_PATH))
    _kv(cols, "Run profile", _fmt_path(RUN_PROFILE_PATH))
    _kv(cols, "Last error", _fmt_path(LAST_ERROR_PATH))
    _kv(cols, "Fish config", _fmt_path(FISH_DIR))
    _kv(cols, "Fish funcs", _fmt_path(FISH_FUNCS_DIR))

    _h("1.1) Command untuk buka workspace (manual)")
    _kv(cols, "VS Code", f'code "{workspace}" --verbose' if workspace else 'code "/path/ke/workspace-kamu" --verbose')

    _h("2) Setting aktif (config.json)")
    mode = backend_mode(cfg)
    prov = api_provider(cfg)
    _kv(cols, "backend_mode", mode)
    _kv(cols, "api.provider", prov)
    _kv(cols, "api.model", api_active_model_raw(cfg) or "(kosong)")
    _kv(cols, "local.ask", local_model_for(cfg, "ask") or "(kosong)")
    _kv(cols, "local.cmd", local_model_for(cfg, "cmd") or "(kosong)")

    _h("3) Dependency (DNF)")
    has_psutil = _dep_check_import("psutil")
    _kv(
        cols,
        "psutil",
        f"{c_green()}TERPASANG ✅{c_reset()}" if has_psutil else f"{c_red()}BELUM ❌{c_reset()}  ({_dnf_hint_for('psutil')})",
    )
    has_json5 = _dep_check_import("json5")
    _kv(
        cols,
        "json5",
        f"{c_green()}TERPASANG ✅{c_reset()}" if has_json5 else f"{c_yellow()}BELUM (opsional) ⏳{c_reset()}  ({_dnf_hint_for('json5')})",
    )
    has_ptk = _dep_check_import("prompt_toolkit")
    _kv(
        cols,
        "prompt_toolkit",
        f"{c_green()}TERPASANG ✅{c_reset()}" if has_ptk else f"{c_yellow()}BELUM (opsional){c_reset()}  ({_dnf_hint_for('prompt_toolkit')})",
    )

    wl = which("wl-copy")
    xclip = which("xclip")
    xsel = which("xsel")
    if wl:
        _kv(cols, "clipboard", "wl-copy ✅ (Wayland)")
    elif xclip:
        _kv(cols, "clipboard", "xclip ✅ (X11)")
    elif xsel:
        _kv(cols, "clipboard", "xsel ✅ (X11)")
    else:
        _kv(cols, "clipboard", "tidak ada (opsional)")
    _kv(cols, "code (CLI)", "ADA ✅" if which("code") else "TIDAK ❌")

    _h("4) Kesiapan server AI LOCAL (Ollama)")
    local = _probe_ollama_fast(cfg, timeout_s=1.2)
    _kv(cols, "host", local.host)
    _kv(cols, "status", _status_badge(local.state))
    _kv(cols, "latency", f"{local.latency_ms}ms" if local.latency_ms else "-")
    _kv(cols, "detail", local.detail)

    if local.models:
        sample = ", ".join(local.models[:6]) + (", ..." if len(local.models) > 6 else "")
        _kv(cols, "models", sample)
    if local.models and not local.selected_ok:
        _kv(cols, "selected_ok", f"{c_red()}NO ❌{c_reset()} (model di config tidak cocok dengan list)")

    _h("5) Self-test ringan (opsional API)")
    # 5.1 Info cepat LOCAL (tanpa inference) + hint deep
    if local.state == "READY":
        print(wrap(f"{tag('LOCAL', c_green())} Terjangkau ✅ (cek cepat). Kalau terasa lambat saat ask/cmd, coba --deep untuk uji inference.", width=min(cols, 120)))
    elif local.state == "SLOW":
        print(wrap(f"{tag('LOCAL', c_yellow())} Respons lambat ⏳ (bukan error). Untuk diagnosa detail, jalankan: ai status --deep", width=min(cols, 120)))
    else:
        print(wrap(f"{tag('LOCAL', c_red())} Belum siap ❌ (service/host/port).", width=min(cols, 120)))

    # 5.2 API self-test benar-benar opsional (hemat kuota)
    ok_cfg, note_cfg, detail_cfg = validate_api_config(cfg)
    prov_now = api_provider(cfg).strip().lower()

    if ok_cfg and prov_now == "gemini":
        key, env_name = gemini_key(cfg)
        _kv(cols, "api env", f"{env_name} ({'YA ✅' if key else 'TIDAK ❌'})")

        want_api_test = False
        # Aman untuk non-interactive (CI/script): skip otomatis
        if sys.stdin.isatty():
            print("\nMau jalankan tes cepat API sekarang? (hemat kuota)")
            print("  - ketik y  : jalankan tes")
            print("  - selain itu: skip")
            try:
                ans = prompt_text("> ").strip().lower()
                want_api_test = (ans == "y")
            except KeyboardInterrupt:
                print("")
                want_api_test = False
        else:
            want_api_test = False

        if want_api_test:
            try:
                msgs = [
                    {"role": "system", "content": "Jawab 1 huruf saja: Y"},
                    {"role": "user", "content": "ping"},
                ]
                t0 = time.perf_counter()
                _ = gemini_generate(cfg, msgs, timeout=10, max_output_tokens=8)
                dt = int((time.perf_counter() - t0) * 1000)
                _kv(cols, "api test", f"{c_green()}PASS ✅{c_reset()} ({dt}ms)")
                _kv(cols, "api detail", "API menjawab (uji cepat).")
            except Exception as ex:
                s = str(ex)
                m = re.search(r"HTTP\s+(\d+)", s)
                if m:
                    code = m.group(1)
                    meaning = {
                        "401": "Unauthorized (key salah/ditolak).",
                        "403": "Permission denied (akses dibatasi).",
                        "404": "Model/endpoint tidak cocok.",
                        "429": "Rate limit / kuota habis.",
                        "500": "Server error (coba lagi).",
                        "503": "Service unavailable (coba lagi).",
                    }.get(code, "HTTP error.")
                    _kv(cols, "api test", f"{c_red()}FAIL ❌{c_reset()} (HTTP {code})")
                    _kv(cols, "api detail", meaning)
                else:
                    _kv(cols, "api test", f"{c_red()}FAIL ❌{c_reset()}")
                    _kv(cols, "api detail", f"{type(ex).__name__}")
        else:
            _kv(cols, "api test", f"{c_dim()}SKIP ⏭{c_reset()}")
            _kv(cols, "api detail", "Dilewati (user memilih skip).")
    else:
        _kv(cols, "api status", f"{c_yellow()}NOT READY ⏳{c_reset()}")
        _kv(cols, "api detail", f"{note_cfg} • {detail_cfg}")

    print_info('Jalankan: ai status --deep  (untuk uji LOCAL inference lebih mendalam)')

    _h("6) Registry/router sanity (smoke check)")
    ok_smoke, detail_smoke = _smoke_registry()
    _kv(cols, "registry", f"{c_green()}OK ✅{c_reset()}" if ok_smoke else f"{c_red()}FAIL ❌{c_reset()}")
    _kv(cols, "detail", detail_smoke)

    _h("7) Kesimpulan (diagnosis + next action)")
    mode_l = (backend_mode(cfg) or "").strip().lower()
    prov_u = (api_provider(cfg) or "").strip().upper()
    api_model = api_active_model_raw(cfg) or "(unset)"

    diagnosis: list[str] = []
    next_actions: list[str] = []

    # Diagnose LOCAL
    if local.state == "READY":
        diagnosis.append("LOCAL siap ✅ (cek cepat /api/tags).")
        if local.models and not local.selected_ok:
            diagnosis.append("Ada mismatch model config vs model tersedia ❗")
            next_actions.append("Perbaiki `active_model_ask/active_model_cmd` agar cocok dengan `ollama list`.")
        else:
            next_actions.append("Kalau respons chat masih terasa berat, jalankan `ai status --deep` untuk ukur latency inference.")
    elif local.state == "SLOW":
        diagnosis.append("LOCAL terjangkau tapi lambat ⏳ (ini biasanya cold-start / beban CPU/RAM tinggi).")
        next_actions.append("Coba warm-up: `ollama run <model> \"ping\"` atau tunggu model selesai loading.")
        next_actions.append("Jika sering, pilih model lebih ringan untuk ask/cmd.")
    elif local.state == "DOWN":
        diagnosis.append("LOCAL tidak bisa diakses ❌ (connection refused).")
        next_actions.append("Pastikan service Ollama jalan dan host/port benar (default 11434).")
    else:
        diagnosis.append("LOCAL error ❌ (lihat detail di bagian 4).")
        next_actions.append("Cek host Ollama di config dan pastikan endpoint bisa diakses.")

    # Diagnose API (shallow only)
    ok_api_cfg, note_api, det_api = validate_api_config(cfg)
    if ok_api_cfg:
        diagnosis.append(f"API config terlihat siap ✅ ({prov_u}/{api_model}) (shallow).")
        if mode_l == "api":
            next_actions.append("Jika API sering rate-limit, pertimbangkan mode AUTO agar fallback LOCAL halus.")
    else:
        diagnosis.append(f"API belum siap ⏳ ({note_api}).")
        if mode_l in ("api", "auto"):
            next_actions.append("Perbaiki env var API key/provider/model bila ingin API aktif stabil.")

    # Routing behavior explanation
    if mode_l == "local":
        diagnosis.append("Routing: Mode LOCAL → selalu pakai LOCAL.")
    elif mode_l == "api":
        diagnosis.append("Routing: Mode API → pakai API dulu, fallback LOCAL bila API error.")
    else:
        diagnosis.append("Routing: Mode AUTO → pakai API jika siap, fallback LOCAL jika API tidak siap/error.")

    # Print nicely
    print(wrap("🧠 Diagnosis: " + " ".join(diagnosis), width=min(cols, 120)))
    if next_actions:
        print(wrap("🎯 Next action: " + " | ".join(next_actions[:4]), width=min(cols, 120)))

    _h("8) Last error (ringkas)")
    last = read_last_error()
    if not last:
        print(wrap("Tidak ada error terakhir tercatat.", width=min(cols, 120)))
    else:
        _kv(cols, "time", str(last.get("time") or ""))
        _kv(cols, "stage", str(last.get("stage") or ""))
        _kv(cols, "backend", str(last.get("backend") or ""))
        d = str(last.get("detail") or "")
        d1 = d.splitlines()[0] if d else ""
        _kv(cols, "ringkas", d1[:180] + ("..." if len(d1) > 180 else ""))

    _h("9) Lokasi detail (manual)")
    _kv(cols, "Detail error", str(LAST_ERROR_PATH))
    if workspace:
        _kv(cols, "Buka workspace", f'code "{workspace}" --verbose')
    _kv(cols, "Buka error file", f'code "{LAST_ERROR_PATH}"')
    _kv(cols, "Lihat cepat", f'cat "{LAST_ERROR_PATH}"')

    # Exit code policy: fail hard hanya kalau mode=local tapi LOCAL benar-benar DOWN/ERROR.
    if mode_l == "local" and local.state in ("DOWN", "ERROR"):
        return 2
    return 0


def run_deep(cfg: dict) -> int:
    cols, _ = term_size()

    # Header khusus deep
    print_system("STATUS CENTER (DEEP LOCAL DIAGNOSTIC MODE)")

    local = _probe_ollama_fast(cfg, timeout_s=1.2)

    _h("1) Target LOCAL & konteks")
    _kv(cols, "host", local.host)
    _kv(cols, "fast check", _status_badge(local.state))
    _kv(cols, "fast latency", f"{local.latency_ms}ms" if local.latency_ms else "-")

    if local.state in ("DOWN", "ERROR"):
        _h("2) Deep inference test")
        print(wrap("Deep test dilewati karena LOCAL belum bisa diakses. Perbaiki service/host dulu.", width=min(cols, 120)))
        return 2

    # Pilih model: config ask > list model pertama > fallback
    model_pick = local_model_for(cfg, "ask") or (local.models[0] if local.models else "llama3.1:8b")

    _h("2) Deep inference test (ukur latency + tampilkan jawaban)")
    instr = "Balas 1 kata saja: OK"
    _kv(cols, "instruction", instr)
    _kv(cols, "model", model_pick)

    deep = _deep_inference_test(cfg, local.host, model_pick, timeout_s=12.0)

    # Output deep result
    if deep.inference_ok:
        _kv(cols, "result", f"{c_green()}READY ✅{c_reset()}")
    else:
        # reachable tapi lambat = SLOW; unreachable = NOT READY/ERROR di detail
        if deep.reachable:
            _kv(cols, "result", f"{c_yellow()}SLOW ⏳{c_reset()}")
        else:
            _kv(cols, "result", f"{c_red()}FAIL ❌{c_reset()}")

    _kv(cols, "latency", f"{deep.latency_ms}ms")
    _kv(cols, "detail", deep.detail)

    # Jawaban server ditampilkan (ringkas, satu baris)
    ans = (deep.answer or "").strip()
    if ans:
        ans1 = re.sub(r"\s+", " ", ans).strip()
        if len(ans1) > 160:
            ans1 = ans1[:160] + "..."
        _kv(cols, "server answer", ans1)
    else:
        _kv(cols, "server answer", "(tidak ada output)")

    _h("3) File sanity (parse check)")
    ok_cfg, note_cfg = _try_parse_json(_safe_read_text(CONFIG_PATH))
    _kv(cols, "config.json", f"{c_green()}OK ✅{c_reset()}" if ok_cfg else f"{c_red()}{note_cfg}{c_reset()}")

    rp_text = _safe_read_text(RUN_PROFILE_PATH)
    ok_rp, note_rp = _try_parse_json5(rp_text) if rp_text else (False, "tidak ada")
    _kv(cols, "run_profile", f"{c_green()}OK ✅{c_reset()}" if ok_rp else f"{c_yellow()}{note_rp}{c_reset()}")

    mh_text = _safe_read_text(MON_HISTORY_PATH)
    ok_mh, note_mh = _try_parse_json(mh_text) if mh_text else (False, "tidak ada")
    _kv(cols, "mon_history", f"{c_green()}OK ✅{c_reset()}" if ok_mh else f"{c_yellow()}{note_mh}{c_reset()}")

    _h("4) Catatan")
    print(wrap("Kalau hasilnya SLOW ⏳ tapi bukan FAIL, biasanya itu cold-start. Setelah beberapa kali pemanggilan, harusnya makin cepat.", width=min(cols, 120)))

    return 0
