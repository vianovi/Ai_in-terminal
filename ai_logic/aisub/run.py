"""
AI Runbook Runner (Autonomous Workflow Executor).
Menjalankan profil instruksi shell (JSON/JSON5) dengan pengamanan.

Fitur (v1+):
- Supports JSON5 parsing (comments/trailing commas) jika python3-json5 terpasang.
- Standard JSON fallback (tanpa komentar) jika json5 tidak ada.
- First-run bootstrap: jika file profile tidak ada, sistem otomatis membuat template TANPA error.
- Safety gates: deny-list destruktif, blok multiline injection, konfirmasi untuk sudo/high-risk/risky-pattern.
- Variable substitution {{var}} dengan quoting aman (shlex.quote) untuk mencegah injection.
- Preflight checks: OS, command existence, DNS resolve (network).
- Execution history logging (last 50).

Usage:
    ai run                  : Help + lokasi profile.
    ai run list             : List profiles.
    ai run show <name>      : Tampilkan detail profile.
    ai run <name> [--dry]   : Jalankan profile (atau dry-run).
    ai run validate         : Validasi schema & profiling ringkas.
    ai run history          : Tampilkan history singkat (last 10).
"""

from __future__ import annotations

import datetime
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Any, Optional, Tuple

from ai_logic.ui import ansi

# --- COMMON IMPORT PATH ---
try:
    # Disarankan: RUN_PROFILE_PATH mengarah ke file: ~/.config/ai-term/run_profile.json
    from ai_logic.common import (
        RUN_PROFILE_PATH,
        DENY_SUBSTRINGS as COMMON_DENY_SUBSTRINGS,
        RISKY_PATTERNS as COMMON_RISKY_PATTERNS,
        run_shell_command,
    )
except Exception:
    # Fallback aman bila common belum tersedia / belum updated.
    RUN_PROFILE_PATH = Path.cwd() / "workspace" / "run_profile.json"
    COMMON_DENY_SUBSTRINGS = ["rm -rf /", "mkfs", "dd if=", "shutdown", "reboot", "poweroff"]
    COMMON_RISKY_PATTERNS = [r"\brm\s+-rf\b", r"\bdd\s+if=", r"\bmkfs(\.\w+)?\b"]
    def run_shell_command(cmd: str, *, cfg=None, check: bool=False, env=None):
        return subprocess.run(cmd, shell=True, executable="/bin/sh", check=check, env=env)

# --- SOFT DEPENDENCY: JSON5 ---
# JSON5 memungkinkan komentar, trailing comma, dsb. Jika tidak ada, fallback JSON standar.
HAS_JSON5 = False
try:
    import json5  # type: ignore

    HAS_JSON5 = True
except Exception:
    HAS_JSON5 = False


# ==========================================================
# CONFIG & CONSTANTS
# ==========================================================

def _resolve_profile_file(run_profile_path: Path) -> Path:
    """
    RUN_PROFILE_PATH bisa berupa:
    - file .json / .json5
    - directory (legacy)
    """
    p = Path(run_profile_path)
    if p.suffix.lower() in (".json", ".json5"):
        return p
    # Kalau diarahkan ke folder, defaultkan ke run_profile.json
    return p / "run_profile.json"


PROFILE_FILE = _resolve_profile_file(RUN_PROFILE_PATH)
HISTORY_FILE = PROFILE_FILE.parent / "run_history.json"

# Deny-list destruktif: blok total (hard-stop).
# NOTE: Ini bukan sandbox; tetapi deny-list mencegah kasus-kasus paling berbahaya.
DENY_SUBSTRINGS = list(COMMON_DENY_SUBSTRINGS)

# Pola berisiko: tidak diblok total, tapi WAJIB konfirmasi walau risk=low.
RISKY_PATTERNS = list(COMMON_RISKY_PATTERNS)
_RISKY_REGEX = re.compile("|".join(f"(?:{p})" for p in RISKY_PATTERNS), re.IGNORECASE)

# Multiline / control injection
_BAD_CONTROL_REGEX = re.compile(r"[\r\n]")


# ==========================================================
# 1) BOOTSTRAP: TEMPLATE, LOADER, SCHEMA
# ==========================================================

