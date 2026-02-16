def render(result):
    data = result.data
    status = data["status"]
    missing_core = data.get("missing_core") or []

    print("toolkit deps")
    print("-----------")
    for name, info in status.items():
        flag = "OK" if info["ok"] else "MISSING"
        ver = info["version"]
        if ver:
            print(f"- {name:8} {flag:7}  {ver}")
        else:
            print(f"- {name:8} {flag:7}")

    if missing_core:
        print("")
        print(f"Core deps missing: {', '.join(missing_core)}")
        print("Install example (Fedora):")
        print("  sudo dnf install -y yt-dlp ffmpeg curl")
