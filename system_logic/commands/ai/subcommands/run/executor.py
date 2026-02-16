"""
Execution Engine for 'ai run'.
Menjalankan langkah-langkah dalam profile secara berurutan.
"""

import time
import subprocess
from typing import Any, Dict, Optional
from system_logic.terminal import ansi

# Import internal modular
from . import safety, storage

# ==========================================================
# 1. FAIL-SAFE SHELL COMMAND IMPORT
# ==========================================================
try:
    # Mencoba import dari common utilitas sistem
    from system_logic.core import run_shell_command
except ImportError:
    try:
        # Mencoba fallback ke system_logic path
        from system_logic.core.exec import run_shell_command
    except ImportError:
        # FINAL FALLBACK: Jika tidak ada di sistem, definisikan secara lokal
        # agar runner tetap bisa bekerja (Zero-Error Integrity).
        def run_shell_command(cmd: str, *, cfg=None, check: bool=False, env=None):
            """Fallback execution logic jika core utilitas tidak ditemukan."""
            return subprocess.run(
                cmd,
                shell=True,
                executable="/bin/sh",
                check=check,
                env=env,
                capture_output=False # Langsung tampil ke terminal user
            )


# ==========================================================
# 2. HELPER FUNCTIONS
# ==========================================================

def _confirm(msg: str) -> bool:
    """Helper untuk meminta konfirmasi user."""
    try:
        resp = input(msg).lower().strip()
        return resp == 'y'
    except KeyboardInterrupt:
        print()
        return False


def _process_prompts(profile: dict, vars_: dict) -> None:
    """
    UPGRADE: Mendeteksi input dinamis dari user sebelum eksekusi.
    Mengisi variabel placeholder {{var}} secara interaktif.
    """
    prompts = profile.get("prompts", {})
    if not prompts or not isinstance(prompts, dict):
        return

    print(f"\n{ansi.c_cyan()}INPUT REQUIRED:{ansi.c_reset()}")
    for var_name, question in prompts.items():
        # Ambil default value jika ada di field 'vars'
        default_val = vars_.get(var_name, "")
        prompt_text = f"  {question} [{default_val}]: " if default_val else f"  {question}: "

        try:
            user_val = input(prompt_text).strip()
            if user_val:
                vars_[var_name] = user_val
        except KeyboardInterrupt:
            print()
            raise


# ==========================================================
# 3. CORE RUNNER LOGIC
# ==========================================================

def run_profile(profile: dict, is_dry: bool = False) -> int:
    """
    Main loop untuk menjalankan profile.
    """
    name = profile.get("name", "unnamed")
    ansi.print_system(f"RUNNING: {name}" + (" (DRY RUN)" if is_dry else ""))

    # A) Preflight Requirements (OS, Commands, Network)
    reqs = profile.get("requires", {})
    if not safety.run_preflight_checks(reqs):
        storage.log_history(name, "fail_reqs", 2, note="preflight_failed")
        return 2

    # B) Setup Variables & Dynamic Prompts
    # Kita buat copy agar tidak merusak data asli di memory
    variables = profile.get("vars", {}).copy()
    try:
        _process_prompts(profile, variables)
    except KeyboardInterrupt:
        print("\nOperation cancelled by user.")
        return 130

    steps = profile.get("steps", [])
    if not steps:
        print("No steps defined in this profile.")
        return 0

    # C) Execution Loop
    total = len(steps)
    for i, step in enumerate(steps, 1):
        raw_cmd = str(step.get("cmd") or "").strip()
        title = str(step.get("title") or raw_cmd or f"Step {i}")
        risk = str(step.get("risk") or "low").lower()
        explicit_confirm = bool(step.get("confirm", False))
        ignore_fail = bool(step.get("ignore_fail", False))

        print(f"\n{ansi.c_cyan()}[{i}/{total}] {title}{ansi.c_reset()}")

        # 1. Command Sanity Check
        bad = safety.is_multiline_or_empty(raw_cmd)
        if bad:
            ansi.print_brief_error(f"Invalid command: {bad}")
            storage.log_history(name, "fail_safety", 3, i, bad)
            return 3

        # 2. Variable Substitution (Safe-Quoted)
        final_cmd, var_ok, var_note = safety.replace_vars(raw_cmd, variables)
        if not var_ok:
            ansi.print_brief_error(f"Substitution error: {var_note}")
            storage.log_history(name, "fail_vars", 2, i, var_note)
            return 2

        # 3. Hard Safety Deny-List Check
        deny_reason = safety.check_deny_list(final_cmd)
        if deny_reason:
            ansi.print_brief_error(f"SAFETY BLOCK: {deny_reason}")
            storage.log_history(name, "fail_safety", 3, i, "deny_list")
            return 3

        print(f"  Command: {ansi.c_bold()}{final_cmd}{ansi.c_reset()}")

        # 4. Risk Detection & Confirmation
        is_sudo = safety.is_sudo(final_cmd)
        is_risky_pattern = safety.needs_extra_confirm(final_cmd)
        is_high_risk = risk in ("high", "critical")

        needs_approval = is_high_risk or is_sudo or explicit_confirm or is_risky_pattern

        if is_dry:
            if needs_approval:
                print(f"  {ansi.c_yellow()}⚠ Would ask confirmation here.{ansi.c_reset()}")
            print(f"  {ansi.c_dim()}(Dry-run: skipping execution){ansi.c_reset()}")
            continue

        if needs_approval:
            msg = f"  {ansi.c_yellow()}⚠ Konfirmasi diperlukan. Lanjutkan? [y/N] {ansi.c_reset()}"
            if not _confirm(msg):
                print("  Cancelled by user.")
                storage.log_history(name, "cancelled", 0, i, "user_declined")
                return 0

        # 5. ACTUAL EXECUTION
        try:
            t0 = time.time()
            # Memanggil fungsi shell yang sudah diaudit di bagian (1)
            result = run_shell_command(final_cmd)
            exit_code = int(result.returncode if hasattr(result, 'returncode') else 0)
            dt = time.time() - t0

            if exit_code != 0:
                ansi.print_brief_error(f"Step gagal (Exit Code: {exit_code})")
                if not ignore_fail:
                    print(f"Stopping profile execution.")
                    storage.log_history(name, "failed", exit_code, i, "stop_on_fail")
                    return exit_code
                print(f"{ansi.c_yellow()}Note:{ansi.c_reset()} ignore_fail=true, lanjut ke langkah berikutnya.")
            else:
                print(f"  {ansi.c_green()}✓ Sukses ({dt:.2f}s){ansi.c_reset()}")

        except KeyboardInterrupt:
            print("\nInterrupted.")
            storage.log_history(name, "cancelled", 130, i, "keyboard_interrupt")
            return 130
        except Exception as e:
            ansi.print_brief_error(f"System Error: {e}")
            storage.log_history(name, "failed", 1, i, "system_error")
            return 1

    ansi.print_system(f"✓ PROFILE '{name}' COMPLETED SUCCESSFULLY.")
    storage.log_history(name, "success", 0, note="completed")
    return 0