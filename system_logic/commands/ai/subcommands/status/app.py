from __future__ import annotations

from . import ui
from .keys import read_key
from .collectors import collect_snapshot
from .actions import deep_local, deep_api
from .models import DashState, Snapshot


def _dnf_hint(mod: str) -> str:
    mapping = {
        "psutil": "sudo dnf install -y python3-psutil",
        "json5": "sudo dnf install -y python3-json5",
        "prompt_toolkit": "sudo dnf install -y python3-prompt-toolkit",
    }
    return mapping.get(mod, "")


def _badge_for_local(s: Snapshot) -> str:
    st = (s.local.state or "").upper()
    if st == "READY":
        return ui.badge_ok("READY")
    if st == "SLOW":
        return ui.badge_warn("SLOW")
    if st == "DOWN":
        return ui.badge_bad("DOWN")
    return ui.badge_bad("ERROR")


def _badge_for_api(s: Snapshot) -> str:
    if s.api.config_ok and s.api.key_present:
        return ui.badge_ok("OK")
    if s.api.key_present and not s.api.config_ok:
        return ui.badge_bad("BAD")
    return ui.badge_warn("CHECK")


def _render_snapshot(s: Snapshot, state: DashState) -> None:
    cols, rows = ui.term_size()
    ui.clear()

    # Title line
    print(ui.clamp(ui.title_line("Dashboard", s.timestamp, cols), cols))
    print(ui.clamp(f"{ui.c_dim()}Status ini menjawab: kondisi sistem kamu sekarang gimana.{ui.c_reset()}", cols))
    print("")

    # Quick summary line
    quick = (
        f"Mode: {s.routing.backend_mode}    "
        f"API: {s.routing.api_provider or '-'} ({_badge_for_api(s)})    "
        f"Local: {_badge_for_local(s)} {s.local.latency_ms}ms"
    )
    print(ui.clamp(quick, cols))
    print("")

    # Workspace
    print(ui.section("Workspace"))
    ws = s.workspace or "(tidak terdeteksi otomatis)"
    print(ui.clamp(f"- Repo root     : {ws}", cols))
    print(ui.clamp(f"- Logic path    : {s.paths.logic}", cols))
    print(ui.clamp(f"- Config        : {s.paths.config}", cols))
    print(ui.clamp(f"- Memory        : {s.paths.memory}", cols))
    print("")

    # Active Settings
    print(ui.section("Active Settings"))
    print(ui.clamp(f"- backend_mode  : {s.routing.backend_mode}", cols))
    print(ui.clamp(f"- api.provider  : {s.routing.api_provider}", cols))
    print(ui.clamp(f"- api.model     : {s.routing.api_active_model or '(unset)'}", cols))
    print(ui.clamp(f"- local.ask     : {s.routing.local_ask or '(unset)'}", cols))
    print(ui.clamp(f"- local.cmd     : {s.routing.local_cmd or '(unset)'}", cols))
    print("")

    # Dependencies
    print(ui.section("Dependencies"))
    ps = ui.badge_ok("OK") if s.deps.psutil else ui.badge_warn("MISSING")
    j5 = ui.badge_ok("OK") if s.deps.json5 else ui.badge_warn("OPTIONAL")
    pt = ui.badge_ok("OK") if s.deps.prompt_toolkit else ui.badge_warn("OPTIONAL")
    cb = s.deps.clipboard or "(none)"
    print(ui.clamp(f"- psutil         : {ps}  {'' if s.deps.psutil else _dnf_hint('psutil')}", cols))
    print(ui.clamp(f"- json5          : {j5}  {'' if s.deps.json5 else _dnf_hint('json5')}", cols))
    print(ui.clamp(f"- prompt_toolkit : {pt}  {'' if s.deps.prompt_toolkit else _dnf_hint('prompt_toolkit')}", cols))
    print(ui.clamp(f"- clipboard      : {cb}", cols))
    print("")

    # LOCAL
    print(ui.section("LOCAL (Ollama)"))
    print(ui.clamp(f"- status         : {_badge_for_local(s)}", cols))
    print(ui.clamp(f"- host           : {s.local.host}", cols))
    print(ui.clamp(f"- latency        : {s.local.latency_ms}ms", cols))
    print(ui.clamp(f"- models_count   : {s.local.models_count}", cols))
    if s.local.models_count > 0 and not s.local.selected_ok:
        print(ui.clamp(f"- selected_ok    : {ui.badge_bad('NO')} (model di config tidak cocok dengan `ollama list`)", cols))
    print(ui.clamp(f"- note           : {s.local.detail}", cols))
    print("")

    # API (shallow)
    print(ui.section("API"))
    print(ui.clamp(f"- provider       : {s.api.provider}", cols))
    print(ui.clamp(f"- active_model   : {s.api.active_model or '(unset)'}", cols))
    print(ui.clamp(f"- api key        : {ui.badge_ok('SET') if s.api.key_present else ui.badge_bad('MISSING')}", cols))
    print(ui.clamp(f"- config valid   : {ui.badge_ok('OK') if s.api.config_ok else ui.badge_bad('BAD')}  {s.api.note}", cols))
    if s.api.detail:
        for ln in ui.wrap_line(f"detail: {s.api.detail}", width=min(cols, 110)):
            print(ui.clamp(f"- {ln}", cols))
    print(ui.clamp(f"- connection     : {ui.badge_info('NOT TESTED')} (deep test diperlukan jika mau validasi koneksi)", cols))
    print("")

    # Registry
    print(ui.section("Registry / Router"))
    print(ui.clamp(f"- status         : {ui.badge_ok('OK') if s.registry.ok else ui.badge_bad('BAD')}", cols))
    for ln in ui.wrap_line(s.registry.detail, width=min(cols, 110)):
        print(ui.clamp(f"- detail         : {ln}", cols))
    print("")

    # Conclusion
    print(ui.section("Conclusion"))
    for d in s.conclusion[:6]:
        for ln in ui.wrap_line(d, width=min(cols, 110)):
            print(ui.clamp(f"- {ln}", cols))
    if s.next_actions:
        print(ui.clamp("", cols))
        print(ui.clamp(f"{ui.c_dim()}Next actions:{ui.c_reset()}", cols))
        for a in s.next_actions[:4]:
            for ln in ui.wrap_line(a, width=min(cols, 110)):
                print(ui.clamp(f"  • {ln}", cols))
    print("")

    # Last error (brief)
    print(ui.section("Last Error (brief)"))
    if not s.last_error.exists:
        print(ui.clamp(f"- {ui.badge_ok('NONE')} tidak ada error terakhir tercatat.", cols))
    else:
        print(ui.clamp(f"- time           : {s.last_error.time}", cols))
        print(ui.clamp(f"- stage          : {s.last_error.stage}", cols))
        print(ui.clamp(f"- backend        : {s.last_error.backend}", cols))
        if s.last_error.summary:
            for ln in ui.wrap_line(s.last_error.summary, width=min(cols, 110)):
                print(ui.clamp(f"- summary        : {ln}", cols))
    print("")

    # Actions
    print(ui.section("Actions"))
    print(ui.clamp("[1] Deep test LOCAL   (inference + latency)", cols))
    print(ui.clamp("[2] Deep test API     (remote inference, quota)", cols))
    print(ui.clamp("[3] Deep test ALL     (LOCAL + API)", cols))
    print(ui.clamp("", cols))

    # Footer
    msg = ""
    if state.message:
        if state.message_kind == "ok":
            msg = ui.badge_ok("OK") + " " + state.message
        elif state.message_kind == "warn":
            msg = ui.badge_warn("WARN") + " " + state.message
        elif state.message_kind == "bad":
            msg = ui.badge_bad("BAD") + " " + state.message
        else:
            msg = state.message
    print(ui.clamp(ui.footer_line(msg, cols), cols))


