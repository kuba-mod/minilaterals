// API for the "vote for the next grouping" feature on the hub page. Every
// request that doesn't hit one of the routes below falls through to the
// static assets binding (the default Worker+Assets behaviour: asset requests
// are served directly without even reaching this fetch handler, so this file
// only ever sees the two routes it explicitly checks for, plus whatever
// unmatched path needs the ASSETS fallback for a 404).
//
// Data lives in the VOTES KV namespace as plain counters/markers:
//   votes:{slug}           -> integer vote count, stored as a string
//   voter:{slug}:{ip}      -> ISO timestamp; presence means this IP's vote for
//                             this slug is already counted, so a repeat visit
//                             (or a re-click after a page reload) can't
//                             inflate the count — see clientIp() below.
//                             Expires on its own, see VOTER_MARKER_TTL_SECONDS
//   notify:{slug}:{email}  -> ISO timestamp of signup (also dedupes re-signups)
//
// KV has no atomic increment, so a vote count is a read-then-write and can
// still under-count if two *different* IPs' first votes land in the same
// instant. Acceptable for a low-traffic "gauge interest" signal, not a real
// ballot.
//
// IP dedup is a soft defense, not a hard guarantee: CF-Connecting-IP reflects
// the real TCP peer as seen by Cloudflare's edge, which a plain HTTP client
// can't forge — but Cloudflare has a documented cross-zone quirk where a
// *Worker* relaying a request into another zone can influence what
// CF-Connecting-IP that zone sees. So this stops casual abuse (a curl loop,
// repeated clicking) cheaply, but doesn't stop someone deliberately routing
// through their own Worker. Good enough for this feature's stakes; Cloudflare
// Turnstile would be the next step up if that ever changes.
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

// How long a voter:{slug}:{ip} marker lives. An IP address is personal data, so
// the marker carries a retention limit rather than sitting in KV indefinitely:
// KV expires it on its own, with no cleanup job to forget to run. The privacy
// note on the hub page states this window, so the two must move together.
//
// The tradeoff is deliberate: once a marker expires that IP can vote for the
// same grouping again. Six months is well past the point where a second vote
// from the same household would tell us anything new, and this was never a
// ballot — it's an interest gauge with `vote_report.py --reset` behind it.
// The votes:{slug} counter itself holds no personal data and does not expire.
const VOTER_MARKER_TTL_SECONDS = 180 * 24 * 60 * 60; // 180 days

function keyPrefix(request) {
  const hostname = new URL(request.url).hostname;
  return hostname === PRODUCTION_HOSTNAME ? "" : "preview:";
}

// Absent under `wrangler dev --local` (no real Cloudflare edge in front of
// it), where every local vote collides on "unknown" and dedupes after the
// first — expected there, not a bug; see the module comment above.
function clientIp(request) {
  return request.headers.get("CF-Connecting-IP") || "unknown";
}

// Per-IP rate limit on the write routes. IP dedup above stops a single IP
// inflating one grouping's *count*, but it still writes a voter marker per
// (slug, IP) and does nothing for /api/notify, so a script cycling slugs or
// emails could still burn the shared VOTES namespace's daily write budget and
// break the feature for everyone. This caps that. Returns true when the caller
// is over budget. No-ops (never limits) when the RATE_LIMITER binding isn't
// configured — e.g. `wrangler dev --local` — and fails open on limiter errors,
// since the per-put 503 guard below is the backstop.
async function rateLimited(request, env) {
  if (!env.RATE_LIMITER) return false;
  try {
    const { success } = await env.RATE_LIMITER.limit({ key: clientIp(request) });
    return !success;
  } catch {
    return false;
  }
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
  if (await rateLimited(request, env)) {
    return json({ error: "rate limited" }, 429);
  }

  const body = await readBody(request);
  const slug = body && body.slug;
  if (typeof slug !== "string" || !VALID_SLUGS.has(slug)) {
    return json({ error: "unknown grouping" }, 400);
  }

  const prefix = keyPrefix(request);
  const voterKey = `${prefix}voter:${slug}:${clientIp(request)}`;

  try {
    if (await env.VOTES.get(voterKey)) {
      // Same IP has already voted for this slug — don't count it again, but
      // still tell the caller it succeeded. Distinguishing "already voted"
      // client-side would just reveal the dedup mechanism (and misfire for
      // legitimate cases like two people sharing a home/office IP).
      return json({ ok: true });
    }
    await env.VOTES.put(voterKey, new Date().toISOString(), {
      expirationTtl: VOTER_MARKER_TTL_SECONDS,
    });

    const countKey = `${prefix}votes:${slug}`;
    const next = parseInt((await env.VOTES.get(countKey)) || "0", 10) + 1;
    await env.VOTES.put(countKey, String(next));

    return json({ ok: true });
  } catch {
    // KV rejected (e.g. daily write quota exhausted) — degrade to a clean 503
    // instead of throwing an unhandled 500 out of the Worker.
    return json({ error: "unavailable" }, 503);
  }
}

async function handleNotify(request, env) {
  if (await rateLimited(request, env)) {
    return json({ error: "rate limited" }, 429);
  }

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

  try {
    await env.VOTES.put(`${keyPrefix(request)}notify:${slug}:${email}`, new Date().toISOString());
    return json({ ok: true });
  } catch {
    return json({ error: "unavailable" }, 503);
  }
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
