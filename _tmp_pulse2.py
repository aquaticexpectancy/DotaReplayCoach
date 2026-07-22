import re
from pathlib import Path
html = Path(r"c:\Users\docky\Downloads\Match Analysis _ DOTApulse _ DOTApulse.htm").read_text(encoding="utf-8", errors="ignore")
# Extract fundamentals scores if present
for m in re.finditer(r'(Last Hitting|Rotations|Objectives|Average|Below avg|Above avg)[^<]{0,20}', html):
    pass
# Get chunks around One Thing to Fix
idx = html.find("One Thing to Fix")
print(html[idx:idx+1200].encode("ascii","replace").decode())
print("====")
idx = html.find("Match Timeline")
print(html[idx:idx+1500].encode("ascii","replace").decode())
