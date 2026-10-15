// Broadcaster: runs in the browser on the glasses.
// Captures the camera with getUserMedia, then either:
//   - streams it over WebRTC to each viewer that asks (default), or
//   - sends JPEG snapshots over the WebSocket to viewers in "frames" mode.
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
  const STREAM = qs.get('stream') !== '0';         // stream=0: no live video to the laptop, only pictures + results

  const video = document.getElementById('preview');
  const statusEl = document.getElementById('status');
  const viewersEl = document.getElementById('viewers');
  const startBtn = document.getElementById('start');

  let ws = null;
  let stream = null;
  let frameViewers = 0;
  let frameTimer = null;
  let requesting = false;
  const peers = new Map(); // viewerId -> RTCPeerConnection

  const setStatus = (text, isErr) => { statusEl.textContent = text; statusEl.className = isErr ? 'err' : ''; };
  const send = (obj) => { if (ws && ws.readyState === 1) ws.send(JSON.stringify(obj)); };

  // ---- signaling ----------------------------------------------------------
  function connectWS() {
    const scheme = location.protocol === 'https:' ? 'wss://' : 'ws://';
    ws = new WebSocket(scheme + location.host + '/ws');
    ws.binaryType = 'arraybuffer';
    ws.onopen = () => { send({ type: 'hello', role: 'broadcaster' }); if (stream) send({ type: 'ready' }); if (!stream && !requesting) setStatus('Connected. Waiting for camera…'); };
    ws.onclose = () => {
      setStatus('Server disconnected, retrying…', true);
      for (const pc of peers.values()) pc.close();
      peers.clear();
      stopFrames();
      setTimeout(connectWS, 2000);
    };
    ws.onmessage = async (ev) => {
      let msg; try { msg = JSON.parse(ev.data); } catch { return; }
      switch (msg.type) {
        case 'viewers': viewersEl.textContent = `${msg.count} viewer${msg.count === 1 ? '' : 's'}`; break;
        case 'frames': frameViewers = STREAM ? msg.count : 0; if (frameViewers > 0) startFrames(); else stopFrames(); break;
        case 'want-stream': if (stream && STREAM) await makeOffer(msg.from); break;
        case 'result': showResult(msg.result); break;
        case 'answer': { const pc = peers.get(msg.from); if (pc) await pc.setRemoteDescription(msg.sdp).catch(console.error); break; }
        case 'ice': { const pc = peers.get(msg.from); if (pc && msg.candidate) pc.addIceCandidate(msg.candidate).catch(() => {}); break; }
        case 'viewer-left': { const pc = peers.get(msg.from); if (pc) { pc.close(); peers.delete(msg.from); } break; }
        case 'snapshot': sendSnapshot(msg.id); break;
      }
    };
  }

  // ---- WebRTC -------------------------------------------------------------
  async function makeOffer(viewerId) {
    const old = peers.get(viewerId);
    if (old) old.close();
    const pc = new RTCPeerConnection({ iceServers: [] }); // same LAN: host candidates are enough
    peers.set(viewerId, pc);
    for (const track of stream.getTracks()) pc.addTrack(track, stream);
    pc.onicecandidate = (e) => send({ type: 'ice', to: viewerId, candidate: e.candidate });
    pc.onconnectionstatechange = () => {
      if (['failed', 'closed'].includes(pc.connectionState) && peers.get(viewerId) === pc) { peers.delete(viewerId); pc.close(); }
    };
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    send({ type: 'offer', to: viewerId, sdp: pc.localDescription });
  }

  // ---- JPEG frames fallback -----------------------------------------------
  const canvas = document.createElement('canvas');
  function startFrames() {
    if (frameTimer || !stream) return;
    const ctx = canvas.getContext('2d');
    let busy = false;
    frameTimer = setInterval(() => {
      if (busy || !ws || ws.readyState !== 1 || ws.bufferedAmount > 1000000 || !video.videoWidth) return;
      const scale = Math.min(1, FRAME_W / video.videoWidth);
      canvas.width = Math.round(video.videoWidth * scale);
      canvas.height = Math.round(video.videoHeight * scale);
      ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
      busy = true;
      canvas.toBlob((blob) => { busy = false; if (blob && ws.readyState === 1) ws.send(blob); }, 'image/jpeg', QUALITY);
    }, 1000 / FPS);
  }
  function stopFrames() { clearInterval(frameTimer); frameTimer = null; }

  // ---- full-resolution snapshot on request (for chess analysis) -----------
  const snapCanvas = document.createElement('canvas');
  function sendSnapshot(id) {
    if (!stream || !video.videoWidth) return;
    snapCanvas.width = video.videoWidth; snapCanvas.height = video.videoHeight;
    snapCanvas.getContext('2d').drawImage(video, 0, 0);
    snapCanvas.toBlob((blob) => {
      if (!blob) return;
      fetch('/snapshot/' + id, { method: 'POST', body: blob, headers: { 'content-type': 'image/jpeg' } }).catch(() => {});
    }, 'image/jpeg', 0.92);
  }

  // ---- chess assist: picture -> Mac analyses -> dots on this display -------
  const dots = document.getElementById('dots');
  const moveEl = document.getElementById('move');
  const hintEl = document.getElementById('hint');
  let lastResult = null, resultAt = 0, assisting = false;

  function takePicture() {
    return new Promise((resolve) => {
      if (!stream || !video.videoWidth) return resolve(null);
      snapCanvas.width = video.videoWidth; snapCanvas.height = video.videoHeight;
      snapCanvas.getContext('2d').drawImage(video, 0, 0);
      snapCanvas.toBlob((blob) => resolve(blob), 'image/jpeg', 0.92);
    });
  }

  async function assistOnce() {
    if (assisting) return;
    assisting = true;
    try {
      const blob = await takePicture();
      if (!blob) return;
      const r = await fetch('/analyze?side=' + SIDE + '&think=1.0', { method: 'POST', body: blob, headers: { 'content-type': 'image/jpeg' } });
      const d = await r.json();
      showResult(d);
    } catch (e) {
      hintEl.textContent = 'analysis failed: ' + e.message;
    } finally {
      assisting = false;
    }
  }

  function showResult(d) {
    if (!d || !d.ok) {
      hintEl.textContent = d && d.error ? d.error : 'no result';
      if ((d && d.clear) || performance.now() - resultAt > 10000) { lastResult = null; moveEl.textContent = ''; drawDots(); }
      return;
    }
    lastResult = d; resultAt = performance.now();
    if (d.problems && d.problems.length) {
      hintEl.textContent = 'position unclear: ' + d.problems.join(', ');
      moveEl.textContent = '';
    } else if (d.moves && d.moves.length) {
      const m = d.moves[0];
      hintEl.textContent = '';
      moveEl.innerHTML = m.san + '<small>' + m.from + ' \u2192 ' + m.to + ' \u00b7 ' + m.eval + '</small>';
    }
    drawDots();
  }

  // frame pixels -> displayed pixels (video is object-fit: contain inside #view)
  function drawDots() {
    const view = document.getElementById('view');
    dots.width = view.clientWidth; dots.height = view.clientHeight;
    const ctx = dots.getContext('2d');
    ctx.clearRect(0, 0, dots.width, dots.height);
    if (!lastResult || !lastResult.arrow || !video.videoWidth) return;
    const fw = lastResult.frame.w, fh = lastResult.frame.h;
    const scale = Math.min(dots.width / fw, dots.height / fh);
    const ox = (dots.width - fw * scale) / 2, oy = (dots.height - fh * scale) / 2;
    const rad = Math.max(6, (lastResult.square_px || 40) * scale * 0.3);
    const P = (p) => [ox + p[0] * scale, oy + p[1] * scale];
    for (const [pt, col] of [[lastResult.arrow.from, '#2ecc40'], [lastResult.arrow.to, '#ff3b30']]) {
      const [x, y] = P(pt);
      ctx.beginPath(); ctx.arc(x, y, rad + 3, 0, Math.PI * 2); ctx.fillStyle = 'rgba(0,0,0,.7)'; ctx.fill();
      ctx.beginPath(); ctx.arc(x, y, rad, 0, Math.PI * 2); ctx.fillStyle = col; ctx.fill();
    }
  }
  window.addEventListener('resize', drawDots);

  // ---- camera -------------------------------------------------------------
  // Synthetic moving test pattern (?fake=1) for checking the pipeline without a camera.
  function fakeStream() {
    const c = document.createElement('canvas'); c.width = 640; c.height = 360;
    const ctx = c.getContext('2d'); const t0 = Date.now();
    setInterval(() => {
      const t = (Date.now() - t0) / 1000;
      ctx.fillStyle = `hsl(${(t * 20) % 360} 50% 20%)`; ctx.fillRect(0, 0, c.width, c.height);
      ctx.fillStyle = '#fff'; ctx.beginPath(); ctx.arc(320 + 200 * Math.cos(t), 180 + 120 * Math.sin(t), 24, 0, Math.PI * 2); ctx.fill();
      ctx.font = '28px monospace'; ctx.fillText(`TEST PATTERN ${t.toFixed(1)}s`, 20, 40);
    }, 1000 / 30);
    return c.captureStream(30);
  }

  async function startCamera() {
    if (stream || requesting) return;
    if (FAKE) { stream = fakeStream(); onStream(); return; }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      setStatus(`Camera API unavailable on ${location.origin}. The page must be loaded over https:// or from http://localhost (use adb reverse).`, true);
      return;
    }
    setStatus('Requesting camera…');
    requesting = true;
    try {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: false, video: { width: { ideal: WIDTH }, height: { ideal: HEIGHT }, facingMode: FACING } });
      } catch {
        stream = await navigator.mediaDevices.getUserMedia({ audio: false, video: true }); // any camera
      }
    } catch (e) {
      setStatus(`Camera error: ${e.name} – ${e.message}`, true);
      startBtn.disabled = false;
      return;
    } finally {
      requesting = false;
    }
    onStream();
  }

  function onStream() {
    video.srcObject = stream;
    startBtn.textContent = 'Camera on';
    startBtn.disabled = true;
    const track = stream.getVideoTracks()[0];
    const s = track.getSettings();
    const label = FAKE ? 'test pattern' : (track.label || 'camera').slice(0, 40);
    setStatus(`Camera ${s.width}×${s.height}${s.frameRate ? ` @${Math.round(s.frameRate)}fps` : ''} – ${label}`);
    track.onended = () => { setStatus('Camera track ended', true); stream = null; startBtn.disabled = false; startBtn.textContent = 'Start camera'; };
    send({ type: 'ready' });
    if (frameViewers > 0) startFrames();
    if (ASSIST) {
      hintEl.textContent = 'assist on';
      document.getElementById('bar').style.display = 'none';     // give the whole display to the picture
      setTimeout(assistOnce, 1500); setInterval(assistOnce, ASSIST_MS);
    }
    // Keep the glasses' screen on; Chrome pauses capture when the display sleeps.
    if (navigator.wakeLock) navigator.wakeLock.request('screen').catch(() => {});
  }

  startBtn.onclick = startCamera;
  connectWS();
  if (AUTOSTART) startCamera();
})();
