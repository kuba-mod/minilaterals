// API for the "vote for the next grouping" feature on the hub page. Every
// request that doesn't hit one of the routes below falls through to the
// static assets binding (the default Worker+Assets behaviour: asset requests
// are served directly without even reaching this fetch handler, so this file
// only ever sees the two routes it explicitly checks for, plus whatever
// unmatched path needs the ASSETS fallback for a 404).
//
// Data lives in the VOTES KV namespace as plain counters/markers:
//   votes:{slug}          -> integer vote count, stored as a string
//   notify:{slug}:{email} -> ISO timestamp of signup (also dedupes re-signups)
//
// KV has no atomic increment, so a vote count is a read-then-write and can
// under-count if two votes land in the same instant. Acceptable for a
// low-traffic "gauge interest" signal, not a real ballot.
//
// Deliberately no GET route to read the tallies back: that would be public
// and unauthenticated like everything else here. pipeline/vote_report.py
// reads votes:* directly from this KV namespace via the Cloudflare API,
// authenticated with the site owner's own CLOUDFLARE_API_TOKEN — the only
// way to make "only I can see the standings" actually true.
//
// wrangler.jsonc binds one KV namespace with no per-environment override, so
// branch previews (*.workers.dev, per the routes comment there) and
// production (minilaterals.com) would otherwise write into the exact same
// keys. Every write below goes through keyPrefix(request) instead, which
// buckets anything not on the production hostname under "preview:" — a
// completely different key prefix, so preview/testing votes never show up
// in a votes:* listing and can't inflate real counts. (Local `wrangler dev
// --local` doesn't need this: it emulates KV entirely on disk, isolated
// from the real namespace regardless of hostname.)

const PRODUCTION_HOSTNAME = "minilaterals.com"; // keep in sync with wrangler.jsonc's routes

function keyPrefix(request) {
  const hostname = new URL(request.url).hostname;
  return hostname === PRODUCTION_HOSTNAME ? "" : "preview:";
}

const VALID_SLUGS = new Set([
  "e3", "visegrad", "baltic_three", "aukus",
  "quad", "squad", "us_japan_rok", "coalition_of_the_willing", "e5",
  "jef", "lancaster_house", "b9", "nb8", "three_seas",
  "i2u2", "negev_forum", "imec", "india_france_uae",
  "aes", "pacific_alliance", "mekong_lancang", "china_pakistan_afghanistan", "csc",
  "mikta", "chip4",
]);

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json" },
  });
}

async function readBody(request) {
  try {
    return await request.json();
  } catch {
    return null;
  }
}

async function handleVote(request, env) {
  const body = await readBody(request);
  const slug = body && body.slug;
  if (typeof slug !== "string" || !VALID_SLUGS.has(slug)) {
    return json({ error: "unknown grouping" }, 400);
  }

  const key = `${keyPrefix(request)}votes:${slug}`;
  const next = parseInt((await env.VOTES.get(key)) || "0", 10) + 1;
  await env.VOTES.put(key, String(next));

  return json({ ok: true, count: next });
}

async function handleNotify(request, env) {
  const body = await readBody(request);
  const slug = body && body.slug;
  const emailRaw = body && body.email;
  if (typeof slug !== "string" || !VALID_SLUGS.has(slug)) {
    return json({ error: "unknown grouping" }, 400);
  }
  if (typeof emailRaw !== "string") {
    return json({ error: "missing email" }, 400);
  }
  const email = emailRaw.trim().toLowerCase();
  if (email.length > 254 || !EMAIL_RE.test(email)) {
    return json({ error: "invalid email" }, 400);
  }

  await env.VOTES.put(`${keyPrefix(request)}notify:${slug}:${email}`, new Date().toISOString());
  return json({ ok: true });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === "/api/vote" && request.method === "POST") {
      return handleVote(request, env);
    }
    if (url.pathname === "/api/notify" && request.method === "POST") {
      return handleNotify(request, env);
    }

    return env.ASSETS.fetch(request);
  },
};
