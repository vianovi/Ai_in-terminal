from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

RenderFn = Callable[["Result"], None]
HandleFn = Callable[[list[str], Any], "Result"]  # (argv, cfg) -> Result


@dataclass
class Result:
    ok: bool
    exit_code: int = 0
    data: Any = None
    error: Optional[str] = None
    render: Optional[RenderFn] = None

    @staticmethod
    def success(data: Any = None, exit_code: int = 0, render: Optional[RenderFn] = None) -> "Result":
        return Result(ok=True, exit_code=exit_code, data=data, render=render)

    @staticmethod
    def fail(msg: str, exit_code: int = 1, data: Any = None, render: Optional[RenderFn] = None) -> "Result":
        return Result(ok=False, exit_code=exit_code, error=msg, data=data, render=render)