def _ensure_profile_dir() -> None:
    try:
        PROFILE_FILE.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        # Permission / sandbox environments: ignore here, handled on write
        pass


def _template_payload() -> dict[str, Any]:
    """
    Template sengaja pure-JSON (valid JSON standar),
    tapi jika json5 terpasang, user bebas menambahkan komentar/trailing comma nanti.
    """
    return {
        "version": 1,
        "profiles": [
            {
                "name": "check_env",
                "description": "Contoh: Cek kesiapan tools development.",
                "requires": {
                    "os": ["fedora", "linux"],
                    "commands": ["python3", "git"],
                    "network": False,
                },
                "vars": {
                    "workspace_dir": str(Path.cwd()),
                },
                "steps": [
                    {"title": "Cek Python", "cmd": "python3 --version", "risk": "low"},
                    {"title": "Cek Git", "cmd": "git --version", "risk": "low"},
                ],
            },
            {
                "name": "setup_dev",
                "description": "Setup environment dev: deps + git hooks + ollama check.",
                "requires": {
                    "os": ["fedora", "linux"],
                    "commands": ["git", "python3"],
                    "network": True,
                },
                "vars": {},
                "steps": [
                    {"title": "Check git", "cmd": "git --version", "risk": "low"},
                    {
                        "title": "Install deps",
                        "cmd": "sudo dnf install -y ripgrep fd-find",
                        "risk": "medium",
                        "confirm": True,
                    },
                    {"title": "Ollama list", "cmd": "ollama list", "risk": "low", "ignore_fail": True},
                ],
            },
        ],
    }


