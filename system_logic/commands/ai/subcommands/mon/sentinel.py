"""
MON Sentinel (The Guardian).
Sistem pengawas yang berjalan bersama 'ai mon live' untuk mencegah
System Freeze/Crash akibat OOM (Out of Memory).

Arsitektur:
    - Sentinel TIDAK membaca data sendiri.
    - Data RAM/Swap dikirim dari dashboard loop via tick(ram_pct, swap_pct).
    - Sentinel hanya mengevaluasi data dan mengambil tindakan.
    - Status dikembalikan ke dashboard untuk ditampilkan di UI.

Analogi:
    mon live: "Aku pantau sistem, kamu tidur dulu.
               Nanti kalau mulai gak beres aku bangunin."
    sentinel: SLEEP → PHASE1 → PHASE2 → PHASE3 (KILL)

Logic Levels:
    SLEEP  : Semua normal, sentinel istirahat.
    PHASE1 : RAM > 87% / Swap > 80%  → Catat log forensik.
    PHASE2 : RAM > 90% & Swap > 85%  → Visual warning ke UI.
    PHASE3 : Swap >= 95% → KILL IMMEDIATE.
             RAM >= 90% held 10s     → KILL.

CHANGELOG:
    - tick() sekarang terima ram_pct, swap_pct dari dashboard (tidak baca psutil sendiri)
    - Hapus time.sleep(1) yang blocking di tick()
    - Tambah SLEEP state — status saat sistem aman
    - Status string lebih informatif untuk UI
    - Deep merge config (tidak shallow lagi)
    - _execute_kill() jalan di thread terpisah agar tidak blocking tick()
"""

from __future__ import annotations

import os
import time
import datetime
import threading
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    import psutil as psutil_type
else:
    psutil_type = None

try:
    import psutil  # type: ignore
except ImportError:
    psutil = None

from . import config


# ==========================================================
# SENTINEL ENGINE
# ==========================================================

