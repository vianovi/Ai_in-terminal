def render(result):
    d = result.data
    print("toolkit audio")
    print("------------")
    print(f"- URL      : {d['url']}")
    print(f"- Format   : {d['format']}")
    print(f"- Output   : {d['out_dir']}")
    if not d["ok"]:
        print("")
        print("Details:")
        print((d.get("raw") or "")[:1200])
