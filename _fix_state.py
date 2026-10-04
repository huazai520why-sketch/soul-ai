import json, sys

for vm in ("0", "1"):
    p = r"E:\soul\_uimap\daemon\state.%s.json" % vm
    d = json.load(open(p, encoding="utf-8"))
    d["fail"] = 0
    json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("vm%s fail -> 0" % vm)