def _create_template_if_missing() -> bool:
    """
    First-run behavior:
    - Jika PROFILE_FILE tidak ada: buat template, tampilkan keterangan, lalu lanjut (tanpa ERROR).
    Return True jika template dibuat, False jika tidak.
    """
    if PROFILE_FILE.exists():
        return False

    _ensure_profile_dir()

    payload = _template_payload()
    try:
        with open(PROFILE_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
    except Exception as e:
        ansi.print_brief_error(f"Gagal membuat template profile (IO/permission): {e}")
        return False

    # Informasi ramah (bukan error)
    ansi.print_system("AI RUNBOOK (BOOTSTRAP MODE)")
    print(f"Sistem mendeteksi file profile belum ada, jadi aku otomatis membuat template.")
    print(f"Lokasi file : {ansi.c_cyan()}{PROFILE_FILE}{ansi.c_reset()}")
    if HAS_JSON5:
        print(f"Engine      : {ansi.c_green()}JSON5 tersedia{ansi.c_reset()} (komentar & trailing comma bisa dipakai).")
    else:
        print(
            f"Engine      : {ansi.c_yellow()}JSON standar{ansi.c_reset()} (tanpa komentar). "
            f"Untuk JSON5: {ansi.c_green()}sudo dnf install python3-json5{ansi.c_reset()}"
        )
    print("Isi         : Template profiles sudah dibuat. Silakan edit sesuai kebutuhan.\n")
    return True


def _parse_profiles_text(raw: str) -> Tuple[dict[str, Any], str]:
    """
    Parse raw file content.
    Return: (data, error_message)
    """
    # JSON5 engine (jika ada) aman untuk parse JSON standar juga.
    if HAS_JSON5:
        try:
            data = json5.loads(raw)  # type: ignore[name-defined]
            if isinstance(data, dict):
                return data, ""
            return {}, "Schema salah: root harus object/dict."
        except Exception as e:
            return {}, f"Syntax Error (JSON5): {e}"

    # Fallback JSON standar
    try:
        data2 = json.loads(raw)
        if isinstance(data2, dict):
            return data2, ""
        return {}, "Schema salah: root harus object/dict."
    except json.JSONDecodeError as e:
        msg = f"Gagal parsing file JSON: {e}\n\n"
        msg += f"{ansi.c_yellow()}NOTE:{ansi.c_reset()} Library JSON5 tidak ditemukan.\n"
        msg += "Mode saat ini: JSON standar (tidak support komentar // atau trailing comma).\n\n"
        msg += "Saran perbaikan:\n"
        msg += "1) Hapus komentar (// atau /* */) dari file run_profile.json\n"
        msg += f"2) Atau install JSON5 via DNF: {ansi.c_green()}sudo dnf install python3-json5{ansi.c_reset()}\n"
        return {}, msg


def _validate_root_schema(data: dict[str, Any]) -> Tuple[bool, str]:
    """
    Validasi minimal schema agar runner tidak crash.
    """
    if "profiles" not in data or not isinstance(data.get("profiles"), list):
        return False, "Schema salah: root harus punya key 'profiles' berupa List/Array."
    v = data.get("version", 1)
    if not isinstance(v, int):
        return False, "Schema salah: 'version' harus integer."
    return True, ""


def _load_profiles() -> Tuple[dict[str, Any], str]:
    """
    Load + parse file profile.
    First-run: buat template otomatis, tidak mengembalikan ERROR hanya karena file belum ada.
    Output: (data_dict, error_message)
    """
    _create_template_if_missing()

    try:
        with open(PROFILE_FILE, "r", encoding="utf-8") as f:
            raw_content = f.read()
    except Exception as e:
        return {}, f"Gagal membaca file profile (Permission/IO Error): {e}"

    data, perr = _parse_profiles_text(raw_content)
    if perr:
        return {}, perr

    ok, verr = _validate_root_schema(data)
    if not ok:
        return {}, verr

    return data, ""


# ==========================================================
# 2) HISTORY LOGGING
# ==========================================================

def _log_history(
    profile_name: str,
    result: str,
    exit_code: int,
    step_index: Optional[int] = None,
    note: str = "",
) -> None:
    """
    Simpan riwayat eksekusi (best effort).
    result: success | failed | cancelled | fail_reqs | fail_vars | fail_safety
    """
    try:
        _ensure_profile_dir()

        hist_data: list[dict[str, Any]] = []
        if HISTORY_FILE.exists():
            try:
                with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    if isinstance(loaded, list):
                        hist_data = loaded
            except Exception:
                hist_data = []

        entry = {
            "time": datetime.datetime.now().isoformat(timespec="seconds"),
            "profile": profile_name,
            "result": result,
            "code": int(exit_code),
            "step": step_index,
            "note": note[:300],
        }
        hist_data.append(entry)

        # Keep last 50 logs
        if len(hist_data) > 50:
            hist_data = hist_data[-50:]

        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(hist_data, f, indent=2)

    except Exception:
        # Logging must never crash runner
        pass


def _read_history(limit: int = 10) -> list[dict[str, Any]]:
    try:
        if not HISTORY_FILE.exists():
            return []
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
            if not isinstance(d, list):
                return []
        return d[-max(1, int(limit)) :]
    except Exception:
        return []


# ==========================================================
# 3) PREFLIGHT & SAFETY
# ==========================================================

def _read_os_release() -> str:
    try:
        return Path("/etc/os-release").read_text(encoding="utf-8", errors="replace").lower()
    except Exception:
        return ""


def _run_preflight_checks(reqs: dict[str, Any]) -> bool:
    """
    Cek dependencies (OS, Network, Command) sebelum jalan.
    - OS check: token match di /etc/os-release (id= / id_like=)
    - Network check: DNS resolve test (bukan HTTP test)
    """
    # 1) OS Check
    if "os" in reqs and isinstance(reqs["os"], list):
        wanted = [str(x).lower().strip() for x in reqs["os"] if str(x).strip()]
        if wanted:
            content = _read_os_release()
            matched = False
            if content:
                for w in wanted:
                    # Match id= / id_like= / quoted token
                    if f"id={w}" in content or f"id_like={w}" in content or f'"{w}"' in content:
                        matched = True
                        break
            else:
                # Kalau tidak bisa baca os-release, untuk fleksibilitas dev: anggap pass.
                matched = True

            if not matched:
                ansi.print_brief_error(f"Requirement fail: OS saat ini tidak cocok dengan {wanted}")
                return False

    # 2) Command existence
    if "commands" in reqs and isinstance(reqs["commands"], list):
        for tool in reqs["commands"]:
            t = str(tool).strip()
            if not t:
                continue
            if not shutil.which(t):
                ansi.print_brief_error(f"Requirement fail: Command '{t}' tidak ditemukan di PATH.")
                print(f"Saran: install '{t}' terlebih dahulu.")
                return False

    # 3) Network check (DNS resolve)
    if reqs.get("network") is True:
        try:
            socket.gethostbyname("google.com")
        except Exception:
            ansi.print_brief_error("Requirement fail: DNS resolve gagal (cek koneksi / resolver).")
            return False

    return True


def _is_multiline_or_empty(cmd: str) -> Optional[str]:
    c = cmd.strip()
    if not c:
        return "Command string kosong."
    if _BAD_CONTROL_REGEX.search(c):
        return "Command mengandung newline/control (indikasi injection multiline)."
    return None


def _check_deny_list(cmd: str) -> Optional[str]:
    c_clean = cmd.lower().strip()
    for banned in DENY_SUBSTRINGS:
        if banned in c_clean:
            return f"Contains banned unsafe syntax: '{banned}'"
    return None


def _needs_extra_confirm(cmd: str) -> bool:
    return bool(_RISKY_REGEX.search(cmd))


def _is_sudo(cmd: str) -> bool:
    # Heuristik: token "sudo" ada sebagai token (bukan substring).
    toks = cmd.strip().split()
    return "sudo" in toks


def _confirm(prompt: str) -> bool:
    try:
        resp = input(prompt).strip().lower()
        return resp == "y"
    except KeyboardInterrupt:
        return False


def _safe_quote_var_value(val: Any) -> str:
    """
    Variable substitution: kita treat variables sebagai value (bukan fragment command).
    Selalu quote via shlex.quote untuk mencegah injection.
    """
    s = str(val)
    # Normalisasi kontrol: tolak newline untuk keamanan
    if _BAD_CONTROL_REGEX.search(s):
        raise ValueError("Variable mengandung newline/control characters.")
    return shlex.quote(s)


_VAR_PATTERN = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def _replace_vars(cmd_str: str, variables: dict[str, Any]) -> Tuple[str, bool, str]:
    """
    Mengganti placeholder {{key}} dengan value yang di-quote aman.
    Return: (new_cmd, ok, note)
    """
    missing: list[str] = []
    notes: list[str] = []

    def repl(m: re.Match[str]) -> str:
        key = m.group(1)
        if key not in variables:
            missing.append(key)
            return m.group(0)
        try:
            return _safe_quote_var_value(variables[key])
        except Exception as e:
            notes.append(f"{key}: {e}")
            return m.group(0)

    new_cmd = _VAR_PATTERN.sub(repl, cmd_str)

    if missing:
        ansi.print_brief_error(f"Missing variables: {missing}")
        return cmd_str, False, "missing_vars"

    if notes:
        ansi.print_brief_error("Variable value tidak valid untuk substitution (mengandung kontrol/newline).")
        return cmd_str, False, "bad_var_value"

    return new_cmd, True, ""


# ==========================================================
# 4) ACTIONS: LIST, SHOW, RUN, VALIDATE, HISTORY
# ==========================================================

def _find_profile(data: dict[str, Any], name: str) -> Optional[dict[str, Any]]:
    profiles = data.get("profiles", [])
    for p in profiles:
        if isinstance(p, dict) and p.get("name") == name:
            return p
    return None


def cmd_list_profiles(data: dict[str, Any]) -> int:
    profiles = data.get("profiles", [])
    if not profiles:
        ansi.print_system("AI RUNBOOK")
        print("File profile valid, tetapi list 'profiles' kosong.")
        return 0

    ansi.print_system(f"AVAILABLE PROFILES ({len(profiles)})")
    if HAS_JSON5:
        print(f"{ansi.c_dim()}Engine: JSON5 parsing aktif (komentar & trailing comma OK).{ansi.c_reset()}")
    else:
        print(f"{ansi.c_dim()}Engine: JSON standar (tanpa komentar). JSON5: sudo dnf install python3-json5{ansi.c_reset()}")

    print(f"\n{ansi.c_dim()}{'NAME':<18} {'STEPS':<5} DESCRIPTION{ansi.c_reset()}")
    print("-" * 72)

    for p in profiles:
        if not isinstance(p, dict):
            continue
        name = str(p.get("name") or "unnamed")
        desc = str(p.get("description") or "(No description)")
        steps = p.get("steps", [])
        nsteps = len(steps) if isinstance(steps, list) else 0
        print(f" {ansi.c_cyan()}{name:<18}{ansi.c_reset()} {nsteps:<5} {desc}")

    print(f"\nConfig Path: {ansi.c_cyan()}{PROFILE_FILE}{ansi.c_reset()}")
    print(f"Usage      : {ansi.c_bold()}ai run <name> [--dry]{ansi.c_reset()}")
    return 0


def cmd_show_profile(profile: dict[str, Any]) -> int:
    name = str(profile.get("name") or "unnamed")
    ansi.print_system(f"PROFILE: {name}")

    desc = str(profile.get("description") or "-")
    reqs = profile.get("requires", {})
    vars_ = profile.get("vars", {})

    print(f"Description : {desc}")
    print(f"Requires    : {reqs if isinstance(reqs, dict) else {}}")
    print(f"Vars        : {vars_ if isinstance(vars_, dict) else {}}")
    print("\n[ Execution Plan ]")

    steps = profile.get("steps", [])
    if not isinstance(steps, list) or not steps:
        print("  (No steps defined)")
        return 0

    for i, s in enumerate(steps, 1):
        if not isinstance(s, dict):
            continue
        cmd = str(s.get("cmd") or "")
        title = str(s.get("title") or cmd or f"Step {i}")

        risk = str(s.get("risk") or "low").lower().strip()
        explicit_confirm = bool(s.get("confirm", False))
        needs_sudo = _is_sudo(cmd)

        flags = []
        col = ansi.c_green()

        if needs_sudo:
            flags.append("SUDO")
            col = ansi.c_yellow()

        if risk in ("high", "critical") or explicit_confirm or _needs_extra_confirm(cmd):
            flags.append("CONFIRM")
            col = ansi.c_red()

        flag_txt = f"[{', '.join(flags)}] " if flags else ""
        print(f" {i:>2}. {col}{flag_txt}{title}{ansi.c_reset()}")
        print(f"     $ {cmd}")

    return 0


def cmd_validate(data: dict[str, Any]) -> int:
    """
    Validasi ringan agar user cepat tahu schema profile aman untuk dieksekusi.
    """
    profiles = data.get("profiles", [])
    ansi.print_system("VALIDATE RUNBOOK PROFILE")
    ok = True

    if not isinstance(profiles, list):
        ansi.print_brief_error("Schema invalid: 'profiles' bukan list.")
        return 2

    if not profiles:
        print("OK: profiles list kosong (tidak ada yang dieksekusi).")
        return 0

    bad = 0
    for p in profiles:
        if not isinstance(p, dict):
            bad += 1
            continue
        n = p.get("name")
        if not n or not isinstance(n, str):
            bad += 1
            continue
        steps = p.get("steps", [])
        if steps is None:
            bad += 1
            continue
        if not isinstance(steps, list):
            bad += 1
            continue

    if bad:
        ok = False
        ansi.print_brief_error(f"Ada {bad} item profile bermasalah (cek field name/steps).")

    if ok:
        print(f"{ansi.c_green()}OK:{ansi.c_reset()} Schema terlihat valid.")
    print(f"Config Path: {ansi.c_cyan()}{PROFILE_FILE}{ansi.c_reset()}")
    return 0 if ok else 2


def cmd_history() -> int:
    ansi.print_system("RUN HISTORY (LAST 10)")
    hist = _read_history(limit=10)
    if not hist:
        print("Belum ada riwayat.")
        print(f"Path: {ansi.c_dim()}{HISTORY_FILE}{ansi.c_reset()}")
        return 0

    print(f"{ansi.c_dim()}{'TIME':<20} {'PROFILE':<16} {'RESULT':<12} CODE{ansi.c_reset()}")
    print("-" * 72)
    for e in hist:
        t = str(e.get("time") or "")[:19]
        p = str(e.get("profile") or "")[:16]
        r = str(e.get("result") or "")[:12]
        c = str(e.get("code") if e.get("code") is not None else "")
        print(f"{t:<20} {ansi.c_cyan()}{p:<16}{ansi.c_reset()} {r:<12} {c}")
    print(f"\nPath: {ansi.c_dim()}{HISTORY_FILE}{ansi.c_reset()}")
    return 0


def cmd_exec_run(profile: dict[str, Any], is_dry_run: bool) -> int:
    name = str(profile.get("name") or "unnamed")

    ansi.print_system(f"RUNNING: {name}" + (" (DRY RUN)" if is_dry_run else ""))
    if is_dry_run:
        print(f"{ansi.c_dim()}Note: Tidak ada command yang akan dieksekusi.{ansi.c_reset()}")

    # 1) REQUIREMENTS
    reqs = profile.get("requires", {})
    if isinstance(reqs, dict) and reqs:
        print("• Checking pre-flight requirements... ", end="")
        if not _run_preflight_checks(reqs):
            _log_history(name, "fail_reqs", 2, note="preflight_failed")
            return 2
        print(f"{ansi.c_green()}OK{ansi.c_reset()}")

    # 2) VARIABLES
    variables = profile.get("vars", {})
    if not isinstance(variables, dict):
        variables = {}

    # 3) STEP LOOP
    steps = profile.get("steps", [])
    if not isinstance(steps, list):
        ansi.print_brief_error("Schema invalid: steps bukan list.")
        _log_history(name, "failed", 2, note="steps_not_list")
        return 2

    total = len(steps)
    if total == 0:
        print("Tidak ada steps. Selesai tanpa eksekusi.")
        _log_history(name, "success", 0, note="no_steps")
        return 0

    for i, step in enumerate(steps, 1):
        if not isinstance(step, dict):
            ansi.print_brief_error(f"Step #{i} invalid (bukan object).")
            _log_history(name, "failed", 2, step_index=i, note="step_not_object")
            return 2

        raw_cmd = str(step.get("cmd") or "").strip()
        title = str(step.get("title") or raw_cmd or f"Step {i}")
        risk = str(step.get("risk") or "low").lower().strip()
        explicit_confirm = bool(step.get("confirm", False))
        ignore_fail = bool(step.get("ignore_fail", False))

        prefix = f"[{i}/{total}]"
        print(f"\n{ansi.c_cyan()}{prefix} {title}{ansi.c_reset()}")

        # A) Basic command sanity
        bad = _is_multiline_or_empty(raw_cmd)
        if bad:
            ansi.print_brief_error(f"Invalid command: {bad}")
            _log_history(name, "fail_safety", 3, step_index=i, note="multiline_or_empty")
            return 3

        # B) Variable substitution (safe-quoted)
        final_cmd, var_ok, var_note = _replace_vars(raw_cmd, variables)
        if not var_ok:
            _log_history(name, "fail_vars", 2, step_index=i, note=var_note)
            return 2

        print(f"  Command: {ansi.c_bold()}{final_cmd}{ansi.c_reset()}")

        # C) Hard safety deny-list
        deny_reason = _check_deny_list(final_cmd)
        if deny_reason:
            ansi.print_brief_error(f"SAFETY BLOCK: {deny_reason}")
            _log_history(name, "fail_safety", 3, step_index=i, note="deny_list")
            return 3

        # D) Risk detection
        sudo = _is_sudo(final_cmd)
        risky_pattern = _needs_extra_confirm(final_cmd)
        is_high_risk = risk in ("high", "critical")
        is_risky = is_high_risk or sudo or explicit_confirm or risky_pattern

        # E) Dry-run
        if is_dry_run:
            if is_risky:
                print(f"  {ansi.c_yellow()}⚠ Would ask confirmation here.{ansi.c_reset()}")
            print(f"  {ansi.c_dim()}(Dry-run: skipped execution){ansi.c_reset()}")
            continue

        # F) Confirmation (if needed)
        if is_risky:
            reason_bits = []
            if sudo:
                reason_bits.append("sudo")
            if is_high_risk:
                reason_bits.append(f"risk={risk}")
            if explicit_confirm:
                reason_bits.append("confirm=true")
            if risky_pattern and "rm -rf" in final_cmd.lower():
                reason_bits.append("risky-pattern(rm -rf)")
            elif risky_pattern:
                reason_bits.append("risky-pattern")

            reason = ", ".join(reason_bits) if reason_bits else "risk"
            ok = _confirm(f"  {ansi.c_yellow()}⚠ Konfirmasi diperlukan ({reason}). Lanjutkan? [y/N] {ansi.c_reset()}")
            if not ok:
                print("  Dibatalkan oleh user.")
                _log_history(name, "cancelled", 0, step_index=i, note="user_cancel")
                return 0

        # G) Execute
        try:
            t0 = time.time()
            exit_code = int(run_shell_command(final_cmd, cfg={}).returncode)
            dt = time.time() - t0

            if exit_code != 0:
                ansi.print_brief_error(f"Step gagal (exit code: {exit_code})")
                if not ignore_fail:
                    print(f"Stopping execution at step {i}.")
                    _log_history(name, "failed", exit_code, step_index=i, note="stop_on_fail")
                    return int(exit_code)
                print(f"{ansi.c_yellow()}Warning:{ansi.c_reset()} ignore_fail=true, lanjut ke step berikutnya.")
                _log_history(name, "failed", exit_code, step_index=i, note="ignored_fail")
            else:
                print(f"  {ansi.c_green()}✓ Sukses ({dt:.2f}s){ansi.c_reset()}")

        except KeyboardInterrupt:
            print("\nInterrupted.")
            _log_history(name, "cancelled", 130, step_index=i, note="keyboard_interrupt")
            return 130
        except Exception as e:
            ansi.print_brief_error(f"System error: {e}")
            _log_history(name, "failed", 1, step_index=i, note="system_error")
            return 1

    ansi.print_system(f"✓ RUN PROFILE '{name}' COMPLETED.")
    _log_history(name, "success", 0, note="completed")
    return 0


# ==========================================================
# 5) MAIN ROUTER HANDLER
# ==========================================================

def _print_help() -> None:
    ansi.print_info("AI Runbook (Automation)")
    print("  ai run list            : Lihat daftar profil tersedia.")
    print("  ai run show <name>     : Lihat detail langkah-langkah.")
    print("  ai run <name>          : Jalankan profil.")
    print("  ai run <name> --dry    : Simulasi run (dry-run).")
    print("  ai run validate        : Validasi schema profile.")
    print("  ai run history         : Lihat riwayat eksekusi.")
    print("\nConfig Path:")
    print(f"  {PROFILE_FILE}")
    if HAS_JSON5:
        print(f"Engine: {ansi.c_green()}JSON5 aktif{ansi.c_reset()} (komentar & trailing comma OK)")
    else:
        print(f"Engine: {ansi.c_dim()}JSON standar{ansi.c_reset()} (install JSON5: sudo dnf install python3-json5)")


def handle(argv: list[str], cfg: dict) -> int:
    """
    Subcommand router: ai run <action> [args]
    Actions:
      list | show | validate | history | <profile_name>
    """
    # cfg disediakan untuk konsistensi signature modul command (future-proof)
    _ = cfg

    data, error = _load_profiles()
    if error:
        ansi.print_brief_error(error)
        return 2

    if not argv:
        _print_help()
        return 0

    action = str(argv[0]).lower().strip()

    if action in ("help", "-h", "--help"):
        _print_help()
        return 0

    # validate
    if action == "validate":
        return cmd_validate(data)

    # history
    if action == "history":
        return cmd_history()

    # list
    if action == "list":
        return cmd_list_profiles(data)

    # show
    if action == "show":
        if len(argv) < 2:
            ansi.print_brief_error("Gunakan: ai run show <profile_name>")
            return 2
        p_name = argv[1]
        p = _find_profile(data, p_name)
        if not p:
            ansi.print_brief_error(f"Profil '{p_name}' tidak ditemukan.")
            return 2
        return cmd_show_profile(p)

    # default: run profile by name
    target_name = action
    is_dry = "--dry" in argv

    p = _find_profile(data, target_name)
    if p:
        return cmd_exec_run(p, is_dry)

    ansi.print_brief_error(f"Perintah atau profil '{action}' tidak ditemukan.")
    print("Gunakan 'ai run list' untuk melihat profil yang tersedia.")
    return 2
