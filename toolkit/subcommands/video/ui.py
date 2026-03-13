def render(result):
    d = result.data
    print("toolkit video")
    print("------------")
    print(f"- URL      : {d['url']}")
    print(f"- Mode     : {d['mode']}")
    print(f"- Output   : {d['out_dir']}")
    if not d["ok"]:
        print("")
        print("Details:")
        print((d.get("raw") or "")[:1200])
