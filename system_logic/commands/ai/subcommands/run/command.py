from system_logic.terminal import ansi
from . import storage, ui, executor, session

def find_profile(data: dict, name: str):
    for p in data.get("profiles", []):
        if p.get("name") == name: return p
    return None

def handle(argv: list, cfg: dict) -> int:
    data, err = storage.load_profiles()
    if err:
        ansi.print_brief_error(err)
        return 2

    if not argv:
        ui.print_help()
        return 0

    action = argv[0].lower().strip()

    if action in ("help", "--help", "-h"):
        ui.print_help()
        return 0
    if action == "list":
        ui.print_list(data.get("profiles", []))
        return 0
    if action == "history":
        ui.print_history(storage.read_history())
        return 0
    if action == "show":
        if len(argv) < 2:
            ansi.print_brief_error("Usage: ai run show <name>")
            return 2
        if p := find_profile(data, argv[1]):
            ui.print_show(p)
        else:
            ansi.print_brief_error("Profile not found.")
        return 0

    if action in ("kill_all", "closed_app"):
        target = argv[1] if len(argv) > 1 and not argv[1].startswith("-") else None
        if not target:
            profs = data.get("profiles", [])
            p = find_profile(data, "daily_start") or (profs[0] if profs else None)
        else:
            p = find_profile(data, target)
        if not p:
            ansi.print_brief_error("Target profile not found.")
            return 2
        wait = action == "closed_app"
        force = "--yes" in argv
        return session.execute_session_control(p, wait_mode=wait, force=force)

    # Run Profile
    is_dry = "--dry" in argv
    if p := find_profile(data, action):
        return executor.run_profile(p, is_dry=is_dry)

    ansi.print_brief_error(f"Unknown action/profile: '{action}'")
    return 2