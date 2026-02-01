from __future__ import annotations

import sys
import termios
import tty
from dataclasses import dataclass


@dataclass(frozen=True)
class Key:
    name: str  # "up" | "down" | "enter" | "esc" | "q" | "r" | "other"
    raw: str


def read_key() -> Key:
    """
    Raw key reader (tanpa curses).
    - Arrow keys: ESC [ A/B
    - Enter: \r / \n
    - Esc: \x1b
    """
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch1 = sys.stdin.read(1)
        if ch1 == "\x1b":
            # maybe arrow
            ch2 = sys.stdin.read(1)
            if ch2 == "[":
                ch3 = sys.stdin.read(1)
                if ch3 == "A":
                    return Key("up", "\x1b[A")
                if ch3 == "B":
                    return Key("down", "\x1b[B")
                if ch3 == "C":
                    return Key("right", "\x1b[C")
                if ch3 == "D":
                    return Key("left", "\x1b[D")
                return Key("esc", "\x1b[" + ch3)
            return Key("esc", "\x1b" + ch2)

        if ch1 in ("\r", "\n"):
            return Key("enter", ch1)

        low = ch1.lower()
        if low == "q":
            return Key("q", ch1)
        if low == "r":
            return Key("r", ch1)
        if low == "j":
            return Key("down", ch1)
        if low == "k":
            return Key("up", ch1)

        return Key("other", ch1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
