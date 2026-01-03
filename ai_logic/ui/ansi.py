from __future__ import annotations

"""
ai_logic.ui.ansi

Minimal, dependency-light ANSI UI utilities used across AI-Term tools (ask/mon/cmd).

Design goals
- No heavy dependencies; pure stdlib.
- Safe and predictable terminal behavior (alt screen, cursor state, input echo).
- Works even when output is not a TTY (pipes, logs): gracefully degrades.
- “Calm errors”: do not print scary stacktraces by default; provide actionable hints.

Compatibility
- Python 3.9+
- Linux/macOS terminals. Windows is best-effort (colors may be disabled).

Notes
- This module intentionally exposes small, composable primitives rather than a “rich”
  framework. Keep it boring and stable.
"""

import os
import re
import sys
import textwrap
import threading
import time
from dataclasses import dataclass
from shutil import get_terminal_size
from typing import Optional, Iterable, Callable, Any


# ============================================================
# ANSI primitives
# ============================================================

CSI = "\x1b["  # Control Sequence Introducer
OSC = "\x1b]"  # Operating System Command
BEL = "\x07"

_RE_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def supports_color(stream: Any = sys.stdout) -> bool:
    """
    Best-effort check whether the target stream likely supports ANSI colors.
    This is intentionally conservative (returns False if uncertain).
    """
    try:
        if os.environ.get("NO_COLOR") is not None:
            return False
        if os.environ.get("TERM") in (None, "", "dumb"):
            return False
        if hasattr(stream, "isatty") and not stream.isatty():
            return False
        # Many modern terminals support color; keep it simple.
        return True
    except Exception:
        return False


def supports_unicode(stream: Any = sys.stdout) -> bool:
    """
    Whether it is safe to output unicode glyphs (bars/spinners).
    """
    try:
        enc = getattr(stream, "encoding", None) or ""
        return "UTF" in enc.upper()
    except Exception:
        return False


def strip_ansi(text: str) -> str:
    return _RE_ANSI.sub("", str(text))


def visible_len(text: str) -> int:
    return len(strip_ansi(text))


def term_size() -> tuple[int, int]:
    """
    Return (columns, rows) with a stable fallback.
    """
    s = get_terminal_size((100, 24))
    return int(s.columns), int(s.lines)


def _isatty() -> bool:
    try:
        return bool(sys.stdout.isatty())
    except Exception:
        return False


def _safe_write(s: str) -> None:
    try:
        sys.stdout.write(s)
        sys.stdout.flush()
    except Exception:
        # If stdout is broken, do nothing rather than crashing.
        pass


# ============================================================
# Terminal control
# ============================================================

def alt_screen_enter() -> None:
    if not _isatty():
        return
    _safe_write(CSI + "?1049h" + CSI + "H")


def alt_screen_exit() -> None:
    if not _isatty():
        return
    _safe_write(CSI + "?1049l")


def clear_screen() -> None:
    if not _isatty():
        # For non-tty output, avoid spamming clear codes.
        _safe_write("\n")
        return
    _safe_write(CSI + "2J" + CSI + "H")


def cursor_hide() -> None:
    if not _isatty():
        return
    _safe_write(CSI + "?25l")


def cursor_show() -> None:
    if not _isatty():
        return
    _safe_write(CSI + "?25h")


# ============================================================
# Styling helpers
# ============================================================

def _maybe(code: str) -> str:
    return code if supports_color() else ""


def c_reset() -> str:
    return _maybe(CSI + "0m")


def c_bold() -> str:
    return _maybe(CSI + "1m")


def c_dim() -> str:
    return _maybe(CSI + "2m")


def c_italic() -> str:
    return _maybe(CSI + "3m")


def c_underline() -> str:
    return _maybe(CSI + "4m")


def c_cyan() -> str:
    return _maybe(CSI + "36m")


def c_green() -> str:
    return _maybe(CSI + "32m")


def c_yellow() -> str:
    return _maybe(CSI + "33m")


def c_red() -> str:
    return _maybe(CSI + "31m")


def c_blue() -> str:
    return _maybe(CSI + "34m")


def c_magenta() -> str:
    return _maybe(CSI + "35m")


def c_gray() -> str:
    # Bright black (often gray)
    return _maybe(CSI + "90m")


def tag(text: str, color: str) -> str:
    """
    Render a bracket tag: [TEXT] with given color.
    """
    return f"{color}{c_bold()}[{text}]{c_reset()}"


