from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


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
