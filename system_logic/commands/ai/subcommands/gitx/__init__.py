"""
GitX — Git Cockpit (Safe Actions + Optional AI via API).

Modular architecture mengikuti pola framework system_logic:
- core/git.py      : subprocess git engine + RepoSnapshot
- actions/repo.py  : safe git actions (non-AI)
- actions/ai.py    : AI features (summary, commit msg, chat)
- ui/render.py     : semua display & rendering logic
- command.py       : CLI router

Usage:
    from .command import handle

    exit_code = handle(argv, cfg)
"""

from .command import handle

__version__ = "2.0.0-refactor"
__all__ = ["handle"]
