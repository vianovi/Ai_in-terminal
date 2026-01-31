from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


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
