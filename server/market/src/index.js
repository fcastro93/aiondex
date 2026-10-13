// AionDex shared market samples (Crafting).
//
// AionDex sends the market samples a user processed, with their yes: only the market's public screens (list and
// offers: item names, prices, offers). It gets back the ones other users took on the same market (server + side).
// Nothing personal is kept: no character, account or IP; a random install id, of which only a hash is stored, lets
// an install replace or delete its own samples and limits how many it can send.
//
// POST   /v1/samples                     {market, at, profession, tax, items}      add or replace one of your samples
// GET    /v1/samples?market=M&after=ID   the others' samples of the last days, oldest change first (paged)
// DELETE /v1/samples?at=T                remove one of your samples
// The install id travels in the X-AionDex-Install header.

const MAX_BODY = 512 * 1024;
const KEEP_DAYS = 30;            // samples older than this are deleted (daily cron)
const SERVE_DAYS = 3;            // GET only returns samples taken in the last days
const PAGE = 200;
const PER_HOUR = 120;            // samples one install can send in an hour
const MARKET_RE = /^\d{3,5}-(asmodian|elyos)$/;
const INSTALL_RE = /^[0-9a-f]{32}$/;
const PROFESSION_RE = /^[A-Za-z]{3,24}$/;

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    try {
      if (url.pathname === "/v1/health") return json({ok: true});
      if (url.pathname !== "/v1/samples") return json({error: "not found"}, 404);
      if (request.method === "POST") return await put(request, env);
      if (request.method === "GET") return await list(request, env, url);
      if (request.method === "DELETE") return await remove(request, env, url);
      return json({error: "method not allowed"}, 405);
    } catch (e) {
      return json({error: "server error"}, 500);
    }
  },

  async scheduled(_event, env) {
    await env.DB.prepare("DELETE FROM samples WHERE received < ?").bind(now() - KEEP_DAYS * 86400).run();
  },
};

async function put(request, env) {
  const author = await authorOf(request);
  if (!author) return json({error: "missing install id"}, 400);
  const text = await request.text();
  if (text.length > MAX_BODY) return json({error: "too large"}, 413);
  let body;
  try {
    body = JSON.parse(text);
  } catch {
    return json({error: "bad json"}, 400);
  }
  const t = now();
  const {market, at, profession} = body || {};
  if (typeof market !== "string" || !MARKET_RE.test(market)) return json({error: "bad market"}, 400);
  if (typeof at !== "number" || at < t - 7 * 86400 || at > t + 600) return json({error: "bad time"}, 400);
  if (typeof profession !== "string" || !PROFESSION_RE.test(profession)) return json({error: "bad profession"}, 400);
  const tax = typeof body.tax === "number" && body.tax >= 0 && body.tax <= 0.5 ? body.tax : null;
  const items = cleanItems(body.items);
  if (!items) return json({error: "no items"}, 400);

  const sent = await env.DB.prepare("SELECT COUNT(*) AS n FROM samples WHERE author = ? AND received > ?")
    .bind(author, t - 3600).first();
  if (sent && sent.n >= PER_HOUR) return json({error: "too many samples, try later"}, 429);

  // replaced as a new row, so the others' next GET (after their last id) brings the new version
  const key = (await sha256(`${author}:${at}`)).slice(0, 16);
  await env.DB.batch([
    env.DB.prepare("DELETE FROM samples WHERE author = ? AND at = ?").bind(author, at),
    env.DB.prepare("INSERT INTO samples (market, author, at, key, profession, tax, received, version, data) " +
                   "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)")
      .bind(market, author, at, key, profession, tax, t, version(request), JSON.stringify(items)),
  ]);
  return json({ok: true, key, items: Object.keys(items).length});
}

