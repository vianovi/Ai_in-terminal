"""
MON Configuration Manager.
Menangani pemuatan konfigurasi dinamis (JSON) untuk Thresholds, UI Colors, dan Sentinel Rules.

Fitur:
- Auto-generate 'mon_config.json' jika belum ada.
- Fail-safe loading (kembali ke default jika JSON korup).
- Deep merge — nested keys dari user config tidak menimpa seluruh section default.
- Menyimpan parameter vital untuk OOM Killer (Sentinel).

CHANGELOG:
- Fixed: shallow merge → deep merge (_deep_merge)
  Sebelumnya merged.update(user_config) menimpa seluruh nested dict.
  Sekarang setiap level dict di-merge secara rekursif.
"""

from __future__ import annotations

from typing import Any

from system_logic.core.paths import MON_CONFIG_PATH, SENTINEL_LOG_PATH, MON_LOG_DIR
from system_logic.core.storage import atomic_write_json, ensure_dir, read_json_safe


# ==========================================================
# DEFAULT CONFIGURATION
# ==========================================================

DEFAULT_CONFIG: dict[str, Any] = {
    "version": 5.0,

    "sentinel": {
        "enabled":          True,
        "interval_seconds": 1.0,
        "log_path":         str(SENTINEL_LOG_PATH),

        "thresholds": {
            "phase1_log": {
                "ram_pct":  87.0,
                "swap_pct": 80.0,
            },
            "phase2_warn": {
                "ram_pct":  90.0,
                "swap_pct": 85.0,
            },
            "phase3_kill": {
                "ram_pct":      90.0,
                "ram_hold_sec": 10,
                "swap_pct":     95.0,
            },
        },

        "whitelist": [
            "systemd", "init",
            "Xorg", "wayland", "kwin_wayland", "gnome-shell", "surfaceflinger",
            "dbus-daemon", "dbus-broker",
            "pipewire", "pulseaudio", "wireplumber",
            "NetworkManager", "wpa_supplicant",
            "sshd", "login", "bash", "fish", "zsh",
            "python", "python3", "ai-term",
            "code", "dockerd", "containerd",
        ],

        "kill_strategy": "largest_rss",
    },

    "ui": {
        "refresh_rate": 1.0,
        "show_disks":   True,
        "compact_mode": False,

        "limits": {
            "cpu_warn":  70,
            "cpu_crit":  90,
            "ram_warn":  80,
            "ram_crit":  90,
            "temp_warn": 70,
            "temp_crit": 85,
            "ping_warn": 50,
            "ping_crit": 150,
        },
    },

    "collectors": {
        "ping_target":               "google.com",
        "ping_interval":             2.0,
        "disk_history_window_months": 30,
    },
}


# ==========================================================
# DEEP MERGE HELPER
# ==========================================================

def _deep_merge(base: dict, override: dict) -> dict:
    """
    Merge dua dict secara rekursif (deep merge).

    Untuk setiap key di override:
    - Jika value-nya dict DAN key yang sama juga dict di base
      → merge rekursif (tidak overwrite seluruh section)
    - Selain itu → override menang

    Contoh masalah shallow merge:
        base     = {"sentinel": {"whitelist": [...], "thresholds": {...}}}
        override = {"sentinel": {"enabled": true}}
        shallow  → {"sentinel": {"enabled": true}}  ← whitelist & thresholds HILANG!
        deep     → {"sentinel": {"enabled": true, "whitelist": [...], "thresholds": {...}}}
    """
    result = base.copy()
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


# ==========================================================
# DIRECTORIES
# ==========================================================

def _ensure_directories() -> None:
    """Pastikan semua direktori yang dibutuhkan ada."""
    ensure_dir(MON_CONFIG_PATH.parent)
    ensure_dir(MON_LOG_DIR)


# ==========================================================
# SAVE & LOAD
# ==========================================================

def save_config(data: dict[str, Any]) -> None:
    """Tulis konfigurasi ke file JSON secara atomic."""
    _ensure_directories()
    try:
        atomic_write_json(MON_CONFIG_PATH, data, indent=2)
    except Exception as e:
        print(f"[CONFIG] Gagal menyimpan config: {e}")


def load_config() -> dict[str, Any]:
    """
    Memuat konfigurasi dengan deep merge ke DEFAULT_CONFIG.

    Priority:
    1. File tidak ada         → buat baru dari DEFAULT_CONFIG
    2. File ada tapi corrupt  → return DEFAULT_CONFIG (fail-safe)
    3. File ada               → deep merge user_config ke DEFAULT_CONFIG
                                (user values menang, default mengisi yang kosong)
    """
    _ensure_directories()

    if not MON_CONFIG_PATH.exists():
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG

    user_config = read_json_safe(MON_CONFIG_PATH, default=None)

    if user_config is None or not isinstance(user_config, dict):
        # File corrupt, gunakan default
        return DEFAULT_CONFIG

    # Deep merge — user config menang tapi tidak menghapus nested defaults
    return _deep_merge(DEFAULT_CONFIG, user_config)


# ==========================================================
# UTILITY
# ==========================================================

def get_nested(data: dict, keys: list, default: Any = None) -> Any:
    """
    Ambil value dari nested dict secara aman.

    Contoh:
        get_nested(cfg, ['sentinel', 'thresholds', 'phase1_log'], {})
    """
    curr = data
    for k in keys:
        if isinstance(curr, dict) and k in curr:
            curr = curr[k]
        else:
            return default
    return curr