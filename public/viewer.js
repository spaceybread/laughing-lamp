// Viewer: runs on your laptop. Receives the glasses camera via WebRTC, or
// JPEG frames over the WebSocket. Auto-falls back to frames if WebRTC
// can't connect (e.g. glasses only reachable over USB / adb reverse).
// Reports Part C telemetry (frame arrival times, stalls, WebRTC stats) to the relay
// and hosts the field-run controls.
(() => {
  const qs = new URLSearchParams(location.search);
  const FALLBACK_MS = 10000;
  const STALL_MS = 500;                 // no new frame for this long = a stall
  const STATS_MS = 2000;
  const $ = (id) => document.getElementById(id);
  const video = $('video'), img = $('img'), placeholder = $('placeholder');
  const dot = $('dot'), statusEl = $('status'), infoEl = $('info'), fpsEl = $('fps'), modeSel = $('mode');
  $('burl').textContent = `${location.origin}/broadcast`;

  let mode = qs.get('mode') === 'frames' ? 'frames' : 'webrtc';
  modeSel.value = mode;
  let ws = null, pc = null, fallbackTimer = null, broadcasterOnline = false;
  let frames = 0, lastFpsAt = performance.now(), lastRes = '';

  const setStatus = (text, level) => { statusEl.textContent = text; dot.className = 'dot' + (level ? ' ' + level : ''); };
  const send = (obj) => { if (ws && ws.readyState === 1) ws.send(JSON.stringify(obj)); };
  const evt = (name, data) => send({ type: 'event', name, data: data || null });
  const showVideo = (on) => { video.classList.toggle('show', on); img.classList.toggle('show', !on && mode === 'frames'); placeholder.style.display = 'none'; };
  const showPlaceholder = () => { video.classList.remove('show'); img.classList.remove('show'); placeholder.style.display = ''; infoEl.textContent = '—'; fpsEl.textContent = '—'; };

  // ---- telemetry: inter-frame gaps, stalls, frame bytes/seq (frames mode) ----------------------
  const st = { frames: 0, bytes: 0, stalls: 0, stallMs: 0, lastFrameAt: 0, gapN: 0, gapSum: 0, gapSq: 0, gapMax: 0, lastSeq: null, lost: 0, shown: [], reconnects: 0, fallbacks: 0 };
  const FRAME_MAGIC = 0x4652;

  function tick(w, h) {
    frames++;
    const now = performance.now();
    if (st.lastFrameAt) {
      const gap = now - st.lastFrameAt;
      st.gapN++; st.gapSum += gap; st.gapSq += gap * gap; if (gap > st.gapMax) st.gapMax = gap;
      if (gap > STALL_MS) { st.stalls++; st.stallMs += gap; }
    }
    st.lastFrameAt = now; st.frames++;
    if (now - lastFpsAt >= 1000) {
      fpsEl.textContent = `${(frames * 1000 / (now - lastFpsAt)).toFixed(1)} fps`;
      frames = 0; lastFpsAt = now;
    }
    const res = `${w}×${h}`;
    if (res !== lastRes) { lastRes = res; infoEl.textContent = `${mode === 'frames' ? 'JPEG frames' : 'WebRTC'} · ${res}`; }
  }

  async function rtcStats() {
    if (!pc) return null;
    const out = { state: pc.connectionState };
    let rep;
    try { rep = await pc.getStats(); } catch (e) { out.error = e.message; return out; }
    let selectedPair = null;
    rep.forEach((r) => { if (r.type === 'transport' && r.selectedCandidatePairId) selectedPair = r.selectedCandidatePairId; });
    rep.forEach((r) => {
      if (r.type === 'inbound-rtp' && (r.kind === 'video' || r.mediaType === 'video')) {
        out.bytesReceived = r.bytesReceived; out.packetsReceived = r.packetsReceived; out.packetsLost = r.packetsLost; out.jitter = r.jitter;
        out.framesReceived = r.framesReceived; out.framesDecoded = r.framesDecoded; out.framesDropped = r.framesDropped; out.fps = r.framesPerSecond;
        out.w = r.frameWidth; out.h = r.frameHeight; out.nack = r.nackCount; out.pli = r.pliCount;
        out.freezeCount = r.freezeCount; out.freezeS = r.totalFreezesDuration; out.pauseCount = r.pauseCount; out.pauseS = r.totalPausesDuration;
        out.jbDelay = r.jitterBufferDelay; out.jbCount = r.jitterBufferEmittedCount; out.decode_s = r.totalDecodeTime; out.ifd_s = r.totalInterFrameDelay; out.ifd_sq = r.totalSquaredInterFrameDelay;
      } else if (r.type === 'candidate-pair' && (r.id === selectedPair || (selectedPair === null && r.nominated && r.state === 'succeeded'))) {
        out.rtt = r.currentRoundTripTime; out.avail_in = r.availableIncomingBitrate; out.pairBytesRecv = r.bytesReceived; out.pairBytesSent = r.bytesSent;
      }
    });
    return out;
  }

  async function reportStats() {
    if (!ws || ws.readyState !== 1) return;
    const now = performance.now();
    const stalling = st.lastFrameAt && (video.srcObject || mode === 'frames') && now - st.lastFrameAt > STALL_MS;
    const s = {
      mode, online: broadcasterOnline, frames: st.frames, bytes: st.bytes, stalls: st.stalls, stall_ms: Math.round(st.stallMs),
      stalling: !!stalling, cur_gap_ms: st.lastFrameAt ? Math.round(now - st.lastFrameAt) : null,
      gap_n: st.gapN, gap_mean: st.gapN ? st.gapSum / st.gapN : null, gap_std: st.gapN ? Math.sqrt(Math.max(0, st.gapSq / st.gapN - (st.gapSum / st.gapN) ** 2)) : null, gap_max: Math.round(st.gapMax),
      lost: st.lost, shown: st.shown.splice(0), reconnects: st.reconnects, fallbacks: st.fallbacks, rtc: await rtcStats(),
    };
    st.gapN = 0; st.gapSum = 0; st.gapSq = 0; st.gapMax = 0;
    send({ type: 'stats', stats: s });
  }
  setInterval(reportStats, STATS_MS);

  // ---- signaling ----------------------------------------------------------
  function connectWS() {
    const scheme = location.protocol === 'https:' ? 'wss://' : 'ws://';
    ws = new WebSocket(scheme + location.host + '/ws');
    ws.binaryType = 'arraybuffer';
    ws.onopen = () => { send({ type: 'hello', role: 'viewer', page: 'viewer' }); setStatus('Waiting for glasses…', 'warn'); };
    ws.onclose = () => { st.reconnects++; setStatus('Server disconnected, retrying…'); teardown(); showPlaceholder(); setTimeout(connectWS, 2000); };
    ws.onmessage = async (ev) => {
      if (ev.data instanceof ArrayBuffer) { onFrame(ev.data); return; }
      let msg; try { msg = JSON.parse(ev.data); } catch { return; }
      switch (msg.type) {
        case 'broadcaster':
          broadcasterOnline = msg.online;
          if (msg.online) request();
          else { teardown(); showPlaceholder(); setStatus('Glasses offline – waiting…', 'warn'); }
          break;
        case 'offer': if (mode === 'webrtc') await onOffer(msg.sdp); break;
        case 'result': if (msg.annotated) msg.result.annotated = msg.annotated; showResult(msg.result); break;
        case 'ice': if (pc && msg.candidate) pc.addIceCandidate(msg.candidate).catch(() => {}); break;
        case 'ping': send({ type: 'pong', ts: msg.ts, tc: Date.now() }); onPingInfo(msg.info); break;
        case 'run': onRun(msg); break;
        case 'probe_result': onProbe(msg); break;
      }
    };
  }

  function request() {
    teardown();
    send({ type: 'want-stream', mode });
    if (mode === 'webrtc') {
      setStatus('Glasses online – connecting WebRTC…', 'warn');
      fallbackTimer = setTimeout(() => {
        if (!pc || pc.connectionState !== 'connected') {
          console.warn('WebRTC did not connect; falling back to JPEG frames');
          st.fallbacks++; evt('webrtc_fallback');
          setMode('frames');
        }
      }, FALLBACK_MS);
    } else {
      setStatus('Glasses online – receiving frames…', 'warn');
    }
  }

  function teardown() {
    clearTimeout(fallbackTimer); fallbackTimer = null;
    if (pc) { pc.close(); pc = null; }
    video.srcObject = null;
    lastRes = ''; st.lastFrameAt = 0; st.lastSeq = null;
  }

  function setMode(m) {
    mode = m; modeSel.value = m;
    history.replaceState(null, '', m === 'frames' ? '?mode=frames' : location.pathname);
    evt('mode', { mode: m });
    if (broadcasterOnline) request();
  }
  modeSel.onchange = () => setMode(modeSel.value);

  // ---- WebRTC -------------------------------------------------------------
  async function onOffer(sdp) {
    if (pc) pc.close();
    pc = new RTCPeerConnection({ iceServers: [] });
    const createdAt = Date.now();
    pc.ontrack = (e) => { video.srcObject = e.streams[0]; showVideo(true); };
    pc.onicecandidate = (e) => send({ type: 'ice', candidate: e.candidate });
    pc.onconnectionstatechange = () => {
      evt('peer_state', { state: pc.connectionState, age_ms: Date.now() - createdAt });
      if (pc.connectionState === 'connected') { clearTimeout(fallbackTimer); setStatus('Live (WebRTC)', 'ok'); }
      else if (pc.connectionState === 'failed') { setStatus('WebRTC failed – switching to frames', 'warn'); st.fallbacks++; setMode('frames'); }
      else if (pc.connectionState === 'disconnected') setStatus('WebRTC disconnected…', 'warn');
    };
    await pc.setRemoteDescription(sdp);
    const answer = await pc.createAnswer();
    await pc.setLocalDescription(answer);
    send({ type: 'answer', sdp: pc.localDescription });
  }
  // Per-frame callback gives a real fps readout; fall back to timeupdate (~4 Hz) elsewhere.
  if ('requestVideoFrameCallback' in HTMLVideoElement.prototype) {
    const loop = () => { if (video.srcObject) tick(video.videoWidth, video.videoHeight); video.requestVideoFrameCallback(loop); };
    video.requestVideoFrameCallback(loop);
  } else {
    video.addEventListener('timeupdate', () => tick(video.videoWidth, video.videoHeight));
  }

  // ---- JPEG frames --------------------------------------------------------
  function onFrame(buf) {
    if (mode !== 'frames') return;
    let seq = null, cap = null, body = buf;
    if (buf.byteLength >= 16 && new DataView(buf).getUint16(0, true) === FRAME_MAGIC) {
      const dv = new DataView(buf); seq = dv.getUint32(4, true); cap = dv.getFloat64(8, true); body = buf.slice(16);
    }
    st.bytes += buf.byteLength;
    if (seq !== null) { if (st.lastSeq !== null && seq > st.lastSeq + 1) st.lost += seq - st.lastSeq - 1; st.lastSeq = seq; }
    const url = URL.createObjectURL(new Blob([body], { type: 'image/jpeg' }));
    img.onload = () => {
      URL.revokeObjectURL(url); showVideo(false); setStatus('Live (JPEG frames)', 'ok'); tick(img.naturalWidth, img.naturalHeight);
      if (seq !== null) st.shown.push([seq, Date.now(), cap]);
    };
    img.onerror = () => URL.revokeObjectURL(url);
    img.src = url;
  }

  // ---- buttons ------------------------------------------------------------
  $('snap').onclick = () => {
    const src = mode === 'frames' ? img : video;
    const w = mode === 'frames' ? img.naturalWidth : video.videoWidth;
    const h = mode === 'frames' ? img.naturalHeight : video.videoHeight;
    if (!w) return;
    const c = document.createElement('canvas'); c.width = w; c.height = h;
    c.getContext('2d').drawImage(src, 0, 0, w, h);
    const a = document.createElement('a');
    a.href = c.toDataURL('image/png');
    a.download = `glasses-${new Date().toISOString().replace(/[:.]/g, '-')}.png`;
    a.click();
  };
  $('full').onclick = () => { const s = $('stage'); document.fullscreenElement ? document.exitFullscreen() : s.requestFullscreen(); };

  // ---- field runs (Part C) + glasses control (Part B) ---------------------
  const runInfoEl = $('runinfo'), runBtn = $('runbtn'), netEl = $('netinfo');
  let run = null, runTimer = null, glassesRtt = null, paused = false;
  const post = (url) => fetch(url, { method: 'POST' }).then((r) => r.json());
  function renderRun() {
    if (!run) { runInfoEl.textContent = 'no run'; runBtn.textContent = 'Start run'; runBtn.classList.add('primary'); return; }
    const s = Math.round((Date.now() - run.startedAt) / 1000);
    runInfoEl.innerHTML = `<b>● REC</b> ${run.scenario} · ${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
    runBtn.textContent = 'Stop run'; runBtn.classList.remove('primary');
  }
  function onRun(msg) {
    run = msg.active ? { id: msg.id, scenario: msg.scenario, startedAt: Date.now() } : null;
    clearInterval(runTimer); runTimer = run ? setInterval(renderRun, 1000) : null;
    renderRun();
  }
  runBtn.onclick = async () => {
    try {
      if (run) { await post('/run/stop'); return; }
      const scenario = $('scenario').value.trim() || 'unnamed';
      const r = await post(`/run/start?scenario=${encodeURIComponent(scenario)}&net=${encodeURIComponent($('net').value)}`);
      if (!r.ok) alert(r.error);
    } catch (e) { alert(e.message); }
  };
  $('markbtn').onclick = () => { const l = $('marktxt').value.trim(); if (l) { post('/run/mark?label=' + encodeURIComponent(l)); $('marktxt').value = ''; } };
  $('pausebtn').onclick = async () => { paused = !paused; await post('/control?action=' + (paused ? 'pause' : 'resume')); $('pausebtn').textContent = paused ? 'Resume glasses' : 'Pause glasses'; };
  $('probebtn').onclick = () => post('/control?action=probe');
  // refresh the run state on (re)connect
  fetch('/run').then((r) => r.json()).then((r) => { if (r.run) onRun({ active: true, id: r.run.id, scenario: r.run.scenario }); }).catch(() => {});
  function onPingInfo(info) {
    if (!info) return;
    glassesRtt = info.glasses_rtt;
    netEl.textContent = `glasses RTT ${glassesRtt == null ? '—' : glassesRtt + ' ms'} · stalls ${st.stalls} (${(st.stallMs / 1000).toFixed(1)} s)` +
      (mode === 'frames' ? ` · lost ${st.lost}` : '');
    if (info.run && !run) onRun({ active: true, id: info.run, scenario: info.run.replace(/^[^_]+_/, '') });
    if (!info.run && run) onRun({ active: false });
  }
  function onProbe(msg) {
    const r = msg.result || {};
    const who = msg.role === 'broadcaster' ? 'glasses' : `viewer ${msg.id}`;
    $('probeinfo').textContent = r.error ? `${who}: probe failed (${r.error})` : `${who}: ↓ ${r.down_mbps.toFixed(1)} Mb/s · ↑ ${r.up_mbps.toFixed(1)} Mb/s`;
  }

  // ---- chess assistant ----------------------------------------------------
  const overlay = $('overlay'), bestEl = $('best'), altsEl = $('alts'), annotEl = $('annot'), fenEl = $('fen'), msgEl = $('msg');
  let analyzing = false, lastResult = null, overlayUntil = 0;

  async function analyze() {
    if (analyzing) return;
    analyzing = true;
    $('analyze').disabled = true;
    msgEl.textContent = 'Capturing & analysing…';
    try {
      const r = await fetch(`/analyze?side=${$('side').value}&think=1.0`);
      showResult(await r.json());
    } catch (e) {
      msgEl.textContent = 'Analysis request failed: ' + e.message;
    } finally {
      analyzing = false; $('analyze').disabled = false;
    }
  }
  function showResult(d) {
    if (!d.ok) {
      msgEl.textContent = d.error || 'analysis failed';
      if (d.annotated) { annotEl.src = d.annotated; annotEl.classList.add('show'); }
      if (d.fen) fenEl.textContent = d.fen;
      if (d.clear) { bestEl.textContent = '—'; altsEl.textContent = ''; }
      lastResult = null; drawOverlay(); return;
    }
    lastResult = d; overlayUntil = performance.now() + 15000;
    if (d.annotated) { annotEl.src = d.annotated; annotEl.classList.add('show'); }
    fenEl.textContent = d.fen;
    if (d.problems && d.problems.length) {
      msgEl.textContent = 'Position looks wrong: ' + d.problems.join(', ') + ' (red labels = low confidence)';
      bestEl.textContent = '—'; altsEl.textContent = '';
    } else if (d.moves && d.moves.length) {
      const m = d.moves[0];
      msgEl.textContent = `You are ${d.side}. Board ${d.board_score >= 0.6 ? 'clear' : 'marginal'}.`;
      bestEl.innerHTML = `${m.san}<small>${m.from} → ${m.to} · ${m.eval}</small>`;
      altsEl.innerHTML = d.moves.slice(1).map((x) => `${x.san} (${x.eval})`).join(' · ') + `<br>${m.pv}`;
    }
    drawOverlay();
  }
  $('analyze').onclick = analyze;
  let autoTimer = null;
  $('auto').onchange = () => { clearInterval(autoTimer); autoTimer = null; if ($('auto').checked) { analyze(); autoTimer = setInterval(analyze, 4000); } };

  // Draw the best-move arrow over the live picture. The video is object-fit: contain inside #stage,
  // so map frame pixels -> displayed pixels.
  function drawOverlay() {
    const st = $('stage');
    overlay.width = st.clientWidth; overlay.height = st.clientHeight;
    const ctx = overlay.getContext('2d');
    ctx.clearRect(0, 0, overlay.width, overlay.height);
    if (!lastResult || !lastResult.arrow || performance.now() > overlayUntil) return;
    const fw = lastResult.frame.w, fh = lastResult.frame.h;
    const scale = Math.min(overlay.width / fw, overlay.height / fh);
    const ox = (overlay.width - fw * scale) / 2, oy = (overlay.height - fh * scale) / 2;
    const P = (p) => [ox + p[0] * scale, oy + p[1] * scale];
    // board outline
    ctx.beginPath(); lastResult.quad.forEach((q, i) => { const [x, y] = P(q); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); }); ctx.closePath();
    ctx.strokeStyle = 'rgba(79,140,255,.8)'; ctx.lineWidth = 2; ctx.stroke();
    // green dot = piece to move, red dot = destination
    const rad = Math.max(5, (lastResult.square_px || 40) * scale * 0.3);
    for (const [pt, col] of [[lastResult.arrow.from, '#2ecc40'], [lastResult.arrow.to, '#ff3b30']]) {
      const [x, y] = P(pt);
      ctx.beginPath(); ctx.arc(x, y, rad + 3, 0, Math.PI * 2); ctx.fillStyle = 'rgba(0,0,0,.7)'; ctx.fill();
      ctx.beginPath(); ctx.arc(x, y, rad, 0, Math.PI * 2); ctx.fillStyle = col; ctx.fill();
    }
  }
  window.addEventListener('resize', drawOverlay);
  setInterval(drawOverlay, 1000);

  connectWS();
})();
