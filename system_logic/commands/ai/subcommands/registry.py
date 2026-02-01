from __future__ import annotations

from typing import Any

from .help import command as help_cmd
from .status import command as status_cmd
from .run import command as run_cmd
from .mon import command as mon_cmd
from .gitx import command as gitx_cmd
from .ghx import command as ghx_cmd

COMMANDS: dict[str, Any] = {
    "help": help_cmd,
    "status": status_cmd,
    "run": run_cmd,
    "mon": mon_cmd,
    "gitx": gitx_cmd,
    "ghx": ghx_cmd,
}


def exists(name: str) -> bool:
    return name in COMMANDS


def get_all_commands() -> list[str]:
    return sorted(COMMANDS.keys())


def get_module(name: str) -> Any:
    return COMMANDS[name]


def dispatch(name: str, argv: list[str], cfg: dict) -> int:
    mod = COMMANDS.get(name)
    if mod is None:
        return 2
    fn = getattr(mod, "handle", None)
    if not callable(fn):
        return 2
    return int(fn(argv, cfg))
