from __future__ import annotations

import re
import shutil

ESC = "\x1b"
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def term_size() -> tuple[int, int]:
    sz = shutil.get_terminal_size(fallback=(110, 34))
    return sz.columns, sz.lines


def clear() -> None:
    print(f"{ESC}[2J{ESC}[H", end="")


def hide_cursor() -> None:
    print(f"{ESC}[?25l", end="")


def show_cursor() -> None:
    print(f"{ESC}[?25h", end="")


def strip_ansi(s: str) -> str:
    return ANSI_RE.sub("", s)


def vis_len(s: str) -> int:
    return len(strip_ansi(s))


def c_reset() -> str: return f"{ESC}[0m"
def c_dim() -> str: return f"{ESC}[2m"
def c_bold() -> str: return f"{ESC}[1m"
def c_cyan() -> str: return f"{ESC}[36m"
def c_green() -> str: return f"{ESC}[32m"
def c_yellow() -> str: return f"{ESC}[33m"
def c_red() -> str: return f"{ESC}[31m"
def c_magenta() -> str: return f"{ESC}[35m"


def clamp(s: str, width: int) -> str:
    if width <= 0:
        return ""
    if vis_len(s) <= width:
        return s + (" " * (width - vis_len(s)))
    # truncate ansi-safe
    out: list[str] = []
    vis = 0
    i = 0
    while i < len(s) and vis < max(0, width - 1):
        m = ANSI_RE.match(s, i)
        if m:
            out.append(m.group(0))
            i = m.end()
            continue
        out.append(s[i])
        vis += 1
        i += 1
    out.append("…")
    out.append(c_reset())
    return "".join(out)


def wrap_line(text: str, width: int) -> list[str]:
    t = strip_ansi(text)
    if width <= 6:
        return [t[:width]]
    words = t.split()
    lines: list[str] = []
    cur: list[str] = []
    cur_len = 0
    for w in words:
        add = len(w) + (1 if cur else 0)
        if cur_len + add > width:
            lines.append(" ".join(cur))
            cur = [w]
            cur_len = len(w)
        else:
            cur.append(w)
            cur_len += add
    if cur:
        lines.append(" ".join(cur))
    return lines or [""]


def badge_ok(text: str = "OK") -> str:
    return f"{c_green()}{c_bold()}[{text}]{c_reset()}"


def badge_warn(text: str = "WARN") -> str:
    return f"{c_yellow()}{c_bold()}[{text}]{c_reset()}"


def badge_bad(text: str = "BAD") -> str:
    return f"{c_red()}{c_bold()}[{text}]{c_reset()}"


def badge_info(text: str = "INFO") -> str:
    return f"{c_cyan()}{c_bold()}[{text}]{c_reset()}"


def section(title: str) -> str:
    return f"{c_bold()}{c_cyan()}{title}{c_reset()}"


def title_line(left: str, right: str, cols: int) -> str:
    left_col = f"{c_magenta()}{c_bold()}AI STATUS{c_reset()}  {c_dim()}•{c_reset()}  {c_bold()}{left}{c_reset()}"
    gap = cols - vis_len(left_col) - len(right)
    if gap < 1:
        return clamp(left_col, cols)
    return left_col + (" " * gap) + right


def footer_line(msg: str, cols: int) -> str:
    base = f"{c_dim()}[1-3] action{c_reset()}  {c_dim()}r refresh{c_reset()}  {c_dim()}q quit{c_reset()}"
    if msg:
        base = base + f"  {c_dim()}|{c_reset()}  " + msg
    return clamp(base, cols)
