"""
MON - System Monitoring & Intelligence Framework.

Modular architecture untuk monitoring sistem dengan fitur:
- Real-time dashboard (CPU, RAM, Network, Disk, Thermal)
- OOM Killer (Sentinel) dengan 3-phase logic
- History tracking dengan atomic writes
- Battery & Disk health monitoring
- Network diagnostics

Usage:
    from mon.command import handle

    # Router CLI
    exit_code = handle(argv, config)
"""

from .command import handle

__version__ = "5.0.0-refactor"
__all__ = ["handle"]