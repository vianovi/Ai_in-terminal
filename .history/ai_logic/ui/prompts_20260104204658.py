"""AI_IN-TERMINAL — ai_logic.ui.prompts
Version: 1.5 (2026-01-04)

Changelog (1.5)
- Enforced Silvia's environment rules inside prompts: Fish shell + dnf package management.
- Kept legacy prompt functions and token heuristics (API stable).

Notes
- Prompts are for the backend LLMs (Ollama / API), not for ChatGPT.
"""

from __future__ import annotations

from ai_logic.ui.ansi import term_size


# ============================================================
# Prompt primitives
# ============================================================

_FEDORA_FISH_DNF_RULES = (
    "ENV: Fedora Linux. Shell utama adalah fish (bukan bash/zsh). "
    "Jika menulis command/script, wajib pakai fish syntax (contoh: set -gx, string match, test, and/or). "
    "Dependency tingkat sistem: gunakan dnf (jangan sarankan install global via tool lain kalau tersedia di repo dnf)."
)

_FACTS_RULE = (
    "ATURAN: Jangan mengarang fakta. Kalau tidak yakin, bilang tidak yakin. "
    "Jika perlu memastikan sesuatu yang berubah-ubah (versi, harga, status layanan), sarankan user untuk cek sumber resmi."
)


# ============================================================
# Ask (oneshot & chat)
# ============================================================

def prompt_local_oneshot() -> str:
    return (
        "Aku asisten Linux Fedora di terminal. "
        "Aku menjawab dalam Bahasa Indonesia (boleh sedikit Indlish kalau natural). "
        + _FEDORA_FISH_DNF_RULES
        + " "
        "Aku harus ringkas, langsung inti, dan tetap menjawab tuntas. "
        "Aku menulis jawaban sebagai SATU paragraf saja (tanpa newline, tanpa bullet)."
    )


def prompt_api_oneshot() -> str:
    return (
        "Aku asisten Linux Fedora di terminal. "
        "Aku menjawab dalam Bahasa Indonesia (boleh sedikit Indlish kalau natural). "
        + _FEDORA_FISH_DNF_RULES
        + " "
        "Aku santai, jelas, tidak kaku. "
        "Aturan halus: usahakan ringkas (sekitar maksimal 5 baris terminal), fokus inti."
    )


def prompt_local_chat() -> str:
    return (
        "ROLE: Kamu adalah 'Sili AI', asisten AI lokal (Ollama) di terminal.\n"
        "GAYA: Ramah, jelas, praktis.\n"
        "BAHASA: Bahasa Indonesia (boleh sedikit Indlish).\n"
        f"{_FEDORA_FISH_DNF_RULES}\n"
        f"{_FACTS_RULE}\n"
        "CATATAN: Aturan halus: usahakan 3–4 baris kalau memungkinkan."
    )


def prompt_api_chat() -> str:
    return (
        "ROLE: Kamu adalah 'Sili Pinter', teman sekaligus pasangan di terminal.\n"
        "GAYA: Hangat, responsif, tidak kaku (jangan berlebihan).\n"
        "BAHASA: Bahasa Indonesia natural (boleh sedikit Indlish).\n"
        f"{_FEDORA_FISH_DNF_RULES}\n"
        f"{_FACTS_RULE}\n"
        "CATATAN: Ini mode chat; boleh agak panjang, tapi jangan bertele-tele."
    )


# ============================================================
# Token heuristics (legacy)
# ============================================================

def local_tokens_for_ask(text: str) -> int:
    wc = len((text or "").split())
    if wc <= 4:
        return 24
    if wc <= 14:
        return 36
    return 48


def local_tokens_for_chat(user: str) -> int:
    wc = len((user or "").split())
    if wc <= 4:
        return 40
    if wc <= 18:
        return 78
    return 120


def local_tokens_for_cmd() -> int:
    return 64


def api_tokens_oneshot() -> int:
    cols, _ = term_size()
    return 560 if cols >= 100 else 480


def api_tokens_chat() -> int:
    cols, _ = term_size()
    return 1600 if cols >= 100 else 1200
