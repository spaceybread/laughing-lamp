// Glasses camera relay server.
// - Serves /            -> viewer page (open on your laptop)
// - Serves /broadcast   -> broadcaster page (open in a browser on the glasses)
// - Serves /hud         -> glasses HUD (broadcaster + chess assistant display)
// - /ws                 -> WebSocket used for WebRTC signaling and for the
//                          JPEG-frame fallback (binary messages).
// - /analyze            -> chess analysis through the Python vision service
// - /run/*              -> Part C field-run controller; everything is traced to traces/<run>/
'use strict';

const express = require('express');
const http = require('http');
const https = require('https');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { WebSocketServer } = require('ws');

const PORT = Number(process.env.PORT) || 3000;
const VISION = process.env.VISION_URL || 'http://127.0.0.1:3001';
const TRACE_DIR = process.env.TRACE_DIR || path.join(__dirname, 'traces');
let broadcaster = null;        // the single camera source (the glasses)
const viewers = new Map();     // id -> ws
let nextId = 1;
const send = (ws, obj) => { if (ws && ws.readyState === ws.OPEN) ws.send(JSON.stringify(obj)); };
const broadcastAll = (obj) => { send(broadcaster, obj); for (const v of viewers.values()) send(v, obj); };
const lanIps = () => Object.values(os.networkInterfaces()).flat().filter((i) => i && i.family === 'IPv4' && !i.internal).map((i) => i.address);
const app = express();
app.use(express.static(path.join(__dirname, 'public')));
app.get('/broadcast', (_req, res) => res.sendFile(path.join(__dirname, 'public', 'broadcast.html')));
app.get('/hud', (_req, res) => res.sendFile(path.join(__dirname, 'public', 'hud.html')));

// ---- trace logger (Part C) ------------------------------------------------
// One directory per field run: traces/<id>/events.jsonl (one JSON object per line, `t` = server
// clock in ms since epoch, `ev` = event type) plus frames/ with every picture that was analysed.
// Outside a run, events still go to traces/_idle/ so nothing is lost; the analysis scripts in
// Bonus-PartC/ only look at named runs. Event types are documented in Bonus-PartC/README.md.
const trace = {
  run: null, stream: null, dir: null, counters: {},
  open(dir) {
    fs.mkdirSync(path.join(dir, 'frames'), { recursive: true });
    if (this.stream) this.stream.end();
    this.dir = dir;
    this.stream = fs.createWriteStream(path.join(dir, 'events.jsonl'), { flags: 'a' });
    this.counters = { events: 0, frames: 0, frame_bytes: 0, analyses: 0, dropped: 0 };
  },
  log(ev, fields) {
    if (!this.stream) this.open(path.join(TRACE_DIR, '_idle'));
    const rec = Object.assign({ t: Date.now(), ev }, fields || {});
    this.stream.write(JSON.stringify(rec) + '\n');
    this.counters.events++;
    return rec;
  },
  saveFrame(name, buf) {
    if (!this.run || !this.run.saveFrames) return null;
    fs.writeFile(path.join(this.dir, 'frames', name), buf, () => {});
    return 'frames/' + name;
  },
};
const clientInfo = (ws) => ws ? { role: ws.role, id: ws.id || 0, ip: ws.ip, ua: ws.ua, glasses: !!ws.isGlasses } : null;
const runInfo = () => trace.run ? Object.assign({}, trace.run, { elapsed_ms: Date.now() - trace.run.startedAt, counters: trace.counters }) : null;

app.get('/run', (_req, res) => res.json({ ok: true, run: runInfo(), glasses: clientInfo(broadcaster), viewers: viewers.size }));

// POST /run/start?scenario=home-wifi&net=wifi&note=...&frames=1&probe=1
app.post('/run/start', (req, res) => {
  if (trace.run) return res.status(409).json({ ok: false, error: 'a run is already active', run: runInfo() });
  const scenario = String(req.query.scenario || 'unnamed').trim().replace(/[^\w.-]+/g, '-') || 'unnamed';
  const stamp = new Date().toISOString().replace(/[-:]/g, '').replace(/\.\d+Z$/, '').replace('T', '-');
  const id = `${stamp}_${scenario}`;
  trace.run = { id, scenario, net: String(req.query.net || ''), note: String(req.query.note || ''), startedAt: Date.now(), saveFrames: req.query.frames !== '0' };
  trace.open(path.join(TRACE_DIR, id));
  trace.log('run_start', Object.assign({}, trace.run, { host: os.hostname(), lan: lanIps(), glasses: clientInfo(broadcaster), viewers: [...viewers.values()].map(clientInfo), vision: VISION }));
  broadcastAll({ type: 'run', active: true, id, scenario });
  if (req.query.probe !== '0') broadcastAll({ type: 'probe', bytes: Number(req.query.probe_bytes) || 2000000 });
  console.log(`[run] started ${id}`);
  res.json({ ok: true, run: runInfo() });
});

