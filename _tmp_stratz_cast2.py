import os, json, requests
from dotenv import load_dotenv
load_dotenv()
token = os.environ["STRATZ_TOKEN"]
headers = {
    "Authorization": f"Bearer {token}",
    "User-Agent": "DotaReplayCoach/0.1",
    "Content-Type": "application/json",
}

q = '{ __type(name: "AbilityUsedEventsType") { fields { name type { name kind ofType { name } } } } }'
r = requests.post("https://api.stratz.com/graphql", json={"query": q}, headers=headers, timeout=30)
print("AbilityUsedEventsType", json.dumps(r.json(), indent=2)[:1500])

q2 = """
query ($id: Long!) {
  match(id: $id) {
    players {
      heroId
      playbackData {
        abilityUsedEvents { time abilityId target attacker }
        itemUsedEvents { time itemId target attacker }
      }
    }
  }
}
"""
r = requests.post("https://api.stratz.com/graphql",
                  json={"query": q2, "variables": {"id": 8905742060}},
                  headers=headers, timeout=60)
body = r.json()
if body.get("errors"):
    print("ERRORS", body["errors"])
else:
    for p in body["data"]["match"]["players"]:
        if p["heroId"] != 104:
            continue
        ab = p["playbackData"]["abilityUsedEvents"] or []
        it = p["playbackData"]["itemUsedEvents"] or []
        print("LC abilities", len(ab), "sample", ab[:6])
        print("LC items", len(it), "sample", it[:8])
        print("near 1420 ab", [e for e in ab if 1405 <= e["time"] <= 1425])
        print("near 1420 it", [e for e in it if 1405 <= e["time"] <= 1425])
        # duel ability id?
        from collections import Counter
        print("top abilityIds", Counter(e["abilityId"] for e in ab).most_common(6))
