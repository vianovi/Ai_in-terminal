from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

# MON additions
import sqlite3
import fcntl
from contextlib import contextmanager
from datetime import date, datetime
from typing import Optional, List, Dict


def ensure_dir(path: Path) -> None:
    """Ensure directory exists (best-effort)."""
    try:
        path.mkdir(parents=True, exist_ok=True)
    except Exception:
        # Caller may not have permission; let the actual write fail.
        pass


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Atomic write within the same directory."""
    path = Path(path)
    ensure_dir(path.parent)

    # NamedTemporaryFile must be in same directory to allow atomic os.replace.
    fd = None
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            delete=False,
            dir=str(path.parent),
            prefix=f".{path.name}.",
        ) as f:
            fd = f.fileno()
            tmp_path = Path(f.name)
            f.write(data)
            f.flush()
            try:
                os.fsync(fd)
            except Exception:
                # Some FS / sandbox setups may not support fsync.
                pass

        os.replace(str(tmp_path), str(path))

        # Also fsync the directory entry if possible (stronger durability).
        try:
            dfd = os.open(str(path.parent), os.O_DIRECTORY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except Exception:
            pass
    finally:
        # Cleanup on failures
        if tmp_path is not None and tmp_path.exists() and tmp_path != path:
            try:
                tmp_path.unlink(missing_ok=True)  # type: ignore[arg-type]
            except Exception:
                pass


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    _atomic_write_bytes(path, (text or "").encode(encoding))


def atomic_write_json(path: Path, payload: Any, *, indent: int = 2, encoding: str = "utf-8") -> None:
    data = json.dumps(payload, indent=indent, ensure_ascii=False)
    atomic_write_text(path, data + "\n", encoding=encoding)


def read_json_safe(path: Path, *, default: Any = None, encoding: str = "utf-8") -> Any:
    """Read JSON safely; returns default on missing/corrupt."""
    try:
        p = Path(path)
        if not p.exists():
            return default
        raw = p.read_text(encoding=encoding)
        return json.loads(raw)
    except Exception:
        return default


# ============================================================
# MON-specific storage functions (SQLite)
# ============================================================

@contextmanager
def file_lock(lock_path):
    """Context manager untuk file locking dengan fcntl."""
    lock_path = Path(lock_path)

    # Ensure parent directory exists
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass  # Best effort

    lock_file = open(lock_path, "w")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            lock_file.close()
        except Exception:
            pass


def init_mon_history_db(db_path) -> None:
    """Initialize MON history database schema."""
    db_path = Path(db_path)

    # Ensure directory exists
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                item_id TEXT NOT NULL,
                metric TEXT NOT NULL,
                date TEXT NOT NULL,
                value REAL NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(category, item_id, metric, date)
            )
        """)

        # Index untuk query cepat
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_lookup
            ON metrics(category, item_id, metric, date)
        """)

        conn.commit()
    finally:
        conn.close()


def snapshot_metric_sql(
    db_path,
    lock_path,
    category: str,
    item_id: str,
    metric: str,
    value: float
) -> bool:
    """
    Save daily snapshot ke SQLite database.
    Returns True jika data baru disimpan, False jika sudah ada.

    LAZY INIT: Database hanya dibuat saat fungsi ini dipanggil pertama kali.
    """
    db_path = Path(db_path)
    lock_path = Path(lock_path)

    today = date.today().isoformat()
    now = datetime.now().isoformat()

    # Lazy init: Pastikan DB schema ada (hanya saat first call)
    if not db_path.exists():
        init_mon_history_db(db_path)

    # Lock untuk prevent race condition
    with file_lock(lock_path):
        conn = sqlite3.connect(str(db_path))
        try:
            # Check apakah hari ini sudah ada
            cursor = conn.execute(
                """
                SELECT 1 FROM metrics
                WHERE category = ? AND item_id = ? AND metric = ? AND date = ?
                """,
                (category, item_id, metric, today)
            )

            if cursor.fetchone():
                return False  # Sudah ada, skip

            # Insert data baru
            conn.execute(
                """
                INSERT INTO metrics (category, item_id, metric, date, value, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (category, item_id, metric, today, value, now)
            )
            conn.commit()
            return True

        finally:
            conn.close()