app.post('/run/stop', (_req, res) => {
  if (!trace.run) return res.status(409).json({ ok: false, error: 'no active run' });
  const info = runInfo();
  trace.log('run_stop', { id: info.id, duration_ms: info.elapsed_ms, counters: info.counters });
  broadcastAll({ type: 'run', active: false, id: info.id });
  trace.run = null;
  trace.open(path.join(TRACE_DIR, '_idle'));
  console.log(`[run] stopped ${info.id} after ${(info.elapsed_ms / 1000).toFixed(0)} s`);
  res.json({ ok: true, run: info });
});

// POST /run/mark?label=walked-into-hallway   -> a labelled marker in the trace
app.post('/run/mark', (req, res) => { trace.log('mark', { label: String(req.query.label || '') }); res.json({ ok: true }); });

// POST /control?action=pause|resume|analyze|probe   -> runtime control of the glasses page
app.post('/control', (req, res) => {
  const action = String(req.query.action || '');
  if (!['pause', 'resume', 'analyze', 'probe', 'reload'].includes(action)) return res.status(400).json({ ok: false, error: 'bad action' });
  trace.log('control', { action });
  if (action === 'probe') broadcastAll({ type: 'probe', bytes: Number(req.query.bytes) || 2000000 });
  else send(broadcaster, { type: 'control', action });
  res.json({ ok: true, glasses: !!broadcaster });
});

// Bandwidth probe: clients download then upload a blob and report Mbps over the WebSocket.
const PROBE_MAX = 8000000;
const probeBuf = Buffer.alloc(PROBE_MAX);
for (let i = 0; i < PROBE_MAX; i += 4) probeBuf.writeUInt32LE((Math.random() * 0xffffffff) >>> 0, i);   // incompressible
app.get('/probe/down', (req, res) => {
  const n = Math.min(PROBE_MAX, Math.max(1000, Number(req.query.bytes) || 2000000));
  res.set({ 'content-type': 'application/octet-stream', 'cache-control': 'no-store' }).send(probeBuf.subarray(0, n));
});
app.post('/probe/up', express.raw({ type: '*/*', limit: '12mb' }), (req, res) => res.json({ ok: true, bytes: req.body.length }));

// ---- chess analysis -------------------------------------------------------
// GET /analyze?side=auto|white|black&think=1  -> asks the glasses for a full-res snapshot,
// hands it to the Python vision service, returns its JSON.
// POST /analyze (image body)                   -> same, but with a supplied image (the glasses'
//                                                 assist loop uses this; headers x-cap / x-seq
//                                                 carry the capture time and picture number).
const pendingSnaps = new Map();   // id -> resolve(buffer)
let snapId = 0, reqId = 0;

function requestSnapshot(timeoutMs = 8000) {
  return new Promise((resolve, reject) => {
    if (!broadcaster || broadcaster.readyState !== broadcaster.OPEN) return reject(new Error('glasses not connected'));
    const id = String(++snapId);
    const t = setTimeout(() => { pendingSnaps.delete(id); reject(new Error('snapshot timed out')); }, timeoutMs);
    pendingSnaps.set(id, (buf, cap) => { clearTimeout(t); resolve({ buf, cap }); });
    send(broadcaster, { type: 'snapshot', id });
  });
}

app.post('/snapshot/:id', express.raw({ type: '*/*', limit: '12mb' }), (req, res) => {
  const resolve = pendingSnaps.get(req.params.id);
  if (resolve) { pendingSnaps.delete(req.params.id); resolve(req.body, Number(req.get('x-cap')) || null); }
  res.sendStatus(204);
});

const num = (v) => (v === undefined || v === null || v === '' ? null : Number(v));

