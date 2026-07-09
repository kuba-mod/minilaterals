/**
 * Cloudflare Worker: static site + the sandbox assessment API.
 *
 * Static assets (docs/, built by scripts/cf-build.sh) are served by the assets
 * binding as before; this script only sees requests that match no asset. Two
 * API routes are handled here, everything else falls through to ASSETS (which
 * applies the configured 404 handling):
 *
 *   POST …/api/stance   rate a visitor statement against the Weimar goals with
 *                       the same Ollama model + prompt the pipeline uses.
 *                       Stateless: the statement is never logged or stored.
 *   POST …/api/unlock   store an email address (with explicit consent) in KV
 *                       and return a token that raises the daily try limit.
 *
 * Routes are matched by path suffix so the same code works in production
 * (minilaterals.com/weimar-triangle/api/…) and on workers.dev previews.
 *
 * Config (see wrangler.jsonc + CLAUDE.md):
 *   OLLAMA_API_KEY  secret; without it /api/stance returns 503.
 *   SANDBOX_KV      KV binding; without it the gate is disabled and
 *                   /api/unlock returns 503 (emails can't be stored).
 */

const FREE_TRIES_PER_DAY = 3;
const UNLOCKED_TRIES_PER_DAY = 25;
const MAX_STATEMENT_CHARS = 1500;
const COUNTRIES = { FR: "France", DE: "Germany", PL: "Poland" };

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname.endsWith("/api/stance")) {
      return request.method === "POST" ? handleStance(request, env, url) : methodNotAllowed();
    }
    if (url.pathname.endsWith("/api/unlock")) {
      return request.method === "POST" ? handleUnlock(request, env) : methodNotAllowed();
    }
    return env.ASSETS.fetch(request);
  },
};

function json(status, body) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
  });
}

function methodNotAllowed() {
  return json(405, { error: "method_not_allowed" });
}

/**
 * Python str.format() subset: named fields plus {{ }} escapes, resolved in a
 * single pass over the template so substituted values are never re-scanned
 * (matching Python, which formats the template once).
 */
function pyFormat(template, vars) {
  return template.replace(/\{\{|\}\}|\{(\w+)\}/g, (m, name) => {
    if (m === "{{") return "{";
    if (m === "}}") return "}";
    return name in vars ? vars[name] : m;
  });
}

/** Mirror of pipeline.enrich._parse_json: strip markdown fences, then parse. */
function parseModelJson(raw) {
  let s = raw.trim();
  if (s.startsWith("```")) {
    s = s.split("```")[1] || "";
    if (s.startsWith("json")) s = s.slice(4);
  }
  return JSON.parse(s.trim());
}

/** Mirror of pipeline.enrich._clean_stance: int clamped to [-2, 2], else null. */
function cleanStance(value) {
  const n = typeof value === "number" ? Math.trunc(value) : parseInt(value, 10);
  if (Number.isNaN(n)) return null;
  return Math.max(-2, Math.min(2, n));
}

/** Mirror of pipeline.enrich._clean_evidence: drop quotes copied from the goal. */
function cleanEvidence(evidence, goal) {
  const ev = (evidence || "").trim();
  if (!ev) return "";
  const g = (goal || "").trim();
  if (g && (g.includes(ev) || ev.includes(g))) return "";
  return ev;
}

/** Load sandbox/data.json from the deployed assets, relative to the API path. */
async function loadSandboxData(env, url, apiSuffix) {
  const prefix = url.pathname.slice(0, -apiSuffix.length);
  const assetUrl = new URL(url);
  assetUrl.pathname = `${prefix}/sandbox/data.json`;
  const res = await env.ASSETS.fetch(new Request(assetUrl));
  if (!res.ok) return null;
  return res.json();
}

/**
 * Daily per-IP try counter in KV — the soft email gate and the rate limiter in
 * one. Returns {allowed, remaining, unlockRequired} and increments on allow.
 * With no KV binding the gate is disabled (deploys stay green before the
 * namespace is created; the endpoint still needs the API key to do anything).
 */
async function checkAndCountTry(env, request, token) {
  if (!env.SANDBOX_KV) return { allowed: true, remaining: null, unlockRequired: false };
  const kv = env.SANDBOX_KV;

  let unlocked = false;
  if (token && /^[\w-]{1,64}$/.test(token)) {
    unlocked = (await kv.get(`token:${token}`)) !== null;
  }
  const limit = unlocked ? UNLOCKED_TRIES_PER_DAY : FREE_TRIES_PER_DAY;

  const ip = request.headers.get("cf-connecting-ip") || "unknown";
  const day = new Date().toISOString().slice(0, 10);
  const key = `try:${ip}:${day}`;
  const used = parseInt(await kv.get(key), 10) || 0;
  if (used >= limit) {
    return { allowed: false, remaining: 0, unlockRequired: !unlocked };
  }
  await kv.put(key, String(used + 1), { expirationTtl: 86400 });
  return { allowed: true, remaining: limit - used - 1, unlockRequired: false };
}

