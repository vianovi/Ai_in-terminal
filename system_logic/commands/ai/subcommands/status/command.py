from __future__ import annotations

import sys

from .app import run_tui
from .collectors import collect_snapshot
from .actions import deep_test_local, self_test_api


def handle(argv: list[str], cfg: dict) -> int:
    args = [a.strip() for a in (argv or []) if a.strip()]

    # Non-interactive or forced plain
    plain = ("--plain" in args) or (not sys.stdout.isatty()) or (not sys.stdin.isatty())

    if "--deep" in args:
        snap = collect_snapshot(cfg)
        ok, msg = deep_test_local(cfg, snap)
        print(msg)
        return 0 if ok else 2

    if "--api-test" in args:
        snap = collect_snapshot(cfg)
        ok, msg = self_test_api(cfg, snap)
        print(msg)
        return 0 if ok else 2

    if "--last-error" in args:
        snap = collect_snapshot(cfg)
        if not snap.last_error.exists:
            print("No last error recorded.")
            return 0
        print(f"Time   : {snap.last_error.time}")
        print(f"Stage  : {snap.last_error.stage}")
        print(f"Backend: {snap.last_error.backend}\n")
        print(snap.last_error.detail)
        return 0

    if plain:
        s = collect_snapshot(cfg)
        print(f"AI STATUS • {s.timestamp}")
        print(f"- Mode    : {s.routing.backend_mode}")
        print(f"- Local   : {s.local.state} • {s.local.host} • {s.local.latency_ms or '-'}ms")
        print(f"- API     : {s.api.provider} • config={'OK' if s.api.config_ok else 'BAD'} • model={s.api.active_model or '-'}")
        print(f"- Registry: {'OK' if s.registry.ok else 'BAD'} • {s.registry.message}")
        if s.last_error.exists:
            print(f"- LastErr : {s.last_error.stage} • {s.last_error.backend} • {s.last_error.time}")
        else:
            print("- LastErr : none")
        return 0

    # TUI
    return run_tui(cfg)
