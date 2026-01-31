"""
AI Runbook Runner (Autonomous Workflow Executor) - FINAL / GOLD RELEASE
======================================================================

Menjalankan profil instruksi shell (JSON/JSON5) dengan pengamanan.

Fitur:
- Supports JSON5 parsing (comments/trailing commas) jika python3-json5 terpasang.
- Standard JSON fallback jika json5 tidak ada.
- First-run bootstrap: jika file profile tidak ada, otomatis membuat template tanpa error.
- Safety gates:
  - Hard deny-list destruktif (hard stop)
  - Blok multiline/control injection
  - Konfirmasi untuk sudo / high-risk / confirm=true / risky-pattern
- Variable substitution {{var}}: value-only + shlex.quote (anti injection).
- Preflight checks: OS, command existence, DNS resolve (network).
- Execution history logging (last 50).
- Output modern: jelas, rapi, informatif.

Usage:
    ai run                  : Help + lokasi profile.
    ai run list             : List profiles.
    ai run show <name>      : Tampilkan detail profile.
    ai run <name> [--dry]   : Jalankan profile (atau dry-run).
    ai run validate         : Validasi schema.
    ai run history          : Tampilkan history singkat (last 10).
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Tuple, Dict, List

from ai_logic.ui import ansi

# ---------------------------------------------------------------------
# COMMON IMPORT PATH (recommended)
# ---------------------------------------------------------------------
try:
    # RUN_PROFILE_PATH biasanya: ~/.config/ai-term/run_profile.json (atau .json5)
    from ai_logic.common import (
        RUN_PROFILE_PATH,
        DENY_SUBSTRINGS as COMMON_DENY_SUBSTRINGS,
        RISKY_PATTERNS as COMMON_RISKY_PATTERNS,
        run_shell_command,
    )
except Exception:
    # Fallback aman bila common belum ada / belum updated.
    RUN_PROFILE_PATH = Path.cwd() / "workspace" / "run_profile.json"
    COMMON_DENY_SUBSTRINGS = ["rm -rf /", "mkfs", "dd if=", "shutdown", "reboot", "poweroff"]
    COMMON_RISKY_PATTERNS = [r"\brm\s+-rf\b", r"\bdd\s+if=", r"\bmkfs(\.\w+)?\b"]

    def run_shell_command(cmd: str, *, cfg=None, check: bool = False, env=None):
        _ = cfg
        return subprocess.run(cmd, shell=True, executable="/bin/sh", check=check, env=env)


# ---------------------------------------------------------------------
# JSON5 soft dependency
# ---------------------------------------------------------------------
HAS_JSON5 = False
try:
    import json5  # type: ignore

    HAS_JSON5 = True
except Exception:
    HAS_JSON5 = False


# ---------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------
def _resolve_profile_file(run_profile_path: Path) -> Path:
    """
    RUN_PROFILE_PATH bisa berupa:
      - file .json / .json5
      - directory (legacy)
    """
    p = Path(run_profile_path)
    if p.suffix.lower() in (".json", ".json5"):
        return p
    return p / "run_profile.json"


PROFILE_FILE = _resolve_profile_file(RUN_PROFILE_PATH)
HISTORY_FILE = PROFILE_FILE.parent / "run_history.json"


# ---------------------------------------------------------------------
# Safety constants
# ---------------------------------------------------------------------
DENY_SUBSTRINGS = list(COMMON_DENY_SUBSTRINGS)
RISKY_PATTERNS = list(COMMON_RISKY_PATTERNS)

_BAD_CONTROL_REGEX = re.compile(r"[\r\n]")
_VAR_PATTERN = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")

_RISKY_REGEX: Optional[re.Pattern[str]] = None
if RISKY_PATTERNS:
    _RISKY_REGEX = re.compile("|".join(f"(?:{p})" for p in RISKY_PATTERNS), re.IGNORECASE)


# ---------------------------------------------------------------------
# Data helpers (future-proof)
# ---------------------------------------------------------------------
@dataclass(frozen=True)
class StepSpec:
    title: str
    cmd: str
    risk: str = "low"          # low | medium | high | critical
    confirm: bool = False
    ignore_fail: bool = False
    timeout: Optional[int] = None   # seconds
    cwd: Optional[str] = None
    env: Optional[Dict[str, str]] = None


# ---------------------------------------------------------------------
# Bootstrap template
# ---------------------------------------------------------------------
def _ensure_profile_dir() -> None:
    try:
        PROFILE_FILE.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        # Permission/sandbox: ignore, handled later on write attempts
        pass


def _template_payload() -> Dict[str, Any]:
    # Pure JSON (valid JSON standar), tetap bisa dibaca JSON5 juga.
    return {
        "version": 1,
        "profiles": [
            {
                "name": "check_env",
                "description": "Contoh: Cek kesiapan tools development.",
                "requires": {"os": ["fedora", "linux"], "commands": ["python3", "git"], "network": False},
                "vars": {"workspace_dir": str(Path.cwd())},
                "steps": [
                    {"title": "Cek Python", "cmd": "python3 --version", "risk": "low"},
                    {"title": "Cek Git", "cmd": "git --version", "risk": "low"},
                ],
            },
            {
                "name": "setup_dev",
                "description": "Setup environment dev: deps + git hooks + ollama check.",
                "requires": {"os": ["fedora", "linux"], "commands": ["git", "python3"], "network": True},
                "vars": {},
                "steps": [
                    {"title": "Check git", "cmd": "git --version", "risk": "low"},
                    {"title": "Install deps", "cmd": "sudo dnf install -y ripgrep fd-find", "risk": "medium", "confirm": True},
                    {"title": "Ollama list", "cmd": "ollama list", "risk": "low", "ignore_fail": True},
                ],
            },
        ],
    }


def _create_template_if_missing() -> bool:
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

    ansi.print_system("AI RUNBOOK (BOOTSTRAP MODE)")
    print("File profile belum ada, jadi template dibuat otomatis (bukan error).")
    print(f"Lokasi file : {ansi.c_cyan()}{PROFILE_FILE}{ansi.c_reset()}")
    if HAS_JSON5:
        print(f"Engine      : {ansi.c_green()}JSON5 tersedia{ansi.c_reset()} (komentar & trailing comma OK).")
    else:
        print(
            f"Engine      : {ansi.c_yellow()}JSON standar{ansi.c_reset()} (tanpa komentar). "
            f"JSON5: {ansi.c_green()}sudo dnf install python3-json5{ansi.c_reset()}"
        )
    print("Silakan edit profiles sesuai kebutuhan.\n")
    return True


# ---------------------------------------------------------------------
# Loader + schema
# ---------------------------------------------------------------------
def _parse_profiles_text(raw: str) -> Tuple[Dict[str, Any], str]:
    if HAS_JSON5:
        try:
            data = json5.loads(raw)  # type: ignore[name-defined]
            if isinstance(data, dict):
                return data, ""
            return {}, "Schema salah: root harus object/dict."
        except Exception as e:
            return {}, f"Syntax Error (JSON5): {e}"

    try:
        data2 = json.loads(raw)
        if isinstance(data2, dict):
            return data2, ""
        return {}, "Schema salah: root harus object/dict."
    except json.JSONDecodeError as e:
        msg = f"Gagal parsing file JSON: {e}\n\n"
        msg += f"{ansi.c_yellow()}NOTE:{ansi.c_reset()} Library JSON5 tidak ditemukan.\n"
        msg += "Mode saat ini: JSON standar (tidak support komentar // atau trailing comma).\n\n"
        msg += "Saran:\n"
        msg += "1) Hapus komentar (// atau /* */)\n"
        msg += f"2) Atau install JSON5: {ansi.c_green()}sudo dnf install python3-json5{ansi.c_reset()}\n"
        return {}, msg


def _validate_root_schema(data: Dict[str, Any]) -> Tuple[bool, str]:
    if "profiles" not in data or not isinstance(data.get("profiles"), list):
        return False, "Schema salah: root harus punya key 'profiles' berupa List/Array."
    v = data.get("version", 1)
    if not isinstance(v, int):
        return False, "Schema salah: 'version' harus integer."
    return True, ""


def _load_profiles() -> Tuple[Dict[str, Any], str]:
    _create_template_if_missing()

    try:
        raw = PROFILE_FILE.read_text(encoding="utf-8")
    except Exception as e:
        return {}, f"Gagal membaca file profile (Permission/IO Error): {e}"

    data, perr = _parse_profiles_text(raw)
    if perr:
        return {}, perr

    ok, verr = _validate_root_schema(data)
    if not ok:
        return {}, verr

    return data, ""


# ---------------------------------------------------------------------
# History
# ---------------------------------------------------------------------
def _log_history(
    profile_name: str,
    result: str,
    exit_code: int,
    step_index: Optional[int] = None,
    note: str = "",
) -> None:
    try:
        _ensure_profile_dir()

        hist: List[Dict[str, Any]] = []
        if HISTORY_FILE.exists():
            try:
                loaded = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
                if isinstance(loaded, list):
                    hist = loaded
            except Exception:
                hist = []

        hist.append(
            {
                "time": _dt.datetime.now().isoformat(timespec="seconds"),
                "profile": profile_name,
                "result": result,
                "code": int(exit_code),
                "step": step_index,
                "note": str(note)[:300],
            }
        )

        if len(hist) > 50:
            hist = hist[-50:]

        HISTORY_FILE.write_text(json.dumps(hist, indent=2), encoding="utf-8")
    except Exception:
        pass


def _read_history(limit: int = 10) -> List[Dict[str, Any]]:
    try:
        if not HISTORY_FILE.exists():
            return []
        d = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        if not isinstance(d, list):
            return []
        return d[-max(1, int(limit)) :]
    except Exception:
        return []


# ---------------------------------------------------------------------
# Safety + preflight
# ---------------------------------------------------------------------
def _read_os_release_kv() -> Dict[str, str]:
    """
    Parse /etc/os-release into key->value (lowercased value for match).
    """
    kv: Dict[str, str] = {}
    try:
        text = Path("/etc/os-release").read_text(encoding="utf-8", errors="replace")
    except Exception:
        return kv

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip().strip('"').strip("'")
        kv[k.strip().upper()] = v.lower()
    return kv


def _run_preflight_checks(reqs: Dict[str, Any]) -> bool:
    # 1) OS check
    wanted = []
    if isinstance(reqs.get("os"), list):
        wanted = [str(x).lower().strip() for x in reqs["os"] if str(x).strip()]

    if wanted:
        kv = _read_os_release_kv()
        if not kv:
            # kalau tidak bisa baca os-release, jangan memblok (mode dev-friendly)
            pass
        else:
            os_id = kv.get("ID", "")
            os_like = kv.get("ID_LIKE", "")
            hay = f"{os_id} {os_like}".strip()
            matched = any(w in hay.split() or w in hay for w in wanted)
            if not matched:
                ansi.print_brief_error(f"Requirement fail: OS tidak cocok dengan {wanted} (detected: {hay})")
                return False

    # 2) Command existence
    if isinstance(reqs.get("commands"), list):
        for tool in reqs["commands"]:
            t = str(tool).strip()
            if not t:
                continue
            if not shutil.which(t):
                ansi.print_brief_error(f"Requirement fail: Command '{t}' tidak ditemukan di PATH.")
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
    c = cmd.lower().strip()
    for banned in DENY_SUBSTRINGS:
        if banned in c:
            return f"Contains banned unsafe syntax: '{banned}'"
    return None


def _needs_extra_confirm(cmd: str) -> bool:
    if _RISKY_REGEX is None:
        return False
    return bool(_RISKY_REGEX.search(cmd))


def _is_sudo(cmd: str) -> bool:
    toks = cmd.strip().split()
    return "sudo" in toks


def _confirm(prompt: str) -> bool:
    try:
        resp = input(prompt).strip().lower()
        return resp == "y"
    except KeyboardInterrupt:
        return False


def _safe_quote_var_value(val: Any) -> str:
    s = str(val)
    if _BAD_CONTROL_REGEX.search(s):
        raise ValueError("Variable mengandung newline/control characters.")
    return shlex.quote(s)


def _replace_vars(cmd_str: str, variables: Dict[str, Any]) -> Tuple[str, bool, str]:
    missing: List[str] = []
    bad: List[str] = []

    def repl(m: re.Match[str]) -> str:
        key = m.group(1)
        if key not in variables:
            missing.append(key)
            return m.group(0)
        try:
            return _safe_quote_var_value(variables[key])
        except Exception as e:
            bad.append(f"{key}: {e}")
            return m.group(0)

    out = _VAR_PATTERN.sub(repl, cmd_str)

    if missing:
        ansi.print_brief_error(f"Missing variables: {missing}")
        return cmd_str, False, "missing_vars"

    if bad:
        ansi.print_brief_error("Variable value tidak valid untuk substitution.")
        return cmd_str, False, "bad_var_value"

    return out, True, ""


# ---------------------------------------------------------------------
# Profile selection + step parsing
# ---------------------------------------------------------------------
def _find_profile(data: Dict[str, Any], name: str) -> Optional[Dict[str, Any]]:
    for p in data.get("profiles", []):
        if isinstance(p, dict) and p.get("name") == name:
            return p
    return None


def _parse_step(step: Dict[str, Any], idx: int) -> Tuple[Optional[StepSpec], str]:
    cmd = step.get("cmd")
    if not isinstance(cmd, str) or not cmd.strip():
        return None, f"Step #{idx}: field 'cmd' wajib string non-kosong."

    title = step.get("title")
    if not isinstance(title, str) or not title.strip():
        title = cmd.strip()

    risk = str(step.get("risk") or "low").lower().strip()
    if risk not in ("low", "medium", "high", "critical"):
        return None, f"Step #{idx}: risk '{risk}' invalid (pakai low|medium|high|critical)."

    confirm = bool(step.get("confirm", False))
    ignore_fail = bool(step.get("ignore_fail", False))

    timeout = step.get("timeout", None)
    if timeout is not None:
        try:
            timeout = int(timeout)
            if timeout <= 0:
                timeout = None
        except Exception:
            return None, f"Step #{idx}: timeout harus integer (seconds)."

    cwd = step.get("cwd", None)
    if cwd is not None and not isinstance(cwd, str):
        return None, f"Step #{idx}: cwd harus string."

    env = step.get("env", None)
    if env is not None:
        if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
            return None, f"Step #{idx}: env harus object string->string."

    return StepSpec(
        title=str(title),
        cmd=str(cmd).strip(),
        risk=risk,
        confirm=confirm,
        ignore_fail=ignore_fail,
        timeout=timeout,
        cwd=cwd,
        env=env,
    ), ""


# ---------------------------------------------------------------------
# Commands: list/show/validate/history/run
# ---------------------------------------------------------------------
def cmd_list_profiles(data: Dict[str, Any]) -> int:
    profiles = data.get("profiles", [])
    ansi.print_system(f"AVAILABLE PROFILES ({len(profiles)})")

    engine = "JSON5" if HAS_JSON5 else "JSON"
    engine_note = "komentar & trailing comma OK" if HAS_JSON5 else "install JSON5: sudo dnf install python3-json5"
    print(f"{ansi.c_dim()}Engine: {engine} ({engine_note}){ansi.c_reset()}")

    if not profiles:
        print("\nTidak ada profiles.")
        print(f"Config Path: {ansi.c_cyan()}{PROFILE_FILE}{ansi.c_reset()}")
        return 0

    print(f"\n{ansi.c_dim()}{'NAME':<22} {'STEPS':<5} DESCRIPTION{ansi.c_reset()}")
    print("-" * 78)

    for p in profiles:
        if not isinstance(p, dict):
            continue
        name = str(p.get("name") or "unnamed")
        desc = str(p.get("description") or "(No description)")
        steps = p.get("steps", [])
        nsteps = len(steps) if isinstance(steps, list) else 0
        print(f" {ansi.c_cyan()}{name:<22}{ansi.c_reset()} {nsteps:<5} {desc}")

    print(f"\nConfig Path: {ansi.c_cyan()}{PROFILE_FILE}{ansi.c_reset()}")
    print(f"Usage      : {ansi.c_bold()}ai run <name> [--dry]{ansi.c_reset()}")
    return 0


def cmd_show_profile(profile: Dict[str, Any]) -> int:
    name = str(profile.get("name") or "unnamed")
    ansi.print_system(f"PROFILE: {name}")

    desc = str(profile.get("description") or "-")
    reqs = profile.get("requires", {})
    vars_ = profile.get("vars", {})

    print(f"Description : {desc}")
    print(f"Requires    : {reqs if isinstance(reqs, dict) else {}}")
    print(f"Vars        : {vars_ if isinstance(vars_, dict) else {}}")
    print("\n[ Execution Plan ]")

    steps_raw = profile.get("steps", [])
    if not isinstance(steps_raw, list) or not steps_raw:
        print("  (No steps defined)")
        return 0

    for i, s in enumerate(steps_raw, 1):
        if not isinstance(s, dict):
            continue
        parsed, err = _parse_step(s, i)
        if err:
            print(f" {i:>2}. {ansi.c_red()}INVALID{ansi.c_reset()} - {err}")
            continue

        assert parsed is not None
        sudo = _is_sudo(parsed.cmd)
        risky = _needs_extra_confirm(parsed.cmd)
        flags: List[str] = []

        if sudo:
            flags.append("SUDO")
        if parsed.risk in ("high", "critical") or parsed.confirm or risky:
            flags.append("CONFIRM")

        col = ansi.c_green()
        if "SUDO" in flags:
            col = ansi.c_yellow()
        if "CONFIRM" in flags and parsed.risk in ("high", "critical"):
            col = ansi.c_red()

        flag_txt = f"[{', '.join(flags)}] " if flags else ""
        print(f" {i:>2}. {col}{flag_txt}{parsed.title}{ansi.c_reset()}")
        print(f"     $ {parsed.cmd}")

    return 0


def cmd_validate(data: Dict[str, Any]) -> int:
    ansi.print_system("VALIDATE RUNBOOK PROFILE")
    profiles = data.get("profiles", [])

    if not isinstance(profiles, list):
        ansi.print_brief_error("Schema invalid: 'profiles' bukan list.")
        return 2

    if not profiles:
        print("OK: profiles list kosong.")
        print(f"Config Path: {ansi.c_cyan()}{PROFILE_FILE}{ansi.c_reset()}")
        return 0

    bad = 0
    for p in profiles:
        if not isinstance(p, dict):
            bad += 1
            continue
        n = p.get("name")
        if not isinstance(n, str) or not n.strip():
            bad += 1
            continue
        steps = p.get("steps", [])
        if not isinstance(steps, list):
            bad += 1
            continue
        for i, s in enumerate(steps, 1):
            if not isinstance(s, dict):
                bad += 1
                continue
            _, err = _parse_step(s, i)
            if err:
                bad += 1

    if bad:
        ansi.print_brief_error(f"Ada {bad} item bermasalah (cek name/steps/cmd/risk).")
        print(f"Config Path: {ansi.c_cyan()}{PROFILE_FILE}{ansi.c_reset()}")
        return 2

    print(f"{ansi.c_green()}OK:{ansi.c_reset()} Schema terlihat valid.")
    print(f"Config Path: {ansi.c_cyan()}{PROFILE_FILE}{ansi.c_reset()}")
    return 0


def cmd_history() -> int:
    ansi.print_system("RUN HISTORY (LAST 10)")
    hist = _read_history(limit=10)
    if not hist:
        print("Belum ada riwayat.")
        print(f"Path: {ansi.c_dim()}{HISTORY_FILE}{ansi.c_reset()}")
        return 0

    print(f"{ansi.c_dim()}{'TIME':<20} {'PROFILE':<18} {'RESULT':<12} CODE{ansi.c_reset()}")
    print("-" * 78)
    for e in hist:
        t = str(e.get("time") or "")[:19]
        p = str(e.get("profile") or "")[:18]
        r = str(e.get("result") or "")[:12]
        c = str(e.get("code") if e.get("code") is not None else "")
        print(f"{t:<20} {ansi.c_cyan()}{p:<18}{ansi.c_reset()} {r:<12} {c}")
    print(f"\nPath: {ansi.c_dim()}{HISTORY_FILE}{ansi.c_reset()}")
    return 0


def _merge_env(step_env: Optional[Dict[str, str]]) -> Dict[str, str]:
    out = dict(os.environ)
    if step_env:
        out.update(step_env)
    return out


def cmd_exec_run(profile: Dict[str, Any], is_dry_run: bool) -> int:
    name = str(profile.get("name") or "unnamed")
    ansi.print_system(f"RUNNING: {name}" + (" (DRY RUN)" if is_dry_run else ""))

    if is_dry_run:
        print(f"{ansi.c_dim()}Note: Tidak ada command yang akan dieksekusi.{ansi.c_reset()}")

    # REQUIREMENTS
    reqs = profile.get("requires", {})
    if isinstance(reqs, dict) and reqs:
        print("• Preflight... ", end="")
        if not _run_preflight_checks(reqs):
            _log_history(name, "fail_reqs", 2, note="preflight_failed")
            return 2
        print(f"{ansi.c_green()}OK{ansi.c_reset()}")

    # VARIABLES
    variables = profile.get("vars", {})
    if not isinstance(variables, dict):
        variables = {}

    # STEPS
    steps_raw = profile.get("steps", [])
    if not isinstance(steps_raw, list):
        ansi.print_brief_error("Schema invalid: steps bukan list.")
        _log_history(name, "failed", 2, note="steps_not_list")
        return 2

    if not steps_raw:
        print("Tidak ada steps. Selesai tanpa eksekusi.")
        _log_history(name, "success", 0, note="no_steps")
        return 0

    total = len(steps_raw)
    ok_count = 0

    for i, s in enumerate(steps_raw, 1):
        if not isinstance(s, dict):
            ansi.print_brief_error(f"Step #{i} invalid (bukan object).")
            _log_history(name, "failed", 2, step_index=i, note="step_not_object")
            return 2

        step, err = _parse_step(s, i)
        if err:
            ansi.print_brief_error(err)
            _log_history(name, "failed", 2, step_index=i, note="step_schema_invalid")
            return 2
        assert step is not None

        print(f"\n{ansi.c_cyan()}[{i}/{total}] {step.title}{ansi.c_reset()}")

        # A) command sanity
        bad = _is_multiline_or_empty(step.cmd)
        if bad:
            ansi.print_brief_error(f"Invalid command: {bad}")
            _log_history(name, "fail_safety", 3, step_index=i, note="multiline_or_empty")
            return 3

        # B) substitution (safe quote)
        final_cmd, var_ok, var_note = _replace_vars(step.cmd, variables)
        if not var_ok:
            _log_history(name, "fail_vars", 2, step_index=i, note=var_note)
            return 2

        print(f"  {ansi.c_dim()}$ {ansi.c_reset()}{ansi.c_bold()}{final_cmd}{ansi.c_reset()}")

        # C) deny-list
        deny_reason = _check_deny_list(final_cmd)
        if deny_reason:
            ansi.print_brief_error(f"SAFETY BLOCK: {deny_reason}")
            _log_history(name, "fail_safety", 3, step_index=i, note="deny_list")
            return 3

        # D) risk decision
        sudo = _is_sudo(final_cmd)
        risky_pattern = _needs_extra_confirm(final_cmd)
        is_high_risk = step.risk in ("high", "critical")
        need_confirm = is_high_risk or sudo or step.confirm or risky_pattern

        # E) dry-run
        if is_dry_run:
            if need_confirm:
                reasons = []
                if sudo:
                    reasons.append("sudo")
                if is_high_risk:
                    reasons.append(f"risk={step.risk}")
                if step.confirm:
                    reasons.append("confirm=true")
                if risky_pattern:
                    reasons.append("risky-pattern")
                print(f"  {ansi.c_yellow()}⚠ Would ask confirmation ({', '.join(reasons)}){ansi.c_reset()}")
            print(f"  {ansi.c_dim()}(Dry-run: skipped){ansi.c_reset()}")
            continue

        # F) confirmation
        if need_confirm:
            reasons = []
            if sudo:
                reasons.append("sudo")
            if is_high_risk:
                reasons.append(f"risk={step.risk}")
            if step.confirm:
                reasons.append("confirm=true")
            if risky_pattern:
                reasons.append("risky-pattern")

            ok = _confirm(
                f"  {ansi.c_yellow()}⚠ Konfirmasi diperlukan ({', '.join(reasons)}). Lanjutkan? [y/N] {ansi.c_reset()}"
            )
            if not ok:
                print("  Dibatalkan.")
                _log_history(name, "cancelled", 0, step_index=i, note="user_cancel")
                return 0

        # G) execute
        try:
            env = _merge_env(step.env)
            t0 = time.time()

            # Bila common runner tidak support timeout/cwd secara native, kita tetap bisa pakai subprocess fallback.
            # Tapi demi konsistensi, kita eksekusi via common runner (shell) dan pakai env hasil merge.
            # Untuk timeout/cwd, kita pakai subprocess langsung agar benar-benar berlaku.
            if step.timeout is not None or step.cwd is not None:
                proc = subprocess.run(
                    final_cmd,
                    shell=True,
                    executable="/bin/sh",
                    env=env,
                    cwd=step.cwd,
                    timeout=step.timeout,
                )
                exit_code = int(proc.returncode)
            else:
                exit_code = int(run_shell_command(final_cmd, cfg={}, env=env).returncode)

            dt = time.time() - t0

            if exit_code != 0:
                ansi.print_brief_error(f"Step gagal (exit code: {exit_code})")
                if not step.ignore_fail:
                    print(f"Stopping at step {i}.")
                    _log_history(name, "failed", exit_code, step_index=i, note="stop_on_fail")
                    return exit_code
                print(f"{ansi.c_yellow()}Warning:{ansi.c_reset()} ignore_fail=true → lanjut.")
                _log_history(name, "failed", exit_code, step_index=i, note="ignored_fail")
            else:
                ok_count += 1
                print(f"  {ansi.c_green()}✓ OK{ansi.c_reset()} {ansi.c_dim()}({dt:.2f}s){ansi.c_reset()}")

        except subprocess.TimeoutExpired:
            ansi.print_brief_error(f"Timeout: step melebihi {step.timeout}s")
            if not step.ignore_fail:
                _log_history(name, "failed", 124, step_index=i, note="timeout")
                return 124
            _log_history(name, "failed", 124, step_index=i, note="timeout_ignored")
        except KeyboardInterrupt:
            print("\nInterrupted.")
            _log_history(name, "cancelled", 130, step_index=i, note="keyboard_interrupt")
            return 130
        except Exception as e:
            ansi.print_brief_error(f"System error: {e}")
            _log_history(name, "failed", 1, step_index=i, note="system_error")
            return 1

    ansi.print_system(f"✓ COMPLETED: {name}  ({ok_count}/{total} steps OK)")
    _log_history(name, "success", 0, note="completed")
    return 0


# ---------------------------------------------------------------------
# Help + router
# ---------------------------------------------------------------------
def _print_help() -> None:
    ansi.print_info("AI Runbook (Automation)")
    print("  ai run list            : Lihat daftar profil tersedia.")
    print("  ai run show <name>     : Lihat detail langkah-langkah.")
    print("  ai run <name>          : Jalankan profil.")
    print("  ai run <name> --dry    : Simulasi run (dry-run).")
    print("  ai run validate        : Validasi schema profile.")
    print("  ai run history         : Lihat riwayat eksekusi.\n")

    print("Config Path:")
    print(f"  {PROFILE_FILE}")
    if HAS_JSON5:
        print(f"Engine: {ansi.c_green()}JSON5 aktif{ansi.c_reset()} (komentar & trailing comma OK)")
    else:
        print(f"Engine: {ansi.c_dim()}JSON standar{ansi.c_reset()} (install JSON5: sudo dnf install python3-json5)")


def handle(argv: List[str], cfg: Dict) -> int:
    """
    Subcommand router: ai run <action> [args]
    Actions:
      list | show | validate | history | <profile_name>
    """
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

    if action == "validate":
        return cmd_validate(data)

    if action == "history":
        return cmd_history()

    if action == "list":
        return cmd_list_profiles(data)

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
