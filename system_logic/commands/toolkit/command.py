from __future__ import annotations

from .ui import print_help, print_error
from .subcommands.registry import dispatch, list_subcommands


def handle(argv, cfg) -> int:
    # argv is list[str] after "toolkit"
    if not argv or argv[0] in ("-h", "--help", "help"):
        print_help()
        return 0

    sub = argv[0]
    if sub in ("-l", "--list"):
        print("Available subcommands:")
        for name in list_subcommands():
            print(f"  - {name}")
        return 0

    try:
        result = dispatch(sub, argv[1:], cfg)
        if result.render:
            result.render(result)
        return int(result.exit_code)
    except KeyError:
        print_error(f"Unknown subcommand: {sub}")
        print_help()
        return 2
    except Exception as e:
        # fail loud, but clear
        print_error(str(e))
        return 1
