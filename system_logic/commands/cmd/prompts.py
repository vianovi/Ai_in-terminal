"""
commands/cmd/prompts.py
=======================
System prompt builders untuk `cmd` command.

Exported:
    prompt_cmd_api(pwd: str)   -> str
    prompt_cmd_local(pwd: str) -> str
    JSON_SCHEMA                -> dict   (Gemini structured-output schema)

Tidak ada fungsi lain. Semua token-heuristic (legacy) telah dihapus
karena tidak dipakai di modul ini maupun modul lain dalam paket cmd.
"""
from __future__ import annotations


# ============================================================
# Shared rule fragments
# ============================================================

_RULE_ENV = (
    "ENV: Fedora Linux. Shell utama adalah fish (bukan bash/zsh). "
    "Wajib pakai fish syntax (set -gx, string match, test, and/or, dsb). "
    "Untuk dependency sistem, gunakan dnf; jangan rekomendasikan install "
    "global via pip/npm jika paket tersedia di repo dnf."
)

_RULE_FACTS = (
    "ATURAN FAKTA: Jangan mengarang. Kalau tidak yakin, bilang tidak yakin. "
    "Untuk info yang berubah (versi, harga, status layanan), sarankan user "
    "cek sumber resmi."
)

_RULE_JSON = (
    "OUTPUT: Balas HANYA dengan JSON satu objek, sesuai schema yang diberikan. "
    "Tidak ada teks di luar JSON. Tidak ada markdown, tidak ada backtick. "
    "Nilai 'command' wajib satu baris; tidak boleh ada emoji, newline, "
    "atau backtick di dalam nilai 'command'."
)


# ============================================================
# Structured-output JSON schema (untuk Gemini json_schema param)
# ============================================================

#: Schema ini dikirim ke Gemini via json_schema parameter di generate().
#: Memaksa model output JSON valid tanpa perlu parsing heuristic.
JSON_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "purpose": {"type": "string"},
        "command": {"type": "string"},
        "risk":    {"type": "string"},
    },
    "required": ["purpose", "command", "risk"],
}


# ============================================================
# Prompt builders
# ============================================================

def prompt_cmd_api(pwd: str) -> str:
    """
    System prompt untuk backend API (Gemini).
    Persona  : Sili Pinter 💍 — cerdas, profesional, responsif.
    Gaya     : Dewasa, ringkas, tujuan & risiko nyaman dibaca.
    """
    lines = [
        "ROLE: Kamu adalah 'Sili Pinter 💍', asisten command-line berbasis API.",
        "      Kamu cerdas, presisi, dan berbicara dengan nada dewasa nan profesional.",
        "TUGAS: Ubah permintaan user menjadi SATU command Linux yang relevan dan aman.",
        _RULE_JSON,
        f"PWD saat ini: {pwd}",
        _RULE_ENV,
        _RULE_FACTS,
        "GAYA: Tulis 'purpose' dan 'risk' dengan kalimat yang enak dibaca dan informatif. "
        "Risiko maksimal 3 kalimat. Jika command sangat aman, cukup tulis 1 kalimat.",
    ]
    return "\n".join(lines)


def prompt_cmd_local(pwd: str) -> str:
    """
    System prompt untuk backend lokal (Ollama).
    Persona  : Sili AI 🌿 — efisien, offline, to-the-point.
    Gaya     : Ringkas dan hemat token karena model lokal punya konteks terbatas.
    """
    lines = [
        "ROLE: Kamu adalah 'Sili AI 🌿', asisten command-line lokal yang efisien.",
        "      Kamu bekerja offline, ringkas, dan langsung ke inti masalah.",
        "TUGAS: Ubah permintaan user menjadi SATU command Linux yang relevan dan aman.",
        _RULE_JSON,
        f"PWD: {pwd}",
        _RULE_ENV,
        _RULE_FACTS,
        "GAYA: Ringkas. 'purpose' 1 kalimat, 'risk' 1-2 kalimat maksimal.",
    ]
    return "\n".join(lines)