def hr(width: Optional[int] = None, char: str = "─") -> str:
    """
    Horizontal rule string. Unicode char is replaced with '-' if unicode unsupported.
    """
    cols, _ = term_size()
    w = int(width) if isinstance(width, int) and width > 0 else cols
    ch = char if supports_unicode() else "-"
    return ch * max(10, w)


def wrap(text: str, width: int) -> str:
    """
    Wrap text to given width. ANSI sequences are not perfectly preserved for wrapping;
    we keep it simple: wrap each line independently and do not attempt to reflow styles.
    """
    width = max(50, int(width))
    lines: list[str] = []
    for ln in str(text).splitlines():
        # If ANSI present, we still wrap based on visible length approximation.
        # This is “good enough” for status lines.
        if "\x1b[" not in ln:
            lines.append(textwrap.fill(ln, width=width))
            continue

        # Naive ANSI-aware wrap: strip ANSI for measuring, but keep original line
        # if it is already short. Otherwise, fall back to stripping ANSI and wrapping.
        if visible_len(ln) <= width:
            lines.append(ln)
        else:
            clean = strip_ansi(ln)
            lines.append(textwrap.fill(clean, width=width))
    return "\n".join(lines)


# ============================================================
# Printing conventions (calm + consistent)
# ============================================================

def print_meta_line(mode: str, route: str, model: str) -> None:
    cols, _ = term_size()
    s = f"{tag('SILI', c_cyan())} {tag(mode, c_yellow())} {c_dim()}•{c_reset()} {route} {c_dim()}•{c_reset()} {tag(model, c_green())}"
    _safe_write(wrap(s, width=min(cols, 120)) + "\n")


def print_info(msg: str) -> None:
    cols, _ = term_size()
    _safe_write(wrap(f"{tag('INFO', c_cyan())} {msg}", width=min(cols, 120)) + "\n")


def print_system(msg: str) -> None:
    cols, _ = term_size()
    _safe_write(wrap(f"{tag('SISTEM AI', c_yellow())} {msg}", width=min(cols, 120)) + "\n")


def print_warn(msg: str) -> None:
    cols, _ = term_size()
    _safe_write(wrap(f"{tag('WARN', c_yellow())} {msg}", width=min(cols, 120)) + "\n")


def print_success(msg: str) -> None:
    cols, _ = term_size()
    _safe_write(wrap(f"{tag('OK', c_green())} {msg}", width=min(cols, 120)) + "\n")


def print_brief_error(msg: str) -> None:
    """
    “Calm error” — short, actionable, no stacktrace.
    Keep the original project convention: '(cek: ai status)'.
    """
    _safe_write(f"{tag('ERROR', c_red())} {msg} (cek: ai status)\n")


def print_exception(prefix: str, ex: BaseException, hint: str = "") -> None:
    """
    For cases where we want to surface an exception in a user-friendly way.
    """
    name = type(ex).__name__
    detail = str(ex).strip()
    if detail:
        print_brief_error(f"{prefix}: {name}: {detail}")
    else:
        print_brief_error(f"{prefix}: {name}")
    if hint:
        print_info(hint)


def print_kv(key: str, value: str, key_color: str = "", pad: int = 14) -> None:
    """
    Simple key-value line. Useful for help/status output.
    """
    kc = key_color or c_dim()
    k = f"{kc}{key:<{pad}}{c_reset()}"
    _safe_write(f"{k}: {value}\n")


# ============================================================
# Input hygiene: discard user input while waiting
# ============================================================

def flush_stdin() -> None:
    """
    Best-effort flush of terminal input buffer.
    On non-POSIX systems or non-tty stdin, this is a no-op.
    """
    try:
        if not sys.stdin.isatty():
            return
        import termios  # type: ignore
        termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)
    except Exception:
        pass


class MuteInputDuringWait:
    """
    Temporarily mute user input while we are showing a spinner:
    - disable echo (typed characters are not printed)
    - flush any buffered input before/after
    Notes:
    - Ctrl+C keeps working (ISIG is not disabled).
    - On environments without termios, this is a safe no-op.
    """
    def __init__(self) -> None:
        self._enabled = False
        self._fd: int | None = None
        self._old_attrs = None

    def __enter__(self):
        flush_stdin()
        try:
            if not sys.stdin.isatty():
                return self
            import termios  # type: ignore

            self._fd = sys.stdin.fileno()
            self._old_attrs = termios.tcgetattr(self._fd)
            new_attrs = termios.tcgetattr(self._fd)

            # local flags (lflags) index = 3
            lflags = new_attrs[3]
            lflags &= ~termios.ECHO

            if hasattr(termios, "ECHOCTL"):
                lflags &= ~termios.ECHOCTL  # type: ignore[attr-defined]

            new_attrs[3] = lflags
            termios.tcsetattr(self._fd, termios.TCSANOW, new_attrs)
            self._enabled = True
        except Exception:
            self._enabled = False

        return self

    def __exit__(self, exc_type, exc, tb):
        flush_stdin()
        if self._enabled and self._fd is not None and self._old_attrs is not None:
            try:
                import termios  # type: ignore
                termios.tcsetattr(self._fd, termios.TCSANOW, self._old_attrs)
            except Exception:
                pass
        flush_stdin()
        return False


