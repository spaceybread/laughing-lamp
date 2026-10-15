// Glasses camera relay server.
// - Serves /            -> viewer page (open on your laptop)
// - Serves /broadcast   -> broadcaster page (open in a browser on the glasses)
// - /ws                 -> WebSocket used for WebRTC signaling and for the
//                          JPEG-frame fallback (binary messages).
'use strict';

const express = require('express');
const http = require('http');
const https = require('https');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { WebSocketServer } = require('ws');

const PORT = Number(process.env.PORT) || 3000;
const app = express();
app.use(express.static(path.join(__dirname, 'public')));
app.get('/broadcast', (_req, res) => res.sendFile(path.join(__dirname, 'public', 'broadcast.html')));

// HTTPS if certs/ exists (needed for camera access over Wi-Fi), else plain HTTP
// (fine when the glasses reach the server as http://localhost via `adb reverse`).
const keyFile = path.join(__dirname, 'certs', 'key.pem');
const certFile = path.join(__dirname, 'certs', 'cert.pem');
const useTls = fs.existsSync(keyFile) && fs.existsSync(certFile);
const server = useTls
  ? https.createServer({ key: fs.readFileSync(keyFile), cert: fs.readFileSync(certFile) }, app)
  : http.createServer(app);

const wss = new WebSocketServer({ server, path: '/ws' });

let broadcaster = null;        // the single camera source (the glasses)
const viewers = new Map();     // id -> ws
let nextId = 1;

const send = (ws, obj) => { if (ws && ws.readyState === ws.OPEN) ws.send(JSON.stringify(obj)); };
const frameViewerCount = () => [...viewers.values()].filter((v) => v.wantsFrames).length;
const notifyCounts = () => {
  send(broadcaster, { type: 'viewers', count: viewers.size });
  send(broadcaster, { type: 'frames', count: frameViewerCount() });
};
const notifyViewers = (online) => { for (const v of viewers.values()) send(v, { type: 'broadcaster', online }); };

wss.on('connection', (ws, req) => {
  ws.role = null;
  ws.wantsFrames = false;
  ws.isAlive = true;
  ws.on('pong', () => { ws.isAlive = true; });

  ws.on('message', (data, isBinary) => {
    // Binary = JPEG frame from the broadcaster; fan out to viewers in frames mode.
    if (isBinary) {
      if (ws !== broadcaster) return;
      for (const v of viewers.values()) {
        if (v.wantsFrames && v.readyState === v.OPEN && v.bufferedAmount < 2_000_000) v.send(data, { binary: true });
      }
      return;
    }

    let msg;
    try { msg = JSON.parse(data.toString()); } catch { return; }

    switch (msg.type) {
      case 'hello':
        if (msg.role === 'broadcaster') {
          if (broadcaster && broadcaster !== ws) broadcaster.close(4000, 'replaced by a new broadcaster');
          broadcaster = ws;
          ws.role = 'broadcaster';
          ws.ready = false; // becomes true once its camera is running
          console.log(`[broadcaster] connected from ${req.socket.remoteAddress}`);
          notifyCounts();
        } else {
          ws.role = 'viewer';
          ws.id = nextId++;
          viewers.set(ws.id, ws);
          console.log(`[viewer ${ws.id}] connected from ${req.socket.remoteAddress}`);
          send(ws, { type: 'broadcaster', online: !!(broadcaster && broadcaster.ready) });
          notifyCounts();
        }
        break;

      case 'ready':            // broadcaster's camera is up -> viewers (re)request the stream
        if (ws === broadcaster) { ws.ready = true; console.log('[broadcaster] camera ready'); notifyViewers(true); }
        break;

      case 'want-stream':      // viewer asks for the stream in a given mode
        if (ws.role !== 'viewer') return;
        ws.wantsFrames = msg.mode === 'frames';
        notifyCounts();
        if (!ws.wantsFrames) send(broadcaster, { type: 'want-stream', from: ws.id });
        break;

      // WebRTC signaling relay. Broadcaster -> viewer messages carry `to`;
      // viewer -> broadcaster messages get `from` stamped on.
      case 'offer':
      case 'answer':
      case 'ice':
        if (ws === broadcaster) send(viewers.get(msg.to), msg);
        else if (ws.role === 'viewer') send(broadcaster, { ...msg, from: ws.id });
        break;
    }
  });

  ws.on('close', () => {
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

server.listen(PORT, '0.0.0.0', () => {
  const scheme = useTls ? 'https' : 'http';
  const lan = Object.values(os.networkInterfaces()).flat()
    .filter((i) => i && i.family === 'IPv4' && !i.internal).map((i) => i.address);
  console.log(`Glasses camera relay (${scheme})`);
  console.log(`  Viewer     : ${scheme}://localhost:${PORT}/`);
  for (const ip of lan) console.log(`  Viewer LAN : ${scheme}://${ip}:${PORT}/`);
  console.log(`  Broadcast  : ${scheme}://localhost:${PORT}/broadcast   (open on the glasses)`);
  if (!useTls) console.log('  (HTTP mode: glasses must reach this as localhost via `adb reverse`, or run `npm run gen-cert` for HTTPS over Wi-Fi)');
});
