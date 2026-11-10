// Paste Link - device registry
// Each ESP32 posts its local IP here when it connects (and every few minutes after).
// Open the site to see every device and its current address.
//
// Routes
//   GET    /                         page listing all devices (auto-refreshes)
//   GET    /health                   for uptime / cron pings
//   POST   /api/report               ESP32 reports itself     (needs the device token)
//   GET    /api/devices              all devices as JSON
//   GET    /api/devices/:id          one device as JSON
//   GET    /api/devices/:id/ip       just the IP, plain text
//   DELETE /api/devices/:id          forget a device          (needs the device token)
//
// Environment
//   DEVICE_TOKEN   required. Same secret as DEVICE_TOKEN in esp.ino.
//   VIEW_KEY       optional. If set, viewing needs ?key=VIEW_KEY (or header x-view-key).
//   DATA_FILE      optional. Where devices are saved (default ./data/devices.json).
//   PORT           set by Render.

const http = require("http");
const fs = require("fs");
const path = require("path");

const PORT = process.env.PORT || 3000;
const DEVICE_TOKEN = process.env.DEVICE_TOKEN || "";
const VIEW_KEY = process.env.VIEW_KEY || "";
const DATA_FILE = process.env.DATA_FILE || path.join(__dirname, "data", "devices.json");
const ONLINE_MS = 12 * 60 * 1000;          // ESP reports every 5 min; 12 min of silence = offline
const MAX_BODY = 8 * 1024;
const MAX_DEVICES = 50;
const started = Date.now();

// ---------- storage ----------
// Render's free disk is wiped on every deploy/restart. That is fine: the ESP32s
// report again within a few minutes, so the list refills by itself.
let devices = {};
try {
  devices = JSON.parse(fs.readFileSync(DATA_FILE, "utf8")) || {};
} catch {
  devices = {};
}

let saveTimer = null;
function save() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    try {
      fs.mkdirSync(path.dirname(DATA_FILE), { recursive: true });
      fs.writeFileSync(DATA_FILE, JSON.stringify(devices, null, 2));
    } catch (e) {
      console.error("Could not save devices:", e.message);
    }
  }, 200);
}

function slug(name) {
  return String(name).toLowerCase().trim().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 60);
}

function view(d) {
  const age = Date.now() - Date.parse(d.lastSeen);
  return { ...d, online: age < ONLINE_MS, lastSeenAgoSec: Math.round(age / 1000) };
}

function list() {
  return Object.values(devices)
    .map(view)
    .sort((a, b) => Date.parse(b.lastSeen) - Date.parse(a.lastSeen));
}

// ---------- helpers ----------
function send(res, code, body, type) {
  const isText = typeof body === "string";
  res.writeHead(code, {
    "Content-Type": type || (isText ? "text/plain; charset=utf-8" : "application/json"),
    "Cache-Control": "no-store",
    "Access-Control-Allow-Origin": "*",
  });
  res.end(isText ? body : JSON.stringify(body));
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    req.on("data", (c) => {
      size += c.length;
      if (size > MAX_BODY) {
        reject(new Error("too large"));
        req.destroy();
        return;
      }
      chunks.push(c);
    });
    req.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    req.on("error", reject);
  });
}

function tokenOk(req) {
  if (!DEVICE_TOKEN) return false;
  const auth = req.headers["authorization"] || "";
  const given = auth.startsWith("Bearer ") ? auth.slice(7) : req.headers["x-device-token"] || "";
  const a = Buffer.from(given);
  const b = Buffer.from(DEVICE_TOKEN);
  return a.length === b.length && require("crypto").timingSafeEqual(a, b);
}

function viewOk(req, url) {
  if (!VIEW_KEY) return true;
  return url.searchParams.get("key") === VIEW_KEY || req.headers["x-view-key"] === VIEW_KEY;
}

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function ago(sec) {
  if (sec < 60) return sec + " s ago";
  if (sec < 3600) return Math.round(sec / 60) + " min ago";
  if (sec < 86400) return Math.round(sec / 3600) + " h ago";
  return Math.round(sec / 86400) + " days ago";
}