def prompt_text(prompt: str) -> str:
    """
    Optional dependency: prompt_toolkit.
    If missing, fallback to built-in input().
    """
    try:
        from prompt_toolkit import PromptSession  # type: ignore
        from prompt_toolkit.formatted_text import ANSI  # type: ignore
        return PromptSession().prompt(ANSI(prompt), mouse_support=False)
    except Exception:
        return input(prompt)


# ============================================================
# Spinners
# ============================================================

@dataclass
class _SpinnerStyle:
    frames: str
    ok_mark: str
    backspace: str = "\b"


def _default_spinner_style() -> _SpinnerStyle:
    if supports_unicode():
        return _SpinnerStyle(frames="⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏", ok_mark="✅")
    return _SpinnerStyle(frames="|/-\\", ok_mark="OK")


class Spinner:
    """
    Spinner for one-shot operations (ask/cmd): includes elapsed time.
    """
    def __init__(self, message: str):
        self.message = message
        self._stop = threading.Event()
        self._mute = MuteInputDuringWait()
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._t0 = 0.0
        self._style = _default_spinner_style()

    def _spin(self) -> None:
        frames = self._style.frames
        i = 0
        _safe_write(f"{c_dim()}{self.message}{c_reset()} ")
        while not self._stop.is_set():
            _safe_write(frames[i % len(frames)])
            time.sleep(0.08)
            _safe_write(self._style.backspace)
            i += 1
        dt = max(0.0, time.time() - self._t0)
        _safe_write(f"{c_green()}{self._style.ok_mark}{c_reset()} {c_dim()}⏱ {dt:.2f}s{c_reset()}\n")

    def __enter__(self):
        self._t0 = time.time()
        self._mute.__enter__()
        if _isatty():
            self._thread.start()
        else:
            # non-tty: just print the message once
            _safe_write(f"{self.message}\n")
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            self._stop.set()
            if self._thread.is_alive():
                self._thread.join(timeout=1)
        finally:
            self._mute.__exit__(exc_type, exc, tb)
        return False


class ChatSpinner:
    """
    Spinner for chat mode:
    - no timer
    - mutes user input while waiting
    """
    def __init__(self, message: str = "Aku lagi mikir..."):
        self.message = message
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._mute = MuteInputDuringWait()
        self._style = _default_spinner_style()

    def _spin(self) -> None:
        frames = self._style.frames
        i = 0
        _safe_write(f"{c_dim()}{self.message}{c_reset()} ")
        while not self._stop.is_set():
            _safe_write(frames[i % len(frames)])
            time.sleep(0.08)
            _safe_write(self._style.backspace)
            i += 1
        _safe_write(f"{c_green()}{self._style.ok_mark}{c_reset()}\n")

    def __enter__(self):
        self._mute.__enter__()
        if _isatty():
            self._thread.start()
        else:
            _safe_write(f"{self.message}\n")
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            self._stop.set()
            if self._thread.is_alive():
                self._thread.join(timeout=1)
        finally:
            self._mute.__exit__(exc_type, exc, tb)
        return False


# ============================================================
# Session endings
# ============================================================

def _goodbye_lines(persona: str, interrupted: bool) -> list[str]:
    if persona == "api":
        if interrupted:
            return [
                "Sili Pinter: Oke, kita stop dulu ya.",
                "Kalau kamu mau lanjut nanti, panggil aku lagi.",
            ]
        return [
            "Sili Pinter: Oke, sesi ngobrol kita aku tutup dulu ya.",
            "Sampai ketemu lagi.",
        ]

    if interrupted:
        return [
            "Sili AI: Oke, aku tangkep. Kita stop dulu ya.",
            "Kalau kamu mau lanjut nanti, panggil aku lagi.",
        ]
    return [
        "Sili AI: Oke, sesi ngobrol kita aku tutup dulu ya.",
        "Sampai ketemu lagi.",
    ]


def print_goodbye(persona: str, interrupted: bool) -> None:
    lines = _goodbye_lines(persona, interrupted)
    _safe_write("\n" + "\n".join(lines) + "\n")
    time.sleep(0.12)
