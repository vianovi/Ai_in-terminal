import shutil
import socket
import shlex
from pathlib import Path
from typing import Tuple
from system_logic.terminal import ansi
from .const import DENY_SUBSTRINGS, RISKY_REGEX, BAD_CONTROL_REGEX, VAR_PATTERN

def check_deny_list(cmd: str) -> str | None:
    c = cmd.lower().strip()
    for banned in DENY_SUBSTRINGS:
        if banned in c: return f"Banned syntax: '{banned}'"
    return None

def is_multiline_or_empty(cmd: str) -> str | None:
    c = cmd.strip()
    if not c: return "Empty command"
    if BAD_CONTROL_REGEX.search(c): return "Multiline/Control char detected"
    return None

def needs_extra_confirm(cmd: str) -> bool:
    return bool(RISKY_REGEX.search(cmd)) if RISKY_REGEX else False

def is_sudo(cmd: str) -> bool:
    return "sudo" in cmd.strip().split()

def replace_vars(cmd_str: str, variables: dict) -> Tuple[str, bool, str]:
    missing = []
    notes = []

    def repl(m):
        key = m.group(1)
        if key not in variables:
            missing.append(key)
            return m.group(0)
        val = str(variables[key])
        if BAD_CONTROL_REGEX.search(val):
            notes.append(f"{key}: bad_value")
            return m.group(0)
        return shlex.quote(val)

    new_cmd = VAR_PATTERN.sub(repl, cmd_str)
    if missing: return cmd_str, False, f"missing: {missing}"
    if notes: return cmd_str, False, "var_value_unsafe"
    return new_cmd, True, ""

def run_preflight_checks(reqs: dict) -> bool:
    # 1. OS Check
    if "os" in reqs:
        wanted = [x.lower() for x in reqs["os"]]
        try:
            curr = Path("/etc/os-release").read_text().lower()
            if not any(w in curr for w in wanted):
                ansi.print_brief_error(f"OS mismatch. Wanted: {wanted}")
                return False
        except: pass

    # 2. Command Check
    for cmd in reqs.get("commands", []):
        if not shutil.which(cmd):
            ansi.print_brief_error(f"Command missing: {cmd}")
            return False

    # 3. Network Check
    if reqs.get("network"):
        try:
            socket.gethostbyname("google.com")
        except:
            ansi.print_brief_error("Network unreachable.")
            return False
    return True