"""AI_IN-TERMINAL — ai_logic.core.safety
Version: 1.5 (2026-01-04)

Changelog (1.5)
- Centralized deny-list and risky-pattern evaluation for shell commands.
- Added structured decision object: blocked / needs_confirm / reasons.

Scope
- This is NOT a sandbox. The goal is to reduce accidental catastrophic commands.
- The final responsibility remains with the user (explicit confirmation flows).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Sequence


# ============================================================
# Policy
# ============================================================

# Hard-stop deny substrings (legacy-compatible; keep minimal and explicit).
DEFAULT_DENY_SUBSTRINGS: list[str] = [
    "rm -rf /",
    " mkfs",
    "mkfs",  # some modules used without leading space
    "dd if=",
    ":(){:|:&};:",
    " shutdown",
    "shutdown",
    " reboot",
    "reboot",
    " poweroff",
    "poweroff",
    "> /dev/sda",
    "mv /",
]

# Risk patterns: do not block, but require explicit confirmation.
DEFAULT_RISKY_PATTERNS: list[str] = [
    r"\brm\s+-rf\b",                 # rm -rf
    r"\bdd\s+if=",                   # dd if=
    r"\bmkfs(\.\w+)?\b",             # mkfs / mkfs.ext4
    r">\s*/dev/sd[a-z]\b",           # redirect to disk raw
    r"\bparted\b|\bfdisk\b|\bgdisk\b",
    r"\bsystemctl\s+(disable|mask)\b",
]

_BAD_CONTROL_REGEX = re.compile(r"[\r\n]")

# Conservative shell metacharacters that typically indicate chaining/pipes/redirection.
# Not always unsafe, but helpful for 'needs_confirm' classification.
_SHELL_META_REGEX = re.compile(r"[;&|<>`$()]")


@dataclass(frozen=True)
class SafetyDecision:
    blocked: bool
    needs_confirm: bool
    reasons: tuple[str, ...] = ()
    flags: tuple[str, ...] = ()


@dataclass
class SafetyPolicy:
    deny_substrings: list[str] = field(default_factory=lambda: list(DEFAULT_DENY_SUBSTRINGS))
    risky_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_RISKY_PATTERNS))

    deny_multiline: bool = True
    mark_shell_meta_as_risky: bool = True

    def compiled_risky_regex(self) -> re.Pattern[str]:
        pats = self.risky_patterns or []
        if not pats:
            return re.compile(r"$^")
        return re.compile("|".join(f"(?:{p})" for p in pats), re.IGNORECASE)


def default_policy() -> SafetyPolicy:
    return SafetyPolicy()


# ============================================================
# Evaluation
# ============================================================

def _has_any_substring(haystack: str, needles: Sequence[str]) -> str | None:
    for s in needles:
        if s and s in haystack:
            return s
    return None


def evaluate_command(cmd: str, policy: SafetyPolicy | None = None) -> SafetyDecision:
    """Evaluate a command string (single line expected)."""
    p = policy or default_policy()
    c = (cmd or "").strip()

    if not c:
        return SafetyDecision(blocked=True, needs_confirm=False, reasons=("empty",), flags=("empty",))

    if p.deny_multiline and _BAD_CONTROL_REGEX.search(c):
        return SafetyDecision(blocked=True, needs_confirm=False, reasons=("multiline",), flags=("multiline",))

    bad = _has_any_substring(c, p.deny_substrings)
    if bad is not None:
        return SafetyDecision(blocked=True, needs_confirm=False, reasons=(f"deny_substring:{bad}",), flags=("deny",))

    reasons: list[str] = []
    flags: list[str] = []
    needs_confirm = False

    risky_re = p.compiled_risky_regex()
    if risky_re.search(c):
        needs_confirm = True
        reasons.append("risky_pattern")
        flags.append("risky")

    if p.mark_shell_meta_as_risky and _SHELL_META_REGEX.search(c):
        needs_confirm = True
        reasons.append("shell_meta")
        flags.append("meta")

    if "sudo" in c.split():
        needs_confirm = True
        reasons.append("sudo")
        flags.append("sudo")

    return SafetyDecision(blocked=False, needs_confirm=needs_confirm, reasons=tuple(reasons), flags=tuple(flags))


def is_denied(cmd: str, policy: SafetyPolicy | None = None) -> bool:
    return evaluate_command(cmd, policy=policy).blocked


def is_risky(cmd: str, policy: SafetyPolicy | None = None) -> bool:
    d = evaluate_command(cmd, policy=policy)
    return (not d.blocked) and d.needs_confirm


def extend_policy(base: SafetyPolicy | None = None, *, deny_add: Iterable[str] = (), risky_add: Iterable[str] = ()) -> SafetyPolicy:
    """Create a new policy extending the default (or provided) policy."""
    p = SafetyPolicy()
    src = base or default_policy()
    p.deny_substrings = list(src.deny_substrings) + [s for s in deny_add if s]
    p.risky_patterns = list(src.risky_patterns) + [s for s in risky_add if s]
    p.deny_multiline = src.deny_multiline
    p.mark_shell_meta_as_risky = src.mark_shell_meta_as_risky
    return p
