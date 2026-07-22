import os, json, requests
from dotenv import load_dotenv
load_dotenv()
token = os.environ["STRATZ_TOKEN"]
headers = {
    "Authorization": f"Bearer {token}",
    "User-Agent": "DotaReplayCoach/0.1",
    "Content-Type": "application/json",
}

def fields(tname):
    q = '{ __type(name: "%s") { fields { name type { name kind ofType { name kind ofType { name } } } } } }' % tname
    r = requests.post("https://api.stratz.com/graphql", json={"query": q}, headers=headers, timeout=30)
    t = r.json()["data"]["__type"]
    out = []
    for f in t["fields"]:
        ty = f["type"]
        name = ty.get("name")
        if not name and ty.get("ofType"):
            name = ty["ofType"].get("name")
            if not name and ty["ofType"].get("ofType"):
                name = ty["ofType"]["ofType"].get("name")
        out.append((f["name"], name, ty.get("kind")))
    return out

for t in ["AbilityUsedEventType", "ItemUsedEventType", "MatchPlayerAbilityUsedEventType",
          "PlayerAbilityUsedEventType", "AbilityUsedType"]:
    try:
        fs = fields(t)
        print(t, fs)
    except Exception as e:
        print(t, "fail", e)

# Probe live query for LC in match
q = """
query ($id: Long!) {
  match(id: $id) {
    players {
      heroId
      playbackData {
        abilityUsedEvents { time abilityId target targetId }
        itemUsedEvents { time itemId }
      }
    }
  }
}
"""
r = requests.post("https://api.stratz.com/graphql",
                  json={"query": q, "variables": {"id": 8905742060}},
                  headers=headers, timeout=60)
body = r.json()
if body.get("errors"):
    print("QUERY ERRORS", json.dumps(body["errors"], indent=2)[:2000])
else:
    for p in body["data"]["match"]["players"]:
        if p["heroId"] == 104:
            ab = p["playbackData"]["abilityUsedEvents"] or []
            it = p["playbackData"]["itemUsedEvents"] or []
            print("LC abilities", len(ab), "sample", ab[:5])
            print("LC items", len(it), "sample", it[:8])
            # around death 1420
            near = [e for e in ab if 1410 <= e["time"] <= 1420]
            print("near death abilities", near)
            near_i = [e for e in it if 1410 <= e["time"] <= 1420]
            print("near death items", near_i)
