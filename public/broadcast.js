// Broadcaster: runs in the browser on the glasses (Chromium 74: no ?. / ?? / numeric separators).
// Captures the camera with getUserMedia, then either:
//   - streams it over WebRTC to each viewer that asks (default), or
//   - sends JPEG snapshots over the WebSocket to viewers in "frames" mode.
// Also runs the chess assist loop and reports Part C telemetry (frame timestamps, WebRTC
// stats, assist timings, clock-sync pongs) to the relay, which writes the trace.
(() => {
  const qs = new URLSearchParams(location.search);
  const AUTOSTART = qs.get('autostart') !== '0';
  const WIDTH = Number(qs.get('w')) || 1280;
  const HEIGHT = Number(qs.get('h')) || 720;
  const FACING = qs.get('facing') || 'environment';
  const FPS = Number(qs.get('fps')) || 10;        // frames mode only
  const QUALITY = Number(qs.get('q')) || 0.6;      // frames mode JPEG quality
  const FRAME_W = Number(qs.get('fw')) || 640;     // frames mode width
  const FAKE = qs.get('fake') === '1';             // test pattern instead of a real camera
  const ASSIST = qs.get('assist') !== '0';         // take a picture every few seconds and show the best move
  const ASSIST_MS = Number(qs.get('every')) || 3000;
  const SIDE = qs.get('side') || 'auto';           // auto | white | black (colour at the bottom of the phone screen)
  const TRACK = qs.get('track') || '1';            // track=0: raw per-picture reading, no game tracking (Part D baseline)
  const STREAM = qs.get('stream') !== '0';         // stream=0: no live video to the laptop, only pictures + results
  const MARKS = qs.get('marks') || (location.pathname.indexOf('hud') >= 0 ? 'arrow' : 'dots');   // arrow | dots
  const MIRROR = qs.get('mirror') === '1';         // on the laptop: show what the glasses' HUD shows (their video + the same results)
  const STATS_MS = Number(qs.get('stats')) || 2000;
  const PAGE = location.pathname.replace(/^\//, '') || 'broadcast';

  const video = document.getElementById('preview');
  const statusEl = document.getElementById('status');
  const viewersEl = document.getElementById('viewers');
  const startBtn = document.getElementById('start');

  let ws = null;
  let stream = null;
  let frameViewers = 0;
  let frameTimer = null;
  let requesting = false;
  let paused = false;
  const peers = new Map(); // viewerId -> RTCPeerConnection

  const setStatus = (text, isErr) => { statusEl.textContent = text; statusEl.className = isErr ? 'err' : ''; };
  const send = (obj) => { if (ws && ws.readyState === 1) ws.send(JSON.stringify(obj)); };
  const evt = (name, data) => send({ type: 'event', name: name, data: data || null });

  // ---- telemetry counters (reported every STATS_MS over the WebSocket) ------------------------
  const st = { framesSent: 0, frameBytes: 0, framesSkipped: 0, assist: [], seq: 0, assistSeq: 0, reconnects: 0 };
  const run = { active: false, id: null, scenario: null };
  const FRAME_MAGIC = 0x4652;
  function frameHeader(seq, capT) {
    const hdr = new ArrayBuffer(16);
    const dv = new DataView(hdr);
    dv.setUint16(0, FRAME_MAGIC, true); dv.setUint16(2, 1, true); dv.setUint32(4, seq, true); dv.setFloat64(8, capT, true);
    return hdr;
  }
  function stripHeader(buf) {
    if (buf.byteLength >= 16 && new DataView(buf).getUint16(0, true) === FRAME_MAGIC) return buf.slice(16);
    return buf;
  }

  // ---- mirror mode (laptop): receive the glasses' video instead of using a camera ----------
  let mpc = null, mirrorFallback = null, mirrorMode = 'webrtc';
  const mimg = document.getElementById('mirrorimg');
  function mirrorRequest() {
    if (mpc) { mpc.close(); mpc = null; }
    send({ type: 'want-stream', mode: mirrorMode });
    clearTimeout(mirrorFallback);
    if (mirrorMode === 'webrtc') mirrorFallback = setTimeout(() => { if (!mpc || mpc.connectionState !== 'connected') { mirrorMode = 'frames'; mirrorRequest(); } }, 10000);
  }
  async function mirrorOffer(sdp) {
    if (mpc) mpc.close();
    mpc = new RTCPeerConnection({ iceServers: window.ICE_SERVERS || [] });
    mpc.ontrack = (e) => {
      video.srcObject = e.streams[0]; if (mimg) mimg.style.display = 'none'; video.style.display = '';
      mirrorPlay('ontrack');
      const pcAt = mpc;
      setTimeout(() => mirrorReport(pcAt), 5000);
    };
    mpc.onicecandidate = (e) => send({ type: 'ice', candidate: e.candidate });
    mpc.onconnectionstatechange = () => { if (mpc.connectionState === 'connected') { clearTimeout(mirrorFallback); setStatus('Mirroring the glasses (WebRTC)'); } };
    await mpc.setRemoteDescription(sdp);
    const answer = await mpc.createAnswer();
    await mpc.setLocalDescription(answer);
    send({ type: 'answer', sdp: mpc.localDescription });
  }
  // Mobile Safari may refuse to start a video by itself (Low Power Mode, autoplay policy): try, and
  // if it refuses, show a "tap to start video" note and play on the first tap (a user gesture).
  function mirrorPlay(why) {
    const p = video.play();
    if (!p || !p.then) return;
    p.then(() => { evt('mirror_play', { ok: true, why: why }); hideTapNote(); })
     .catch((err) => { evt('mirror_play', { ok: false, why: why, err: err && err.name }); showTapNote(); });
  }
  let tapNote = null;
  function showTapNote() {
    if (tapNote) return;
    tapNote = document.createElement('div');
    tapNote.textContent = 'Tap to start video';
    tapNote.style.cssText = 'position:fixed;left:0;right:0;top:40%;text-align:center;font-size:28px;color:#000;background:#ffd23c;padding:18px;z-index:9;';
    document.body.appendChild(tapNote);
    document.addEventListener('click', function once() { document.removeEventListener('click', once); mirrorPlay('tap'); });
  }
  function hideTapNote() { if (tapNote) { tapNote.remove(); tapNote = null; } }
  async function mirrorReport(pcAt) {
    const d = { w: video.videoWidth, h: video.videoHeight, paused: video.paused, ready: video.readyState, pc: pcAt ? pcAt.connectionState : null };
    try {
      const rep = await pcAt.getStats();
      rep.forEach((r) => { if (r.type === 'inbound-rtp' && (r.kind === 'video' || r.mediaType === 'video')) { d.framesDecoded = r.framesDecoded; d.bytes = r.bytesReceived; d.codec = r.codecId; } });
    } catch (e) { d.err = String(e); }
    evt('mirror_video', d);
  }
  function mirrorFrame(buf) {
    if (!mimg) return;
    const url = URL.createObjectURL(new Blob([stripHeader(buf)], { type: 'image/jpeg' }));
    mimg.onload = () => { URL.revokeObjectURL(url); video.style.display = 'none'; mimg.style.display = 'block'; };
    mimg.src = url;
  }
  function connectMirror() {
    const scheme = location.protocol === 'https:' ? 'wss://' : 'ws://';
    ws = new WebSocket(scheme + location.host + '/ws');
    ws.binaryType = 'arraybuffer';
    ws.onopen = () => { send({ type: 'hello', role: 'viewer', page: PAGE + '?mirror' }); setStatus('Waiting for the glasses…'); setModel('Waiting for the glasses…', 'warn'); };
    ws.onclose = () => { setStatus('Server disconnected, retrying…', true); if (mpc) { mpc.close(); mpc = null; } setTimeout(connectMirror, 2000); };
    ws.onmessage = async (ev) => {
      if (ev.data instanceof ArrayBuffer) { mirrorFrame(ev.data); return; }
      let msg; try { msg = JSON.parse(ev.data); } catch (e) { return; }
      switch (msg.type) {
        case 'broadcaster': if (msg.online) mirrorRequest(); else setModel('Glasses offline', 'bad'); break;
        case 'offer': await mirrorOffer(msg.sdp); break;
        case 'ice': if (mpc && msg.candidate) mpc.addIceCandidate(msg.candidate).catch(() => {}); break;
        case 'result': showResult(msg.result); break;
        case 'ping': send({ type: 'pong', ts: msg.ts, tc: Date.now() }); break;
        case 'run': onRun(msg); break;
      }
    };
  }
  // the mirror draws over whichever element is showing; both are object-fit: contain in #view
  function mirrorFrameSize() {
    if (mimg && mimg.style.display === 'block' && mimg.naturalWidth) return { w: mimg.naturalWidth, h: mimg.naturalHeight };
    return video.videoWidth ? { w: video.videoWidth, h: video.videoHeight } : null;
  }

  // ---- signaling ----------------------------------------------------------
  function connectWS() {
    const scheme = location.protocol === 'https:' ? 'wss://' : 'ws://';
    ws = new WebSocket(scheme + location.host + '/ws');
    ws.binaryType = 'arraybuffer';
    ws.onopen = () => {
      send({ type: 'hello', role: 'broadcaster', fake: FAKE, page: PAGE });
      if (stream) { send({ type: 'ready', cam: camInfo() }); evt('ws_reconnected', { reconnects: st.reconnects }); }
      if (!stream && !requesting) setStatus('Connected. Waiting for camera…');
    };
    ws.onclose = (ev) => {
      for (const pc of peers.values()) pc.close();
      peers.clear();
      stopFrames();
      if (ev && (ev.code === 4000 || ev.code === 4001)) {        // another source owns the slot: do not fight for it
        setStatus(ev.code === 4001 ? 'Glasses are broadcasting; this page stays passive.' : 'Replaced by another camera source.', true);
        setModel('Another camera source is active', 'warn');
        return;
      }
      st.reconnects++;
      setStatus('Server disconnected, retrying…', true);
      setTimeout(connectWS, 2000);
    };
    ws.onmessage = async (ev) => {
      let msg; try { msg = JSON.parse(ev.data); } catch (e) { return; }
      switch (msg.type) {
        case 'viewers': viewersEl.textContent = msg.count + ' viewer' + (msg.count === 1 ? '' : 's'); break;
        case 'frames': frameViewers = STREAM ? msg.count : 0; if (frameViewers > 0) startFrames(); else stopFrames(); break;
        case 'want-stream': if (stream && STREAM && !paused) await makeOffer(msg.from); break;
        case 'result': showResult(msg.result); break;
        case 'answer': { const pc = peers.get(msg.from); if (pc) await pc.setRemoteDescription(msg.sdp).catch(console.error); break; }
        case 'ice': { const pc = peers.get(msg.from); if (pc && msg.candidate) pc.addIceCandidate(msg.candidate).catch(() => {}); break; }
        case 'viewer-left': { const pc = peers.get(msg.from); if (pc) { pc.close(); peers.delete(msg.from); } break; }
        case 'snapshot': sendSnapshot(msg.id); break;
        case 'ping': send({ type: 'pong', ts: msg.ts, tc: Date.now() }); break;
        case 'run': onRun(msg); break;
        case 'control': onControl(msg.action); break;
        case 'probe': runProbe(msg.bytes || 2000000); break;
      }
    };
  }

  // ---- runtime control (Part B: start / stop / pause) + field runs (Part C) ----------------
  function onRun(msg) {
    run.active = !!msg.active; run.id = msg.id || null; run.scenario = msg.scenario || null;
    if (run.active) { st.framesSent = 0; st.frameBytes = 0; st.framesSkipped = 0; }
    renderAge();
  }
  function onControl(action) {
    if (action === 'pause') {
      paused = true;
      clearInterval(assistTimer); assistTimer = null;
      stopFrames();
      for (const pc of peers.values()) pc.close();
      peers.clear();
      setModel('Paused', 'warn');
      evt('paused');
    } else if (action === 'resume') {
      paused = false;
      if (frameViewers > 0) startFrames();
      send({ type: 'ready', cam: camInfo() });          // viewers re-request WebRTC
      if (ASSIST && stream && !assistTimer) { setModel('Looking for the board…', 'warn'); assistTimer = setInterval(assistOnce, ASSIST_MS); assistOnce(); }
      evt('resumed');
    } else if (action === 'analyze') {
      assistOnce();
    } else if (action === 'reload') {
      location.reload();
    }
  }

  // Bandwidth probe: download then upload `n` incompressible bytes over the same path as the stream.
  async function runProbe(n) {
    const result = { bytes: n };
    try {
      const t0 = Date.now();
      const r = await fetch('/probe/down?bytes=' + n + '&r=' + Math.random(), { cache: 'no-store' });
      const ab = await r.arrayBuffer();
      const t1 = Date.now();
      result.down_ms = t1 - t0; result.down_mbps = ab.byteLength * 8 / (result.down_ms / 1000) / 1e6;
      const body = new Uint8Array(ab);                 // reuse the random bytes so the upload is incompressible too
      const t2 = Date.now();
      await fetch('/probe/up', { method: 'POST', body: body, headers: { 'content-type': 'application/octet-stream' } });
      const t3 = Date.now();
      result.up_ms = t3 - t2; result.up_mbps = n * 8 / (result.up_ms / 1000) / 1e6;
    } catch (e) {
      result.error = e.message;
    }
    send({ type: 'probe_result', result: result });
  }

  // ---- WebRTC -------------------------------------------------------------
  async function makeOffer(viewerId) {
    const old = peers.get(viewerId);
    if (old) old.close();
    const pc = new RTCPeerConnection({ iceServers: window.ICE_SERVERS || [] }); // [] = same LAN only; demo.sh adds STUN
    peers.set(viewerId, pc);
    pc.createdAt = Date.now();
    for (const track of stream.getTracks()) pc.addTrack(track, stream);
    pc.onicecandidate = (e) => send({ type: 'ice', to: viewerId, candidate: e.candidate });
    pc.onconnectionstatechange = () => {
      evt('peer_state', { viewer: viewerId, state: pc.connectionState, age_ms: Date.now() - pc.createdAt });
      if (['failed', 'closed'].includes(pc.connectionState) && peers.get(viewerId) === pc) { peers.delete(viewerId); pc.close(); }
    };
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    send({ type: 'offer', to: viewerId, sdp: pc.localDescription });
  }

  // Subset of getStats() for one peer: cumulative counters, the analysis takes deltas.
  async function peerStats(viewerId, pc) {
    const out = { viewer: viewerId, state: pc.connectionState };
    let rep;
    try { rep = await pc.getStats(); } catch (e) { out.error = e.message; return out; }
    let selectedPair = null;
    rep.forEach((r) => { if (r.type === 'transport' && r.selectedCandidatePairId) selectedPair = r.selectedCandidatePairId; });
    rep.forEach((r) => {
      if (r.type === 'outbound-rtp' && (r.kind === 'video' || r.mediaType === 'video' || !r.kind)) {
        out.bytesSent = r.bytesSent; out.packetsSent = r.packetsSent; out.framesEncoded = r.framesEncoded; out.framesSent = r.framesSent;
        out.nack = r.nackCount; out.pli = r.pliCount; out.fir = r.firCount; out.limit = r.qualityLimitationReason;
        out.w = r.frameWidth; out.h = r.frameHeight; out.fps = r.framesPerSecond; out.encode_s = r.totalEncodeTime; out.retrans = r.retransmittedBytesSent;
      } else if (r.type === 'remote-inbound-rtp') {
        out.lost = r.packetsLost; out.jitter = r.jitter; out.rtt_rtcp = r.roundTripTime; out.fractionLost = r.fractionLost;
      } else if (r.type === 'candidate-pair' && (r.id === selectedPair || (selectedPair === null && r.nominated && r.state === 'succeeded'))) {
        out.rtt = r.currentRoundTripTime; out.avail_out = r.availableOutgoingBitrate; out.pairBytesSent = r.bytesSent; out.pairBytesRecv = r.bytesReceived;
      }
    });
    return out;
  }

  // ---- JPEG frames fallback -----------------------------------------------
  const canvas = document.createElement('canvas');
  function startFrames() {
    if (frameTimer || !stream || paused) return;
    const ctx = canvas.getContext('2d');
    let busy = false;
    frameTimer = setInterval(() => {
      if (busy || !ws || ws.readyState !== 1 || ws.bufferedAmount > 1000000 || !video.videoWidth) { st.framesSkipped++; return; }
      const scale = Math.min(1, FRAME_W / video.videoWidth);
      canvas.width = Math.round(video.videoWidth * scale);
      canvas.height = Math.round(video.videoHeight * scale);
      const capT = Date.now();
      ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
      busy = true;
      canvas.toBlob((blob) => {
        busy = false;
        if (!blob || ws.readyState !== 1) return;
        const seq = ++st.seq;
        ws.send(new Blob([frameHeader(seq, capT), blob]));
        st.framesSent++; st.frameBytes += blob.size + 16;
      }, 'image/jpeg', QUALITY);
    }, 1000 / FPS);
  }
  function stopFrames() { clearInterval(frameTimer); frameTimer = null; }

  // ---- full-resolution snapshot on request (for chess analysis) -----------
  const snapCanvas = document.createElement('canvas');
  function sendSnapshot(id) {
    if (!stream || !video.videoWidth) return;
    const capT = Date.now();
    snapCanvas.width = video.videoWidth; snapCanvas.height = video.videoHeight;
    snapCanvas.getContext('2d').drawImage(video, 0, 0);
    snapCanvas.toBlob((blob) => {
      if (!blob) return;
      fetch('/snapshot/' + id, { method: 'POST', body: blob, headers: { 'content-type': 'image/jpeg', 'x-cap': String(capT) } }).catch(() => {});
    }, 'image/jpeg', 0.92);
  }

  // ---- chess assist: picture -> Mac analyses -> dots on this display -------
  const dots = document.getElementById('dots');
  const moveEl = document.getElementById('move');
  const hintEl = document.getElementById('hint');
  let lastResult = null, resultAt = 0, assisting = false, assistTimer = null;
  const modelEl = document.getElementById('model');
  let lastStatus = { text: 'starting…', level: '' }, lastOkAt = 0;

  // HUD status block: what the model thinks it is doing, plus its confidence numbers
  function setModel(text, level, d) {
    lastStatus = { text, level };
    if (!modelEl) return;
    const bits = [];
    if (d && d.board_score != null) bits.push('board ' + Number(d.board_score).toFixed(2));
    if (d && d.agree != null) bits.push('match ' + d.agree + '/64');
    if (d && d.moves && d.moves[0]) bits.push('eval ' + d.moves[0].eval);
    if (d && d.side) bits.push('you: ' + d.side);
    modelEl.innerHTML = '<b class="' + (level || '') + '">' + text + '</b>' +
      (bits.length ? '<br>' + bits.join(' · ') : '') +
      '<br><span id="age"></span>';
    renderAge();
  }
  function renderAge() {
    const age = document.getElementById('age');
    if (!age) return;
    age.textContent = (lastOkAt ? ('last good read ' + Math.round((performance.now() - lastOkAt) / 1000) + 's ago') : 'no good read yet') +
      (run.active ? ' · ● REC ' + (run.scenario || '') : '');
  }
  setInterval(renderAge, 1000);

  function takePicture() {
    return new Promise((resolve) => {
      if (!stream || !video.videoWidth) return resolve(null);
      snapCanvas.width = video.videoWidth; snapCanvas.height = video.videoHeight;
      snapCanvas.getContext('2d').drawImage(video, 0, 0);
      snapCanvas.toBlob((blob) => resolve(blob), 'image/jpeg', 0.92);
    });
  }

  async function assistOnce() {
    if (assisting || paused) return;
    assisting = true;
    const a = { seq: ++st.assistSeq, cap: Date.now() };
    try {
      const blob = await takePicture();
      if (!blob) { a.status = -1; return; }
      a.bytes = blob.size; a.t_send = Date.now();
      const r = await fetch('/analyze?side=' + SIDE + '&think=1.0&track=' + TRACK, { method: 'POST', body: blob,
        headers: { 'content-type': 'image/jpeg', 'x-cap': String(a.cap), 'x-seq': String(a.seq), 'x-src': 'glasses' } });
      a.status = r.status;
      const d = await r.json();
      a.t_resp = Date.now();
      a.req = d.req; a.ok = !!d.ok; a.state = d.state || null;
      showResult(d);
      a.t_shown = Date.now();
    } catch (e) {
      a.status = a.status || 0; a.error = e.message; a.t_resp = Date.now();
      hintEl.textContent = 'analysis failed: ' + e.message;
      setModel('Mac unreachable: ' + e.message, 'bad');
    } finally {
      assisting = false;
      st.assist.push(a);
    }
  }

  function showResult(d) {
    if (!d || !d.ok) {
      const err = d && d.error ? d.error : 'no result';
      hintEl.textContent = err;
      const level = /opponent/.test(err) ? 'ok' : (/confirm|unclear|unreadable/.test(err) ? 'warn' : 'bad');
      setModel(/opponent/.test(err) ? 'Opponent to move' : err, level, d);
      if (d && d.agree != null) lastOkAt = performance.now();
      if ((d && d.clear) || performance.now() - resultAt > 10000) { lastResult = null; moveEl.textContent = modelEl ? '…' : ''; drawDots(); }
      return;
    }
    lastResult = d; resultAt = performance.now(); lastOkAt = resultAt;
    if (d.problems && d.problems.length) {
      hintEl.textContent = 'position unclear: ' + d.problems.join(', ');
      setModel('Position unclear: ' + d.problems.join(', '), 'warn', d);
      moveEl.textContent = '';
    } else if (d.moves && d.moves.length) {
      const m = d.moves[0];
      hintEl.textContent = '';
      moveEl.innerHTML = m.san + '<small>' + m.from + ' → ' + m.to + ' · ' + m.eval + '</small>';
      if (modelEl) moveEl.style.fontSize = (m.san.length <= 3 ? 150 : m.san.length <= 5 ? 110 : 78) + 'px';   // HUD: fit the panel
      setModel('Your move', 'ok', d);
    }
    drawDots();
  }

  // frame pixels -> displayed pixels (video is object-fit: contain inside #view)
  function drawDots() {
    const view = document.getElementById('view');
    dots.width = view.clientWidth; dots.height = view.clientHeight;
    const ctx = dots.getContext('2d');
    ctx.clearRect(0, 0, dots.width, dots.height);
    if (!lastResult || !lastResult.arrow) return;
    if (!MIRROR && !video.videoWidth) return;
    if (MIRROR && !mirrorFrameSize()) return;
    const fw = lastResult.frame.w, fh = lastResult.frame.h;
    const scale = Math.min(dots.width / fw, dots.height / fh);
    const ox = (dots.width - fw * scale) / 2, oy = (dots.height - fh * scale) / 2;
    const rad = Math.max(6, (lastResult.square_px || 40) * scale * 0.3);
    const P = (p) => [ox + p[0] * scale, oy + p[1] * scale];
    if (MARKS === 'arrow') {
      const [x1, y1] = P(lastResult.arrow.from), [x2, y2] = P(lastResult.arrow.to);
      const ang = Math.atan2(y2 - y1, x2 - x1), head = Math.max(14, rad * 1.6), w = Math.max(6, rad * 0.6);
      // shorten the shaft so the head ends at the square centre
      const ex = x2 - head * 0.6 * Math.cos(ang), ey = y2 - head * 0.6 * Math.sin(ang);
      for (const [col, lw] of [['rgba(0,0,0,.75)', w + 6], ['#ffd23c', w]]) {
        ctx.strokeStyle = col; ctx.fillStyle = col; ctx.lineWidth = lw; ctx.lineCap = 'round'; ctx.lineJoin = 'round';
        ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(ex, ey); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(x2, y2);
        ctx.lineTo(x2 - head * Math.cos(ang - 0.45), y2 - head * Math.sin(ang - 0.45));
        ctx.lineTo(x2 - head * Math.cos(ang + 0.45), y2 - head * Math.sin(ang + 0.45)); ctx.closePath(); ctx.fill();
        ctx.beginPath(); ctx.arc(x1, y1, lw * 0.9, 0, Math.PI * 2); ctx.fill();
      }
      return;
    }
    for (const [pt, col] of [[lastResult.arrow.from, '#2ecc40'], [lastResult.arrow.to, '#ff3b30']]) {
      const [x, y] = P(pt);
      ctx.beginPath(); ctx.arc(x, y, rad + 3, 0, Math.PI * 2); ctx.fillStyle = 'rgba(0,0,0,.7)'; ctx.fill();
      ctx.beginPath(); ctx.arc(x, y, rad, 0, Math.PI * 2); ctx.fillStyle = col; ctx.fill();
    }
  }
  window.addEventListener('resize', drawDots);

  // ---- periodic telemetry ---------------------------------------------------
  async function reportStats() {
    if (!ws || ws.readyState !== 1 || !stream) return;
    const peerList = [];
    for (const [vid, pc] of peers) peerList.push(await peerStats(vid, pc));
    const s = {
      ws_buffered: ws.bufferedAmount, frames_sent: st.framesSent, frame_bytes: st.frameBytes, frames_skipped: st.framesSkipped,
      frames_on: !!frameTimer, peers: peerList, paused: paused, reconnects: st.reconnects,
      last_ok_age_ms: lastOkAt ? Math.round(performance.now() - lastOkAt) : null, status: lastStatus.text,
      assist: st.assist.splice(0), cam: camInfo(),
    };
    if (performance.memory) s.heap_mb = Math.round(performance.memory.usedJSHeapSize / 1048576);
    if (navigator.getBattery) { try { const b = await navigator.getBattery(); s.battery = b.level; s.charging = b.charging; } catch (e) {} }
    send({ type: 'stats', stats: s });
  }
  setInterval(reportStats, STATS_MS);

  // ---- camera -------------------------------------------------------------
  // Synthetic moving test pattern (?fake=1) for checking the pipeline without a camera.
  function fakeStream() {
    const c = document.createElement('canvas'); c.width = 640; c.height = 360;
    const ctx = c.getContext('2d'); const t0 = Date.now();
    setInterval(() => {
      const t = (Date.now() - t0) / 1000;
      ctx.fillStyle = 'hsl(' + ((t * 20) % 360) + ' 50% 20%)'; ctx.fillRect(0, 0, c.width, c.height);
      ctx.fillStyle = '#fff'; ctx.beginPath(); ctx.arc(320 + 200 * Math.cos(t), 180 + 120 * Math.sin(t), 24, 0, Math.PI * 2); ctx.fill();
      ctx.font = '28px monospace'; ctx.fillText('TEST PATTERN ' + t.toFixed(1) + 's', 20, 40);
    }, 1000 / 30);
    return c.captureStream(30);
  }

  function camInfo() {
    if (!stream) return null;
    const track = stream.getVideoTracks()[0];
    const s = track ? track.getSettings() : {};
    return { w: s.width || video.videoWidth, h: s.height || video.videoHeight, fps: s.frameRate || null, fake: FAKE };
  }

  async function startCamera() {
    if (stream || requesting) return;
    if (FAKE) { stream = fakeStream(); onStream(); return; }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      setStatus('Camera API unavailable on ' + location.origin + '. The page must be loaded over https:// or from http://localhost (use adb reverse).', true);
      return;
    }
    setStatus('Requesting camera…');
    requesting = true;
    const t0 = Date.now();
    try {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: false, video: { width: { ideal: WIDTH }, height: { ideal: HEIGHT }, facingMode: FACING } });
      } catch (e) {
        stream = await navigator.mediaDevices.getUserMedia({ audio: false, video: true }); // any camera
      }
    } catch (e) {
      setStatus('Camera error: ' + e.name + ' – ' + e.message, true);
      setModel('Camera error: ' + e.name, 'bad');
      evt('camera_error', { name: e.name, message: e.message });
      startBtn.disabled = false;
      return;
    } finally {
      requesting = false;
    }
    evt('camera_started', { ms: Date.now() - t0, cam: camInfo() });
    onStream();
  }

  function onStream() {
    video.srcObject = stream;
    startBtn.textContent = 'Camera on';
    startBtn.disabled = true;
    const track = stream.getVideoTracks()[0];
    const s = track.getSettings();
    const label = FAKE ? 'test pattern' : (track.label || 'camera').slice(0, 40);
    setStatus('Camera ' + s.width + '×' + s.height + (s.frameRate ? ' @' + Math.round(s.frameRate) + 'fps' : '') + ' – ' + label);
    track.onended = () => { setStatus('Camera track ended', true); evt('camera_ended'); stream = null; startBtn.disabled = false; startBtn.textContent = 'Start camera'; };
    send({ type: 'ready', cam: camInfo() });
    if (frameViewers > 0) startFrames();
    if (ASSIST) {
      hintEl.textContent = 'assist on';
      setModel('Looking for the board…', 'warn');
      document.getElementById('bar').style.display = 'none';     // give the whole display to the picture
      setTimeout(assistOnce, 1500); assistTimer = setInterval(assistOnce, ASSIST_MS);
    }
    // Keep the glasses' screen on; Chrome pauses capture when the display sleeps.
    if (navigator.wakeLock) navigator.wakeLock.request('screen').catch(() => {});
  }

  if (MIRROR) {
    if (document.getElementById('bar')) document.getElementById('bar').style.display = 'none';
    connectMirror();
  } else {
    startBtn.onclick = startCamera;
    connectWS();
    if (AUTOSTART) startCamera();
  }
})();
