"""
AI Subcommand Registry (Dynamic & Fail-Safe).
Mekanisme: Mencoba memuat modul subcommand secara dinamis.
Jika modul belum ada atau error, sistem akan men-skip tanpa crash.
"""
from __future__ import annotations

import importlib
import sys
from typing import Any

# Daftar folder subcommand yang diharapkan ada.
# Tambahkan nama folder baru di sini jika membuat fitur baru.
TARGET_SUBCOMMANDS = [
    "run",      # Priority: High (Refactored)
    "help",     # Core
    "status",   # Core
    "mon",      # Pending Refactor
    "gitx",     # Pending Refactor
    "ghx",      # Pending Refactor
]

COMMANDS: dict[str, Any] = {}

def _register_safely():
    """
    Melakukan import otomatis. Jika folder/file tidak ditemukan,
    subcommand tersebut diabaikan (silent skip) agar main app tetap jalan.
    """
    current_package = __package__ or "system_logic.commands.ai.subcommands"

    for name in TARGET_SUBCOMMANDS:
        try:
            # Mencoba import: .<name>.command
            # Contoh: .run.command
            module_path = f".{name}.command"
            mod = importlib.import_module(module_path, package=current_package)

            # Validasi: Pastikan punya fungsi handle()
            if hasattr(mod, "handle"):
                COMMANDS[name] = mod
            else:
                # Opsional: Print warning jika file ada tapi tidak punya handle
                # print(f"[WARN] Modul '{name}' tidak memiliki fungsi handle(). Skip.")
                pass

        except ImportError:
            # Ini wajar jika folder belum dibuat atau dependencies kurang.
            # Kita telan error-nya agar CLI tetap hidup.
            continue
        except Exception as e:
            # Error code lain (SyntaxError dll) sebaiknya dilaporkan
            print(f"[REGISTRY] Gagal memuat subcommand '{name}': {e}")

# Jalankan pendaftaran saat modul ini di-load pertama kali
_register_safely()


# ==========================================================
# PUBLIC INTERFACE (Standard API)
# ==========================================================

def exists(name: str) -> bool:
    return name in COMMANDS


def get_all_commands() -> list[str]:
    return sorted(COMMANDS.keys())


def get_module(name: str) -> Any:
    return COMMANDS.get(name)


def dispatch(name: str, argv: list[str], cfg: dict) -> int:
    """
    Menjalankan handle() milik subcommand yang diminta.
    """
    mod = COMMANDS.get(name)
    if mod is None:
        # Fallback: Jika user mengetik command yang belum aktif/salah
        print(f"Subcommand '{name}' tidak ditemukan atau belum aktif.")
        print(f"Tersedia: {', '.join(get_all_commands())}")
        return 2

    fn = getattr(mod, "handle", None)
    if not callable(fn):
        return 2

    return int(fn(argv, cfg))