class SentinelEngine:
    """
    Mesin pengawas sistem.

    Cara pakai di dashboard:
        sentinel = SentinelEngine()

        # Di main loop (setiap detik):
        result = sentinel.tick(ram_pct, swap_pct)
        # result["status"]      → string untuk ditampilkan di UI
        # result["alert_level"] → 0=SLEEP, 1=PHASE1, 2=PHASE2, 3=PHASE3
    """

    def __init__(self) -> None:
        self.cfg      = config.load_config()
        self.settings = self.cfg.get("sentinel", {})

        # Thread safety
        self._lock = threading.Lock()

        # State
        self.active = self.settings.get("enabled", True)

        # Log path
        log_path_str = self.settings.get("log_path", "").strip()
        if not log_path_str:
            log_path_str = "/tmp/sentinel.log"
        self.log_path = Path(log_path_str)

        # Whitelist
        self.whitelist: set = set(self.settings.get("whitelist", []))
        if psutil:
            try:
                self.whitelist.add(psutil.Process(os.getpid()).name())
                self.whitelist.add("ai-term")
            except (psutil.NoSuchProcess, psutil.AccessDenied, AttributeError):
                pass

        # Thresholds dari config
        t  = self.settings.get("thresholds", {})
        p1 = t.get("phase1_log",  {})
        p2 = t.get("phase2_warn", {})
        p3 = t.get("phase3_kill", {})

        self.limit_p1_ram  = float(p1.get("ram_pct",      87.0))
        self.limit_p1_swap = float(p1.get("swap_pct",     80.0))

        self.limit_p2_ram  = float(p2.get("ram_pct",      90.0))
        self.limit_p2_swap = float(p2.get("swap_pct",     85.0))

        self.limit_p3_ram  = float(p3.get("ram_pct",      90.0))
        self.limit_p3_swap = float(p3.get("swap_pct",     95.0))
        self.ram_hold_sec  = int(p3.get("ram_hold_sec",   10))

        # Runtime state
        self.ram_high_start: Optional[float] = None
        self.last_log_time  = 0.0
        self.is_killing     = False
        self.status_message = "SLEEP"

    # ----------------------------------------------------------
    # FORENSIC LOGGING
    # ----------------------------------------------------------

    def _log_forensic(
        self,
        level:   str,
        ram:     float,
        swap:    float,
        message: str = "",
    ) -> None:
        """Tulis log ke file secara atomic (append + fsync)."""
        if not self.log_path.parent.exists():
            try:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
            except (OSError, PermissionError) as e:
                print(f"Sentinel: Cannot create log dir: {e}")
                return

        ts      = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        top_str = ", ".join(
            f"{n}({m:.0f}MB)" for n, m, _ in self._get_top_hogs(3)
        )
        line = (
            f"[{ts}] [{level}] "
            f"RAM:{ram:.1f}% SWAP:{swap:.1f}% | "
            f"{message} | Hogs: {top_str}\n"
        )

        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())
        except (OSError, IOError, PermissionError) as e:
            print(f"Sentinel Log Error: {e}")

    # ----------------------------------------------------------
    # PROCESS HELPERS
    # ----------------------------------------------------------

    def _get_top_hogs(self, limit: int = 5) -> List[Tuple[str, float, int]]:
        """Return [(name, rss_mb, pid)] sorted by RAM, descending."""
        if not psutil:
            return []
        procs: List[Tuple[str, float, int]] = []
        for p in psutil.process_iter(["name", "memory_info", "pid"]):
            try:
                mem = p.info.get("memory_info")
                if mem is None:
                    continue
                procs.append((
                    p.info.get("name", "unknown"),
                    mem.rss / (1024 * 1024),
                    p.info.get("pid", 0),
                ))
            except (psutil.NoSuchProcess, psutil.AccessDenied, AttributeError, KeyError):
                continue
        procs.sort(key=lambda x: x[1], reverse=True)
        return procs[:limit]

    def _find_victim(self) -> Optional["psutil_type.Process"]:
        """Cari proses RAM terbesar yang tidak ada di whitelist."""
        if not psutil:
            return None

        candidates: List[Tuple["psutil_type.Process", int]] = []
        for p in psutil.process_iter(["name", "memory_info", "pid", "username"]):
            try:
                name     = p.info.get("name", "")
                mem_info = p.info.get("memory_info")
                if not name or mem_info is None:
                    continue
                if name in self.whitelist:
                    continue
                candidates.append((p, mem_info.rss))
            except (psutil.NoSuchProcess, psutil.AccessDenied, AttributeError, KeyError):
                continue

        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates[0][0] if candidates else None

    def _execute_kill(
        self,
        target:     "psutil_type.Process",
        reason:     str,
        ram_pct:    float,
        swap_pct:   float,
    ) -> None:
        """
        Eksekusi kill di thread terpisah agar tidak blocking tick().
        Urutan: SIGTERM → tunggu 3s → SIGKILL jika masih hidup.
        """
        if not target:
            return

        with self._lock:
            if self.is_killing:
                return
            self.is_killing = True

        def _do_kill() -> None:
            try:
                if not target.is_running():
                    self._log_forensic("KILL_SKIP", ram_pct, swap_pct, "Target already terminated")
                    return

                name = target.name()
                pid  = target.pid

                self._log_forensic(
                    "ACTION_REQUIRED", ram_pct, swap_pct,
                    f"Target: {name} (PID {pid}). Reason: {reason}"
                )

                # Soft kill
                target.terminate()
                gone, alive = psutil.wait_procs([target], timeout=3)

                if alive:
                    for proc in alive:
                        try:
                            proc.kill()
                            self._log_forensic("KILL_HARD", ram_pct, swap_pct, f"SIGKILL → {name}")
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            pass
                else:
                    self._log_forensic("KILL_SOFT", ram_pct, swap_pct, f"{name} terminated gracefully")

            except (psutil.NoSuchProcess, psutil.AccessDenied, AttributeError) as e:
                self._log_forensic("KILL_FAIL", ram_pct, swap_pct, f"Failed: {e}")
            finally:
                with self._lock:
                    self.is_killing     = False
                    self.ram_high_start = None  # Reset hold timer

        # Jalankan di thread — tidak blocking tick()
        t = threading.Thread(target=_do_kill, daemon=True)
        t.start()

    # ----------------------------------------------------------
    # TICK — dipanggil dari dashboard loop setiap detik
    # ----------------------------------------------------------

    def tick(self, ram_pct: float, swap_pct: float) -> Dict[str, Any]:
        """
        Evaluasi kondisi sistem berdasarkan data dari dashboard.

        Args:
            ram_pct:  RAM usage percentage (dari psutil di dashboard loop)
            swap_pct: Swap usage percentage (dari psutil di dashboard loop)

        Returns:
            Dict:
                active      (bool)   — apakah sentinel enabled
                alert_level (int)    — 0=SLEEP, 1=PHASE1, 2=PHASE2, 3=PHASE3
                status      (str)    — string untuk ditampilkan di UI
                phase       (str)    — label phase saat ini
        """
        if not psutil or not self.active:
            return {
                "active":      False,
                "alert_level": 0,
                "status":      "DISABLED",
                "phase":       "DISABLED",
            }

        alert_level = 0
        status      = "SLEEP"
        phase       = "SLEEP"

        # --- PHASE 1: LOGGING ---
        if ram_pct > self.limit_p1_ram or swap_pct > self.limit_p1_swap:
            alert_level = 1
            phase       = "PHASE 1"
            status      = f"WATCHING  RAM:{ram_pct:.1f}%  SWAP:{swap_pct:.1f}%"

            current_time = time.time()
            if current_time - self.last_log_time >= 1.0:
                self._log_forensic("PHASE_1", ram_pct, swap_pct, "Threshold exceeded")
                self.last_log_time = current_time

        # --- PHASE 2: WARNING ---
        if ram_pct > self.limit_p2_ram and swap_pct > self.limit_p2_swap:
            alert_level = 2
            phase       = "PHASE 2"
            status      = f"WARNING   RAM:{ram_pct:.1f}%  SWAP:{swap_pct:.1f}%"

        # --- PHASE 3: EXECUTION ---
        kill_reason: Optional[str] = None

        # Trigger A: SWAP panic (immediate)
        if swap_pct >= self.limit_p3_swap:
            kill_reason = f"CRITICAL SWAP ({swap_pct:.1f}%)"
            alert_level = 3
            phase       = "PHASE 3"

        # Trigger B: RAM hold (delayed)
        elif ram_pct >= self.limit_p3_ram:
            current_time = time.time()
            if self.ram_high_start is None:
                self.ram_high_start = current_time

            elapsed   = current_time - self.ram_high_start
            remaining = max(0.0, self.ram_hold_sec - elapsed)

            if elapsed >= self.ram_hold_sec:
                kill_reason = f"CRITICAL RAM ({ram_pct:.1f}% held {elapsed:.1f}s)"
                alert_level = 3
                phase       = "PHASE 3"
            else:
                alert_level = 2
                phase       = "PHASE 2"
                status      = f"HOLD {remaining:.0f}s  RAM:{ram_pct:.1f}% — menunggu konfirmasi"
        else:
            # RAM turun, reset hold timer
            self.ram_high_start = None

        # --- EKSEKUSI ---
        if alert_level == 3 and kill_reason and not self.is_killing:
            phase  = "PHASE 3"
            victim = self._find_victim()
            if victim:
                try:
                    vname = victim.name()
                except Exception:
                    vname = "unknown"
                status = f"KILLING {vname}  ({kill_reason})"
                # Non-blocking — jalan di thread terpisah
                self._execute_kill(victim, kill_reason, ram_pct, swap_pct)
            else:
                self._log_forensic("KILL_ABORT", ram_pct, swap_pct, "No victim found")
                status = "NO VICTIM — semua proses di whitelist"

        elif self.is_killing:
            # Kill sedang berlangsung di thread lain
            phase  = "PHASE 3"
            status = f"KILLING...  RAM:{ram_pct:.1f}%  SWAP:{swap_pct:.1f}%"

        # Update status_message (untuk akses dari luar jika diperlukan)
        self.status_message = status

        return {
            "active":      True,
            "alert_level": alert_level,
            "status":      status,
            "phase":       phase,
            "ram_pct":     ram_pct,
            "swap_pct":    swap_pct,
        }