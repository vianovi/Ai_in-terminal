from system_logic.terminal import ansi
from .const import PROFILE_FILE

def print_help():
    ansi.print_info("AI Runbook (Automation)")
    print("  ai run list            : List profiles")
    print("  ai run <name> [--dry]  : Execute profile")
    print("  ai run show <name>     : Show steps")
    print("  ai run history         : Execution logs")
    print("  ai run kill_all <name> : Soft-close apps")
    print(f"\nConfig: {PROFILE_FILE}")

def print_list(profiles: list):
    ansi.print_system(f"AVAILABLE PROFILES ({len(profiles)})")
    print(f"{ansi.c_dim()}{'NAME':<20} {'STEPS':<6} DESCRIPTION{ansi.c_reset()}")
    print("-" * 70)
    for p in profiles:
        name = p.get("name", "unnamed")
        steps = len(p.get("steps", []))
        desc = p.get("description", "")
        print(f" {ansi.c_cyan()}{name:<20}{ansi.c_reset()} {steps:<6} {desc}")

def print_show(p: dict):
    ansi.print_system(f"PROFILE: {p.get('name')}")
    print(f"Desc: {p.get('description')}")
    print(f"Prompts: {list(p.get('prompts', {}).keys())}")
    print("\nSteps:")
    for i, s in enumerate(p.get("steps", []), 1):
        print(f" {i}. {ansi.c_bold()}{s.get('title', 'Cmd')}{ansi.c_reset()}")
        print(f"    $ {s.get('cmd')}")

def print_history(hist: list):
    ansi.print_system("RUN HISTORY")
    print(f"{ansi.c_dim()}{'TIME':<20} {'PROFILE':<16} {'RES':<10} CODE{ansi.c_reset()}")
    for h in hist:
        res = h.get("result", "")
        col = ansi.c_green() if res == "success" else ansi.c_red()
        print(f"{h['time'][:19]:<20} {h['profile']:<16} {col}{res:<10}{ansi.c_reset()} {h['code']}")