const fs = require("fs");
const h = fs.readFileSync("reports/match_8905742060.html", "utf8");
const startR = h.indexOf("const R = ") + "const R = ".length;
let depth = 0, end = startR;
for (let i = startR; i < h.length; i++) {
  const c = h[i];
  if (c === "{") depth++;
  else if (c === "}") {
    depth--;
    if (depth === 0) { end = i + 1; break; }
  }
}
const R = JSON.parse(h.slice(startR, end));
const start = h.indexOf("function layoutMarkers");
const endFn = h.indexOf("\nfunction marker(");
eval(h.slice(start, endFn));
for (const d of R.deaths) {
  const laid = layoutMarkers(d.markers);
  let min = 999;
  for (let i = 0; i < laid.length; i++) {
    for (let j = i + 1; j < laid.length; j++) {
      min = Math.min(min, Math.hypot(laid[i].x - laid[j].x, laid[i].y - laid[j].y));
    }
  }
  console.log(d.clock, "minSep", min.toFixed(1), "spread", laid.filter(m => m.spread).length);
}
