from __future__ import annotations

import os
import re

from ai_logic.common import http_json, normalize_model_id


def api_provider(cfg: dict) -> str:
    return (cfg.get("api", {}).get("provider") or "gemini").strip().lower()


def api_active_model_raw(cfg: dict) -> str:
    return str(cfg.get("api", {}).get("active_model") or "").strip()


def gemini_key(cfg: dict) -> tuple[str, str]:
    env_name = str(cfg.get("api", {}).get("gemini_api_key_env") or "GEMINI_API_KEY")
    return (os.environ.get(env_name) or "").strip(), env_name


def gemini_model_id(cfg: dict) -> str:
    return normalize_model_id(api_active_model_raw(cfg) or "gemini-2.5-flash")


def validate_api_config(cfg: dict) -> tuple[bool, str, str]:
    prov = api_provider(cfg)
    raw_model = api_active_model_raw(cfg)
    model = normalize_model_id(raw_model)

    if not prov:
        return False, "Konfigurasi API belum lengkap.", "Field api.provider kosong."
    if not model:
        return False, "Konfigurasi API belum lengkap.", "Field api.active_model kosong."

    # Cegah mismatch: provider=gemini tapi model gpt-*
    if prov == "gemini" and re.match(r"^(gpt-|o1|o3)", model):
        return False, "Provider Gemini aktif tapi model tidak cocok.", f"active_model terlihat seperti OpenAI: {raw_model}"

    if prov == "openai":
        return False, "Provider OpenAI belum didukung di versi ini.", "Gunakan provider=gemini atau pakai mode local/auto."

    if prov != "gemini":
        return False, "Provider API belum didukung.", f"provider='{prov}' belum ada handler-nya."

    key, env_name = gemini_key(cfg)
    if not key:
        return False, "API key Gemini belum terbaca.", f"Env '{env_name}' kosong di proses yang menjalankan ai-term."

    return True, "API siap.", "Validasi provider/model/key lulus."


def build_payload(messages: list[dict], max_output_tokens: int, json_schema: dict | None = None) -> dict:
    system_texts: list[str] = []
    contents: list[dict] = []

    for m in messages:
        role = (m.get("role") or "").lower()
        content = str(m.get("content") or "")
        if not content:
            continue
        if role == "system":
            system_texts.append(content)
        elif role == "user":
            contents.append({"role": "user", "parts": [{"text": content}]})
        else:
            contents.append({"role": "model", "parts": [{"text": content}]})

    payload: dict = {
        "contents": contents or [{"role": "user", "parts": [{"text": ""}]}],
        "generationConfig": {
            "maxOutputTokens": int(max_output_tokens),
            "temperature": 0.7,
            "topP": 0.95,
        },
    }

    if system_texts:
        payload["system_instruction"] = {"parts": [{"text": "\n\n".join(system_texts)}]}

    if json_schema is not None:
        payload["generationConfig"]["responseMimeType"] = "application/json"
        payload["generationConfig"]["responseSchema"] = json_schema

    return payload


def generate(cfg: dict, messages: list[dict], timeout: int, max_output_tokens: int, json_schema: dict | None = None) -> str:
    key, _env = gemini_key(cfg)
    model = gemini_model_id(cfg)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    payload = build_payload(messages, max_output_tokens=max_output_tokens, json_schema=json_schema)
    headers = {"x-goog-api-key": key}

    data = http_json("POST", url, payload, timeout=timeout, headers=headers)
    candidates = data.get("candidates") or []
    if not candidates:
        raise RuntimeError("Respons API kosong (tidak ada kandidat).")
    parts = ((candidates[0].get("content") or {}).get("parts") or [])
    text = "".join(str(p.get("text") or "") for p in parts).strip()
    if not text:
        raise RuntimeError("Respons API kosong.")
    return text


def list_models(cfg: dict, timeout: int = 12) -> list[dict]:
    key, _env = gemini_key(cfg)
    if not key:
        return []
    url = "https://generativelanguage.googleapis.com/v1beta/models"
    headers = {"x-goog-api-key": key}
    data = http_json("GET", url, None, timeout=timeout, headers=headers)
    models = data.get("models") or []
    return models if isinstance(models, list) else []


def model_support_summary(cfg: dict) -> tuple[bool, str]:
    raw = api_active_model_raw(cfg) or ""
    wanted_norm = "models/" + normalize_model_id(raw or "")
    if wanted_norm == "models/":
        return False, "api.active_model kosong."

    try:
        models = list_models(cfg, timeout=12)
    except Exception as ex:
        return False, f"Gagal ListModels: {ex}"

    if not models:
        return False, "ListModels kosong atau tidak bisa dibaca."

    hit = None
    for m in models:
        name = str(m.get("name") or "")
        if name == wanted_norm:
            hit = m
            break

    if not hit:
        sample = []
        w = wanted_norm.replace("models/", "")
        for m in models:
            n = str(m.get("name") or "")
            if w and (w in n):
                sample.append(n)
            if len(sample) >= 4:
                break
        if sample:
            return False, f"Model aktif tidak cocok. Mungkin maksudmu salah satu ini: {', '.join(sample)}"
        return False, "Model aktif tidak ditemukan di ListModels (nama tidak cocok)."

    methods = hit.get("supportedGenerationMethods") or []
    methods = methods if isinstance(methods, list) else []
    if "generateContent" not in methods:
        return False, f"Model terdeteksi tapi tidak mendukung generateContent. Methods: {methods}"

    return True, "Model terdeteksi dan mendukung generateContent."


def selftest(cfg: dict) -> tuple[bool, str]:
    try:
        msgs = [{"role": "system", "content": "Jawab satu kata saja: OK."}, {"role": "user", "content": "ping"}]
        _ = generate(cfg, msgs, timeout=12, max_output_tokens=16)
        return True, "Self-test API berhasil."
    except Exception as ex:
        return False, str(ex)
