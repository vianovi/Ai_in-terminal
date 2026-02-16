from __future__ import annotations

import sys
import termios
import tty
from dataclasses import dataclass


@dataclass(frozen=True)
class Key:
    name: str  # up/down/enter/q/r/num/esc/other
    raw: str


def read_key() -> Key:
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch1 = sys.stdin.read(1)

        if ch1 == "\x1b":
            ch2 = sys.stdin.read(1)
            if ch2 == "[":
                ch3 = sys.stdin.read(1)
                if ch3 == "A":
                    return Key("up", "\x1b[A")
                if ch3 == "B":
                    return Key("down", "\x1b[B")
                return Key("esc", "\x1b[" + ch3)
            return Key("esc", "\x1b" + ch2)

        if ch1 in ("\r", "\n"):
            return Key("enter", ch1)

        low = ch1.lower()
        if low == "q":
            return Key("q", ch1)
        if low == "r":
            return Key("r", ch1)

        if ch1.isdigit():
            return Key("num", ch1)

        return Key("other", ch1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
