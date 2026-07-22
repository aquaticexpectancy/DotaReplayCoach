import json
html = open(r"reports/match_8905742060.html", encoding="utf-8").read()
assert "Replay <em>Coach</em>" in html
assert "Most important" in html
assert "Why this happened" in html
start = html.index("const R = ") + len("const R = ")
R, _ = json.JSONDecoder().raw_decode(html, start)
assert "summary" in R
assert R["hero"]["name"] == "Legion Commander"
assert R["summary"]["deathCount"] == 3
for d in R["deaths"]:
    assert "title" in d and "tip" in d and "severity" in d and "findings" in d
    assert isinstance(d["chips"], list)
    if d["chips"]:
        assert "t" in d["chips"][0]
print("OK")
print("focus", R["summary"]["focusClock"], R["summary"]["focusTitle"])
for d in sorted(R["deaths"], key=lambda x: -x["score"]):
    print(d["clock"], d["severity"]["label"], "-", d["title"], f"(score {d['score']})")
    print("  tip:", d["tip"])
    print("  findings:", len(d["findings"]))
