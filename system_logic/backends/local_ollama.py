from __future__ import annotations

from system_logic.core.utils import http_json, looks_like_conn_refused


def ollama_host(cfg: dict) -> str:
    return str(cfg.get("ollama", {}).get("host") or "http://localhost:11434").strip()


def local_model_for(cfg: dict, kind: str) -> str:
    o = cfg.get("ollama", {})
    key = "active_model_ask" if kind == "ask" else "active_model_cmd"
    return str(o.get(key) or "").strip()


def list_models(host: str) -> list[str]:
    data = http_json("GET", f"{host}/api/tags", None, timeout=10)
    out: list[str] = []
    for m in data.get("models", []):
        name = m.get("name")
        if name:
            out.append(name)
    return out


def chat(host: str, model: str, messages: list[dict], timeout: int, num_predict: int) -> str:
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {
            "num_predict": int(num_predict),
            "num_ctx": 2048,
        },
    }
    data = http_json("POST", f"{host}/api/chat", payload, timeout=timeout)
    msg = data.get("message", {})
    return (msg.get("content") or "").strip()


def readiness(cfg: dict) -> dict:
    """
    Check readiness untuk status:
    - server reachable
    - model list non-empty
    - selected models exist
    - uji inferensi kecil
    """
    host = ollama_host(cfg)
    sel_ask = local_model_for(cfg, "ask")
    sel_cmd = local_model_for(cfg, "cmd")

    res = {
        "ok": False,
        "catatan": "",
        "detail": "",
        "langkah": [],
        "models": [],
        "selected_ok": False,
    }

    if not host:
        res["catatan"] = "Host Ollama kosong."
        res["detail"] = "Field config: ollama.host tidak terisi."
        res["langkah"] = ["Isi host Ollama di config.json (umumnya http://localhost:11434)."]
        return res

    try:
        models = list_models(host)
        res["models"] = models
    except Exception as ex:
        if looks_like_conn_refused(ex):
            res["catatan"] = "Ollama server mati / tidak bisa diakses."
            res["detail"] = str(ex)
            res["langkah"] = [
                "Pastikan service Ollama berjalan.",
                "Cek port 11434 tidak dipakai aplikasi lain.",
                "Ulangi ai status setelah Ollama hidup.",
            ]
        else:
            res["catatan"] = "Gagal mengakses Ollama."
            res["detail"] = str(ex)
            res["langkah"] = ["Cek host di config.json dan koneksi localhost/port."]
        return res

    if not res["models"]:
        res["catatan"] = "Ollama hidup, tapi belum ada model terdeteksi."
        res["detail"] = "Endpoint /api/tags mengembalikan daftar kosong."
        res["langkah"] = ["Pastikan minimal 1 model sudah terpasang (cek: `ollama list`)."]
        return res

    selected = {m.strip() for m in (sel_ask, sel_cmd) if m.strip()}
    available = set(res["models"])
    res["selected_ok"] = (not selected) or selected.issubset(available)
    if not res["selected_ok"]:
        res["catatan"] = "Model aktif di config tidak ditemukan."
        res["detail"] = f"Model aktif: {sorted(selected)} | Tersedia: {sorted(available)[:12]}{'...' if len(available) > 12 else ''}"
        res["langkah"] = [
            "Ubah active_model_ask/active_model_cmd agar cocok dengan `ollama list`.",
            "Atau pasang (pull) model yang kamu pilih.",
        ]
        return res

    try:
        test_model = sel_ask or res["models"][0]
        _ = chat(host, test_model, [{"role": "user", "content": "ping"}], timeout=12, num_predict=8)
    except Exception as ex:
        res["catatan"] = "Ollama terjangkau, tapi inference bermasalah."
        res["detail"] = str(ex)
        res["langkah"] = [
            "Coba ganti model ask/cmd ke yang lebih ringan.",
            "Cek CPU/RAM saat inferensi (mungkin lagi loading berat).",
            "Kalau perlu, naikkan timeout lokal.",
        ]
        return res

    res["ok"] = True
    res["catatan"] = "LOCAL siap."
    res["detail"] = "Model terdeteksi dan self-test inference berhasil."
    res["langkah"] = ["Kamu bisa pakai ask/cmd di mode local atau auto."]
    return res
