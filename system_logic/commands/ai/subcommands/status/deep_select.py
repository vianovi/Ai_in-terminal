from __future__ import annotations

from .keys import read_key
from . import ui


def select_mode() -> str:
    """
    Return: "local" | "api" | "all" | "cancel"
    """
    options = [
        ("local", "LOCAL only  (ollama inference + latency)"),
        ("api", "API only    (remote inference, consumes quota)"),
        ("all", "ALL         (LOCAL + API)"),
        ("cancel", "Cancel"),
    ]
    sel = 0

    ui.hide_cursor()
    try:
        while True:
            cols, rows = ui.term_size()
            ui.clear()

            print(ui.clamp(ui.title_line("Deep Diagnostics", "", cols), cols))
            print(ui.clamp(f"{ui.c_dim()}Pilih mode deep test (↑↓ lalu Enter).{ui.c_reset()}", cols))
            print("")

            for i, (_key, label) in enumerate(options):
                prefix = "➤ " if i == sel else "  "
                line = prefix + label
                if i == sel:
                    line = f"{ui.c_bold()}{ui.c_cyan()}{line}{ui.c_reset()}"
                print(ui.clamp(line, cols))

            print("")
            print(ui.clamp(ui.footer_line("q untuk batal", cols), cols))

            k = read_key()
            if k.name == "q":
                return "cancel"
            if k.name == "up":
                sel = (sel - 1) % len(options)
                continue
            if k.name == "down":
                sel = (sel + 1) % len(options)
                continue
            if k.name == "enter":
                return options[sel][0]
    finally:
        ui.show_cursor()
        ui.clear()
