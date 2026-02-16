"""
MON Configuration Manager.
Menangani pemuatan konfigurasi dinamis (JSON) untuk Thresholds, UI Colors, dan Sentinel Rules.

Fitur:
- Auto-generate 'mon_config.json' jika belum ada.
- Fail-safe loading (kembali ke default jika JSON korup).
- Menyimpan parameter vital untuk OOM Killer (Sentinel).
"""

from __future__ import annotations

from typing import Any

from system_logic.core.paths import MON_CONFIG_PATH, SENTINEL_LOG_PATH, MON_LOG_DIR
from system_logic.core.storage import atomic_write_json, ensure_dir, read_json_safe


# ==========================================================
# DEFAULT CONFIGURATION (TEMPLATE)
# ==========================================================

DEFAULT_CONFIG: dict[str, Any] = {
    "version": 5.0,

    # --- SENTINEL (OOM KILLER & GUARDIAN) ---
    "sentinel": {
        "enabled": True,
        "interval_seconds": 1.0,
        "log_path": str(SENTINEL_LOG_PATH),

        "thresholds": {
            # Fase 1: Black Box Recording
            "phase1_log": {
                "ram_pct": 87.0,
                "swap_pct": 50.0
            },
            # Fase 2: Visual Warning
            "phase2_warn": {
                "ram_pct": 90.0,
                "swap_pct": 78.0
            },
            # Fase 3: The Executioner
            "phase3_kill": {
                "ram_pct": 90.0,
                "ram_hold_sec": 5,
                "swap_pct": 87.0
            }
        },

        # Daftar Kebal Hukum (Immunity List)
        "whitelist": [
            "systemd", "init",
            "Xorg", "wayland", "kwin_wayland", "gnome-shell", "surfaceflinger",
            "dbus-daemon", "dbus-broker",
            "pipewire", "pulseaudio", "wireplumber",
            "NetworkManager", "wpa_supplicant",
            "sshd", "login", "bash", "fish", "zsh",
            "python", "python3", "ai-term",
            "code", "dockerd", "containerd"
        ],

        "kill_strategy": "largest_rss"
    },

    # --- UI & VISUALIZATION ---
    "ui": {
        "refresh_rate": 1.0,
        "show_disks": True,
        "compact_mode": False,

        "limits": {
            "cpu_warn": 70,
            "cpu_crit": 90,
            "ram_warn": 80,
            "ram_crit": 90,
            "temp_warn": 70,
            "temp_crit": 85,
            "ping_warn": 50,
            "ping_crit": 150
        }
    },

    # --- COLLECTORS SETTINGS ---
    "collectors": {
        "ping_target": "google.com",
        "ping_interval": 2.0,
        "disk_history_window_months": 30
    }
}


# ==========================================================
# LOGIC LOAD & SAVE
# ==========================================================

def _ensure_directories() -> None:
    """Pastikan semua direktori yang dibutuhkan ada."""
    ensure_dir(MON_CONFIG_PATH.parent)
    ensure_dir(MON_LOG_DIR)


def save_config(data: dict[str, Any]) -> None:
    """Menulis konfigurasi ke file JSON."""
    _ensure_directories()
    try:
        # Compact JSON (no indent) untuk efisiensi
        atomic_write_json(MON_CONFIG_PATH, data, indent=0)
    except Exception as e:
        print(f"[CONFIG] Gagal menyimpan config: {e}")


def load_config() -> dict[str, Any]:
    """
    Memuat konfigurasi.
    1. Jika file tidak ada -> Buat baru dari DEFAULT_CONFIG.
    2. Jika file ada tapi rusak -> Return DEFAULT_CONFIG (Fail-safe).
    3. Jika file ada -> Return isinya.
    """
    _ensure_directories()

    if not MON_CONFIG_PATH.exists():
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG

    user_config = read_json_safe(MON_CONFIG_PATH, default=None)

    if user_config is None or not isinstance(user_config, dict):
        # File corrupt, gunakan default
        return DEFAULT_CONFIG

    # Merge dengan default untuk memastikan key baru tetap ada
    merged = DEFAULT_CONFIG.copy()
    merged.update(user_config)
    return merged


def get_nested(data: dict, keys: list, default=None):
    """
    Mengambil value dari nested dictionary dengan aman.
    Contoh: get_nested(cfg, ['sentinel', 'thresholds', 'phase1_log'], {})
    """
    curr = data
    for k in keys:
        if isinstance(curr, dict) and k in curr:
            curr = curr[k]
        else:
            return default
    return curr