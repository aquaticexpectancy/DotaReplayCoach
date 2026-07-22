import os, json, requests
from dotenv import load_dotenv
load_dotenv()
token = os.environ["STRATZ_TOKEN"]
headers = {
    "Authorization": f"Bearer {token}",
    "User-Agent": "DotaReplayCoach/0.1",
    "Content-Type": "application/json",
}

# Find playback type name variants
for tname in [
    "PlayerPlaybackDataType",
    "PlayerPlaybackData",
    "MatchPlayerPlaybackDataType",
    "PlaybackDataType",
]:
    q = '{ __type(name: "%s") { name fields { name } } }' % tname
    r = requests.post("https://api.stratz.com/graphql", json={"query": q}, headers=headers, timeout=30)
    data = r.json()
    t = (data.get("data") or {}).get("__type")
    print(tname, "->", "OK" if t else data.get("errors", data)[:1] if isinstance(data.get("errors"), list) else "missing")
    if t:
        names = sorted(f["name"] for f in t["fields"])
        interesting = [n for n in names if any(x in n.lower() for x in [
            "abilit", "item", "spell", "cast", "use", "cool", "ult", "skill", "action"
        ])]
        print("  interesting:", interesting)
        print("  all:", names)
        break
