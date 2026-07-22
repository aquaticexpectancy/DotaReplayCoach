import json
h = open("reports/match_8905742060.html", encoding="utf-8").read()
assert "Enemy items" in h
assert "Your items" in h
assert "classifyJump" in h
assert "BLINK_MAX_PX" in h
s = h.index("const R = ") + 10
R, _ = json.JSONDecoder().raw_decode(h, s)
for d in R["deaths"]:
    me = [m for m in d["markers"] if m.get("me")]
    print(d["clock"], "me_markers", len(me), "items", [i["name"] for i in d["items"]],
          "enemies_with_blink", [e["name"] for e in d["enemyLoadouts"] if e["hasBlink"]])
    print("  findings0", d["findings"][0]["title"] if d["findings"] else None)
    # playback flags
    fr = d["playback"]["frames"][-1]["markers"]
    print("  death frame hasBlink flags", [(m["name"], m.get("hasBlink")) for m in fr if m.get("hasBlink")])
print("OK")
