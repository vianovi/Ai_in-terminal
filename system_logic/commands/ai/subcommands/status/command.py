from __future__ import annotations

import sys

from .app import run_dashboard
from .collectors import collect_snapshot
from .actions import deep_local, deep_api
from .deep_select import select_mode


def handle(argv: list[str], cfg: dict) -> int:
    args = [a.strip() for a in (argv or []) if a.strip()]

    # Plain report: no fullscreen, no action, no prompt
    if "--plain" in args:
        s = collect_snapshot(cfg)
        print(f"AI STATUS  {s.timestamp}\n")
        print("Workspace")
        print(f"- Repo root   : {s.workspace or '(tidak terdeteksi)'}")
        print(f"- Logic path  : {s.paths.logic}")
        print(f"- Config      : {s.paths.config}")
        print(f"- Memory      : {s.paths.memory}\n")

        print("Active Settings")
        print(f"- backend_mode: {s.routing.backend_mode}")
        print(f"- api.provider: {s.routing.api_provider}")
        print(f"- api.model   : {s.routing.api_active_model or '(unset)'}")
        print(f"- local.ask   : {s.routing.local_ask or '(unset)'}")
        print(f"- local.cmd   : {s.routing.local_cmd or '(unset)'}\n")

        print("LOCAL")
        print(f"- status      : {s.local.state}")
        print(f"- host        : {s.local.host}")
        print(f"- latency     : {s.local.latency_ms}ms")
        print(f"- models_count: {s.local.models_count}")
        print(f"- note        : {s.local.detail}\n")

        print("API (shallow)")
        print(f"- provider    : {s.api.provider}")
        print(f"- model       : {s.api.active_model or '(unset)'}")
        print(f"- key         : {'SET' if s.api.key_present else 'MISSING'}")
        print(f"- config      : {'OK' if s.api.config_ok else 'BAD'}")
        if s.api.note:
            print(f"- note        : {s.api.note}")
        if s.api.detail:
            print(f"- detail      : {s.api.detail}")
        print("- connection  : NOT TESTED\n")

        print("Registry")
        print(f"- status      : {'OK' if s.registry.ok else 'BAD'}")
        print(f"- detail      : {s.registry.detail}\n")

        print("Conclusion")
        for d in s.conclusion:
            print(f"- {d}")
        if s.next_actions:
            print("\nNext actions")
            for a in s.next_actions:
                print(f"- {a}")

        if s.last_error.exists:
            print("\nLast error (brief)")
            print(f"- time   : {s.last_error.time}")
            print(f"- stage  : {s.last_error.stage}")
            print(f"- backend: {s.last_error.backend}")
            if s.last_error.summary:
                print(f"- summary: {s.last_error.summary}")

        return 0

    # Deep selector (interactive mini screen)
    if "--deep" in args:
        mode = select_mode()
        s = collect_snapshot(cfg)
        if mode == "cancel":
            print("Dibatalkan.")
            return 0
        if mode == "local":
            ok, msg = deep_local(cfg, s)
            print(msg)
            return 0 if ok else 2
        if mode == "api":
            ok, msg = deep_api(cfg, s)
            print(msg)
            return 0 if ok else 2
        if mode == "all":
            ok1, msg1 = deep_local(cfg, s)
            ok2, msg2 = deep_api(cfg, s)
            print(msg1)
            print(msg2)
            return 0 if (ok1 and ok2) else 2
        return 0

    # Default: fullscreen dashboard (but fallback to plain if not tty)
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        # safety fallback for non-interactive
        return handle(["--plain"], cfg)

    return run_dashboard(cfg)
