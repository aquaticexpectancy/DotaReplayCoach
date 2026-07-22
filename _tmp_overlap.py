import json
html = open(r"reports/match_8905742060.html", encoding="utf-8").read()
start = html.index("const R = ") + len("const R = ")
R, _ = json.JSONDecoder().raw_decode(html, start)
import math
for d in R["deaths"]:
    print("===", d["clock"], d["title"], "markers", len(d["markers"]))
    ms = d["markers"]
    for i, a in enumerate(ms):
        close = []
        for j, b in enumerate(ms):
            if i >= j:
                continue
            dist = math.hypot(a["x"] - b["x"], a["y"] - b["y"])
            if dist < 28:
                close.append((round(dist, 1), b["name"], b["kind"]))
        if close:
            print(f"  {a['name']} ({a['kind']}) overlaps:", close)
