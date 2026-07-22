import re
from pathlib import Path
html = Path(r"c:\Users\docky\Downloads\Match Analysis _ DOTApulse _ DOTApulse.htm").read_text(encoding="utf-8", errors="ignore")
# Pull headings and card titles
for pat in [
    r">([A-Z][^<]{2,60})</h[12]>",
    r'font-semibold text-text-primary">([^<]{5,80})</p>',
    r'tracking-\[0\.15em\][^>]*>([^<]+)<',
    r'accent-gold[^>]*>([^<]{3,40})<',
]:
    hits = re.findall(pat, html)
    print("PAT", pat[:40], "->", len(hits))
    for h in hits[:25]:
        print(" ", h.encode("ascii", "replace").decode())
    print()

# Color tokens from CSS
css = Path(r"c:\Users\docky\Downloads\Match Analysis _ DOTApulse _ DOTApulse_files\0j6kqoqpe_u6z_vp2L.css").read_text(encoding="utf-8", errors="ignore")
for token in ["accent-radiant", "accent-dire", "accent-gold", "bg-primary", "bg-secondary", "text-primary", "text-secondary", "border"]:
    m = re.search(rf"\.--{re.escape(token)}[^\{{]*\{{[^}}]+}}|--{re.escape(token)}:[^;]+;", css)
    # also search :root style
print("root vars sample:")
for m in re.finditer(r"--[a-zA-Z0-9_-]+:\s*[^;]{1,40};", css):
    s = m.group(0)
    if any(x in s for x in ["accent", "bg-", "text-", "radiant", "dire", "gold", "primary"]):
        print(" ", s)
        if m.start() > 50000:
            break