// ---------- page ----------
function page() {
  const rows = list();
  const cards = rows.length
    ? rows
        .map(
          (d) => `
    <div class="card">
      <div class="top">
        <span class="name">${esc(d.name)}</span>
        <span class="dot ${d.online ? "on" : "off"}">${d.online ? "online" : "offline"}</span>
      </div>
      <a class="ip" href="http://${esc(d.ip)}" target="_blank" rel="noopener">${esc(d.ip)}</a>
      <div class="meta">
        Wi-Fi ${esc(d.ssid || "?")}${d.rssi ? " (" + esc(d.rssi) + " dBm)" : ""}
        · Bluetooth ${d.ble ? "paired" : "not paired"}
        · seen ${esc(ago(d.lastSeenAgoSec))}
      </div>
      <div class="meta">ID ${esc(d.id)} · MAC ${esc(d.mac || "?")}</div>
    </div>`
        )
        .join("")
    : `<p class="empty">No devices yet. Power on an ESP32 and wait a few seconds.</p>`;

  return `<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="30">
<title>Paste Link Devices</title>
<style>
  :root{--bg:#fff;--fg:#111;--muted:#666;--line:#ddd;--on:#1e9e55;--off:#999}
  @media (prefers-color-scheme: dark){:root{--bg:#000;--fg:#fff;--muted:#999;--line:#333;--on:#2ecc71;--off:#666}}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;padding:20px 16px}
  .wrap{max-width:560px;margin:0 auto}
  h1{font-size:20px;margin:0 0 4px}
  .sub{color:var(--muted);font-size:13px;margin-bottom:18px}
  .card{border:1px solid var(--line);border-radius:12px;padding:14px;margin-bottom:12px}
  .top{display:flex;justify-content:space-between;align-items:center;gap:10px}
  .name{font-weight:600}
  .dot{font-size:12px}.dot.on{color:var(--on)}.dot.off{color:var(--off)}
  .ip{display:block;font-size:26px;font-weight:600;color:var(--fg);margin:8px 0;text-decoration:none;font-variant-numeric:tabular-nums}
  .meta{font-size:12px;color:var(--muted);margin-top:4px}
  .empty{color:var(--muted)}
</style></head>
<body><div class="wrap">
  <h1>Paste Link devices</h1>
  <div class="sub">Tap an address to open that ESP32 (your phone must be on the same Wi-Fi). Refreshes every 30 s.</div>
  ${cards}
</div></body></html>`;
}

// ---------- server ----------
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, "http://x");
  const p = url.pathname.replace(/\/+$/, "") || "/";

  try {
    if (req.method === "OPTIONS") {
      res.writeHead(204, {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET,POST,DELETE,OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type,Authorization,x-device-token,x-view-key",
      });
      return res.end();
    }

    if (p === "/health" && (req.method === "GET" || req.method === "HEAD")) {
      return send(res, 200, { status: "ok", uptimeSec: Math.round((Date.now() - started) / 1000), devices: Object.keys(devices).length });
    }

    if (p === "/api/report" && req.method === "POST") {
      if (!DEVICE_TOKEN) return send(res, 500, { error: "DEVICE_TOKEN is not set on the server" });
      if (!tokenOk(req)) return send(res, 401, { error: "bad token" });

      let b;
      try {
        b = JSON.parse(await readBody(req));
      } catch {
        return send(res, 400, { error: "send JSON" });
      }
      const name = String(b.name || "").trim().slice(0, 60);
      const ip = String(b.ip || "").trim();
      if (!name) return send(res, 400, { error: "name missing" });
      if (!/^\d{1,3}(\.\d{1,3}){3}$/.test(ip)) return send(res, 400, { error: "ip missing or invalid" });

      const id = slug(name);
      if (!id) return send(res, 400, { error: "name has no letters or digits" });
      if (!devices[id] && Object.keys(devices).length >= MAX_DEVICES) return send(res, 507, { error: "too many devices" });

      const old = devices[id];
      const now = new Date().toISOString();
      devices[id] = {
        id,
        name,
        ip,
        ssid: String(b.ssid || "").slice(0, 64),
        rssi: Number.isFinite(+b.rssi) ? +b.rssi : null,
        mac: String(b.mac || "").slice(0, 32),
        ble: !!b.ble,
        uptimeSec: Number.isFinite(+b.uptime) ? +b.uptime : null,
        firstSeen: old ? old.firstSeen : now,
        lastSeen: now,
        ipChangedAt: old && old.ip === ip ? old.ipChangedAt : now,
      };
      save();
      if (!old || old.ip !== ip) console.log(`${name}: ${old ? old.ip + " -> " : ""}${ip}`);
      return send(res, 200, { ok: true, id });
    }

    const m = p.match(/^\/api\/devices(?:\/([^/]+))?(\/ip)?$/);
    if (m) {
      const id = m[1] ? slug(decodeURIComponent(m[1])) : null;

      if (req.method === "DELETE" && id) {
        if (!tokenOk(req)) return send(res, 401, { error: "bad token" });
        if (!devices[id]) return send(res, 404, { error: "not found" });
        delete devices[id];
        save();
        return send(res, 200, { ok: true });
      }

      if (req.method === "GET") {
        if (!viewOk(req, url)) return send(res, 401, { error: "add ?key=..." });
        if (!id) return send(res, 200, list());
        if (!devices[id]) return send(res, 404, m[2] ? "not found" : { error: "not found" });
        return m[2] ? send(res, 200, devices[id].ip) : send(res, 200, view(devices[id]));
      }
    }

    if (p === "/" && req.method === "GET") {
      if (!viewOk(req, url)) return send(res, 401, "Add ?key=YOUR_VIEW_KEY to the address.");
      return send(res, 200, page(), "text/html; charset=utf-8");
    }

    return send(res, 404, { error: "not found" });
  } catch (e) {
    console.error(e);
    return send(res, 500, { error: "server error" });
  }
});

server.listen(PORT, () => {
  console.log(`Device registry on port ${PORT}`);
  if (!DEVICE_TOKEN) console.warn("WARNING: DEVICE_TOKEN is not set - ESP32 reports will be refused.");
});
