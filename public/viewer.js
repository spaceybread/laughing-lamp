// Viewer: runs on your laptop. Receives the glasses camera via WebRTC, or
// JPEG frames over the WebSocket. Auto-falls back to frames if WebRTC
// can't connect (e.g. glasses only reachable over USB / adb reverse).
(() => {
  const qs = new URLSearchParams(location.search);
  const FALLBACK_MS = 10000;
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
  const showVideo = (on) => { video.classList.toggle('show', on); img.classList.toggle('show', !on && mode === 'frames'); placeholder.style.display = 'none'; };
  const showPlaceholder = () => { video.classList.remove('show'); img.classList.remove('show'); placeholder.style.display = ''; infoEl.textContent = '—'; fpsEl.textContent = '—'; };

  function tick(w, h) {
    frames++;
    const now = performance.now();
    if (now - lastFpsAt >= 1000) {
      fpsEl.textContent = `${(frames * 1000 / (now - lastFpsAt)).toFixed(1)} fps`;
      frames = 0; lastFpsAt = now;
    }
    const res = `${w}×${h}`;
    if (res !== lastRes) { lastRes = res; infoEl.textContent = `${mode === 'frames' ? 'JPEG frames' : 'WebRTC'} · ${res}`; }
  }

  // ---- signaling ----------------------------------------------------------
  function connectWS() {
    const scheme = location.protocol === 'https:' ? 'wss://' : 'ws://';
    ws = new WebSocket(scheme + location.host + '/ws');
    ws.binaryType = 'arraybuffer';
    ws.onopen = () => { send({ type: 'hello', role: 'viewer' }); setStatus('Waiting for glasses…', 'warn'); };
    ws.onclose = () => { setStatus('Server disconnected, retrying…'); teardown(); showPlaceholder(); setTimeout(connectWS, 2000); };
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
    lastRes = '';
  }

  function setMode(m) {
    mode = m; modeSel.value = m;
    history.replaceState(null, '', m === 'frames' ? '?mode=frames' : location.pathname);
    if (broadcasterOnline) request();
  }
  modeSel.onchange = () => setMode(modeSel.value);

  // ---- WebRTC -------------------------------------------------------------
  async function onOffer(sdp) {
    if (pc) pc.close();
    pc = new RTCPeerConnection({ iceServers: [] });
    pc.ontrack = (e) => { video.srcObject = e.streams[0]; showVideo(true); };
    pc.onicecandidate = (e) => send({ type: 'ice', candidate: e.candidate });
    pc.onconnectionstatechange = () => {
      if (pc.connectionState === 'connected') { clearTimeout(fallbackTimer); setStatus('Live (WebRTC)', 'ok'); }
      else if (pc.connectionState === 'failed') { setStatus('WebRTC failed – switching to frames', 'warn'); setMode('frames'); }
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
    const url = URL.createObjectURL(new Blob([buf], { type: 'image/jpeg' }));
    img.onload = () => { URL.revokeObjectURL(url); showVideo(false); setStatus('Live (JPEG frames)', 'ok'); tick(img.naturalWidth, img.naturalHeight); };
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