def run_dashboard(cfg: dict) -> int:
    state = DashState()
    snap = collect_snapshot(cfg)

    ui.hide_cursor()
    try:
        while True:
            _render_snapshot(snap, state)
            state.message = ""
            state.message_kind = ""

            k = read_key()

            if k.name == "q":
                return 0

            if k.name == "r":
                snap = collect_snapshot(cfg)
                state.message = "Status diperbarui."
                state.message_kind = "ok"
                continue

            if k.name == "num":
                if k.raw == "1":
                    ok, msg = deep_local(cfg, snap)
                    snap = collect_snapshot(cfg)
                    state.message = msg
                    state.message_kind = "ok" if ok else "bad"
                    continue
                if k.raw == "2":
                    ok, msg = deep_api(cfg, snap)
                    snap = collect_snapshot(cfg)
                    state.message = msg
                    state.message_kind = "ok" if ok else "bad"
                    continue
                if k.raw == "3":
                    ok1, msg1 = deep_local(cfg, snap)
                    ok2, msg2 = deep_api(cfg, snap)
                    snap = collect_snapshot(cfg)
                    if ok1 and ok2:
                        state.message = f"{msg1} | {msg2}"
                        state.message_kind = "ok"
                    else:
                        state.message = f"{msg1} | {msg2}"
                        state.message_kind = "bad"
                    continue

            # ignore lainnya
    finally:
        ui.show_cursor()
        ui.clear()
