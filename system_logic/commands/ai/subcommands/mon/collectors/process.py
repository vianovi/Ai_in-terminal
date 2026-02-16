"""
MON Process Collector.
Membaca: Top processes by RAM/CPU, Process monster detection.
"""

from typing import Dict, Any, List, Optional

try:
    import psutil  # type: ignore
except ImportError:
    psutil = None


def collect_process_stats(limit: int = 10) -> Dict[str, Any]:
    """
    Mengambil daftar proses dengan penggunaan RAM/CPU tertinggi.

    Args:
        limit: Jumlah proses yang dikembalikan

    Returns:
        Dict berisi: ok, processes (list of dict), count
    """
    if not psutil:
        return {
            "ok": False,
            "error": "psutil not available"
        }

    try:
        procs = []
        for p in psutil.process_iter(['pid', 'name', 'memory_info', 'cpu_percent', 'username']):
            try:
                info = p.info
                rss = info.get('memory_info').rss if info.get('memory_info') else 0
                procs.append({
                    "pid": info.get('pid'),
                    "name": info.get('name', 'unknown'),
                    "rss_bytes": rss,
                    "rss_mb": rss / (1024 * 1024),
                    "cpu_pct": info.get('cpu_percent', 0.0),
                    "username": info.get('username', 'unknown')
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied, AttributeError):
                continue

        # Sort by RSS descending
        procs.sort(key=lambda x: x['rss_bytes'], reverse=True)

        return {
            "ok": True,
            "processes": procs[:limit],
            "count": len(procs)
        }

    except Exception as e:
        return {
            "ok": False,
            "error": str(e)
        }


def find_process_monster(whitelist: Optional[set] = None) -> Optional[Dict[str, Any]]:
    """
    Mencari proses dengan RAM usage terbesar (untuk OOM killer).

    Args:
        whitelist: Set nama proses yang tidak boleh dikill

    Returns:
        Dict info proses atau None jika tidak ada kandidat
    """
    if not psutil:
        return None

    if whitelist is None:
        whitelist = set()

    try:
        candidates = []
        for p in psutil.process_iter(['pid', 'name', 'memory_info']):
            try:
                name = p.info.get('name', '')
                if name in whitelist:
                    continue

                mem_info = p.info.get('memory_info')
                if mem_info:
                    rss = mem_info.rss
                    candidates.append({
                        "pid": p.info.get('pid'),
                        "name": name,
                        "rss_bytes": rss,
                        "rss_mb": rss / (1024 * 1024),
                        "process": p
                    })
            except (psutil.NoSuchProcess, psutil.AccessDenied, AttributeError):
                continue

        if not candidates:
            return None

        # Sort by RSS
        candidates.sort(key=lambda x: x['rss_bytes'], reverse=True)
        return candidates[0]

    except Exception:
        return None