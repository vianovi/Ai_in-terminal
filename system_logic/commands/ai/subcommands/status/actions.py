from __future__ import annotations

import json
import time
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

from system_logic.backends.api_gemini import generate as gemini_generate
from system_logic.backends.local_ollama import local_model_for

from .models import Snapshot


def _post_json(url: str, payload: dict, timeout: float = 25.0) -> tuple[bool, dict, str, int]:
    t0 = time.time()
    try:
        data = json.dumps(payload).encode("utf-8")
        req = Request(
            url=url,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            ms = int((time.time() - t0) * 1000)
            return True, (json.loads(raw) if raw.strip() else {}), "", ms
    except HTTPError as ex:
        ms = int((time.time() - t0) * 1000)
        return False, {}, f"HTTP {ex.code}", ms
    except URLError as ex:
        ms = int((time.time() - t0) * 1000)
        return False, {}, f"URLError: {ex.reason}", ms
    except Exception as ex:
        ms = int((time.time() - t0) * 1000)
        return False, {}, f"{type(ex).__name__}: {ex}", ms


def deep_test_local(cfg: dict, snap: Snapshot) -> tuple[bool, str]:
    host = snap.local.host.rstrip("/")
    model = local_model_for(cfg, "ask") or snap.local.model_ask or "llama3.2:latest"
    payload = {
        "model": model,
        "stream": False,
        "messages": [{"role": "user", "content": "Explain 1+1 in one short sentence."}],
    }
    ok, data, err, ms = _post_json(f"{host}/api/chat", payload, timeout=25.0)
    if not ok:
        return False, f"LOCAL deep test FAIL ({ms}ms): {err}"

    msg = ""
    try:
        msg = str((data.get("message") or {}).get("content") or "").strip()
    except Exception:
        msg = ""
    if not msg:
        msg = str(data)[:180]

    return True, f"LOCAL deep test OK ({ms}ms): {msg}"


def self_test_api(cfg: dict, snap: Snapshot) -> tuple[bool, str]:
    if snap.api.provider != "gemini":
        return False, f"API provider '{snap.api.provider}' belum didukung untuk self-test."
    t0 = time.time()
    try:
        out = gemini_generate(cfg, "ping", max_output_tokens=32)
        ms = int((time.time() - t0) * 1000)
        short = str(out).strip().replace("\n", " ")
        if len(short) > 140:
            short = short[:140] + "…"
        return True, f"API self-test OK ({ms}ms): {short}"
    except Exception as ex:
        ms = int((time.time() - t0) * 1000)
        return False, f"API self-test FAIL ({ms}ms): {type(ex).__name__}: {ex}"
