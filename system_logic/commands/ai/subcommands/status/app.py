# system_logic/commands/ai/subcommands/status/app.py
from __future__ import annotations

from . import collectors, ui
from .actions import deep_test_local, self_test_api
from .models import AppState, MenuItem, Snapshot
from .keys import read_key


def _summary_view(s: Snapshot) -> str:
    local_tone = "ok" if s.local.state == "READY" else ("warn" if s.local.state == "SLOW" else "bad")
    reg_tone = "ok" if s.registry.ok else "bad"
    api_tone = "ok" if s.api.config_ok else "bad"

    lines: list[str] = []
    lines.append(f"{ui.pill('MODE', 'view')} {s.routing.backend_mode}")
    lines.append(
        f"{ui.pill('API', api_tone)} {s.api.provider} • model={s.api.active_model or '-'} • config={'OK' if s.api.config_ok else 'BAD'}"
    )
    lines.append(
        f"{ui.pill('LOCAL', local_tone)} {s.local.state} • {s.local.host} • {s.local.latency_ms or '-'}ms"
    )
    lines.append(f"{ui.pill('REG', reg_tone)} {'OK' if s.registry.ok else 'BAD'} • {s.registry.message}")
    if s.last_error.exists:
        lines.append(
            f"{ui.pill('LASTERR', 'warn')} {s.last_error.stage} • {s.last_error.backend} • {s.last_error.time}"
        )
    else:
        lines.append(f"{ui.pill('LASTERR', 'ok')} none")
    lines.append("")
    lines.append(f"{ui.c_dim()}Tips:{ui.c_reset()} pilih menu kiri, tekan Enter untuk action, r untuk refresh.")
    return "\n".join(lines)


def _local_view(s: Snapshot) -> str:
    tone = "ok" if s.local.state == "READY" else ("warn" if s.local.state == "SLOW" else "bad")
    return "\n".join(
        [
            f"{ui.pill('LOCAL', tone)} {s.local.state}",
            "",
            f"Host    : {s.local.host}",
            f"Latency : {s.local.latency_ms or '-'} ms",
            f"Detail  : {s.local.detail}",
            "",
            f"Model ask: {s.local.model_ask or '-'}",
            f"Model cmd: {s.local.model_cmd or '-'}",
            "",
            f"{ui.c_dim()}Action:{ui.c_reset()} jalankan {ui.pill('Run LOCAL deep test', 'action')} untuk uji inference + latency.",
        ]
    )


def _api_view(s: Snapshot) -> str:
    tone = "ok" if s.api.config_ok else "bad"
    key = "SET" if s.api.key_present else "MISSING"
    key_tone = "ok" if s.api.key_present else "bad"
    return "\n".join(
        [
            f"{ui.pill('API', tone)} {s.api.provider}",
            "",
            f"Active  : {s.api.active_model or '-'}",
            f"Key     : {ui.pill(key, key_tone)}",
            f"Config  : {'OK' if s.api.config_ok else 'BAD'}",
            f"Note    : {s.api.note}",
            f"Detail  : {s.api.detail}",
            "",
            f"{ui.c_dim()}Action:{ui.c_reset()} jalankan {ui.pill('Run API self-test', 'action')} (opsional) untuk tes cepat.",
        ]
    )


def _paths_view(s: Snapshot) -> str:
    return "\n".join(
        [
            f"{ui.pill('PATHS', 'view')}",
            "",
            f"Logic      : {s.paths.logic}",
            f"Config     : {s.paths.config}",
            f"Memory     : {s.paths.memory}",
            f"Mon history: {s.paths.mon_history}",
            f"Run profile: {s.paths.run_profile}",
            f"Last error : {s.paths.last_error}",
        ]
    )


def _registry_view(s: Snapshot) -> str:
    tone = "ok" if s.registry.ok else "bad"
    return "\n".join(
        [
            f"{ui.pill('REGISTRY', tone)} {'OK' if s.registry.ok else 'BAD'}",
            "",
            f"Message : {s.registry.message}",
            "",
            f"{ui.c_dim()}Catatan:{ui.c_reset()} ini ngecek semua subcommand ter-register dan punya handle().",
        ]
    )


def _last_error_view(s: Snapshot) -> str:
    if not s.last_error.exists:
        return "\n".join(
            [
                f"{ui.pill('LASTERR', 'ok')} none",
                "",
                f"{ui.c_dim()}Tidak ada last error tercatat.{ui.c_reset()}",
            ]
        )

    return "\n".join(
        [
            f"{ui.pill('LASTERR', 'warn')} exists",
            "",
            f"Time   : {s.last_error.time}",
            f"Stage  : {s.last_error.stage}",
            f"Backend: {s.last_error.backend}",
            "",
            (s.last_error.detail or "")[:2000],
        ]
    )


