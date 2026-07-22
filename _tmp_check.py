import json
h = open("reports/match_8905742060.html", encoding="utf-8").read()
assert "sb-radiant" in h
assert "btn-play" in h
assert "playback" in h
assert "What the lead-up looks like" in h
assert "Green = Radiant" in h
# name labels should not be drawn as SVG text in marker()
assert "tg.textContent = label" not in h
start = h.index("const R = ") + len("const R = ")
R, _ = json.JSONDecoder().raw_decode(h, start)
assert "scoreboard" in R
assert len(R["scoreboard"]["radiant"]) == 5
assert len(R["scoreboard"]["dire"]) == 5
d = R["deaths"][1]
assert "playback" in d
assert len(d["playback"]["frames"]) >= 20
assert d["playback"]["frames"][0]["rel"] <= -9.5
assert abs(d["playback"]["frames"][-1]["rel"]) < 0.05
titles = [f["title"] for f in d["findings"]]
print("focus", R["summary"]["focusClock"], R["summary"]["focusTitle"])
print("frames", len(d["playback"]["frames"]), "story:", d["story"][:80])
print("findings:", titles)
print("OK")
