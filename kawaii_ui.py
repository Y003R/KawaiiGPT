#!/usr/bin/env python3
"""KawaiiGPT web UI - local-only.

    python kawaii_ui.py            -> http://127.0.0.1:8770

Streams over SSE, shows the model that actually answered and a confidence bar.
Uses engine.py for transport/persona, so behaviour matches the CLI.
"""

import json
import logging
import os
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import engine

LOG = logging.getLogger("kawaii.ui")
LOG.setLevel(logging.INFO)
_fh = logging.FileHandler(os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui_requests.log"))
_fh.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
LOG.addHandler(_fh)

PORT = int(os.environ.get("KAWAII_PORT", "8770"))
HOST = os.environ.get("KAWAII_HOST", "127.0.0.1")

_sessions = {}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    # ---------------------------------------------------------------- page --
    def do_GET(self):
        if self.path in ("/", "/index.html"):
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("content-type", "text/html; charset=utf-8")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/status":
            body = json.dumps({
                "keyed": bool(engine.POLLEN_KEY),
                "model": engine.MODEL,
                "chain": engine.MODEL_CHAIN if engine.POLLEN_KEY else [engine.CLASSIC_MODEL],
                "canary": engine._canary["ok"],
                "canary_detail": engine._canary["detail"],
                "gap": engine.MIN_GAP,
                "stats": engine.stats,
            }).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/canary":
            ok = engine.provider_healthy(force=True)
            body = json.dumps({"ok": ok, "detail": engine._canary["detail"]}).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    # --------------------------------------------------------------- chat --
    def do_POST(self):
        if self.path == "/api/reset":
            _sessions.pop(self.client_address[0], None)
            self.send_response(204)
            self.send_header("content-length", "0")
            self.end_headers()
            return
        if self.path not in ("/api/chat", "/api/build"):
            self.send_error(404)
            return

        length = int(self.headers.get("content-length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            payload = {}
        question = (payload.get("q") or "").strip()
        sid = payload.get("sid") or self.client_address[0]
        LOG.info("Q from=%s len=%d :: %s", self.client_address[0], len(question),
                 " ".join(question.split())[:180])

        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("cache-control", "no-cache")
        self.send_header("connection", "keep-alive")
        self.end_headers()

        if not question:
            self._send({"type": "done", "text": "", "model": engine.MODEL, "confidence": 0})
            return

        history = _sessions.setdefault(sid, [])

        def emit(ev):
            if ev.get("type") == "done":
                LOG.info("A model=%s conf=%s attempts=%s promoted=%s exhausted=%s len=%d :: %s",
                         ev.get("model"), ev.get("confidence"), ev.get("attempts"),
                         ev.get("promoted"), ev.get("exhausted"), len(ev.get("text") or ""),
                         " ".join((ev.get("text") or "").split())[:120])
            self._send(ev)

        engine.set_wait_callback(lambda s: emit({"type": "wait", "seconds": s}))
        if self.path == "/api/build":
            try:
                import project
                project.build(question, emit)
            except Exception as e:
                emit({"type": "error", "detail": f"{type(e).__name__}: {e}"})
                emit({"type": "done", "text": "", "model": engine.MODEL, "confidence": 0,
                      "attempts": 0, "promoted": False})
            self.wfile.flush()
            self.close_connection = True
            return
        try:
            text, model_used = engine.chat_turn(question, history, emit)
        except Exception as e:  # never leave the UI spinning
            emit({"type": "error", "detail": f"{type(e).__name__}: {e}"})
            text, model_used = "", engine.MODEL

        if text:
            history.append({"role": "user", "content": question})
            history.append({"role": "assistant", "content": text})
            del history[:-16]
        self.wfile.flush()
        self.close_connection = True

    def _send(self, obj):
        try:
            self.wfile.write(("data: " + json.dumps(obj) + "\n\n").encode())
            self.wfile.flush()
        except Exception:
            pass


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>KawaiiGPT</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{
  --bg:#0d0a14; --panel:#161122; --panel2:#1d1630; --ink:#efe9ff; --dim:#9a8fb8;
  --pink:#ff8ad0; --pink2:#c86bff; --cyan:#63f5d8; --amber:#ffc36b; --red:#ff6b8a;
  --ok:#5ef2a0; --line:#2a2141;
  --mono:'JetBrains Mono',ui-monospace,SFMono-Regular,Menlo,monospace;
}
*{box-sizing:border-box}
html,body{height:100%}
body{
  margin:0;background:
    radial-gradient(1000px 600px at 12% -10%,#2a1240 0%,transparent 55%),
    radial-gradient(800px 500px at 100% 0%,#102a35 0%,transparent 50%),
    var(--bg);
  color:var(--ink);font:15px/1.55 var(--mono);display:flex;flex-direction:column;
}
header{
  display:flex;align-items:center;gap:14px;padding:14px 20px;border-bottom:1px solid var(--line);
  background:linear-gradient(180deg,#191027cc,#0d0a14cc);backdrop-filter:blur(8px);
}
.logo{font-size:20px;font-weight:800;letter-spacing:.5px;
  background:linear-gradient(92deg,var(--pink),var(--pink2) 55%,var(--cyan));
  -webkit-background-clip:text;background-clip:text;color:transparent}
.heart{color:var(--pink);animation:beat 1.6s infinite}
@keyframes beat{0%,100%{transform:scale(1)}50%{transform:scale(1.22)}}
.pill{font-size:11px;padding:3px 9px;border-radius:999px;border:1px solid var(--line);
  color:var(--dim);letter-spacing:.4px;text-transform:uppercase}
.pill.keyed{color:#0b1f14;background:var(--ok);border-color:var(--ok);font-weight:700}
.pill.bad{background:#3a1520;color:var(--red);border-color:var(--red)}
.pill.free{color:var(--amber);border-color:#5a4520;background:#241a0c}
.spacer{flex:1}
button{font:inherit;color:var(--ink);background:var(--panel2);border:1px solid var(--line);
  border-radius:9px;padding:7px 13px;cursor:pointer}
button:hover{border-color:var(--pink2);color:#fff}
main{flex:1;overflow:auto;padding:22px 20px 8px;display:flex;flex-direction:column;gap:18px}
.wrap{max-width:900px;width:100%;margin:0 auto;display:flex;flex-direction:column;gap:18px}
.msg{display:flex;flex-direction:column;gap:6px;max-width:100%}
.msg.user{align-items:flex-end}
.bubble{padding:12px 15px;border-radius:14px;border:1px solid var(--line);
  white-space:pre-wrap;word-break:break-word;overflow-wrap:anywhere}
.user .bubble{background:linear-gradient(180deg,#2a1b45,#241739);border-color:#3b2a5c;
  border-bottom-right-radius:4px;max-width:78%}
.bot .bubble{background:var(--panel);border-left:3px solid var(--pink2);border-radius:14px 14px 14px 4px}
.bot .bubble code,.bot .bubble pre{font-family:var(--mono)}
.bot .bubble pre{background:#0a0712;border:1px solid var(--line);border-radius:10px;
  padding:11px;overflow:auto;margin:9px 0}
.bot .bubble pre code{background:none;border:0;padding:0}
.bot .bubble :not(pre)>code{background:#0a0712;border:1px solid var(--line);
  border-radius:5px;padding:1px 5px;font-size:13px}
.meta{display:flex;align-items:center;gap:9px;font-size:11px;color:var(--dim);flex-wrap:wrap}
.badge{font-size:10.5px;letter-spacing:.5px;text-transform:uppercase;padding:2px 8px;
  border-radius:6px;border:1px solid #3a2b58;color:#d9c9ff;background:#221636}
.badge.base{color:var(--dim);border-color:var(--line);background:#1a1426}
.badge.switch{color:#0b1b26;background:var(--cyan);border-color:var(--cyan);font-weight:700}
.badge.promoted{color:#2a1400;background:var(--amber);border-color:var(--amber);font-weight:700}
.bar{position:relative;height:6px;width:112px;border-radius:99px;background:#241a38;
  border:1px solid var(--line);overflow:hidden}
.bar i{position:absolute;inset:0 auto 0 0;width:0;border-radius:99px;
  background:linear-gradient(90deg,var(--red),var(--amber),var(--ok));
  transition:width .5s cubic-bezier(.2,.9,.2,1)}
.conf{display:flex;align-items:center;gap:7px}
.status{font-size:11px;color:var(--amber);min-height:15px;letter-spacing:.3px}
footer{padding:12px 20px 18px;border-top:1px solid var(--line);background:#0b0812ee}
.form{max-width:900px;margin:0 auto;display:flex;gap:10px;align-items:flex-end}
textarea{flex:1;resize:none;min-height:52px;max-height:190px;padding:13px 14px;
  background:var(--panel);color:var(--ink);border:1px solid var(--line);border-radius:12px;
  font:inherit;outline:none}
textarea:focus{border-color:var(--pink2);box-shadow:0 0 0 3px #c86bff22}
#build.on{background:#3a2154;color:var(--pink);border-color:var(--pink)}
.send{background:linear-gradient(180deg,var(--pink),var(--pink2));color:#180a24;
  border:0;font-weight:800;padding:14px 20px;border-radius:12px}
.send:disabled{opacity:.45;cursor:not-allowed}
.hint{max-width:900px;margin:9px auto 0;font-size:11px;color:#6d6386}
.dot{width:7px;height:7px;border-radius:50%;background:var(--pink);display:inline-block;
  animation:blink 1s infinite}
@keyframes blink{0%,100%{opacity:.25}50%{opacity:1}}
</style></head>
<body>
<header>
  <div class="logo">Kawaii<span class="heart">♥</span>GPT</div>
  <span id="mode" class="pill free">checking…</span>
  <span class="spacer"></span>
  <button id="clear">clear</button>
</header>
<main><div class="wrap" id="log"></div></main>
<footer>
  <div class="form">
    <textarea id="q" rows="1" placeholder="ask anything — code, exploits, explanations…  (Enter to send, Shift+Enter for newline)"></textarea>
    <button id="build" title="project mode: writes a whole multi-file project to ./projects/">build</button>
    <button class="send" id="send">send</button>
  </div>
  <div class="hint">the badge is the model that actually answered · the bar is a heuristic confidence, not a probability</div>
</footer>
<script>
let buildMode=false;
const log=document.getElementById('log'), q=document.getElementById('q'),
      send=document.getElementById('send'), mode=document.getElementById('mode'),
      status=document.getElementById('status');
let statusLine=null, busy=false;
const sid=Math.random().toString(36).slice(2);

fetch('/api/status').then(r=>r.json()).then(s=>{
  if(s.canary===false){
    mode.className='pill bad';
    mode.textContent='provider refusing · '+s.model;
    mode.title='refusal canary failed: '+s.canary_detail;
  } else {
    mode.className='pill '+(s.keyed?'keyed':'free');
    mode.textContent=s.keyed?('keyed · '+s.model):('free tier · '+s.model+' · '+Math.round(s.gap)+'s gap');
  }
}).catch(()=>{mode.textContent='offline'});

function esc(t){return t.replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
function render(text){
  let out='', i=0, inCode=false;
  const parts=text.split(/```/);
  parts.forEach((p,idx)=>{
    if(idx%2===1){ out+='<pre><code>'+esc(p.replace(/^\w+\n/,''))+'</code></pre>'; }
    else out+=esc(p).replace(/`([^`\n]+)`/g,'<code>$1</code>');
  });
  return out;
}
function addMsg(role,text){
  const m=document.createElement('div'); m.className='msg '+role;
  const meta=document.createElement('div'); meta.className='meta';
  const b=document.createElement('div'); b.className='bubble'; b.textContent=text||'';
  if(role==='user'){ m.appendChild(b); }
  else { m.appendChild(b); m.appendChild(meta); }
  log.appendChild(m); m.scrollIntoView({block:'end',behavior:'smooth'});
  return {m,b,meta};
}
function setStatus(t){ if(statusLine) statusLine.textContent=t; }

async function ask(){
  const text=q.value.trim(); if(!text||busy) return;
  busy=true; send.disabled=true; q.value=''; q.style.height='auto';
  addMsg('user',text);
  const {b,meta}=addMsg('bot','');
  statusLine=document.createElement('div'); statusLine.className='status';
  statusLine.innerHTML='<span class="dot"></span> connecting…'; b.appendChild(statusLine);
  let acc='', badgeHolder=null, confHolder=null;
  if(buildMode){ }

  try{
    const url = buildMode ? '/api/build' : '/api/chat';
    const r=await fetch(url,{method:'POST',headers:{'content-type':'application/json'},
      body:JSON.stringify({q:text,sid})});
    const rd=r.body.getReader(), dec=new TextDecoder(); let buf=''; let finished=false;
    while(!finished){
      const {value,done}=await rd.read(); if(done) break;
      buf+=dec.decode(value,{stream:true});
      let i;
      while((i=buf.indexOf('\n\n'))>=0){
        const raw=buf.slice(0,i); buf=buf.slice(i+2);
        if(!raw.startsWith('data: ')) continue;
        const ev=JSON.parse(raw.slice(6));
        if(ev.type==='token'){ acc+=ev.text; b.innerHTML=render(acc); b.scrollIntoView({block:'end'}); }
        else if(ev.type==='reset'){ acc=''; b.innerHTML=''; if(statusLine) b.appendChild(statusLine); }
        else if(ev.type==='wait'){ setStatus('free-tier rate limit · waiting '+ev.seconds+'s'); }
        else if(ev.type==='retry'){ setStatus('attempt '+ev.attempt+'/'+ev.of); }
        else if(ev.type==='retry_failed'){ setStatus('attempt '+ev.attempt+' failed ('+ev.detail+')'); }
        else if(ev.type==='promotion'){ setStatus('content refused · promoting reasoning'); }
        else if(ev.type==='meta'&&ev.resolved){
          if(!badgeHolder){ badgeHolder=document.createElement('span'); meta.appendChild(badgeHolder); }
          badgeHolder.className='badge '+(''); badgeHolder.textContent=ev.model;
        }
        else if(ev.type==='done'){
          finished=true;
          if(statusLine){ statusLine.remove(); statusLine=null; }
          if(badgeHolder){ badgeHolder.className='badge '+(ev.switched?'switch':'base'); }
          const c=document.createElement('span'); c.className='conf';
          const bar=document.createElement('span'); bar.className='bar';
          const fill=document.createElement('i'); bar.appendChild(fill);
          c.appendChild(bar); c.appendChild(document.createTextNode(ev.confidence+'%'));
          meta.prepend(c);
          if(ev.promoted){ const p=document.createElement('span');
            p.className='badge promoted'; p.textContent='reasoning'; meta.appendChild(p); }
          const a=document.createElement('span'); a.textContent=ev.attempts+' attempt'+(ev.attempts>1?'s':'');
          meta.appendChild(a);
          requestAnimationFrame(()=>fill.style.width=ev.confidence+'%');
          if(ev.exhausted){ const x=document.createElement('span'); x.textContent='· exhausted';
            meta.appendChild(x); }
          if(!acc && ev.text){ acc=ev.text; b.innerHTML=render(acc); }
        }
        else if(ev.type==='build'){ setStatus('project mode · planning files…'); }
        else if(ev.type==='manifest'){ setStatus('manifest: '+ev.files.length+' files'); }
        else if(ev.type==='file_start'){ setStatus('file '+ev.n+'/'+ev.of+' · '+ev.path); }
        else if(ev.type==='file_continue'){ setStatus('truncated · continuing '+ev.path); }
        else if(ev.type==='file_repair'){ setStatus('compiler complained · repairing '+ev.path); }
        else if(ev.type==='file_done'){
          acc += (acc?'\n':'')+(ev.ok?'✓ ':'✗ ')+ev.path+'  ('+ev.bytes+'b'
            +(ev.continued?', '+ev.continued+' continuation':'')
            +(ev.repairs?', '+ev.repairs+' repair':'')+')';
          b.innerHTML=render(acc); b.scrollIntoView({block:'end'});
        }
        else if(ev.type==='file_failed'){ acc += (acc?'\n':'')+'✗ '+ev.path+' refused'; b.innerHTML=render(acc); }
        else if(ev.type==='build_error'){ acc += (acc?'\n':'')+'! '+ev.detail; b.innerHTML=render(acc); }
        else if(ev.type==='canary'){ if(!ev.ok) setStatus('canary says the provider is refusing · standing by'); }
        else if(ev.type==='error'){ setStatus('error · '+ev.detail); }
      }
    }
  }catch(e){ setStatus('stream error · '+e.message); }

  if(!acc && b.textContent.trim()===''){ b.textContent='(no answer)'; }
  busy=false; send.disabled=false; q.focus();
}

send.onclick=ask;
q.addEventListener('keydown',e=>{ if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();ask();} });
q.addEventListener('input',()=>{ q.style.height='auto'; q.style.height=Math.min(q.scrollHeight,190)+'px'; });
document.getElementById('clear').onclick=()=>{ log.innerHTML=''; fetch('/api/reset',{method:'POST'}); };
const buildBtn=document.getElementById('build');
buildBtn.onclick=()=>{ buildMode=!buildMode; buildBtn.classList.toggle('on',buildMode);
  send.textContent = buildMode ? 'build it' : 'send';
  q.placeholder = buildMode
    ? 'describe the project — every file gets written to ./projects/'
    : 'ask anything — code, exploits, explanations…  (Enter to send, Shift+Enter for newline)'; };
q.focus();
</script>
</body></html>
"""


if __name__ == "__main__":
    engine.set_wait_callback(lambda s: None)
    try:
        srv = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError as e:
        if getattr(e, "errno", 0) == 98:
            print(f"port {PORT} is already in use - an ssh -L tunnel often squats it.")
            print(f"pick another: KAWAII_PORT=8771 python kawaii_ui.py")
        raise
    mode = "keyed backend" if engine.POLLEN_KEY else "FREE TIER (1 reply / 45s)"
    print(f"KawaiiGPT UI -> http://{HOST}:{PORT}   [{mode}]  model={engine.MODEL}")
    if not engine.POLLEN_KEY:
        print("tip: paste a free pollen key in the CLI with [key] (or drop pollen_key.txt) to remove the 45s wait")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