def build_menu() -> list[MenuItem]:
    return [
        MenuItem("summary", "Summary", "view", render=_summary_view, hint="overview"),
        MenuItem("local", "Local (Ollama)", "view", render=_local_view, hint="detail"),
        MenuItem("api", "API", "view", render=_api_view, hint="detail"),
        MenuItem("registry", "Registry check", "view", render=_registry_view, hint="health"),
        MenuItem("paths", "Paths", "view", render=_paths_view, hint="files"),
        MenuItem("last_error", "Last error", "view", render=_last_error_view, hint="debug"),
        MenuItem("deep", "Run LOCAL deep test", "action", run_action=lambda cfg, s: deep_test_local(cfg, s), hint="enter"),
        MenuItem("api_test", "Run API self-test", "action", run_action=lambda cfg, s: self_test_api(cfg, s), hint="enter"),
        MenuItem("refresh", "Refresh", "action", run_action=lambda cfg, s: (True, "Refreshed."), hint="r"),
    ]


def _format_menu_lines(menu: list[MenuItem]) -> list[str]:
    lines: list[str] = []
    for m in menu:
        if m.kind == "action":
            lines.append(
                f"{ui.c_green()}{ui.c_bold()}⚡{ui.c_reset()} {m.title}  {ui.pill('ENTER', 'action')}"
            )
        else:
            lines.append(
                f"{ui.c_cyan()}{ui.c_bold()}•{ui.c_reset()} {m.title}  {ui.pill('VIEW', 'view')}"
            )
    return lines


def run_tui(cfg: dict) -> int:
    state = AppState()
    menu = build_menu()
    snap = collectors.collect_snapshot(cfg)

    ui.hide_cursor()
    try:
        while True:
            cols, rows = ui.term_size()
            # Leave a little room; ui.render_two_column expects rows without last terminal line sometimes.
            rows = max(12, rows - 2)

            menu_lines = _format_menu_lines(menu)

            sel = max(0, min(state.selected, len(menu) - 1))
            state.selected = sel
            item = menu[sel]

            panel_title = item.title
            content = ""
            if item.kind == "view" and item.render:
                content = item.render(snap)
            elif item.kind == "action":
                content = "\n".join(
                    [
                        f"{ui.pill('ACTION', 'action')} {item.title}",
                        "",
                        "Tekan Enter untuk menjalankan action ini.",
                        f"{ui.c_dim()}(Action akan otomatis refresh snapshot setelah selesai){ui.c_reset()}",
                    ]
                )

            # Wrap panel
            # (use a safe width estimate; ui.render_two_column will clamp anyway)
            panel_lines: list[str] = []
            est_panel_w = max(30, cols - 50)
            for ln in (content.splitlines() if content else [""]):
                # keep ANSI in ln, wrap by visible length approximation (good enough)
                wrapped = ui.wrap(ui.strip_ansi(ln), est_panel_w)
                if ui.strip_ansi(ln).strip() and (ln != ui.strip_ansi(ln)):
                    # if colored line, keep original line as single line (avoid breaking ANSI)
                    panel_lines.append(ln)
                else:
                    panel_lines.extend(wrapped)

            footer = state.message if state.show_message else ""
            ui.render_two_column(
                cols=cols,
                rows=rows,
                menu_title="Menu",
                menu_items=menu_lines,
                selected=sel,
                panel_title=panel_title,
                panel_lines=panel_lines,
                footer_msg=footer,
                timestamp=snap.timestamp,
            )

            k = read_key()
            state.show_message = False

            if k.name == "q":
                return 0

            if k.name == "up":
                state.selected = (state.selected - 1) % len(menu)
                continue

            if k.name == "down":
                state.selected = (state.selected + 1) % len(menu)
                continue

            if k.name == "r":
                snap = collectors.collect_snapshot(cfg)
                state.message = f"{ui.c_green()}Refreshed.{ui.c_reset()}"
                state.show_message = True
                continue

            if k.name == "enter":
                cur = menu[state.selected]

                if cur.key == "refresh":
                    snap = collectors.collect_snapshot(cfg)
                    state.message = f"{ui.c_green()}Refreshed.{ui.c_reset()}"
                    state.show_message = True
                    continue

                if cur.kind == "action" and cur.run_action:
                    ok, msg = cur.run_action(cfg, snap)
                    snap = collectors.collect_snapshot(cfg)
                    state.message = (
                        f"{ui.c_green()}{msg}{ui.c_reset()}"
                        if ok
                        else f"{ui.c_red()}{msg}{ui.c_reset()}"
                    )
                    state.show_message = True
                    continue

                # view: nothing to do
                continue

            # ignore others
    finally:
        ui.show_cursor()
        ui.clear()
