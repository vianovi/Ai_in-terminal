from __future__ import annotations

import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from system_logic.backends.local_ollama import local_model_for
from system_logic.backends.api_gemini import generate as gemini_generate

from .models import Snapshot


def _post_json(url: str, payload: dict, timeout_s: float) -> tuple[bool, dict, str, int]:
    t0 = time.perf_counter()
    try:
        data = json.dumps(payload).encode("utf-8")
        req = Request(
            url=url,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        with urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            dt = int((time.perf_counter() - t0) * 1000)
            return True, (json.loads(raw) if raw.strip() else {}), "", dt
    except HTTPError as ex:
        dt = int((time.perf_counter() - t0) * 1000)
        return False, {}, f"HTTP {ex.code}", dt
    except URLError as ex:
        dt = int((time.perf_counter() - t0) * 1000)
        return False, {}, f"URLError: {getattr(ex, 'reason', ex)}", dt
    except Exception as ex:
        dt = int((time.perf_counter() - t0) * 1000)
        return False, {}, f"{type(ex).__name__}: {ex}", dt


def deep_local(cfg: dict, snap: Snapshot) -> tuple[bool, str]:
    host = snap.local.host.rstrip("/")
    model = local_model_for(cfg, "ask") or snap.routing.local_ask or "llama3.2:latest"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Balas 1 kata saja: OK"}],
        "stream": False,
        "options": {"num_predict": 8, "num_ctx": 256},
    }
    ok, data, err, ms = _post_json(f"{host}/api/chat", payload, timeout_s=12.0)
    if not ok:
        return False, f"LOCAL deep test gagal ({ms}ms): {err}"

    content = ""
    try:
        msg = data.get("message") or {}
        content = str(msg.get("content") or "").strip()
    except Exception:
        content = ""

    if not content:
        content = str(data)[:160]
    return True, f"LOCAL deep test OK ({ms}ms): {content.replace('\n',' ')[:180]}"


def deep_api(cfg: dict, snap: Snapshot) -> tuple[bool, str]:
    # Saat ini implementnya untuk Gemini saja (karena backend kamu yang jelas ada validator+generate).
    if (snap.api.provider or "").lower() != "gemini":
        return False, f"Deep API test belum didukung untuk provider '{snap.api.provider}'."

    t0 = time.perf_counter()
    try:
        out = gemini_generate(cfg, "ping", max_output_tokens=16)
        ms = int((time.perf_counter() - t0) * 1000)
        txt = str(out).strip().replace("\n", " ")
        if len(txt) > 180:
            txt = txt[:180] + "…"
        return True, f"API deep test OK ({ms}ms): {txt}"
    except Exception as ex:
        ms = int((time.perf_counter() - t0) * 1000)
        return False, f"API deep test gagal ({ms}ms): {type(ex).__name__}: {ex}"
