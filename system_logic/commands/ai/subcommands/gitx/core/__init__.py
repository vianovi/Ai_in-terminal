"""
GitX Core Package.
Git engine, subprocess helpers, RepoSnapshot.
"""

from .git import (
    run_git,
    ensure_git_exists,
    find_repo_root,
    collect_snapshot,
    RepoSnapshot,
)

__all__ = [
    "run_git",
    "ensure_git_exists",
    "find_repo_root",
    "collect_snapshot",
    "RepoSnapshot",
]