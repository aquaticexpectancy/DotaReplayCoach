import os, requests
from dotenv import load_dotenv
load_dotenv()
token = os.environ.get("STRATZ_TOKEN")
headers = {
    "Authorization": f"Bearer {token}",
    "User-Agent": "DotaReplayCoach/0.1",
    "Content-Type": "application/json",
}
r = requests.post(
    "https://api.stratz.com/graphql",
    json={
        "query": """
        {
          __schema {
            types { name }
          }
        }
        """
    },
    headers=headers,
    timeout=90,
)
names = [t["name"] for t in r.json()["data"]["__schema"]["types"]]
for n in sorted(names):
    low = n.lower()
    if any(k in low for k in ["modif", "buff", "debuff", "status", "stun", "crowd", "disable", "silence"]):
        print(n)
