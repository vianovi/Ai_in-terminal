"""
MON Sentinel (The Guardian) - FIXED VERSION.
Sistem pengawas background yang mencegah System Freeze/Crash akibat OOM (Out of Memory).

Logic Levels:
1. PHASE 1 (Black Box): RAM > 87% / Swap > 50% -> Catat log forensik ke SSD.
2. PHASE 2 (Warning): RAM > 90% & Swap > 78% -> Beri sinyal visual ke UI.
3. PHASE 3 (Execution):
   - Swap >= 87% -> KILL IMMEDIATE.
   - RAM >= 90% (held 5s) -> KILL.

Dependency: psutil (Wajib).

CHANGELOG:
- Fixed: Added Tuple to typing imports
- Fixed: Replaced bare except with specific exception handling
- Fixed: Added type guards for psutil.Process
- Fixed: Added thread safety with Lock
- Fixed: Improved log_path validation
- Fixed: Better race condition handling
- Fixed: More specific exception types
"""

import os
import time
import datetime
import threading
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple, TYPE_CHECKING

# Type checking imports
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
    def __init__(self):
        self.cfg = config.load_config()
        self.settings = self.cfg.get("sentinel", {})

        # Thread safety
        self._lock = threading.Lock()

        # State Variables
        self.active = self.settings.get("enabled", True)

        # Validate and set log path
        log_path_str = self.settings.get("log_path", "")
        if not log_path_str or log_path_str.strip() == "":
            # Default to safe location if empty
            log_path_str = "/tmp/sentinel.log"
        self.log_path = Path(log_path_str)

        self.whitelist = set(self.settings.get("whitelist", []))

        # Tambahkan diri sendiri ke whitelist (Self-Protection)
        if psutil:
            try:
                self_proc = psutil.Process(os.getpid())
                self.whitelist.add(self_proc.name())
                self.whitelist.add("ai-term")
            except (psutil.NoSuchProcess, psutil.AccessDenied, AttributeError) as e:
                # Log error but continue - self-protection is optional
                print(f"Warning: Could not add self to whitelist: {e}")

        # Thresholds (Ambil dari config atau default aman)
        t = self.settings.get("thresholds", {})
        p1 = t.get("phase1_log", {})
        p2 = t.get("phase2_warn", {})
        p3 = t.get("phase3_kill", {})

        self.limit_p1_ram = float(p1.get("ram_pct", 87.0))
        self.limit_p1_swap = float(p1.get("swap_pct", 50.0))

        self.limit_p2_ram = float(p2.get("ram_pct", 90.0))
        self.limit_p2_swap = float(p2.get("swap_pct", 78.0))

        self.limit_p3_ram = float(p3.get("ram_pct", 90.0))
        self.limit_p3_swap = float(p3.get("swap_pct", 87.0))
        self.ram_hold_sec = int(p3.get("ram_hold_sec", 5))

        # Runtime State
        self.ram_high_start: Optional[float] = None  # Timestamp start RAM tinggi
        self.last_log_time = 0.0
        self.status_message = ""  # Untuk ditampilkan di UI dashboard
        self.is_killing = False

    def _log_forensic(self, level: str, ram: float, swap: float, message: str = "") -> None:
        """
        Menulis log ke SSD secara atomic (append + flush).
        Penting untuk forensik jika sistem crash setelah ini.
        """
        if not self.log_path.parent.exists():
            try:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
            except (OSError, PermissionError) as e:
                print(f"Sentinel: Cannot create log directory: {e}")
                return

        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Ambil Top 3 Process saat kejadian
        top_procs = self._get_top_hogs(3)
        hog_str = ", ".join([f"{n}({m:.0f}MB)" for n, m, _ in top_procs])

        line = f"[{ts}] [{level}] RAM:{ram:.1f}% SWAP:{swap:.1f}% | {message} | Hogs: {hog_str}\n"

        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(line)
                f.flush()  # Force write to disk immediately
                os.fsync(f.fileno())  # Ensure OS flushes buffer
        except (OSError, IOError, PermissionError) as e:
            print(f"Sentinel Log Error: {e}")

    def _get_top_hogs(self, limit: int = 5) -> List[Tuple[str, float, int]]:
        """Return list of (name, rss_mb, pid) sorted by RAM usage."""
        if not psutil:
            return []

        procs: List[Tuple[str, float, int]] = []

        for p in psutil.process_iter(['name', 'memory_info', 'pid']):
            try:
                name = p.info.get('name', 'unknown')
                mem_info = p.info.get('memory_info')
                pid = p.info.get('pid', 0)

                if mem_info is None:
                    continue

                rss = mem_info.rss / (1024 * 1024)  # MB
                procs.append((name, rss, pid))
            except (psutil.NoSuchProcess, psutil.AccessDenied, AttributeError, KeyError):
                continue

        # Sort by RSS descending
        procs.sort(key=lambda x: x[1], reverse=True)
        return procs[:limit]

    def _find_victim(self) -> Optional["psutil_type.Process"]:
        """
        Mencari kandidat proses untuk dibunuh.
        Syarat: Penggunaan RAM terbesar DAN tidak ada di Whitelist.
        """
        if not psutil:
            return None

        # Scan semua proses
        candidates: List[Tuple["psutil_type.Process", int]] = []

        for p in psutil.process_iter(['name', 'memory_info', 'pid', 'username']):
            try:
                name = p.info.get('name', '')
                mem_info = p.info.get('memory_info')

                if not name or mem_info is None:
                    continue

                # Cek Whitelist
                if name in self.whitelist:
                    continue

                rss = mem_info.rss
                candidates.append((p, rss))

            except (psutil.NoSuchProcess, psutil.AccessDenied, AttributeError, KeyError):
                continue

        # Sort by RAM usage (Biggest first)
        candidates.sort(key=lambda x: x[1], reverse=True)

        if candidates:
            return candidates[0][0]  # Return psutil.Process object
        return None

    def _execute_kill(self, target: "psutil_type.Process", reason: str) -> None:
        """
        Eksekusi mati.
        Urutan: SIGTERM -> Tunggu 3s -> SIGKILL.
        """
        if not target:
            return

        with self._lock:
            if self.is_killing:
                # Prevent concurrent kills
                return
            self.is_killing = True

        try:
            # Re-check if process still exists (race condition mitigation)
            if not target.is_running():
                self._log_forensic("KILL_SKIP", 0, 0, "Target already terminated")
                return

            name = target.name()
            pid = target.pid

            # Log PRE-KILL
            msg = f"Target identified: {name} (PID {pid}). Reason: {reason}"
            self._log_forensic("ACTION_REQUIRED", 0, 0, msg)
            self.status_message = f"KILLING {name}..."

            # 1. Soft Kill (SIGTERM)
            target.terminate()

            # Tunggu maksimal 3 detik
            gone, alive = psutil.wait_procs([target], timeout=3)

            if alive:
                # 2. Hard Kill (SIGKILL)
                for proc in alive:
                    try:
                        proc.kill()
                        self._log_forensic("KILL_HARD", 0, 0, f"SIGKILL sent to {name}")
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
            else:
                self._log_forensic("KILL_SOFT", 0, 0, f"{name} terminated gracefully")

        except (psutil.NoSuchProcess, psutil.AccessDenied, AttributeError) as e:
            self._log_forensic("KILL_FAIL", 0, 0, f"Failed to kill: {e}")
        finally:
            with self._lock:
                self.is_killing = False
                # Reset timer agar tidak langsung kill proses berikutnya seketika
                self.ram_high_start = None

    def tick(self) -> Dict[str, Any]:
        """
        Dipanggil setiap detik oleh Dashboard UI loop.
        Mengembalikan status untuk ditampilkan di UI.
        """
        if not psutil or not self.active:
            return {"active": False, "msg": "Sentinel Disabled/No Lib"}

        # 1. Baca Sensor
        try:
            vm = psutil.virtual_memory()
            sw = psutil.swap_memory()
            ram_pct = float(vm.percent)
            swap_pct = float(sw.percent)
        except (AttributeError, OSError) as e:
            return {"active": True, "msg": f"Sensor Error: {e}"}

        status = "OK"
        alert_level = 0  # 0=Ok, 1=Log, 2=Warn, 3=Kill

        # --- PHASE 1: LOGGING (The Recorder) ---
        # "Fase 1 itu dia mulai mengawasi dan mencatat terus"
        if ram_pct > self.limit_p1_ram or swap_pct > self.limit_p1_swap:
            alert_level = 1
            status = "LOGGING"
            # Throttle log writing (misal max 1x per detik)
            current_time = time.time()
            if current_time - self.last_log_time >= 1.0:
                self._log_forensic("PHASE_1", ram_pct, swap_pct, "Threshold exceeded")
                self.last_log_time = current_time

        # --- PHASE 2: WARNING (Visual Alert) ---
        if ram_pct > self.limit_p2_ram and swap_pct > self.limit_p2_swap:
            alert_level = 2
            status = "WARNING"

        # --- PHASE 3: EXECUTION (The Killer) ---
        kill_reason: Optional[str] = None

        # Trigger A: SWAP Panic (Immediate)
        if swap_pct >= self.limit_p3_swap:
            kill_reason = f"CRITICAL SWAP ({swap_pct:.1f}%)"
            alert_level = 3

        # Trigger B: RAM Hold (Delayed)
        elif ram_pct >= self.limit_p3_ram:
            current_time = time.time()

            if self.ram_high_start is None:
                self.ram_high_start = current_time

            elapsed = current_time - self.ram_high_start
            remaining = max(0.0, self.ram_hold_sec - elapsed)

            if elapsed >= self.ram_hold_sec:
                kill_reason = f"CRITICAL RAM ({ram_pct:.1f}% held for {elapsed:.1f}s)"
                alert_level = 3
            else:
                status = f"HOLD {remaining:.0f}s"
                alert_level = 2
        else:
            # Reset timer jika RAM turun
            self.ram_high_start = None

        # EKSEKUSI
        if alert_level == 3 and kill_reason:
            status = "KILLING..."
            victim = self._find_victim()
            if victim:
                self._execute_kill(victim, kill_reason)
                # Beri waktu sistem napas sedikit setelah kill
                time.sleep(1)
            else:
                self._log_forensic(
                    "KILL_ABORT",
                    ram_pct,
                    swap_pct,
                    "No non-whitelisted victim found!"
                )
                status = "NO VICTIM"

        return {
            "active": True,
            "status": status,
            "alert_level": alert_level,
            "ram_pct": ram_pct,
            "swap_pct": swap_pct,
        }