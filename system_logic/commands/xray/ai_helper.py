"""
commands/xray/ai_helper.py
==========================
AI interpretation engine untuk command `xray`.

Aturan isolasi:
    BOLEH import dari luar xray/:
        - system_logic.backends.api_gemini  → generate, gemini_key
        - system_logic.core.utils           → http_json (untuk Groq & OpenRouter)
    TIDAK BOLEH import dari luar xray/ selain di atas.

Cara kerja:
    1. Baca provider & model dari section 'xray' di config.json.
    2. Reuse API key dari section 'api' yang sudah ada (Gemini).
    3. Kirim raw result + system prompt per-modul ke AI.
    4. Return string interpretasi.

Provider yang didukung:
    - gemini      (default) — reuse key dari section 'api'
    - groq        — baca key dari env GROQ_API_KEY
    - openrouter  — baca key dari env OPENROUTER_API_KEY

Exported:
    interpret(result, cfg, module)   -> str
"""
from __future__ import annotations

import json


# ============================================================
# [1] System prompts per modul
# ============================================================

# Setiap modul punya sudut pandang analisis yang berbeda
# agar interpretasi AI lebih relevan dan kontekstual.

XRAY_SYSTEM_PROMPTS: dict[str, str] = {
    "osint": (
        "You are a cybersecurity OSINT analyst. "
        "You receive structured findings from a public data scan. "
        "Interpret the data clearly and concisely in Bahasa Indonesia. "
        "Highlight anything suspicious, unusual, or worth noting. "
        "Keep your response under 150 words. Use plain language."
    ),
    "steg": (
        "You are a digital forensics expert specializing in steganography. "
        "You receive analysis results from an image or file inspection. "
        "Explain what was found and its significance in Bahasa Indonesia. "
        "Keep your response under 150 words."
    ),
    "file": (
        "You are a malware analyst and file forensics expert. "
        "You receive file analysis results including type, entropy, and strings. "
        "Flag anything suspicious. Explain findings in plain Bahasa Indonesia. "
        "Keep your response under 150 words."
    ),
    "net": (
        "You are a network security analyst. "
        "You receive network scan or traffic data. "
        "Highlight open ports, unusual services, or security concerns in Bahasa Indonesia. "
        "Keep your response under 150 words."
    ),
}

_DEFAULT_PROMPT = (
    "You are a cybersecurity analyst. "
    "Interpret the following technical findings clearly in Bahasa Indonesia. "
    "Highlight anything suspicious. Keep your response under 150 words."
)


# ============================================================
# [2] Provider handlers
# ============================================================

def _generate_gemini(cfg: dict, messages: list[dict]) -> str:
    """
    Generate interpretasi via Gemini API.
    Reuse API key dari config utama (section 'api').
    Model diambil dari section 'xray'.

    Args:
        cfg      : Config dict dari load_config().
        messages : List pesan [system, user].

    Returns:
        String hasil generate.

    Raises:
        RuntimeError: Jika API key kosong atau generate gagal.
    """
    from system_logic.backends.api_gemini import gemini_key, generate

    # Reuse API key — tidak duplikat penyimpanan key
    key, env_name = gemini_key(cfg)
    if not key:
        raise RuntimeError(
            f"API key Gemini tidak ditemukan. "
            f"Pastikan env '{env_name}' sudah di-set."
        )

    # Override active_model dengan model dari section xray
    xray_section = cfg.get("xray", {})
    model = str(xray_section.get("model") or "gemini-2.5-flash").strip()

    merged_cfg = {
        **cfg,
        "api": {
            **cfg.get("api", {}),
            "active_model": model,
        },
    }

    return generate(merged_cfg, messages, timeout=30, max_output_tokens=1000)


