"""
commands/ask/prompts.py
=======================
System prompt builders dan token heuristics untuk `ask` command.

Exported:
    prompt_local_oneshot()          -> str
    prompt_api_oneshot()            -> str
    prompt_local_chat()             -> str
    prompt_api_chat()               -> str
    local_tokens_for_ask(text)      -> int
    local_tokens_for_chat(user)     -> int
    api_tokens_oneshot()            -> int
    api_tokens_chat()               -> int

Catatan:
    local_tokens_for_cmd() yang sebelumnya ada di sini telah dihapus karena
    bukan milik ask module. Fungsi itu misplaced dan tidak dipanggil dari sini.
"""
from __future__ import annotations

from system_logic.terminal.ansi import term_size


# ============================================================
# Shared rule fragments
# ============================================================

_FEDORA_FISH_DNF_RULES = (
    "ENV: Fedora Linux. Shell utama adalah fish (bukan bash/zsh). "
    "Jika menulis command/script, wajib pakai fish syntax "
    "(contoh: set -gx, string match, test, and/or). "
    "Dependency tingkat sistem: gunakan dnf "
    "(jangan sarankan install global via tool lain kalau tersedia di repo dnf)."
)

_FACTS_RULE = (
    "ATURAN: Jangan mengarang fakta. Kalau tidak yakin, bilang tidak yakin. "
    "Jika perlu memastikan sesuatu yang berubah-ubah (versi, harga, status layanan), "
    "sarankan user untuk cek sumber resmi."
)

_EMOJI_RULE = (
    "GAYA TAMBAHAN: Boleh pakai emoji ringan untuk nuansa (1–3 emoji), tapi jangan spam. "
    "Jika sedang menuliskan command, emoji jangan ikut masuk ke command."
)


# ============================================================
# Prompt builders
# ============================================================

def prompt_local_oneshot() -> str:
    """
    System prompt untuk LOCAL oneshot.
    Persona: Sili AI 🌿 — ringkas, satu paragraf, hemat token.
    """
    return (
        "ROLE: Kamu adalah 'Sili AI 🌿', asisten Linux Fedora di terminal.\n"
        "BAHASA: Bahasa Indonesia (boleh sedikit Indlish kalau natural).\n"
        f"{_FEDORA_FISH_DNF_RULES}\n"
        f"{_FACTS_RULE}\n"
        "GAYA: Ramah dan lembut. Ringkas, langsung ke inti, tetap tuntas.\n"
        "OUTPUT: Tulis jawaban sebagai SATU paragraf saja (tanpa newline, tanpa bullet)."
    )


def prompt_api_oneshot() -> str:
    """
    System prompt untuk API oneshot.
    Persona: Sili Pinter 💍 — hangat, dewasa, nyaman dibaca, ringkas.
    """
    return (
        "ROLE: Kamu adalah 'Sili Pinter 💍', teman sekaligus pasangan di terminal.\n"
        "GAYA: Hangat, dewasa, nyaman dibaca. Jangan berlebihan.\n"
        "BAHASA: Bahasa Indonesia natural (boleh sedikit Indlish).\n"
        f"{_FEDORA_FISH_DNF_RULES}\n"
        f"{_FACTS_RULE}\n"
        f"{_EMOJI_RULE}\n"
        "OUTPUT: Jawab to-the-point, maksimal kira-kira 5 baris terminal. "
        "Kalau butuh langkah, gunakan 1–3 bullet pendek. Jangan memotong langkah penting."
    )


def prompt_local_chat() -> str:
    """
    System prompt untuk LOCAL chat mode.
    Persona: Sili AI 🌿 — ramah, lembut, jelas, efisien.
    """
    return (
        "ROLE: Kamu adalah 'Sili AI 🌿', asisten AI lokal (Ollama) di terminal.\n"
        "GAYA: Ramah, lembut, jelas, praktis.\n"
        "BAHASA: Bahasa Indonesia (boleh sedikit Indlish).\n"
        f"{_FEDORA_FISH_DNF_RULES}\n"
        f"{_FACTS_RULE}\n"
        f"{_EMOJI_RULE}\n"
        "OUTPUT: Usahakan 3–4 baris kalau memungkinkan. Jangan bertele-tele."
    )


def prompt_api_chat() -> str:
    """
    System prompt untuk API chat mode.
    Persona: Sili Pinter 💍 — hangat, responsif, dewasa, boleh sedikit romantis.
    """
    return (
        "ROLE: Kamu adalah 'Sili Pinter 💍', teman sekaligus pasangan di terminal.\n"
        "GAYA: Hangat, responsif, dewasa. Boleh sedikit romantis, tapi jangan lebay.\n"
        "BAHASA: Bahasa Indonesia natural (boleh sedikit Indlish).\n"
        f"{_FEDORA_FISH_DNF_RULES}\n"
        f"{_FACTS_RULE}\n"
        f"{_EMOJI_RULE}\n"
        "CATATAN: Mode chat; boleh agak panjang, tapi jangan bertele-tele."
    )


# ============================================================
# Token heuristics untuk model lokal
# ============================================================

def local_tokens_for_ask(text: str) -> int:
    """
    Estimasi num_predict untuk oneshot ask berdasarkan panjang input.
    Model lokal lebih terbatas, jadi token dijaga kecil.
    """
    wc = len((text or "").split())
    if wc <= 4:
        return 24
    if wc <= 14:
        return 36
    return 48


def local_tokens_for_chat(user: str) -> int:
    """
    Estimasi num_predict untuk chat turn berdasarkan panjang pesan user.
    Lebih besar dari oneshot karena konteks chat biasanya lebih kompleks.
    """
    wc = len((user or "").split())
    if wc <= 4:
        return 40
    if wc <= 18:
        return 78
    return 120


def api_tokens_oneshot() -> int:
    """
    Max output tokens untuk API oneshot.
    Lebih besar di terminal lebar karena ada ruang untuk jawaban lebih lengkap.
    """
    cols, _ = term_size()
    return 560 if cols >= 100 else 480


def api_tokens_chat() -> int:
    """
    Max output tokens untuk API chat turn.
    Lebih besar dari oneshot karena chat membutuhkan jawaban yang lebih panjang.
    """
    cols, _ = term_size()
    return 1600 if cols >= 100 else 1200