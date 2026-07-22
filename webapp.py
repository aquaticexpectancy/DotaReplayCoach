"""DotaReplayCoach web app.

Landing page takes a match id + Steam id, generates the report in a background
thread (STRATZ + OpenDota fetches are slow), and the page polls until it's ready.

Run locally:   python webapp.py
Production:    gunicorn webapp:app        (Linux)
               waitress-serve --listen=0.0.0.0:8000 webapp:app   (Windows)
"""
from __future__ import annotations
import os
import threading
import traceback

from dotenv import load_dotenv
from flask import Flask, request, jsonify, send_file, abort

import pipeline

load_dotenv()
app = Flask(__name__)

# Simple in-memory job registry. Keyed by "<match>_<account32>" so duplicate
# requests for the same report share one job. Fine for a single-process server;
# for multi-worker deploys, put the cache check first (reports are on disk).
_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def _key(match_id: int, account_id: int) -> str:
    return f"{match_id}_{account_id}"


def _worker(key: str, match_id: int, account_id: int) -> None:
    def log(msg):
        with _lock:
            _jobs[key]["log"].append(str(msg))
    try:
        res = pipeline.generate(match_id, account_id, log=log)
        with _lock:
            _jobs[key].update(
                status="done",
                url=f"/r/{res['match_id']}/{res['account_id']}",
                partial=bool(res.get("partial")),
            )
    except pipeline.PipelineError as e:
        with _lock:
            _jobs[key].update(status="error", error=str(e))
    except Exception as e:                       # unexpected — log server-side
        traceback.print_exc()
        with _lock:
            _jobs[key].update(status="error",
                              error=f"Unexpected server error: {e}")


@app.route("/")
def index():
    return INDEX_HTML


@app.route("/generate", methods=["POST"])
def generate():
    data = request.get_json(silent=True) or request.form
    try:
        match_id = int(str(data.get("match_id", "")).strip())
        account_id = pipeline.to_steam32(str(data.get("steam_id", "")).strip())
    except (TypeError, ValueError):
        return jsonify(error="Enter a numeric match id and Steam id."), 400

    key = _key(match_id, account_id)

    # Instant path: report already on disk.
    if os.path.exists(pipeline.report_path(match_id, account_id)):
        return jsonify(status="done", key=key,
                       url=f"/r/{match_id}/{account_id}", partial=False)

    with _lock:
        job = _jobs.get(key)
        if not job or job.get("status") == "error":
            _jobs[key] = {"status": "running", "log": [], "url": None}
            threading.Thread(target=_worker, args=(key, match_id, account_id),
                             daemon=True).start()
    return jsonify(status="running", key=key)


@app.route("/status/<key>")
def status(key):
    with _lock:
        job = _jobs.get(key)
        if not job:
            return jsonify(status="unknown"), 404
        return jsonify(status=job["status"], url=job.get("url"),
                       error=job.get("error"), partial=job.get("partial", False),
                       log=job.get("log", [])[-6:])


@app.route("/r/<int:match_id>/<int:account_id>")
def report(match_id, account_id):
    path = pipeline.report_path(match_id, account_id)
    if not os.path.exists(path):
        abort(404)
    return send_file(path)


