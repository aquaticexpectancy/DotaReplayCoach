_TEMPLATE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700&family=Source+Sans+3:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root{
  --bg:#0a0d12; --bg-elev:#10151d; --bg-soft:#151b25;
  --line:#243041; --line-soft:#1c2533;
  --text:#e8eef5; --muted:#8b9aab; --faint:#627084;
  --you:#f0b429; --radiant:#3dca8a; --dire:#ef5b5b;
  --ward:#e0b83a; --eward:#b56fd4; --accent:#f0b429;
  --ok:#3dca8a; --warn:#e0a23a; --bad:#ef5b5b; --fight:#6ea8ff;
  --font-display:"Outfit",system-ui,sans-serif;
  --font-body:"Source Sans 3",system-ui,sans-serif;
}
*{box-sizing:border-box}
html,body{height:100%;margin:0}
body{
  font-family:var(--font-body);
  background:
    radial-gradient(1200px 700px at 12% -10%, #1a2433 0%, transparent 55%),
    radial-gradient(900px 600px at 100% 0%, #1c1810 0%, transparent 45%),
    var(--bg);
  color:var(--text); overflow:hidden;
}
button{font:inherit;color:inherit;background:none;border:0;cursor:pointer;padding:0}
.app{display:flex;flex-direction:column;height:100vh;min-height:0}

.board{
  display:grid;grid-template-columns:1fr auto 1fr;align-items:center;gap:16px;
  padding:10px 18px;border-bottom:1px solid var(--line-soft);
  background:rgba(10,13,18,.8);backdrop-filter:blur(10px);flex:0 0 auto;
}
.team{display:flex;gap:8px;align-items:center}
.team.radiant{justify-content:flex-end}
.team.dire{justify-content:flex-start}
.slot{
  position:relative;width:42px;height:42px;border-radius:8px;overflow:hidden;
  background:#0d1219;border:2px solid transparent;opacity:.92;
}
.slot img{width:100%;height:100%;object-fit:cover;display:block}
.slot.radiant{border-color:var(--radiant)}
.slot.dire{border-color:var(--dire)}
.slot.me{box-shadow:0 0 0 2px var(--you);border-color:var(--you)}
.slot.dead{opacity:.28;filter:grayscale(.8)}
.slot.killer::after{
  content:"";position:absolute;inset:0;border:2px dashed rgba(239,91,91,.85);border-radius:6px;pointer-events:none;
}
.mid{text-align:center;min-width:180px}
.mid .brand{font-family:var(--font-display);font-weight:700;font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}
.mid .brand em{color:var(--accent);font-style:normal}
.mid .meta{font-size:12px;color:var(--faint);margin-top:2px}
.badge{display:inline-flex;font-size:10px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;padding:3px 8px;border-radius:999px;border:1px solid transparent;margin-left:6px}
.badge.win{color:var(--ok);background:rgba(61,202,138,.12);border-color:rgba(61,202,138,.28)}
.badge.loss{color:var(--bad);background:rgba(239,91,91,.12);border-color:rgba(239,91,91,.28)}

.main{display:flex;flex:1;min-height:0}
.rail{width:260px;flex:0 0 auto;display:flex;flex-direction:column;min-height:0;border-right:1px solid var(--line-soft);background:rgba(16,21,29,.55)}
.rail-head{padding:14px 14px 10px;border-bottom:1px solid var(--line-soft)}
.rail-head h2{margin:0 0 10px;font-family:var(--font-display);font-size:13px;font-weight:650;letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}
.sort{display:grid;grid-template-columns:1fr 1fr;gap:6px}
.sort button{padding:7px 8px;border-radius:8px;font-size:12px;font-weight:600;color:var(--muted);background:var(--bg-soft);border:1px solid var(--line-soft)}
.sort button.on{color:var(--text);border-color:#3a4d66;background:#1a2330}
#deathlist{flex:1;overflow-y:auto;padding:10px}
.drow{display:grid;grid-template-columns:auto 1fr auto;gap:10px;align-items:start;padding:12px;border-radius:12px;cursor:pointer;margin-bottom:8px;border:1px solid transparent}
.drow:hover{background:rgba(255,255,255,.03)}
.drow.on{background:#182231;border-color:#334860}
.drow .dot{width:10px;height:10px;border-radius:50%;margin-top:5px}
.drow .dot.critical{background:var(--bad)}
.drow .dot.notable{background:var(--warn)}
.drow .dot.fight{background:var(--fight)}
.drow .dot.normal{background:#4a586a}
.drow .clock{font-family:var(--font-display);font-weight:650;font-size:15px}
.drow .title{font-size:13px;color:#c5d0dc;margin-top:2px;line-height:1.3}
.drow .sev{font-size:11px;color:var(--faint);margin-top:4px;text-transform:uppercase;letter-spacing:.05em}
.drow .num{font-size:11px;color:var(--faint);font-weight:600;background:var(--bg);border:1px solid var(--line-soft);border-radius:6px;padding:2px 6px}

.mapwrap{flex:1;min-width:0;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:12px 16px;gap:10px}
.map-stage{position:relative;flex:1;min-height:0;aspect-ratio:1/1;max-width:100%;max-height:calc(100vh - 210px)}
svg{width:100%;height:100%;border-radius:16px;border:1px solid var(--line);background:#05070b}
.map-tools{position:absolute;left:12px;bottom:12px;display:flex;gap:8px;flex-wrap:wrap;pointer-events:none}
.map-tools span{font-size:11px;color:#d5deea;background:rgba(8,11,16,.78);border:1px solid rgba(255,255,255,.08);border-radius:999px;padding:5px 10px}

.transport{
  width:min(100%,640px);display:grid;grid-template-columns:auto 1fr auto;gap:12px;align-items:center;
  padding:10px 12px;border-radius:12px;background:rgba(16,21,29,.85);border:1px solid var(--line-soft);
}
.transport .play{
  width:40px;height:40px;border-radius:10px;border:1px solid var(--line);background:var(--bg-elev);
  font-size:16px;display:flex;align-items:center;justify-content:center;
}
.transport .play:hover{border-color:#3d516b}
.scrub{display:flex;flex-direction:column;gap:4px;min-width:0}
.scrub input[type=range]{width:100%;accent-color:var(--you)}
.scrub .times{display:flex;justify-content:space-between;font-size:11px;color:var(--faint);font-family:var(--font-display)}
.scrub .times strong{color:var(--text);font-weight:650}
.nav-death{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--muted)}
.nav-death button{width:30px;height:30px;border-radius:8px;border:1px solid var(--line);background:var(--bg-elev);font-size:15px}
.nav-death button:disabled{opacity:.35;cursor:default}
.kbd{display:inline-block;font-size:10px;border:1px solid var(--line);border-radius:4px;padding:1px 5px;color:var(--muted)}

.panel{width:390px;flex:0 0 auto;overflow-y:auto;min-height:0;border-left:1px solid var(--line-soft);background:rgba(16,21,29,.7);padding:18px 18px 28px}
.sev-tag{display:inline-flex;font-size:11px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;padding:5px 10px;border-radius:8px;border:1px solid transparent}
.sev-tag.critical{color:#ffb0b0;background:rgba(239,91,91,.12);border-color:rgba(239,91,91,.3)}
.sev-tag.notable{color:#ffd39a;background:rgba(224,162,58,.12);border-color:rgba(224,162,58,.3)}
.sev-tag.fight{color:#b7d3ff;background:rgba(110,168,255,.12);border-color:rgba(110,168,255,.3)}
.sev-tag.normal{color:#b7c2d0;background:rgba(255,255,255,.04);border-color:var(--line)}
.panel h1{font-family:var(--font-display);font-size:22px;font-weight:700;margin:12px 0 6px;line-height:1.15}
.panel .blurb{color:#b7c4d2;font-size:14.5px;line-height:1.45;margin:0 0 10px}
.story,.tip,.situation{margin:0 0 14px;padding:12px 14px;border-radius:12px;font-size:13.5px;line-height:1.45}
.story{background:rgba(110,168,255,.08);border:1px solid rgba(110,168,255,.22);color:#cfe0ff}
.tip{background:rgba(240,180,41,.07);border:1px solid rgba(240,180,41,.22);color:#f0dfb4}
.situation{background:var(--bg-soft);border:1px solid var(--line-soft);color:#c5d0dc}
.story .k,.tip .k{display:block;font-size:10.5px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;margin-bottom:4px}
.story .k{color:var(--fight)}.tip .k{color:var(--you)}
.section{margin:0 0 18px}
.section h3{margin:0 0 10px;font-family:var(--font-display);font-size:12px;font-weight:650;letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}
.chips{display:flex;flex-wrap:wrap;gap:6px}
.chip{font-size:12.5px;font-weight:600;color:#d5deea;background:var(--bg-soft);border:1px solid var(--line);border-radius:999px;padding:5px 10px}
.kb{display:flex;align-items:center;gap:10px;font-size:14px;color:#c5d0dc;padding:10px 12px;border-radius:12px;background:var(--bg-soft);border:1px solid var(--line-soft)}
.kb img{width:28px;height:28px;border-radius:50%;border:1.5px solid var(--dire)}
.meters{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.meter{padding:10px 12px;border-radius:12px;background:var(--bg-soft);border:1px solid var(--line-soft)}
.meter .lbl{font-size:11px;color:var(--faint);text-transform:uppercase;letter-spacing:.06em}
.meter .val{font-family:var(--font-display);font-weight:650;font-size:15px;margin-top:2px}
.bar{height:6px;border-radius:3px;background:#232b36;margin-top:8px;overflow:hidden}
.bar i{display:block;height:100%;border-radius:3px}
.lvl{display:flex;align-items:center;justify-content:center;font-family:var(--font-display);font-weight:700;font-size:18px;border-radius:12px;background:var(--bg-soft);border:1px solid var(--line-soft)}
.findings{display:flex;flex-direction:column;gap:8px}
.finding{display:grid;grid-template-columns:8px 1fr;gap:10px;padding:10px 12px;border-radius:12px;background:var(--bg-soft);border:1px solid var(--line-soft)}
.finding .pipe{border-radius:4px;margin:2px 0}
.finding .pipe.bad{background:var(--bad)}
.finding .pipe.warn{background:var(--warn)}
.finding .pipe.ok{background:var(--ok)}
.finding .ft{font-weight:700;font-size:13px;margin-bottom:2px}
.finding .fx{font-size:13px;color:#aeb9c7;line-height:1.4}
.roster{display:flex;flex-direction:column;gap:6px}
.rrow{display:grid;grid-template-columns:28px 1fr auto;gap:10px;align-items:center;padding:8px 10px;border-radius:10px;background:var(--bg-soft);border:1px solid var(--line-soft)}
.rrow img{width:28px;height:28px;border-radius:50%;border:2px solid #445}
.rrow.radiant img{border-color:var(--radiant)}
.rrow.dire img{border-color:var(--dire)}
.rrow.me img{border-color:var(--you)}
.rrow .rn{font-weight:650;font-size:13px}
.rrow .rm{font-size:11.5px;color:var(--faint);margin-top:1px}
.rrow .rd{font-family:var(--font-display);font-size:12px;font-weight:650;color:var(--muted);text-align:right}
.rrow .tagkill{display:inline-block;margin-left:6px;font-size:10px;font-weight:700;letter-spacing:.04em;text-transform:uppercase;color:#ffb0b0}
.items{display:flex;flex-wrap:wrap;gap:6px}
.items img,.items .ph{width:48px;height:36px;border-radius:6px;background:#0d1219;border:1px solid var(--line);object-fit:cover}
.items .ph{display:flex;align-items:center;justify-content:center;font-size:9px;color:var(--faint);text-align:center;padding:2px}
.items .neu{outline:1px solid #7b6ae0;outline-offset:1px}
.leg{font-size:12px;color:var(--faint);line-height:1.7;border-top:1px solid var(--line-soft);padding-top:12px}
.sw{display:inline-block;width:9px;height:9px;border-radius:50%;margin:0 4px 0 8px;vertical-align:0}
.sw:first-child{margin-left:0}
.hint{font-size:11.5px;color:var(--faint);margin-top:6px}

@media (max-width:980px){
  .panel{width:320px}.rail{width:220px}
  .board{grid-template-columns:1fr;justify-items:center}
  .team{justify-content:center !important}
}
@media (max-width:860px){
  body{overflow:auto}.app{height:auto;min-height:100vh}
  .main{flex-direction:column}
  .rail,.panel{width:100%;border:0}
  .rail{max-height:200px;border-bottom:1px solid var(--line-soft)}
  .map-stage{max-height:none;width:min(92vw,640px);height:auto}
}
</style>
</head><body>
<div class="app">
  <header class="board">
    <div class="team radiant" id="sb-radiant"></div>
    <div class="mid">
      <div class="brand">Replay <em>Coach</em></div>
      <div class="meta" id="h-sub"></div>
    </div>
    <div class="team dire" id="sb-dire"></div>
  </header>

  <div class="main">
    <aside class="rail">
      <div class="rail-head">
        <h2>Your deaths</h2>
        <div class="sort">
          <button type="button" id="sort-priority" class="on">Most important</button>
          <button type="button" id="sort-time">By time</button>
        </div>
      </div>
      <div id="deathlist"></div>
    </aside>

    <section class="mapwrap">
      <div class="map-stage">
        <svg id="map" viewBox="0 0 640 640" role="img" aria-label="Minimap playback">
          <image href="__MAP_B64__" x="0" y="0" width="640" height="640"></image>
          <g id="ov"></g>
        </svg>
        <div class="map-tools" aria-hidden="true">
          <span>Green = Radiant · Red = Dire</span>
          <span>Gold ring = you</span>
          <span>Fan-out = stacked heroes</span>
        </div>
      </div>
      <div class="transport">
        <button type="button" class="play" id="btn-play" aria-label="Play or pause">▶</button>
        <div class="scrub">
          <input type="range" id="scrub" min="0" max="20" step="1" value="20">
          <div class="times"><span id="t-rel">-10.0s</span><strong id="t-clock">0:00</strong><span>death</span></div>
        </div>
        <div class="nav-death">
          <button type="button" id="prev" aria-label="Previous death">‹</button>
          <span id="pos">1/1</span>
          <button type="button" id="next" aria-label="Next death">›</button>
          <span class="kbd">Space</span>
        </div>
      </div>
    </section>

    <aside class="panel" id="panel" aria-live="polite"></aside>
  </div>
</div>

<script>
const R = __REPORT__, SPR = __SPRITES__;
const NS = "http://www.w3.org/2000/svg";
const ov = document.getElementById("ov");
let order = [], cur = -1, sortMode = "priority";
let frameIdx = 0, playing = false, playTimer = null;

function el(tag, attrs){
  const e = document.createElementNS(NS, tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  return e;
}
function esc(s){
  return String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}
function pct(a,b){ if (a == null || !b) return 0; return Math.max(0, Math.min(100, 100 * a / b)); }
function clock(t){
  const s = Math.max(0, Math.floor(t));
  return Math.floor(s/60) + ":" + String(s%60).padStart(2,"0");
}
function teamColor(m){ return m.isRadiant ? "#3dca8a" : "#ef5b5b"; }

function layoutMarkers(raw){
  const SEP = 34;
  const marks = raw.map(m => Object.assign({}, m, {tx:m.x, ty:m.y, x:m.x, y:m.y, spread:false}));
  const n = marks.length;
  if (n < 2) return marks;
  const parent = Array.from({length:n}, (_,i)=>i);
  const find = (i) => parent[i] === i ? i : (parent[i] = find(parent[i]));
  const uni = (a,b) => { a=find(a); b=find(b); if (a!==b) parent[a]=b; };
  for (let i=0;i<n;i++) for (let j=i+1;j<n;j++)
    if (Math.hypot(marks[i].tx-marks[j].tx, marks[i].ty-marks[j].ty) < SEP) uni(i,j);
  const groups = {};
  for (let i=0;i<n;i++){ const r=find(i); (groups[r]||(groups[r]=[])).push(i); }
  for (const idxs of Object.values(groups)){
    if (idxs.length === 1) continue;
    let cx=0, cy=0;
    idxs.forEach(i => { cx += marks[i].tx; cy += marks[i].ty; });
    cx /= idxs.length; cy /= idxs.length;
    idxs.sort((a,b) => {
      const ka = marks[a].me ? -2 : (marks[a].killer ? -1 : 0);
      const kb = marks[b].me ? -2 : (marks[b].killer ? -1 : 0);
      if (ka !== kb) return ka - kb;
      return Math.atan2(marks[a].ty-cy, marks[a].tx-cx) - Math.atan2(marks[b].ty-cy, marks[b].tx-cx);
    });
    const meIdx = idxs.find(i => marks[i].me);
    if (meIdx != null){
      marks[meIdx].x = marks[meIdx].tx; marks[meIdx].y = marks[meIdx].ty;
      const others = idxs.filter(i => i !== meIdx);
      const radius = Math.max(36, 20 + others.length * 9);
      others.forEach((i,k) => {
        const ang = others.length === 1 ? -0.85 : (-Math.PI/2 + (2*Math.PI*k)/others.length);
        marks[i].x = +(marks[meIdx].tx + Math.cos(ang)*radius).toFixed(1);
        marks[i].y = +(marks[meIdx].ty + Math.sin(ang)*radius).toFixed(1);
        marks[i].spread = true;
      });
    } else {
      const radius = Math.max(30, 16 + idxs.length * 8);
      idxs.forEach((i,k) => {
        const ang = -Math.PI/2 + (2*Math.PI*k)/idxs.length;
        marks[i].x = +(cx + Math.cos(ang)*radius).toFixed(1);
        marks[i].y = +(cy + Math.sin(ang)*radius).toFixed(1);
        marks[i].spread = Math.hypot(marks[i].x-marks[i].tx, marks[i].y-marks[i].ty) > 2;
      });
    }
  }
  for (let iter=0; iter<10; iter++){
    for (let i=0;i<n;i++) for (let j=i+1;j<n;j++){
      let dx = marks[j].x - marks[i].x, dy = marks[j].y - marks[i].y;
      let dist = Math.hypot(dx, dy);
      if (dist >= SEP) continue;
      if (dist < 0.01){ dx=1; dy=0; dist=1; }
      const push = (SEP - dist) / 2, ux = dx/dist, uy = dy/dist;
      if (marks[i].me){ marks[j].x += ux*push*2; marks[j].y += uy*push*2; marks[j].spread = true; }
      else if (marks[j].me){ marks[i].x -= ux*push*2; marks[i].y -= uy*push*2; marks[i].spread = true; }
      else {
        marks[i].x -= ux*push; marks[i].y -= uy*push; marks[i].spread = true;
        marks[j].x += ux*push; marks[j].y += uy*push; marks[j].spread = true;
      }
    }
  }
  for (const m of marks){
    m.x = Math.max(18, Math.min(622, +m.x.toFixed(1)));
    m.y = Math.max(18, Math.min(622, +m.y.toFixed(1)));
    if (Math.hypot(m.x-m.tx, m.y-m.ty) > 2) m.spread = true;
  }
  return marks;
}

function marker(m){
  const g = el("g", {});
  const col = teamColor(m);
  if (m.spread){
    g.appendChild(el("line", {
      x1:m.x, y1:m.y, x2:m.tx, y2:m.ty,
      stroke:col, "stroke-width":1.2, opacity:.5, "stroke-linecap":"round"
    }));
    g.appendChild(el("circle", {cx:m.tx, cy:m.ty, r:2.4, fill:col, opacity:.9}));
  }
  const r = m.me ? 14 : 11;
  const cid = "c" + m.heroId + "_" + Math.random().toString(36).slice(2,7);
  const clip = el("clipPath", {id: cid});
  clip.appendChild(el("circle", {cx:m.x, cy:m.y, r:r}));
  g.appendChild(clip);
  if (m.icon){
    g.appendChild(el("image", {
      href:m.icon, x:m.x-r, y:m.y-r, width:2*r, height:2*r,
      "clip-path":`url(#${cid})`, preserveAspectRatio:"xMidYMid slice"
    }));
  }
  // Thin team ring only — no heavy black outline.
  g.appendChild(el("circle", {
    cx:m.x, cy:m.y, r:r, fill:"none", stroke:col, "stroke-width": m.me ? 3 : 2.2
  }));
  if (m.me){
    g.appendChild(el("circle", {
      cx:m.x, cy:m.y, r:r+4, fill:"none", stroke:"#f0b429", "stroke-width":2, opacity:.95
    }));
  }
  if (m.killer){
    g.appendChild(el("circle", {
      cx:m.x, cy:m.y, r:r+7, fill:"none", stroke:"#ef5b5b",
      "stroke-width":1.4, "stroke-dasharray":"3 3"
    }));
  }
  if (m.hp != null && m.maxHp){
    const bw = 2*r+2, hx = m.x-bw/2, hy = m.y+r+3;
    g.appendChild(el("rect", {x:hx, y:hy, width:bw, height:3, fill:"#000a", rx:1.5}));
    g.appendChild(el("rect", {
      x:hx, y:hy, width:bw*Math.max(0,Math.min(1,m.hp/m.maxHp)),
      height:3, fill:"#4caf50", rx:1.5
    }));
  }
  const title = document.createElementNS(NS, "title");
  title.textContent = m.name + (m.isRadiant ? " · Radiant" : " · Dire") + (m.me ? " · you" : "");
  g.appendChild(title);
  return g;
}

function pathUntil(points, t){
  const pts = points.filter(p => p.t <= t + 0.05).map(p => [p.x, p.y]);
  if (pts.length < 2) return null;
  // Split on big jumps (TP).
  const segs = [];
  let cur = [pts[0]];
  for (let i=1;i<pts.length;i++){
    const d = Math.hypot(pts[i][0]-pts[i-1][0], pts[i][1]-pts[i-1][1]);
    if (d > 40){ if (cur.length>1) segs.push(cur); cur = [pts[i]]; }
    else cur.push(pts[i]);
  }
  if (cur.length>1) segs.push(cur);
  return segs;
}

function drawFrame(D, fi){
  const pb = D.playback;
  const F = pb.frames[fi];
  const laid = layoutMarkers(F.markers);
  const meM = laid.find(m => m.me) || laid[0];
  ov.innerHTML = "";

  const dead = new Set(F.deadBuildings);
  const sz = {tower:16, rax:13, fort:28};
  for (const b of R.buildings){
    if (dead.has(b.key)) continue;
    const s = sz[b.kind] || 14;
    ov.appendChild(el("image", {href:SPR[b.icon], x:b.x-s/2, y:b.y-s/2, width:s, height:s}));
  }
  for (const w of F.wards){
    const col = w.isRadiant ? "#e0b83a" : "#b56fd4";
    if (w.isRadiant){
      ov.appendChild(el("circle", {
        cx:w.x, cy:w.y, r:R.visionR, fill:col, opacity:.05,
        stroke:col, "stroke-width":.8, "stroke-dasharray":"3 5", "stroke-opacity":.45
      }));
    }
    ov.appendChild(el("circle", {cx:w.x, cy:w.y, r:4.2, fill:col, stroke:"#000", "stroke-width":1}));
  }

  const segs = pathUntil(pb.mePath || [], F.t) || [];
  for (const s of segs){
    const d = s.map((p,i)=>(i?"L":"M")+p[0]+" "+p[1]).join(" ");
    ov.appendChild(el("path", {d, fill:"none", stroke:"#f0b429", "stroke-width":2.2, opacity:.85, "stroke-linecap":"round"}));
  }

  if (meM){
    const dx = meM.tx != null ? meM.tx : meM.x;
    const dy = meM.ty != null ? meM.ty : meM.y;
    if (Math.abs(F.rel) < 0.05){
      ov.appendChild(el("circle", {cx:dx, cy:dy, r:22, fill:"none", stroke:"#ef5b5b", "stroke-width":2.4, opacity:.9}));
      ov.appendChild(el("circle", {cx:dx, cy:dy, r:30, fill:"none", stroke:"#ef5b5b", "stroke-width":1, opacity:.3}));
    }
  }
  for (const m of laid) if (!m.me) ov.appendChild(marker(m));
  if (meM) ov.appendChild(marker(meM));

  // Scoreboard live / dead state for this frame.
  const aliveIds = new Set(F.markers.map(m => m.heroId));
  const killerIds = new Set(F.markers.filter(m => m.killer).map(m => m.heroId));
  document.querySelectorAll(".slot").forEach(node => {
    const id = +node.dataset.heroId;
    node.classList.toggle("dead", !aliveIds.has(id));
    node.classList.toggle("killer", killerIds.has(id));
  });

  document.getElementById("t-rel").textContent = (F.rel >= 0 ? "+" : "") + F.rel.toFixed(1) + "s";
  document.getElementById("t-clock").textContent = clock(F.t);
  document.getElementById("scrub").value = String(fi);
}

function stopPlay(){
  playing = false;
  if (playTimer){ clearInterval(playTimer); playTimer = null; }
  document.getElementById("btn-play").textContent = "▶";
}
function startPlay(){
  const D = R.deaths[cur];
  if (!D) return;
  playing = true;
  document.getElementById("btn-play").textContent = "❚❚";
  if (frameIdx >= D.playback.frames.length - 1) frameIdx = 0;
  playTimer = setInterval(() => {
    frameIdx += 1;
    if (frameIdx >= D.playback.frames.length){
      frameIdx = D.playback.frames.length - 1;
      drawFrame(D, frameIdx);
      stopPlay();
      return;
    }
    drawFrame(D, frameIdx);
  }, Math.max(80, D.playback.step * 1000));
}

function rebuildOrder(){
  const idxs = R.deaths.map((_,i)=>i);
  if (sortMode === "time") idxs.sort((a,b)=>R.deaths[a].time-R.deaths[b].time);
  else idxs.sort((a,b)=>(R.deaths[b].score-R.deaths[a].score)||(R.deaths[a].time-R.deaths[b].time));
  order = idxs;
  paintList();
}

function paintList(){
  const nav = document.getElementById("deathlist");
  nav.innerHTML = "";
  order.forEach(i => {
    const d = R.deaths[i];
    const div = document.createElement("div");
    div.className = "drow" + (i === cur ? " on" : "");
    div.innerHTML =
      `<span class="dot ${esc(d.severity.key)}"></span>
       <div><div class="clock">${esc(d.clock)}</div><div class="title">${esc(d.title)}</div>
       <div class="sev">${esc(d.severity.label)}</div></div>
       <span class="num">#${d.idx+1}</span>`;
    div.onclick = () => show(i);
    nav.appendChild(div);
  });
}

function paintBoard(){
  const paint = (elId, rows) => {
    const root = document.getElementById(elId);
    root.innerHTML = rows.map(h => `
      <div class="slot ${h.isRadiant?"radiant":"dire"}${h.me?" me":""}" data-hero-id="${h.heroId}" title="${esc(h.name)}">
        ${h.icon ? `<img src="${esc(h.icon)}" alt="${esc(h.name)}">` : ""}
      </div>`).join("");
  };
  paint("sb-radiant", R.scoreboard.radiant);
  paint("sb-dire", R.scoreboard.dire);
  const H = R.hero, S = R.summary;
  const result = H.won == null ? "" : H.won
    ? `<span class="badge win">Victory</span>` : `<span class="badge loss">Defeat</span>`;
  document.getElementById("h-sub").innerHTML =
    `${esc(H.name)} · ${esc(H.side)} · match ${R.matchId} · ${S.deathCount} deaths ${result}`;
}

function paintPanel(D, meM){
  const assistIcons = (D.killedBy && D.killedBy.assistHeroes || []).map(h =>
    h.icon ? `<img src="${esc(h.icon)}" title="${esc(h.name)}" alt="" style="width:22px;height:22px;border-radius:50%;border:1.5px solid #ef5b5b;margin-left:4px">` : ""
  ).join("");
  const kb = D.killedBy
    ? `<div class="kb">${D.killedBy.icon?`<img src="${esc(D.killedBy.icon)}" alt="">`:""}
        <div>Killed by <b>${esc(D.killedBy.name)}</b>${D.killedBy.assists?` · +${D.killedBy.assists}`:""}
        ${assistIcons?`<div style="margin-top:6px;display:flex;flex-wrap:wrap">${assistIcons}</div>`:""}</div></div>`
    : `<div class="kb"><div>Killed by creeps or tower</div></div>`;
  const meters = meM && meM.maxHp ? `
    <div class="meters">
      <div class="meter"><div class="lbl">Health</div><div class="val">${meM.hp}/${meM.maxHp}</div>
        <div class="bar"><i style="width:${pct(meM.hp,meM.maxHp)}%;background:#4caf50"></i></div></div>
      <div class="meter"><div class="lbl">Mana</div><div class="val">${meM.mp}/${meM.maxMp}</div>
        <div class="bar"><i style="width:${pct(meM.mp,meM.maxMp)}%;background:#3d9be9"></i></div></div>
    </div><div class="hint">At / near death</div>` : "";
  const findings = (D.findings||[]).map(f => `
    <div class="finding"><div class="pipe ${esc(f.tone)}"></div>
    <div><div class="ft">${esc(f.title)}</div><div class="fx">${esc(f.text)}</div></div></div>`).join("");
  const inv = (D.items||[]).map(it => it.icon
    ? `<img src="${esc(it.icon)}" title="${esc(it.name)}" alt="">`
    : `<div class="ph">${esc(it.name)}</div>`).join("")
    + (D.neutral ? (D.neutral.icon
      ? `<img class="neu" src="${esc(D.neutral.icon)}" title="${esc(D.neutral.name)}" alt="">`
      : `<div class="ph neu">${esc(D.neutral.name)}</div>`) : "");
  const chips = (D.chips||[]).map(c => `<span class="chip">${esc(c.t)}</span>`).join("");
  const counts = D.counts || {};
  const roster = (D.roster||[]).map(m => {
    const side = m.isRadiant ? "Radiant" : "Dire";
    const cls = (m.me ? "me " : "") + (m.isRadiant ? "radiant" : "dire");
    const dist = m.me ? "death spot" : `~${m.dist} units`;
    const hp = (m.hp != null && m.maxHp) ? ` · ${Math.round(100*m.hp/m.maxHp)}% HP` : "";
    const kill = m.killer ? `<span class="tagkill">kill credit</span>` : "";
    return `<div class="rrow ${cls}">
      ${m.icon?`<img src="${esc(m.icon)}" alt="">`:`<div></div>`}
      <div><div class="rn">${esc(m.name)}${kill}</div>
      <div class="rm">${m.me?"You":side}${m.level!=null?` · lvl ${m.level}`:""}${hp}</div></div>
      <div class="rd">${esc(dist)}</div></div>`;
  }).join("");

  document.getElementById("panel").innerHTML = `
    <div class="sev-tag ${esc(D.severity.key)}">${esc(D.severity.label)}</div>
    <h1>${esc(D.title)}</h1>
    <p class="blurb">${esc(D.blurb)} <span style="color:var(--faint)">· ${esc(D.clock)} · death #${D.idx+1}</span></p>
    <div class="story"><span class="k">What the lead-up looks like</span>${esc(D.story||"")}</div>
    <div class="tip"><span class="k">Remember</span>${esc(D.tip)}</div>
    ${D.situation?`<div class="situation">${esc(D.situation)}</div>`:""}
    <div class="section"><h3>Who was here · ${counts.allies||0} allies · ${counts.enemies||0} enemies</h3>
      <div class="roster">${roster}</div></div>
    <div class="section"><h3>Cost &amp; flags</h3>
      <div class="chips">${chips || `<span class="chip">No extra flags</span>`}</div></div>
    <div class="section"><h3>How you died</h3>${kb}</div>
    <div class="section"><h3>Your state</h3>${meters}
      ${meM && meM.level != null ? `<div class="lvl" style="margin-top:10px;height:52px">Level ${meM.level}</div>`:""}</div>
    <div class="section"><h3>Why this happened</h3><div class="findings">${findings}</div></div>
    <div class="section"><h3>Items at death</h3><div class="items">${inv||`<span class="chip">Empty</span>`}</div></div>
    <div class="leg">
      <span class="sw" style="background:#3dca8a;margin-left:0"></span>Radiant
      <span class="sw" style="background:#ef5b5b"></span>Dire
      <span class="sw" style="background:#f0b429"></span>you
      <br>Press Play to watch the last 10 seconds · Space toggles play · ← → change death
    </div>`;
}

function paintPos(){
  const n = R.deaths.length, place = order.indexOf(cur)+1;
  document.getElementById("pos").textContent = `${place}/${n}`;
  document.getElementById("prev").disabled = place <= 1;
  document.getElementById("next").disabled = place >= n;
}

function show(i){
  stopPlay();
  cur = i;
  const D = R.deaths[i];
  const scrub = document.getElementById("scrub");
  scrub.max = String(D.playback.frames.length - 1);
  frameIdx = D.playback.frames.length - 1;
  scrub.value = String(frameIdx);
  drawFrame(D, frameIdx);
  const meM = (D.playback.frames[frameIdx].markers.find(m => m.me)
            || D.markers.find(m => m.me) || D.markers[0]);
  paintList();
  paintPanel(D, meM);
  paintPos();
}

function step(delta){
  const at = order.indexOf(cur), next = at + delta;
  if (next < 0 || next >= order.length) return;
  show(order[next]);
}

document.getElementById("sort-priority").onclick = () => {
  sortMode = "priority";
  document.getElementById("sort-priority").classList.add("on");
  document.getElementById("sort-time").classList.remove("on");
  const keep = cur; rebuildOrder(); show(keep);
};
document.getElementById("sort-time").onclick = () => {
  sortMode = "time";
  document.getElementById("sort-time").classList.add("on");
  document.getElementById("sort-priority").classList.remove("on");
  const keep = cur; rebuildOrder(); show(keep);
};
document.getElementById("prev").onclick = () => step(-1);
document.getElementById("next").onclick = () => step(1);
document.getElementById("btn-play").onclick = () => playing ? stopPlay() : startPlay();
document.getElementById("scrub").oninput = (e) => {
  stopPlay();
  frameIdx = +e.target.value;
  const D = R.deaths[cur];
  drawFrame(D, frameIdx);
};
window.addEventListener("keydown", (e) => {
  if (e.key === "ArrowLeft"){ e.preventDefault(); step(-1); }
  if (e.key === "ArrowRight"){ e.preventDefault(); step(1); }
  if (e.key === " " || e.code === "Space"){
    e.preventDefault();
    playing ? stopPlay() : startPlay();
  }
});

paintBoard();
rebuildOrder();
show(__DEFAULT__);
</script>
</body></html>
"""
