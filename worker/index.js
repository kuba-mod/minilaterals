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
// The endpoints are unauthenticated by design (a low-stakes interest signal),
// so the two write routes are guarded two ways against scripted abuse:
//   1. An optional per-IP rate-limit binding (RATE_LIMITER) blunts floods that
//      would otherwise exhaust the shared VOTES namespace's daily write budget
//      and break the feature for everyone. It's optional so `wrangler dev`
//      and any environment without the binding configured still work.
//   2. KV writes are wrapped so a rejected put (e.g. quota exceeded) returns a
//      clean 503 instead of throwing an unhandled 500 out of the Worker.

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

// Returns true when the caller has exceeded the per-IP write budget. No-ops
// (never limits) when the RATE_LIMITER binding isn't configured, so local dev
// and unbound environments keep working. Fails open on limiter errors — the
// KV-write guard below is the backstop if the limiter is unavailable.
async function rateLimited(request, env) {
  if (!env.RATE_LIMITER) return false;
  const ip = request.headers.get("CF-Connecting-IP") || "unknown";
  try {
    const { success } = await env.RATE_LIMITER.limit({ key: ip });
    return !success;
  } catch {
    return false;
  }
}

async function handleVotesList(env) {
  const counts = {};
  await Promise.all(
    Array.from(VALID_SLUGS, async (slug) => {
      counts[slug] = parseInt((await env.VOTES.get(`votes:${slug}`)) || "0", 10);
    }),
  );
  return json({ counts });
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

  const key = `votes:${slug}`;
  try {
    const next = parseInt((await env.VOTES.get(key)) || "0", 10) + 1;
    await env.VOTES.put(key, String(next));
    return json({ ok: true, count: next });
  } catch {
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
    await env.VOTES.put(`notify:${slug}:${email}`, new Date().toISOString());
    return json({ ok: true });
  } catch {
    return json({ error: "unavailable" }, 503);
  }
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === "/api/votes" && request.method === "GET") {
      return handleVotesList(env);
    }
    if (url.pathname === "/api/vote" && request.method === "POST") {
      return handleVote(request, env);
    }
    if (url.pathname === "/api/notify" && request.method === "POST") {
      return handleNotify(request, env);
    }

    return env.ASSETS.fetch(request);
  },
};
