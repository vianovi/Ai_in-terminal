def render(result):
    d = result.data
    print("toolkit extract")
    print("--------------")
    print(f"- Archive  : {d['archive']}")
    print(f"- Output   : {d['out_dir']}")
    if not d["ok"]:
        print("")
        print("Details:")
        print((d.get("raw") or "")[:1200])