async function analyzeImage(buf, query, res, meta) {
  const id = ++reqId;
  const t0 = Date.now();
  const frameFile = trace.saveFrame(`a${String(id).padStart(5, '0')}.jpg`, buf);
  const rec = { req: id, src: meta.src, bytes: buf.length, cap: meta.cap || null, seq: meta.seq || null, frame: frameFile, track: query.track || '1' };
  const qs = new URLSearchParams({ side: query.side || 'auto', think: query.think || '1.0', track: query.track || '1' });
  try {
    const t1 = Date.now();
    const r = await fetch(`${VISION}/analyze?${qs}`, { method: 'POST', body: buf, headers: { 'content-type': 'image/jpeg' } });
    const text = await r.text();
    const t2 = Date.now();
    let d = {};
    try { d = JSON.parse(text); } catch {}
    d.req = id;
    res.status(r.status).json(d);
    const best = d.moves && d.moves[0] ? d.moves[0] : null;
    trace.counters.analyses++;
    trace.log('analyze', Object.assign(rec, {
      status: r.status, vision_ms: t2 - t1, total_ms: t2 - t0, resp_bytes: text.length,
      ok: !!d.ok, error: d.error || null, state: d.state || null, lost: !!d.lost, side: d.side || null,
      agree: num(d.agree), board_score: num(d.board_score), min_conf: num(d.min_conf), plies: num(d.plies),
      fen: d.fen || null, raw: d.raw || null, san: best ? best.san : null, eval: best ? best.eval : null,
      cached: d.cached === undefined ? null : !!d.cached, timing: d.timing || null,
    }));
    // share the outcome with everyone connected (glasses draw the arrow, laptop viewer shows the panel)
    const lite = Object.assign({}, d); delete lite.annotated;              // keep the websocket message small
    const msg = { type: 'result', result: lite, annotated: d.annotated || null };
    for (const v of viewers.values()) send(v, msg);
    if (meta.src !== 'glasses') send(broadcaster, { type: 'result', result: lite });
  } catch (e) {
    trace.log('analyze', Object.assign(rec, { status: 0, total_ms: Date.now() - t0, ok: false, error: e.message }));
    res.status(502).json({ ok: false, req: id, error: `vision service unreachable (${e.message}); start it with: .venv/bin/python vision/service.py` });
  }
}

app.get('/analyze', async (req, res) => {
  let snap;
  try { snap = await requestSnapshot(); } catch (e) { trace.log('analyze', { req: ++reqId, src: 'viewer', status: 503, ok: false, error: e.message }); return res.status(503).json({ ok: false, error: e.message }); }
  lastSnapshot = snap.buf;
  analyzeImage(snap.buf, req.query, res, { src: 'viewer', cap: snap.cap });
});
app.post('/analyze', express.raw({ type: '*/*', limit: '12mb' }), (req, res) =>
  analyzeImage(req.body, req.query, res, { src: req.get('x-src') || 'glasses', cap: Number(req.get('x-cap')) || null, seq: Number(req.get('x-seq')) || null }));

// GET /snapshot.jpg -> the most recent full-res frame fetched from the glasses (handy for collecting samples)
let lastSnapshot = null;
app.get('/snapshot.jpg', async (req, res) => {
  try { lastSnapshot = (await requestSnapshot()).buf; } catch (e) { if (!lastSnapshot) return res.status(503).send(e.message); }
  res.type('jpeg').send(lastSnapshot);
});

// HTTPS if certs/ exists (needed for camera access over Wi-Fi), else plain HTTP
// (fine when the glasses reach the server as http://localhost via `adb reverse`).
const keyFile = path.join(__dirname, 'certs', 'key.pem');
const certFile = path.join(__dirname, 'certs', 'cert.pem');
const useTls = fs.existsSync(keyFile) && fs.existsSync(certFile);
const server = useTls
  ? https.createServer({ key: fs.readFileSync(keyFile), cert: fs.readFileSync(certFile) }, app)
  : http.createServer(app);

const wss = new WebSocketServer({ server, path: '/ws' });

const frameViewerCount = () => [...viewers.values()].filter((v) => v.wantsFrames).length;
const notifyCounts = () => {
  send(broadcaster, { type: 'viewers', count: viewers.size });
  send(broadcaster, { type: 'frames', count: frameViewerCount() });
};
const notifyViewers = (online) => { for (const v of viewers.values()) send(v, { type: 'broadcaster', online }); };