def _generate_groq(cfg: dict, messages: list[dict]) -> str:
    """
    Generate interpretasi via Groq API.
    Key dibaca dari env GROQ_API_KEY (atau custom env dari config).

    Args:
        cfg      : Config dict dari load_config().
        messages : List pesan [system, user].

    Returns:
        String hasil generate.

    Raises:
        RuntimeError: Jika API key kosong atau generate gagal.
    """
    import os
    from system_logic.core.utils import http_json

    xray_section = cfg.get("xray", {})
    model    = str(xray_section.get("model") or "llama3-70b-8192").strip()
    env_name = str(xray_section.get("groq_api_key_env") or "GROQ_API_KEY")
    key      = (os.environ.get(env_name) or "").strip()

    if not key:
        raise RuntimeError(
            f"API key Groq tidak ditemukan. "
            f"Pastikan env '{env_name}' sudah di-set."
        )

    data = http_json(
        "POST",
        "https://api.groq.com/openai/v1/chat/completions",
        {
            "model": model,
            "messages": messages,
            "max_tokens": 1000,
            "temperature": 0.7,
        },
        timeout=30,
        headers={"Authorization": f"Bearer {key}"},
    )

    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("Respons Groq kosong.")

    return str((choices[0].get("message") or {}).get("content") or "").strip()


def _generate_openrouter(cfg: dict, messages: list[dict]) -> str:
    """
    Generate interpretasi via OpenRouter API.
    Key dibaca dari env OPENROUTER_API_KEY (atau custom env dari config).

    Args:
        cfg      : Config dict dari load_config().
        messages : List pesan [system, user].

    Returns:
        String hasil generate.

    Raises:
        RuntimeError: Jika API key kosong atau generate gagal.
    """
    import os
    from system_logic.core.utils import http_json

    xray_section = cfg.get("xray", {})
    model    = str(xray_section.get("model") or "mistralai/mistral-7b-instruct").strip()
    env_name = str(xray_section.get("openrouter_api_key_env") or "OPENROUTER_API_KEY")
    key      = (os.environ.get(env_name) or "").strip()

    if not key:
        raise RuntimeError(
            f"API key OpenRouter tidak ditemukan. "
            f"Pastikan env '{env_name}' sudah di-set."
        )

    data = http_json(
        "POST",
        "https://openrouter.ai/api/v1/chat/completions",
        {
            "model": model,
            "messages": messages,
            "max_tokens": 1000,
        },
        timeout=30,
        headers={
            "Authorization": f"Bearer {key}",
            "HTTP-Referer": "https://github.com/ai-term",
        },
    )

    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("Respons OpenRouter kosong.")

    return str((choices[0].get("message") or {}).get("content") or "").strip()


# ============================================================
# [3] Main interpret function
# ============================================================

def interpret(result: dict, *, cfg: dict, module: str) -> str:
    """
    Kirim hasil scan ke AI untuk interpretasi.

    Flow:
        1. Baca provider dari cfg['xray']['provider'].
        2. Pilih system prompt sesuai modul.
        3. Serialize result (skip internal keys) sebagai user message.
        4. Dispatch ke handler provider yang sesuai.
        5. Return string interpretasi.

    Args:
        result : Dict hasil dari modul xray.
        cfg    : Config dict dari load_config().
        module : Nama modul ('osint'|'steg'|'file'|'net').

    Returns:
        String interpretasi dari AI.

    Raises:
        RuntimeError: Jika provider tidak didukung atau generate gagal.
    """
    xray_section  = cfg.get("xray", {})
    provider      = str(xray_section.get("provider") or "gemini").strip().lower()
    system_prompt = XRAY_SYSTEM_PROMPTS.get(module, _DEFAULT_PROMPT)

    # Serialize — skip internal keys yang diawali '_'
    result_clean = {k: v for k, v in result.items() if not k.startswith("_")}
    user_content = json.dumps(result_clean, indent=2, ensure_ascii=False)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": f"Analyze this finding:\n\n{user_content}"},
    ]

    # Dispatch ke provider
    if provider == "gemini":
        return _generate_gemini(cfg, messages)
    elif provider == "groq":
        return _generate_groq(cfg, messages)
    elif provider == "openrouter":
        return _generate_openrouter(cfg, messages)
    else:
        raise RuntimeError(
            f"Provider xray '{provider}' belum didukung. "
            f"Pilihan yang valid: gemini | groq | openrouter"
        )