INDEX_HTML = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Dota Replay Coach</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@500;600;700&family=Source+Sans+3:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root{--bg:#0a0e1a;--card:#111827;--line:#2a3548;--text:#f1f5f9;--muted:#94a3b8;--faint:#64748b;--accent:#fbbf24;--bad:#f87171;--ok:#4ade80}
*{box-sizing:border-box}html,body{height:100%;margin:0}
body{font-family:"Source Sans 3",system-ui,sans-serif;color:var(--text);
 background:radial-gradient(1000px 600px at 12% -8%,#152038 0,transparent 55%),
 radial-gradient(800px 520px at 100% 0,#1a1620 0,transparent 48%),var(--bg);
 display:flex;align-items:center;justify-content:center;padding:24px}
.card{width:100%;max-width:460px;background:rgba(17,24,39,.8);border:1px solid var(--line);
 border-radius:18px;padding:34px 30px;backdrop-filter:blur(8px)}
.brand{font-family:"Outfit";font-weight:700;font-size:12px;letter-spacing:.16em;text-transform:uppercase;color:var(--muted)}
.brand em{color:var(--accent);font-style:normal}
h1{font-family:"Outfit";font-weight:700;font-size:26px;margin:6px 0 4px}
.sub{color:var(--muted);font-size:14px;margin:0 0 24px;line-height:1.5}
label{display:block;font-size:12px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;color:var(--faint);margin:0 0 6px}
input{width:100%;padding:13px 14px;border-radius:11px;border:1px solid var(--line);
 background:#0d1219;color:var(--text);font-size:15px;font-family:inherit;margin-bottom:18px}
input:focus{outline:none;border-color:#3d516b}
.hint{font-size:12px;color:var(--faint);margin:-12px 0 18px}
button{width:100%;padding:14px;border:0;border-radius:11px;cursor:pointer;
 font-family:"Outfit";font-weight:700;font-size:15px;color:#06121f;background:var(--accent)}
button:disabled{opacity:.55;cursor:default}
.status{margin-top:20px;font-size:14px;line-height:1.5;display:none}
.status.show{display:block}
.spin{display:inline-block;width:14px;height:14px;border:2px solid var(--line);
 border-top-color:var(--accent);border-radius:50%;animation:s .8s linear infinite;vertical-align:-2px;margin-right:8px}
@keyframes s{to{transform:rotate(360deg)}}
.log{margin-top:10px;font-size:12px;color:var(--faint);font-family:"Outfit";white-space:pre-line;min-height:1.2em}
.err{color:var(--bad)}.foot{margin-top:22px;font-size:12px;color:var(--faint);line-height:1.6}
a{color:var(--accent)}
</style></head><body>
<div class="card">
  <div class="brand">Replay <em>Coach</em></div>
  <h1>Review your deaths</h1>
  <p class="sub">Enter a Dota 2 match and your Steam ID to get an interactive, map-based breakdown of every death.</p>
  <form id="f">
    <label for="m">Match ID</label>
    <input id="m" inputmode="numeric" placeholder="8905742060" required>
    <label for="s">Steam ID</label>
    <input id="s" inputmode="numeric" placeholder="1855477712" required>
    <div class="hint">32-bit account id or full 64-bit SteamID both work.</div>
    <button id="go" type="submit">Generate report</button>
  </form>
  <div class="status" id="st"><span class="spin"></span><span id="msg">Starting…</span>
    <div class="log" id="log"></div></div>
  <div class="foot">The match must be parsed (most public matches are within minutes of finishing).
    Reports are cached, so re-opening one is instant.</div>
</div>
<script>
const f=document.getElementById("f"),go=document.getElementById("go"),
 st=document.getElementById("st"),msg=document.getElementById("msg"),logEl=document.getElementById("log");
let poll=null;
function fail(t){st.classList.add("show");st.querySelector(".spin").style.display="none";
 msg.innerHTML='<span class="err">'+t+'</span>';go.disabled=false;go.textContent="Generate report";}
f.addEventListener("submit",async e=>{
  e.preventDefault();
  clearInterval(poll);
  go.disabled=true;go.textContent="Working…";
  st.classList.add("show");st.querySelector(".spin").style.display="inline-block";
  msg.textContent="Fetching match data…";logEl.textContent="";
  let r;
  try{
    r=await fetch("/generate",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({match_id:document.getElementById("m").value,steam_id:document.getElementById("s").value})});
  }catch(_){return fail("Network error — is the server running?");}
  const d=await r.json();
  if(!r.ok){return fail(d.error||"Bad request.");}
  if(d.status==="done"){location.href=d.url;return;}
  const key=d.key;
  poll=setInterval(async()=>{
    let s;try{s=await(await fetch("/status/"+key)).json();}catch(_){return;}
    if(s.log&&s.log.length)logEl.textContent=s.log[s.log.length-1];
    if(s.status==="done"){clearInterval(poll);msg.textContent="Opening report…";location.href=s.url;}
    else if(s.status==="error"){clearInterval(poll);fail(s.error||"Generation failed.");}
  },1500);
});
</script></body></html>
"""

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"DotaReplayCoach running at http://127.0.0.1:{port}")
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=port, debug=False)