// JPEG frames from the glasses carry a 16-byte header: u16 magic 'FR', u16 flags, u32 seq, f64 capture
// time (glasses clock, ms). The relay logs it and forwards the message unchanged; viewers strip it.
const FRAME_MAGIC = 0x4652;

wss.on('connection', (ws, req) => {
  ws.role = null;
  ws.wantsFrames = false;
  ws.isAlive = true;
  ws.ip = req.socket.remoteAddress;
  ws.ua = String(req.headers['user-agent'] || '').slice(0, 160);
  ws.openedAt = Date.now();
  ws.rtt = null; ws.offset = null; ws.dropped = 0; ws.sentFrames = 0; ws.sentBytes = 0;
  ws.on('pong', () => { ws.isAlive = true; });

  ws.on('message', (data, isBinary) => {
    // Binary = JPEG frame from the broadcaster; fan out to viewers in frames mode.
    if (isBinary) {
      if (ws !== broadcaster) return;
      let seq = null, cap = null;
      if (data.length >= 16 && data.readUInt16LE(0) === FRAME_MAGIC) { seq = data.readUInt32LE(4); cap = data.readDoubleLE(8); }
      let sent = 0;
      for (const v of viewers.values()) {
        if (!v.wantsFrames || v.readyState !== v.OPEN) continue;
        if (v.bufferedAmount < 2000000) { v.send(data, { binary: true }); v.sentFrames++; v.sentBytes += data.length; sent++; }
        else { v.dropped++; trace.counters.dropped++; }
      }
      trace.counters.frames++; trace.counters.frame_bytes += data.length;
      trace.log('frame', { seq, cap, bytes: data.length, sent, buffered: ws.bufferedAmount });
      return;
    }

    let msg;
    try { msg = JSON.parse(data.toString()); } catch { return; }

    switch (msg.type) {
      case 'hello':
        if (msg.role === 'broadcaster') {
          // One camera source at a time. The glasses (Android browser) always win; a page on the
          // laptop (test pattern / webcam) may only take the slot when no glasses are connected.
          ws.isGlasses = /Android/i.test(ws.ua) && !msg.fake;
          if (broadcaster && broadcaster !== ws && broadcaster.readyState === broadcaster.OPEN && broadcaster.isGlasses && !ws.isGlasses) {
            send(ws, { type: 'rejected', reason: 'the glasses are already broadcasting' });
            ws.close(4001, 'glasses already broadcasting');
            return;
          }
          if (broadcaster && broadcaster !== ws) { send(broadcaster, { type: 'replaced' }); broadcaster.close(4000, 'replaced by a new broadcaster'); }
          broadcaster = ws;
          ws.role = 'broadcaster';
          ws.ready = false; // becomes true once its camera is running
          console.log(`[broadcaster] connected from ${ws.ip}`);
          notifyCounts();
        } else {
          ws.role = 'viewer';
          ws.id = nextId++;
          viewers.set(ws.id, ws);
          console.log(`[viewer ${ws.id}] connected from ${ws.ip}`);
          send(ws, { type: 'broadcaster', online: !!(broadcaster && broadcaster.ready) });
          notifyCounts();
        }
        trace.log('ws_open', Object.assign(clientInfo(ws), { page: msg.page || null, fake: !!msg.fake }));
        if (trace.run) send(ws, { type: 'run', active: true, id: trace.run.id, scenario: trace.run.scenario });
        break;

      case 'ready':            // broadcaster's camera is up -> viewers (re)request the stream
        if (ws === broadcaster) { ws.ready = true; console.log('[broadcaster] camera ready'); trace.log('camera_ready', { cam: msg.cam || null }); notifyViewers(true); }
        break;

      case 'want-stream':      // viewer asks for the stream in a given mode
        if (ws.role !== 'viewer') return;
        ws.wantsFrames = msg.mode === 'frames';
        trace.log('want_stream', { id: ws.id, mode: msg.mode });
        notifyCounts();
        if (!ws.wantsFrames) send(broadcaster, { type: 'want-stream', from: ws.id });
        break;

      // WebRTC signaling relay. Broadcaster -> viewer messages carry `to`;
      // viewer -> broadcaster messages get `from` stamped on.
      case 'offer':
      case 'answer':
      case 'ice':
        if (msg.type !== 'ice') trace.log('signal', { kind: msg.type, from: ws === broadcaster ? 'glasses' : ws.id, to: ws === broadcaster ? msg.to : 'glasses' });
        if (ws === broadcaster) send(viewers.get(msg.to), msg);
        else if (ws.role === 'viewer') send(broadcaster, Object.assign({}, msg, { from: ws.id }));
        break;

      // ---- Part C instrumentation ----------------------------------------
      case 'pong': {           // reply to our clock-sync ping: RTT and client-minus-server clock offset
        const now = Date.now();
        const rtt = now - msg.ts;
        const offset = msg.tc - (msg.ts + rtt / 2);
        ws.rtt = rtt; ws.offset = offset;
        trace.log('rtt', { role: ws.role, id: ws.id || 0, rtt, offset });
        break;
      }
      case 'stats':            // periodic client-side counters (frames, WebRTC getStats, assist timings)
        trace.log('stats', { role: ws.role, id: ws.id || 0, s: msg.stats });
        break;
      case 'event':            // one-off client events (camera started/ended, peer state, errors)
        trace.log('client_event', { role: ws.role, id: ws.id || 0, name: msg.name, data: msg.data || null });
        break;
      case 'probe_result':
        trace.log('probe', Object.assign({ role: ws.role, id: ws.id || 0 }, msg.result));
        for (const v of viewers.values()) send(v, { type: 'probe_result', role: ws.role, id: ws.id || 0, result: msg.result });
        break;
    }
  });

  ws.on('close', (code, reason) => {
    trace.log('ws_close', Object.assign(clientInfo(ws) || {}, { code, reason: String(reason || ''), dur_ms: Date.now() - ws.openedAt, sent_frames: ws.sentFrames, dropped: ws.dropped }));
    if (ws === broadcaster) {
      broadcaster = null;
      console.log('[broadcaster] disconnected');
      notifyViewers(false);
    } else if (ws.role === 'viewer') {
      viewers.delete(ws.id);
      console.log(`[viewer ${ws.id}] disconnected`);
      send(broadcaster, { type: 'viewer-left', from: ws.id });
      notifyCounts();
    }
  });
});

