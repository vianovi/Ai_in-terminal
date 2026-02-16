"""
MON Storage Engine (History Database) - SQLite Version

Menangani penyimpanan metrik jangka panjang (Battery, Disk TBW, dll).

CHANGELOG dari versi JSON:
- ✅ Switched to SQLite untuk ACID transactions
- ✅ File locking dengan fcntl (fix race condition)
- ✅ Efficient writes (INSERT, bukan full rewrite)
- ✅ Auto-migration dari JSON lama
- ✅ Backwards compatible API
"""

from __future__ import annotations

from datetime import date as _date, datetime as _dt, timedelta as _timedelta
from typing import Optional

from system_logic.core.paths import (
    MON_HISTORY_DB,
    MON_HISTORY_LOCK,
    MON_HISTORY_PATH,  # Legacy JSON path
)
from system_logic.core.storage import (
    ensure_dir,
    file_lock,
    get_all_metrics_sql,
    get_metric_value_sql,
    init_mon_history_db,
    list_metric_dates_sql,
    migrate_json_to_sqlite,
    read_json_safe,
    snapshot_metric_sql,
)
from system_logic.terminal import ansi


# ==========================================================
# AUTO MIGRATION (JSON → SQLite)
# ==========================================================

def _auto_migrate_if_needed() -> None:
    """
    Check apakah ada legacy JSON file, kalau ada migrate ke SQLite.
    Hanya jalan sekali, idempotent.
    """
    legacy_json = MON_HISTORY_PATH  # Path ke mon_history.json lama

    if not legacy_json.exists():
        return  # Tidak ada JSON lama

    if MON_HISTORY_DB.exists():
        # SQLite sudah ada, skip
        return

    print(f"{ansi.c_dim()}[STORAGE] Migrating history from JSON to SQLite...{ansi.c_reset()}")

    try:
        count = migrate_json_to_sqlite(legacy_json, MON_HISTORY_DB, MON_HISTORY_LOCK)
        print(f"{ansi.c_green()}[STORAGE] Migrated {count} records successfully{ansi.c_reset()}")

        # Backup JSON lama
        backup_path = legacy_json.with_suffix(".json.backup")
        legacy_json.rename(backup_path)
        print(f"{ansi.c_dim()}[STORAGE] Old JSON backed up to: {backup_path}{ansi.c_reset()}")

    except Exception as e:
        print(f"{ansi.c_red()}[STORAGE] Migration failed: {e}{ansi.c_reset()}")
        print(f"{ansi.c_dim()}[STORAGE] Continuing with fresh database{ansi.c_reset()}")


# Initialize database schema dan check migration (LAZY - tidak blocking)
def _ensure_db_initialized():
    """Lazy initialization - hanya jalan saat pertama kali dipakai."""
    ensure_dir(MON_HISTORY_DB.parent)
    if not MON_HISTORY_DB.exists():
        init_mon_history_db(MON_HISTORY_DB)
        _auto_migrate_if_needed()


# ==========================================================
# PUBLIC API (Backwards Compatible)
# ==========================================================

def snapshot_metric(category: str, item_id: str, metric: str, value: float) -> bool:
    """
    Menyimpan snapshot harian.
    Hanya menyimpan jika data hari ini BELUM ada.

    Args:
        category: "battery", "disk", dll.
        item_id: Nama unik item (misal: "nvme0n1").
        metric: Nama metrik (misal: "health_pct").
        value: Nilai float.

    Returns:
        True jika data baru berhasil disimpan.
    """
    _ensure_db_initialized()
    return snapshot_metric_sql(
        MON_HISTORY_DB,
        MON_HISTORY_LOCK,
        category,
        item_id,
        metric,
        value
    )


def get_metric_value(
    category: str,
    item_id: str,
    metric: str,
    date_iso: str
) -> Optional[float]:
    """Mengambil nilai pada tanggal tertentu."""
    _ensure_db_initialized()
    return get_metric_value_sql(MON_HISTORY_DB, category, item_id, metric, date_iso)


def list_metric_dates(category: str, item_id: str, metric: str) -> list[str]:
    """Mengembalikan list tanggal (ISO sorted) yang tersedia untuk metrik tertentu."""
    _ensure_db_initialized()
    return list_metric_dates_sql(MON_HISTORY_DB, category, item_id, metric)


# ==========================================================
# TIME TRAVEL & COMPARISON LOGIC
# ==========================================================

def _parse_iso_date(s: str) -> Optional[_date]:
    """Parse ISO date string dengan validasi."""
    try:
        stripped = (s or "").strip()
        if not stripped:
            return None
        return _date.fromisoformat(stripped)
    except (ValueError, AttributeError):
        return None


def _months_ago(d: _date, months: int) -> _date:
    """
    Mengurangi bulan dengan aman (handling akhir bulan).
    """
    m = int(max(0, months))
    year = d.year
    month = d.month

    # Calculate target month/year
    total_months = year * 12 + month - m
    if total_months <= 0:
        return _date(1, 1, 1)

    target_year = total_months // 12
    target_month = total_months % 12
    if target_month == 0:
        target_month = 12
        target_year -= 1

    # Handle day overflow
    if target_month == 12:
        next_month = _date(target_year + 1, 1, 1)
    else:
        next_month = _date(target_year, target_month + 1, 1)

    last_day = (next_month - _timedelta(days=1)).day
    safe_day = min(d.day, last_day)

    return _date(target_year, target_month, safe_day)