async function list(request, env, url) {
  const market = url.searchParams.get("market") || "";
  if (!MARKET_RE.test(market)) return json({error: "bad market"}, 400);
  const after = Math.max(0, parseInt(url.searchParams.get("after") || "0", 10) || 0);
  const author = (await authorOf(request)) || "";
  const rows = await env.DB.prepare(
    "SELECT id, key, at, profession, tax, data FROM samples " +
    "WHERE market = ? AND id > ? AND at > ? AND author != ? ORDER BY id LIMIT ?")
    .bind(market, after, now() - SERVE_DAYS * 86400, author, PAGE + 1).all();
  const found = rows.results || [];
  const samples = found.slice(0, PAGE).map(r => r.data === null
    ? {id: r.id, key: r.key, deleted: true}
    : {id: r.id, key: r.key, at: r.at, profession: r.profession, tax: r.tax, items: JSON.parse(r.data)});
  const last = samples.length ? samples[samples.length - 1].id : after;
  return json({samples, after: last, more: found.length > PAGE});
}

async function remove(request, env, url) {
  const author = await authorOf(request);
  if (!author) return json({error: "missing install id"}, 400);
  const at = Number(url.searchParams.get("at"));
  const row = await env.DB.prepare("SELECT market, key FROM samples WHERE author = ? AND at = ?")
    .bind(author, at).first();
  if (!row) return json({ok: true, gone: false});
  // a row without data tells the others to drop their copy
  await env.DB.batch([
    env.DB.prepare("DELETE FROM samples WHERE author = ? AND at = ?").bind(author, at),
    env.DB.prepare("INSERT INTO samples (market, author, at, key, profession, tax, received, version, data) " +
                   "VALUES (?, ?, ?, ?, NULL, NULL, ?, ?, NULL)")
      .bind(row.market, author, at, row.key, now(), version(request)),
  ]);
  return json({ok: true, gone: true});
}

// --- checks ---------------------------------------------------------------------------------------------------
function cleanItems(items) {
  // only the fields AionDex keeps for a market item; what does not look like a market reading is dropped
  if (!items || typeof items !== "object" || Array.isArray(items)) return null;
  const names = Object.keys(items);
  if (names.length > 400) return null;
  const out = {};
  for (const name of names) {
    if (name.length < 2 || name.length > 100 || /[\u0000-\u001f<>{}]/.test(name)) continue;
    const it = items[name];
    if (!it || typeof it !== "object" || Array.isArray(it)) continue;
    const clean = {};
    if (isInt(it.lowest, 1, 1e11)) clean.lowest = it.lowest;
    if (isInt(it.listings, 0, 1e6)) clean.listings = it.listings;
    if (Array.isArray(it.offers)) {
      const offers = it.offers.slice(0, 100).filter(o => Array.isArray(o) && o.length === 4 &&
        isInt(o[0], 1, 1e11) && isInt(o[1], 1, 1e13) && isInt(o[2], 1, 1e6) &&
        (o[3] === null || (typeof o[3] === "number" && o[3] >= 0 && o[3] <= 2000)) &&
        Math.abs(o[1] - o[0] * o[2]) <= o[0] * 0.05);         // a total that is a whole number of units
      if (offers.length) clean.offers = offers;
    }
    if (clean.lowest !== undefined || clean.offers) out[name] = clean;
  }
  return Object.keys(out).length ? out : null;
}

function isInt(v, lo, hi) {
  return typeof v === "number" && Number.isInteger(v) && v >= lo && v <= hi;
}

async function authorOf(request) {
  const id = (request.headers.get("X-AionDex-Install") || "").toLowerCase();
  return INSTALL_RE.test(id) ? (await sha256(`aiondex:${id}`)).slice(0, 32) : null;
}

function version(request) {
  const m = /AionDex\/([\w.]+)/.exec(request.headers.get("User-Agent") || "");
  return m ? m[1] : null;
}

async function sha256(text) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map(b => b.toString(16).padStart(2, "0")).join("");
}

function now() {
  return Math.floor(Date.now() / 1000);
}

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {status, headers: {"Content-Type": "application/json; charset=utf-8"}});
}
