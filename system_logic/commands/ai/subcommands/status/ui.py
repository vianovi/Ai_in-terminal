from __future__ import annotations

import re
import shutil

ESC = "\x1b"
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

# ---- Colors (simple + consistent) ----
def c_reset() -> str: return f"{ESC}[0m"
def c_dim() -> str: return f"{ESC}[2m"
def c_bold() -> str: return f"{ESC}[1m"

def c_cyan() -> str: return f"{ESC}[36m"
def c_green() -> str: return f"{ESC}[32m"
def c_yellow() -> str: return f"{ESC}[33m"
def c_red() -> str: return f"{ESC}[31m"
def c_magenta() -> str: return f"{ESC}[35m"

# background highlight for selected row (subtle)
def c_sel_bg() -> str: return f"{ESC}[48;5;237m"   # dark gray background
def c_sel_fg() -> str: return f"{ESC}[38;5;231m"   # near-white fg


def term_size() -> tuple[int, int]:
    sz = shutil.get_terminal_size(fallback=(100, 30))
    return sz.columns, sz.lines


def clear() -> None:
    print(f"{ESC}[2J{ESC}[H", end="")


def hide_cursor() -> None:
    print(f"{ESC}[?25l", end="")


def show_cursor() -> None:
    print(f"{ESC}[?25h", end="")


def move(row: int, col: int) -> None:
    print(f"{ESC}[{row};{col}H", end="")


def strip_ansi(s: str) -> str:
    return ANSI_RE.sub("", s)


def visible_len(s: str) -> int:
    return len(strip_ansi(s))


def clamp(s: str, width: int) -> str:
    """ANSI-aware clamp/pad."""
    if width <= 0:
        return ""
    raw = s or ""
    vis = visible_len(raw)
    if vis == width:
        return raw
    if vis < width:
        return raw + (" " * (width - vis))

    # Need truncate while keeping ANSI sequences
    out = []
    vis_count = 0
    i = 0
    while i < len(raw) and vis_count < max(0, width - 1):
        m = ANSI_RE.match(raw, i)
        if m:
            out.append(m.group(0))
            i = m.end()
            continue
        out.append(raw[i])
        vis_count += 1
        i += 1
    out.append("…")
    out.append(c_reset())
    return "".join(out)


def wrap(text: str, width: int) -> list[str]:
    if width <= 3:
        return [""]
    words = (text or "").split()
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
    if not lines:
        return [""]
    return lines


def header_line(title: str, right: str, cols: int) -> str:
    left = f"{c_bold()}{c_magenta()}AI STATUS{c_reset()}  {c_dim()}•{c_reset()}  {c_bold()}{title}{c_reset()}"
    space = cols - visible_len(left) - len(right)
    if space < 1:
        return clamp(left, cols)
    return left + (" " * space) + right


def footer_line(message: str, cols: int) -> str:
    base = f"{c_dim()}↑/↓ or k/j{c_reset()} • {c_bold()}Enter{c_reset()} • {c_dim()}r refresh{c_reset()} • {c_dim()}q quit{c_reset()}"
    if message:
        base = base + f"  {c_dim()}|{c_reset()}  {message}"
    return clamp(base, cols)


def pill(text: str, tone: str) -> str:
    # tone: "view" | "action" | "ok" | "warn" | "bad"
    if tone == "action":
        return f"{c_green()}{c_bold()}[{text}]{c_reset()}"
    if tone == "view":
        return f"{c_cyan()}{c_bold()}[{text}]{c_reset()}"
    if tone == "ok":
        return f"{c_green()}{c_bold()}[{text}]{c_reset()}"
    if tone == "warn":
        return f"{c_yellow()}{c_bold()}[{text}]{c_reset()}"
    if tone == "bad":
        return f"{c_red()}{c_bold()}[{text}]{c_reset()}"
    return f"[{text}]"


def render_two_column(
    cols: int,
    rows: int,
    menu_title: str,
    menu_items: list[str],
    selected: int,
    panel_title: str,
    panel_lines: list[str],
    footer_msg: str,
    timestamp: str,
) -> None:
    clear()

    left_w = max(28, min(42, cols // 3))
    right_w = max(20, cols - left_w - 3)
    top = 1

    # Header
    move(top, 1)
    print(clamp(header_line(panel_title, timestamp, cols), cols), end="")

    # Separator
    move(top + 1, 1)
    print(clamp(f"{c_dim()}" + ("─" * cols) + f"{c_reset()}", cols), end="")

    # Column titles
    move(top + 2, 1)
    print(clamp(f"{pill(menu_title, 'view')}", left_w), end="")
    move(top + 2, left_w + 2)
    print(f"{c_dim()}│{c_reset()}", end="")
    move(top + 2, left_w + 4)
    print(clamp(f"{pill(panel_title, 'view')}", right_w), end="")

    body_top = top + 3
    body_bottom = rows - 2
    body_h = max(1, body_bottom - body_top + 1)

    # Body render
    for i in range(body_h):
        row = body_top + i
        move(row, 1)

        # left cell
        if i < len(menu_items):
            raw = menu_items[i]
            if i == selected:
                raw = f"{c_sel_bg()}{c_sel_fg()}{raw}{c_reset()}"
            print(clamp(raw, left_w), end="")
        else:
            print(" " * left_w, end="")

        move(row, left_w + 2)
        print(f"{c_dim()}│{c_reset()}", end="")

        move(row, left_w + 4)
        if i < len(panel_lines):
            print(clamp(panel_lines[i], right_w), end="")
        else:
            print(" " * right_w, end="")

    # Footer
    move(rows, 1)
    print(clamp(f"{c_dim()}" + ("─" * cols) + f"{c_reset()}", cols), end="")
    move(rows + 1, 1)
    print(footer_line(footer_msg, cols), end="")
    print("", end="", flush=True)
