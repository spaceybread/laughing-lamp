// Replay board (Part C/D): plays the games in Bonus-PartC/pgn/games.json one ply at a time on the laptop
// screen, for the glasses to look at instead of a live game. Every position change is reported to the
// relay, which logs it as a `replay` event, so each analysed picture can be scored against the position
// that was actually on screen when it was taken (Bonus-PartC/analyze.py).
//
//   /replay                   waits on the start position until the relay says play
//   /replay?autoplay=1        starts playing right away
//   /replay?every=10000       ms per ply (default 10000)
//
// Relay control (POST /replay?action=..., or ./Bonus-PartC/run.sh replay <action>):
//   play | pause | restart (back to game 1, start position, paused) | next (one ply)
'use strict';
(() => {
  const qs = new URLSearchParams(location.search);
  const EVERY = Number(qs.get('every')) || 10000;
  const LIGHT = '#eeeed2', DARK = '#769656';
  const svg = document.getElementById('board');
  const info = document.getElementById('info');
  const stateEl = document.getElementById('state');

  let data = null, gi = 0, pi = 0, playing = qs.get('autoplay') === '1', timer = null, ws = null;

  function draw() {
    const g = data.games[gi], p = g.plies[pi];
    const flip = g.orient === 'black';
    const rows = p.fen.split(' ')[0].split('/');
    let out = '';
    for (let r = 0; r < 8; r++) {
      for (let c = 0; c < 8; c++) {
        const x = c * 45, y = r * 45;
        out += `<rect x="${x}" y="${y}" width="45" height="45" fill="${(r + c) % 2 ? DARK : LIGHT}"/>`;
      }
    }
    rows.forEach((row, r) => {
      let c = 0;
      for (const ch of row) {
        if (/\d/.test(ch)) { c += Number(ch); continue; }
        const rr = flip ? 7 - r : r, cc = flip ? 7 - c : c;
        out += `<g transform="translate(${cc * 45},${rr * 45})">${data.pieces[ch]}</g>`;
        c++;
      }
    });
    svg.innerHTML = out;
    info.innerHTML = `<b>${g.name}</b> ${g.white} – ${g.black} · ply ${pi}/${g.plies.length - 1}${p.san ? ' · ' + p.san : ''} · game ${gi + 1}/${data.games.length}`;
    stateEl.textContent = playing ? `playing, ${EVERY / 1000} s per ply` : 'paused';
    // report once the new position has actually been painted
    requestAnimationFrame(() => requestAnimationFrame(() => report()));
  }

  function report() {
    const g = data.games[gi], p = g.plies[pi];
    send({ type: 'replay_pos', game: g.name, gi: gi, ply: pi, fen: p.fen, san: p.san, orient: g.orient, playing: playing, tc: Date.now() });
  }

  function step() {
    const g = data.games[gi];
    if (pi < g.plies.length - 1) pi++;
    else { gi = (gi + 1) % data.games.length; pi = 0; }
    draw();
  }

  function schedule() {
    clearInterval(timer); timer = null;
    if (playing) timer = setInterval(step, EVERY);
  }

  function control(action) {
    if (action === 'play') playing = true;
    else if (action === 'pause') playing = false;
    else if (action === 'restart') { playing = false; gi = 0; pi = 0; }
    else if (action === 'next') step();
    schedule(); draw();
  }

  function send(obj) { if (ws && ws.readyState === 1) ws.send(JSON.stringify(obj)); }

  function connect() {
    ws = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws');
    ws.onopen = () => { send({ type: 'hello', role: 'replay', page: 'replay', every: EVERY }); if (data) report(); };
    ws.onclose = () => setTimeout(connect, 2000);
    ws.onmessage = (ev) => {
      let msg; try { msg = JSON.parse(ev.data); } catch (e) { return; }
      if (msg.type === 'replay') control(msg.action);
      else if (msg.type === 'ping') send({ type: 'pong', ts: msg.ts, tc: Date.now() });
    };
  }

  fetch('/replay/games.json').then((r) => r.json()).then((d) => {
    data = d; draw(); schedule(); connect();
  }).catch((e) => { info.textContent = 'could not load games.json: ' + e; });

  // keyboard: space = play/pause, right arrow = next ply, r = restart
  document.addEventListener('keydown', (e) => {
    if (e.key === ' ') control(playing ? 'pause' : 'play');
    else if (e.key === 'ArrowRight') control('next');
    else if (e.key === 'r') control('restart');
  });
})();