def get_metric_value_sql(
    db_path,
    category: str,
    item_id: str,
    metric: str,
    date_iso: str
) -> Optional[float]:
    """Ambil nilai metric pada tanggal tertentu."""
    db_path = Path(db_path)

    if not db_path.exists():
        return None

    conn = sqlite3.connect(str(db_path))
    try:
        cursor = conn.execute(
            """
            SELECT value FROM metrics
            WHERE category = ? AND item_id = ? AND metric = ? AND date = ?
            """,
            (category, item_id, metric, date_iso)
        )
        row = cursor.fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def list_metric_dates_sql(
    db_path,
    category: str,
    item_id: str,
    metric: str
) -> List[str]:
    """List semua tanggal yang ada untuk metric tertentu (sorted)."""
    db_path = Path(db_path)

    if not db_path.exists():
        return []

    conn = sqlite3.connect(str(db_path))
    try:
        cursor = conn.execute(
            """
            SELECT date FROM metrics
            WHERE category = ? AND item_id = ? AND metric = ?
            ORDER BY date ASC
            """,
            (category, item_id, metric)
        )
        return [row[0] for row in cursor.fetchall()]
    finally:
        conn.close()


def get_all_metrics_sql(db_path) -> Dict[str, Any]:
    """
    Get semua data dalam format dict (untuk migration dari JSON).
    Returns: {"category": {"item_id": {"metric": {"date": value}}}}
    """
    db_path = Path(db_path)

    if not db_path.exists():
        return {}

    result: Dict[str, Any] = {}

    conn = sqlite3.connect(str(db_path))
    try:
        cursor = conn.execute("SELECT category, item_id, metric, date, value FROM metrics")

        for row in cursor.fetchall():
            cat, item, met, dt, val = row

            if cat not in result:
                result[cat] = {}
            if item not in result[cat]:
                result[cat][item] = {}
            if met not in result[cat][item]:
                result[cat][item][met] = {}

            result[cat][item][met][dt] = val

        return result
    finally:
        conn.close()


def migrate_json_to_sqlite(json_path, db_path, lock_path) -> int:
    """
    Migrate data dari JSON lama ke SQLite baru.
    Returns: jumlah records yang di-migrate.
    """
    json_path = Path(json_path)
    db_path = Path(db_path)
    lock_path = Path(lock_path)

    if not json_path.exists():
        return 0

    # Load JSON lama (using read_json_safe from this module)
    data = read_json_safe(json_path, default={})
    if not data:
        return 0

    # Pastikan DB schema ada
    if not db_path.exists():
        init_mon_history_db(db_path)

    count = 0
    now = datetime.now().isoformat()

    with file_lock(lock_path):
        conn = sqlite3.connect(str(db_path))
        try:
            for category, items in data.items():
                if not isinstance(items, dict):
                    continue

                for item_id, metrics in items.items():
                    if not isinstance(metrics, dict):
                        continue

                    for metric, dates in metrics.items():
                        if not isinstance(dates, dict):
                            continue

                        for date_iso, value in dates.items():
                            try:
                                # Skip jika sudah ada (idempotent)
                                cursor = conn.execute(
                                    """
                                    SELECT 1 FROM metrics
                                    WHERE category = ? AND item_id = ?
                                    AND metric = ? AND date = ?
                                    """,
                                    (category, item_id, metric, date_iso)
                                )

                                if not cursor.fetchone():
                                    conn.execute(
                                        """
                                        INSERT INTO metrics
                                        (category, item_id, metric, date, value, created_at)
                                        VALUES (?, ?, ?, ?, ?, ?)
                                        """,
                                        (category, item_id, metric, date_iso, float(value), now)
                                    )
                                    count += 1
                            except Exception:
                                # Skip invalid entries
                                continue

            conn.commit()
            return count

        finally:
            conn.close()