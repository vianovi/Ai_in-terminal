from __future__ import annotations

import sys
import textwrap
import threading
import time
from shutil import get_terminal_size
from typing import Optional


CSI = "\x1b["


def term_size() -> tuple[int, int]:
    s = get_terminal_size((100, 24))
    return int(s.columns), int(s.lines)


def alt_screen_enter() -> None:
    sys.stdout.write(CSI + "?1049h" + CSI + "H")
    sys.stdout.flush()


def alt_screen_exit() -> None:
    sys.stdout.write(CSI + "?1049l")
    sys.stdout.flush()


def clear_screen() -> None:
    sys.stdout.write(CSI + "2J" + CSI + "H")
    sys.stdout.flush()


def cursor_hide() -> None:
    sys.stdout.write(CSI + "?25l")
    sys.stdout.flush()


def cursor_show() -> None:
    sys.stdout.write(CSI + "?25h")
    sys.stdout.flush()


def c_reset() -> str:
    return CSI + "0m"


def c_bold() -> str:
    return CSI + "1m"


def c_dim() -> str:
    return CSI + "2m"


def c_cyan() -> str:
    return CSI + "36m"


def c_green() -> str:
    return CSI + "32m"


def c_yellow() -> str:
    return CSI + "33m"


def c_red() -> str:
    return CSI + "31m"


def tag(text: str, color: str) -> str:
    return f"{color}{c_bold()}[{text}]{c_reset()}"


def wrap(text: str, width: int) -> str:
    width = max(50, int(width))
    lines: list[str] = []
    for ln in str(text).splitlines():
        lines.append(textwrap.fill(ln, width=width))
    return "\n".join(lines)


def print_meta_line(mode: str, route: str, model: str) -> None:
    cols, _ = term_size()
    s = f"{tag('SILI', c_cyan())} {tag(mode, c_yellow())} {c_dim()}•{c_reset()} {route} {c_dim()}•{c_reset()} {tag(model, c_green())}"
    sys.stdout.write(wrap(s, width=min(cols, 120)) + "\n")
    sys.stdout.flush()


def print_info(msg: str) -> None:
    cols, _ = term_size()
    sys.stdout.write(wrap(f"{tag('INFO', c_cyan())} {msg}", width=min(cols, 120)) + "\n")
    sys.stdout.flush()


def print_system(msg: str) -> None:
    cols, _ = term_size()
    sys.stdout.write(wrap(f"{tag('SISTEM AI', c_yellow())} {msg}", width=min(cols, 120)) + "\n")
    sys.stdout.flush()


def print_brief_error(msg: str) -> None:
    sys.stdout.write(f"{tag('ERROR', c_red())} {msg} (cek: ai status)\n")
    sys.stdout.flush()


# ============================================================
# Input hygiene: buang input saat menunggu spinner
# ============================================================

def flush_stdin() -> None:
    try:
        import termios  # type: ignore
        termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)
    except Exception:
        pass


class MuteInputDuringWait:
    """
    Membisukan input user saat menunggu:
    - Matikan echo (ketikan tidak tampil)
    - Buang semua ketikan saat menunggu (flush)
    Catatan: Ctrl+C tetap berfungsi (ISIG tidak dimatikan).
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
    Dependency opsional: prompt_toolkit.
    Kalau tidak tersedia, fallback ke input().
    """
    try:
        from prompt_toolkit import PromptSession  # type: ignore
        from prompt_toolkit.formatted_text import ANSI  # type: ignore
        return PromptSession().prompt(ANSI(prompt), mouse_support=False)
    except Exception:
        return input(prompt)


class Spinner:
    """
    Spinner untuk one-shot (ask/cmd): tampilkan timer.
    """
    def __init__(self, message: str):
        self.message = message
        self._stop = threading.Event()
        self._mute = MuteInputDuringWait()
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._t0 = 0.0

    def _spin(self) -> None:
        frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        i = 0
        sys.stdout.write(f"{c_dim()}{self.message}{c_reset()} ")
        sys.stdout.flush()
        while not self._stop.is_set():
            sys.stdout.write(frames[i % len(frames)])
            sys.stdout.flush()
            time.sleep(0.08)
            sys.stdout.write("\b")
            i += 1
        dt = max(0.0, time.time() - self._t0)
        sys.stdout.write(f"{c_green()}✅{c_reset()} {c_dim()}⏱ {dt:.2f}s{c_reset()}\n")
        sys.stdout.flush()

    def __enter__(self):
        self._t0 = time.time()
        self._mute.__enter__()
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            self._stop.set()
            self._thread.join(timeout=1)
        finally:
            self._mute.__exit__(exc_type, exc, tb)
        return False


class ChatSpinner:
    """
    Spinner untuk mode chat:
    - Tanpa timer
    - Input user dibisukan total saat menunggu (echo off + flush)
    """
    def __init__(self, message: str = "Aku lagi mikir..."):
        self.message = message
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._mute = MuteInputDuringWait()

    def _spin(self) -> None:
        frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        i = 0
        sys.stdout.write(f"{c_dim()}{self.message}{c_reset()} ")
        sys.stdout.flush()
        while not self._stop.is_set():
            sys.stdout.write(frames[i % len(frames)])
            sys.stdout.flush()
            time.sleep(0.08)
            sys.stdout.write("\b")
            i += 1
        sys.stdout.write(f"{c_green()}✅{c_reset()}\n")
        sys.stdout.flush()

    def __enter__(self):
        self._mute.__enter__()
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            self._stop.set()
            self._thread.join(timeout=1)
        finally:
            self._mute.__exit__(exc_type, exc, tb)
        return False


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
    sys.stdout.write("\n" + "\n".join(lines) + "\n")
    sys.stdout.flush()
    time.sleep(0.12)
