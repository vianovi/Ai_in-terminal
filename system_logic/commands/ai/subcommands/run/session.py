import shutil
import subprocess
import time
from typing import List
from system_logic.terminal import ansi

def _proc_running(name: str) -> bool:
    if not shutil.which("pgrep"): return False
    return subprocess.run(["pgrep", "-x", name], stdout=subprocess.DEVNULL).returncode == 0

def _soft_term(name: str) -> tuple:
    if not shutil.which("pkill"): return False, "no_pkill"
    res = subprocess.run(["pkill", "-TERM", "-x", name], stdout=subprocess.DEVNULL)
    if res.returncode == 0: return True, "sent_SIGTERM"
    if res.returncode == 1: return True, "not_running"
    return False, f"rc={res.returncode}"

def extract_targets(profile: dict) -> List[str]:
    vars_ = profile.get("vars", {})
    targets = []
    for k, v in vars_.items():
        if k.endswith("_proc") and v and v not in targets:
            if v.lower() not in ("bash", "sh", "pgrep", "pkill"):
                targets.append(v)
    return targets

def execute_session_control(profile: dict, wait_mode: bool = False, force: bool = False) -> int:
    targets = extract_targets(profile)
    name = profile.get("name", "unknown")

    ansi.print_system(f"SESSION CONTROL: {name}")
    if not targets:
        print("No targets found (vars ending with *_proc).")
        return 0

    print("Targets:")
    for t in targets:
        state = "RUNNING" if _proc_running(t) else "STOPPED"
        col = ansi.c_green() if state == "RUNNING" else ansi.c_dim()
        print(f" - {col}{t} ({state}){ansi.c_reset()}")

    if not force:
        try:
            if input(f"\n{ansi.c_yellow()}Send SIGTERM? [y/N] {ansi.c_reset()}").lower() != 'y':
                return 0
        except: return 0

    for t in targets:
        ok, msg = _soft_term(t)
        mark = ansi.c_green() + "✓" if ok else ansi.c_red() + "✗"
        print(f"{mark}{ansi.c_reset()} {t}: {msg}")

    if wait_mode:
        print("Waiting for exit...")
        for _ in range(20):
            if not [t for t in targets if _proc_running(t)]:
                print(f"{ansi.c_green()}All clear.{ansi.c_reset()}")
                return 0
            time.sleep(0.5)
        print(f"{ansi.c_yellow()}Timeout.{ansi.c_reset()}")
        return 1
    return 0