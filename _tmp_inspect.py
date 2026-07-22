import json
html = open(r"reports/match_8905742060.html", encoding="utf-8").read()
start = html.index("const R = ") + len("const R = ")
R, _ = json.JSONDecoder().raw_decode(html, start)
print("hero", R["hero"])
print("deaths", len(R["deaths"]))
for d in sorted(R["deaths"], key=lambda x: -x["score"]):
    print(f"  #{d['idx']+1} {d['clock']} score={d['score']} | {d['label']}")
    for i in d["insights"]:
        print("   -", i)
    print("   chips:", d["chips"])