def _select_baseline_date(dates: list[str], window_months: int = 30) -> Optional[str]:
    """
    Memilih tanggal baseline otomatis.
    Strategi: Cari tanggal terlama yang masih masuk dalam 'window_months'.
    """
    if not dates:
        return None

    today = _date.today()
    if window_months > 0:
        limit_date = _months_ago(today, window_months)
        candidates = []
        for s in dates:
            d = _parse_iso_date(s)
            if d and d >= limit_date:
                candidates.append(s)

        if candidates:
            return candidates[0]

    return dates[0]


def _nearest_date_on_or_before(dates: list[str], want_iso: str) -> Optional[str]:
    """Mencari tanggal yang paling mendekati (<=) target."""
    target = _parse_iso_date(want_iso)
    if not target or not dates:
        return None

    best_iso: Optional[str] = None
    best_date: Optional[_date] = None

    for s in dates:
        d = _parse_iso_date(s)
        if not d:
            continue

        if d <= target:
            if best_date is None or d > best_date:
                best_date = d
                best_iso = s

    return best_iso


def get_prev_date(category: str, item_id: str, metric: str) -> Optional[str]:
    """Mendapatkan tanggal snapshot sebelum hari ini (untuk delta harian)."""
    dates = list_metric_dates(category, item_id, metric)
    if not dates:
        return None

    today = _date.today().isoformat()
    if dates[-1] == today:
        return dates[-2] if len(dates) >= 2 else None

    return dates[-1]


def get_comparison_text(
    category: str,
    item_id: str,
    metric: str,
    current: float,
    unit: str = "",
    ref_date: Optional[str] = None,
    window_months: int = 30,
) -> str:
    """
    Menghasilkan string perbandingan: "vs 30 days ago [2023-01-01]: 90 -> 80 (-10)"
    """
    dates = list_metric_dates(category, item_id, metric)
    if not dates:
        return ""

    # Tentukan baseline date
    chosen_iso: Optional[str] = None
    if ref_date:
        if ref_date in dates:
            chosen_iso = ref_date
        else:
            chosen_iso = _nearest_date_on_or_before(dates, ref_date)

    if not chosen_iso:
        chosen_iso = _select_baseline_date(dates, window_months)

    if not chosen_iso:
        return ""

    today_iso = _date.today().isoformat()
    if chosen_iso == today_iso:
        return f"{ansi.c_dim()}(mulai tracking hari ini){ansi.c_reset()}"

    old_val = get_metric_value(category, item_id, metric, chosen_iso)
    if old_val is None:
        return ""

    diff = current - old_val

    chosen_date = _parse_iso_date(chosen_iso)
    if chosen_date is None:
        return ""

    days_ago = (_date.today() - chosen_date).days

    icon = "⚪"
    if diff > 0:
        icon = "📈+"
    elif diff < 0:
        icon = "📉"

    date_tag = f"[{chosen_iso}]"

    return (
        f"{ansi.c_dim()}vs {days_ago} hari lalu {date_tag}: "
        f"{old_val:.2f} -> {current:.2f} "
        f"({icon}{diff:+.2f}{unit}){ansi.c_reset()}"
    )


def pick_date_interactive(dates: list[str], title: str = "Pilih tanggal") -> Optional[str]:
    """
    Helper UI sederhana untuk memilih tanggal dari list.
    """
    if not dates:
        return None

    print(f"\n{ansi.c_bold()}{title}{ansi.c_reset()}")

    show_dates = dates[-20:]
    offset = len(dates) - len(show_dates)

    if offset > 0:
        print(f"  ... ({offset} tanggal terlama disembunyikan)")

    for i, d in enumerate(show_dates, start=1 + offset):
        print(f"  {i:>2}. {d}")

    print(f"  {ansi.c_dim()}0. Batal{ansi.c_reset()}")

    try:
        raw = input(
            f"{ansi.c_cyan()}Pilih nomor{ansi.c_reset()} (0-{len(dates)}): "
        ).strip()
        if not raw or raw == "0":
            return None

        idx = int(raw)
        if 1 <= idx <= len(dates):
            return dates[idx - 1]
    except (ValueError, KeyboardInterrupt, EOFError):
        pass

    return None


# ==========================================================
# UTILITY / DEBUG
# ==========================================================

def get_database_stats() -> dict:
    """Get statistik database untuk monitoring."""
    import sqlite3

    if not MON_HISTORY_DB.exists():
        return {"exists": False}

    conn = sqlite3.connect(str(MON_HISTORY_DB))
    try:
        cursor = conn.execute("SELECT COUNT(*) FROM metrics")
        total_records = cursor.fetchone()[0]

        cursor = conn.execute("SELECT COUNT(DISTINCT category) FROM metrics")
        total_categories = cursor.fetchone()[0]

        # Database file size
        size_bytes = MON_HISTORY_DB.stat().st_size
        size_kb = size_bytes / 1024

        return {
            "exists": True,
            "path": str(MON_HISTORY_DB),
            "total_records": total_records,
            "total_categories": total_categories,
            "size_kb": round(size_kb, 2),
        }
    finally:
        conn.close()