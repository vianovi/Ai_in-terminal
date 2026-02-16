import time
from system_logic.terminal import ansi
from system_logic.core import run_shell_command
from . import safety, storage

def _confirm(msg: str) -> bool:
    try:
        return input(msg).lower().strip() == 'y'
    except: return False

def _process_prompts(profile: dict, vars_: dict) -> None:
    """New Feature: Ask user for input if defined in 'prompts'."""
    prompts = profile.get("prompts", {})
    if not prompts or not isinstance(prompts, dict):
        return

    print(f"\n{ansi.c_cyan()}INPUT REQUIRED:{ansi.c_reset()}")
    for var_name, question in prompts.items():
        default_val = vars_.get(var_name, "")
        prompt_text = f"  {question} [{default_val}]: " if default_val else f"  {question}: "

        try:
            user_val = input(prompt_text).strip()
            if user_val:
                vars_[var_name] = user_val
            # If empty, keep default from vars_
        except KeyboardInterrupt:
            print()
            raise

def run_profile(profile: dict, is_dry: bool = False) -> int:
    name = profile.get("name", "unnamed")
    ansi.print_system(f"RUNNING: {name}" + (" (DRY)" if is_dry else ""))

    # 1. Preflight
    if not safety.run_preflight_checks(profile.get("requires", {})):
        storage.log_history(name, "fail_reqs", 2)
        return 2

    # 2. Setup Variables & Prompts (UPGRADE HERE)
    vars_ = profile.get("vars", {}).copy() # Copy to avoid mutating original data
    try:
        _process_prompts(profile, vars_)
    except KeyboardInterrupt:
        print("\nCancelled.")
        return 130

    steps = profile.get("steps", [])
    if not steps:
        print("No steps defined.")
        return 0

    # 3. Execution Loop
    for i, step in enumerate(steps, 1):
        raw_cmd = step.get("cmd", "")
        title = step.get("title", raw_cmd)

        print(f"\n{ansi.c_cyan()}[{i}/{len(steps)}] {title}{ansi.c_reset()}")

        # Safety & Substitution
        err = safety.is_multiline_or_empty(raw_cmd)
        if err:
            ansi.print_brief_error(f"Invalid: {err}")
            storage.log_history(name, "fail_safe", 3, i, err)
            return 3

        final_cmd, ok, note = safety.replace_vars(raw_cmd, vars_)
        if not ok:
            ansi.print_brief_error(f"Var error: {note}")
            storage.log_history(name, "fail_vars", 2, i, note)
            return 2

        if deny := safety.check_deny_list(final_cmd):
            ansi.print_brief_error(f"BLOCKED: {deny}")
            storage.log_history(name, "fail_deny", 3, i, deny)
            return 3

        print(f"  $ {ansi.c_bold()}{final_cmd}{ansi.c_reset()}")

        # Risk Assessment
        is_risky = (
            step.get("risk") in ("high", "critical") or
            step.get("confirm") or
            safety.is_sudo(final_cmd) or
            safety.needs_extra_confirm(final_cmd)
        )

        if is_dry:
            if is_risky: print(f"  {ansi.c_yellow()}[Would Ask Confirm]{ansi.c_reset()}")
            continue

        if is_risky:
            if not _confirm(f"  {ansi.c_yellow()}⚠ Execute risky command? [y/N] {ansi.c_reset()}"):
                print("  Cancelled.")
                storage.log_history(name, "cancelled", 0, i)
                return 0

        # Execute
        try:
            t0 = time.time()
            res = run_shell_command(final_cmd, check=False)
            dt = time.time() - t0

            if res.returncode != 0:
                ansi.print_brief_error(f"Exit code: {res.returncode}")
                if not step.get("ignore_fail"):
                    storage.log_history(name, "failed", res.returncode, i)
                    return res.returncode
                print(f"{ansi.c_yellow()}Ignoring failure...{ansi.c_reset()}")
            else:
                print(f"  {ansi.c_green()}✓ OK ({dt:.2f}s){ansi.c_reset()}")

        except KeyboardInterrupt:
            storage.log_history(name, "interrupt", 130, i)
            return 130
        except Exception as e:
            ansi.print_brief_error(f"Sys Error: {e}")
            return 1

    ansi.print_system("✓ COMPLETED")
    storage.log_history(name, "success", 0)
    return 0