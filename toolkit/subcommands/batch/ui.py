def render(result):
    d = result.data
    print("toolkit batch")
    print("------------")
    print(f"- Mode         : {d['mode']}")
    print(f"- Input file   : {d['input_file']}")
    print(f"- Output dir   : {d['out_dir']}")
    print(f"- Total items  : {d['total']}")
    print(f"- Prev failed  : {d['prev_failed']}  (auto-included)")
    print(f"- Parallel     : {d['parallel']}  (jobs={d['jobs']})")
    print("")
    print("Result (this run):")
    print(f"- OK           : {d['ok']}")
    print(f"- Failed       : {d['failed']}")
    if d["failed"]:
        print(f"- Failed list  : {d['failed_file']}")
