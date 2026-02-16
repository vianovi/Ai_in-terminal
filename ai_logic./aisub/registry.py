from typing import Protocol, Any

# Import module implementasi
from . import status
from . import help
from . import run
from . import mon
from . import gitx
from . import ghx

# Protocol untuk type hinting handler module
class CommandModule(Protocol):
    def handle(self, argv: list[str], cfg: dict) -> int:
        ...

# Single source of truth
COMMANDS: dict[str, Any] = {
    "status": status,
    "help": help,
    "run": run,
    "mon": mon,
    "gitx": gitx,
    "ghx": ghx,
}

def exists(name: str) -> bool:
    return name in COMMANDS

def get_module(name: str) -> Any | None:
    return COMMANDS.get(name)

def get_all_commands() -> list[str]:
    return list(COMMANDS.keys())

def dispatch(name: str, argv: list[str], cfg: dict) -> int:
    module = COMMANDS.get(name)
    if not module:
        return 1
    return module.handle(argv, cfg)