async function handleStance(request, env, url) {
  if (!env.OLLAMA_API_KEY) {
    return json(503, { error: "not_configured", detail: "assessment service is not configured" });
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return json(400, { error: "bad_json" });
  }

  const statement = typeof body.statement === "string" ? body.statement.trim() : "";
  const country = body.country;
  const topics = Array.isArray(body.topics) ? body.topics : [];

  if (!statement) return json(400, { error: "empty_statement" });
  if (statement.length > MAX_STATEMENT_CHARS) {
    return json(400, { error: "too_long", detail: `statement must be ≤ ${MAX_STATEMENT_CHARS} characters` });
  }
  if (!(country in COUNTRIES)) return json(400, { error: "bad_country" });

  const data = await loadSandboxData(env, url, "/api/stance");
  if (!data) return json(503, { error: "not_configured", detail: "sandbox data asset missing" });

  const validTopics = topics.filter((t) => t in data.goals);
  if (validTopics.length === 0) {
    // Mirrors the pipeline's relevance gate: statements touching no tracked
    // issue area are never stance-rated.
    return json(400, { error: "no_tracked_topic" });
  }

  const gate = await checkAndCountTry(env, request, body.token);
  if (!gate.allowed) {
    return json(gate.unlockRequired ? 402 : 429, {
      error: gate.unlockRequired ? "unlock_required" : "rate_limited",
      remaining: 0,
    });
  }

  // Same prompt construction as pipeline.enrich._backfill_stances.
  const goalsBlock = validTopics.map((t) => `- ${t}: ${data.goals[t]}`).join("\n");
  const prompt = pyFormat(data.prompts.template, {
    source: COUNTRIES[country],
    title: "Statement by the Ministry of Foreign Affairs",
    text: statement,
    goals_block: goalsBlock,
    stance_rubric: data.prompts.rubric,
    topics: validTopics.join(", "),
  });

  const host = (env.OLLAMA_HOST || "https://ollama.com").replace(/\/+$/, "");
  const model = env.OLLAMA_MODEL || "gemma4:latest";

  let ratings = null;
  for (let attempt = 0; attempt < 2 && ratings === null; attempt++) {
    let res;
    try {
      res = await fetch(`${host}/v1/chat/completions`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${env.OLLAMA_API_KEY}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          model,
          messages: [
            { role: "system", content: data.prompts.system },
            { role: "user", content: prompt },
          ],
          max_tokens: 800,
          temperature: 0,
          response_format: { type: "json_object" },
          think: false,
        }),
      });
    } catch {
      return json(502, { error: "judge_unreachable" });
    }
    if (!res.ok) return json(502, { error: "judge_error", status: res.status });
    const completion = await res.json();
    const content = completion?.choices?.[0]?.message?.content || "";
    try {
      ratings = parseModelJson(content);
    } catch {
      ratings = null; // retry once, like the pipeline
    }
  }
  if (ratings === null) return json(502, { error: "judge_bad_output" });

  const stances = {};
  for (const topic of validTopics) {
    const entry = ratings[topic];
    if (!entry || typeof entry !== "object") continue;
    const score = cleanStance(entry.stance);
    if (score === null) continue;
    stances[topic] = { score, evidence: cleanEvidence(entry.evidence, data.goals[topic]) };
  }
  if (Object.keys(stances).length === 0) return json(502, { error: "judge_no_ratings" });

  return json(200, { stances, model, remaining: gate.remaining });
}

async function handleUnlock(request, env) {
  if (!env.SANDBOX_KV) {
    return json(503, { error: "not_configured", detail: "email storage is not configured" });
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return json(400, { error: "bad_json" });
  }

  if (body.consent !== true) return json(400, { error: "consent_required" });
  const email = typeof body.email === "string" ? body.email.trim().toLowerCase() : "";
  if (!email || email.length > 254 || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    return json(400, { error: "bad_email" });
  }

  await env.SANDBOX_KV.put(
    `email:${email}`,
    JSON.stringify({ ts: new Date().toISOString(), country: request.cf?.country || null })
  );
  const token = crypto.randomUUID();
  await env.SANDBOX_KV.put(`token:${token}`, email);
  return json(200, { token });
}
