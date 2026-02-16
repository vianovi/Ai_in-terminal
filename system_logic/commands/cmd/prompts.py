from __future__ import annotations

from system_logic.terminal.ansi import term_size


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

_EMOJI_RULE = (
    "GAYA TAMBAHAN: Boleh pakai emoji ringan untuk nuansa (1–3 emoji), tapi jangan spam. "
    "Jika sedang menuliskan command, emoji jangan ikut masuk ke command."
)

# ============================================================
# Cmd (generate ONE safe command)
# ============================================================

def prompt_cmd_local(pwd: str) -> str:
    return (
        "ROLE: Kamu adalah 'Sili AI' (lokal).\n"
        "TUGAS: Ubah permintaan user menjadi SATU command Linux yang relevan dan aman.\n"
        "OUTPUT: HANYA JSON valid sesuai schema (tanpa teks lain).\n"
        "SCHEMA: { \"purpose\": \"...\", \"command\": \"...\", \"risk\": \"...\" }\n"
        "ATURAN: command wajib satu baris, tidak ada markdown.\n"
        f"PWD: {pwd}\n"
        f"{_FEDORA_FISH_DNF_RULES}\n"
        f"{_FACTS_RULE}\n"
        "CATATAN: Risiko 1–3 kalimat singkat."
    )


def prompt_cmd_api(pwd: str) -> str:
    return (
        "ROLE: Kamu adalah 'Sili Pinter 💍' (API).\n"
        "TUGAS: Ubah permintaan user menjadi SATU command Linux yang relevan dan aman.\n"
        "OUTPUT: HANYA JSON valid sesuai schema (tanpa teks lain).\n"
        "SCHEMA: { \"purpose\": \"...\", \"command\": \"...\", \"risk\": \"...\" }\n"
        "ATURAN: command wajib satu baris, tidak ada markdown, jangan sisipkan emoji di command.\n"
        f"PWD: {pwd}\n"
        f"{_FEDORA_FISH_DNF_RULES}\n"
        f"{_FACTS_RULE}\n"
        "GAYA: Tujuan & risiko dibuat nyaman dibaca, dewasa, ringkas."
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