// Drop dead sockets (e.g. glasses went to sleep) so viewers see "offline".
setInterval(() => {
  for (const ws of wss.clients) {
    if (!ws.isAlive) return ws.terminate();
    ws.isAlive = false;
    ws.ping();
  }
}, 15000);

// Clock-sync ping every 2 s to every client (application level, so the pages can answer with their
// own clock), plus a relay snapshot so viewer back-pressure and fan-out drops are in the trace.
setInterval(() => {
  const info = { glasses_rtt: broadcaster ? broadcaster.rtt : null, run: trace.run ? trace.run.id : null };
  for (const ws of wss.clients) if (ws.role) send(ws, { type: 'ping', ts: Date.now(), info });
}, 2000);
setInterval(() => {
  if (!broadcaster && viewers.size === 0) return;
  trace.log('relay', {
    glasses: broadcaster ? { rtt: broadcaster.rtt, buffered: broadcaster.bufferedAmount } : null,
    viewers: [...viewers.values()].map((v) => ({ id: v.id, frames: v.wantsFrames, rtt: v.rtt, buffered: v.bufferedAmount, sent: v.sentFrames, dropped: v.dropped })),
    counters: trace.counters, mem_mb: Math.round(process.memoryUsage().rss / 1048576),
  });
}, 5000);

server.listen(PORT, '0.0.0.0', () => {
  const scheme = useTls ? 'https' : 'http';
  console.log(`Glasses camera relay (${scheme})`);
  console.log(`  Viewer     : ${scheme}://localhost:${PORT}/`);
  for (const ip of lanIps()) console.log(`  Viewer LAN : ${scheme}://${ip}:${PORT}/`);
  console.log(`  Broadcast  : ${scheme}://localhost:${PORT}/broadcast   (open on the glasses)`);
  console.log(`  Traces     : ${TRACE_DIR}   (POST /run/start?scenario=... to begin a field run)`);
  if (!useTls) console.log('  (HTTP mode: glasses must reach this as localhost via `adb reverse`, or run `npm run gen-cert` for HTTPS over Wi-Fi)');
});
