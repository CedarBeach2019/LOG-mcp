/**
 * LOG-mcp Vault — Cloudflare Worker (D1-enhanced)
 * Privacy-first AI proxy: strips PII before forwarding to AI providers,
 * rehydrates responses so users never notice.
 */

// ─── Name Detection ─────────────────────────────────────────────────────

const COMMON_NON_NAMES = new Set([
  'Email','Send','Contact','Call','The','This','That','There',
  'Hello','Please','Thank','Hi','Hey','To','From','Subject',
  'Re','FW','Fwd','Attn','Attention','Dear','Regards','Sincerely',
  'Best','Kind','Yours','Cordially','Respectfully','Also','Just',
  'Then','Will','Would','Could','Should','About','After','Before',
  'With','When','What','Where','Which','Have','Here','Some',
  'Other','More','Only','Over','Into','Very','Much','Many',
  'Such','Each','Every','Both','Few','Most','Than','Them',
  'These','Those','Being','Made','Does','Did','How','Our',
  'Your','Their','His','Her','My','Its','We','They','You',
  'Not','But','And','Or','Nor','For','Yet','So',
  'Account','Bank','Card','Case','Chapter','Company','Conference',
  'Country','Department','Division','Document','Employee','Employer',
  'Employment','Group','Insurance','Message','Note','Number',
  'Office','Order','Page','Patient','Payment','Phone','Project',
  'Question','Reference','Report','Request','Response','Section',
  'Service','State','System','Team','Total','Type','Unit',
  'Work','World','Year','Monday','Tuesday','Wednesday','Thursday',
  'Friday','Saturday','Sunday','January','February','March','April',
  'May','June','July','August','September','October','November','December',
  'New','Old','First','Last','Next','Previous','Current','Final',
  'Good','Great','Small','Large','Long','Short','High','Low',
  'Open','Close','Start','End','Top','Bottom','Left','Right',
  'Google','Apple','Microsoft','Amazon','OpenAI','Cloudflare','Stripe',
  'GitHub','LinkedIn','Twitter','Facebook','Meta','Netlify','Vercel',
]);

const NAME_REGEX = /\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\b/g;

// ─── PII Patterns ───────────────────────────────────────────────────────

const PII_PATTERNS = [
  { type: 'EMAIL', regex: /[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/g },
  { type: 'PHONE', regex: /(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}/g },
  { type: 'SSN', regex: /\b\d{3}-\d{2}-\d{4}\b/g },
  { type: 'CC', regex: /\b(?:4\d{12}(?:\d{3})?|5[1-5]\d{14}|3[47]\d{13}|6(?:011|5\d{2})\d{12})\b/g },
  { type: 'API_KEY', regex: /\b(?:sk|pk|api[_-]?key|secret)[_-][a-zA-Z0-9]{20,}\b/gi },
  { type: 'ADDRESS', regex: /\d+\s+[\w\s]+(?:Street|St|Avenue|Ave|Boulevard|Blvd|Drive|Dr|Lane|Ln|Road|Rd|Court|Ct)\b(?:[.,]?\s*[\w\s]+)?(?:\d{5})?/gi },
  { type: 'IPV4', regex: /\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b/g },
];

// ─── Error Helpers ──────────────────────────────────────────────────────

function error(code, message, status = 400) {
  return { status, body: { error: code, message, ts: Date.now() } };
}

// ─── D1 Init ────────────────────────────────────────────────────────────

async function ensureD1Schema(db) {
  const tables = await db.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('pii_entities','sessions','request_log')").all();
  const existing = new Set(tables.results?.map(r => r.name) || []);
  if (existing.size < 3) {
    await db.batch([
      db.prepare(`CREATE TABLE IF NOT EXISTS pii_entities (
        id TEXT PRIMARY KEY, entity_type TEXT NOT NULL, real_value TEXT NOT NULL,
        placeholder TEXT NOT NULL, session_id TEXT, created_at TEXT NOT NULL, last_used_at TEXT
      )`),
      db.prepare(`CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY, provider TEXT, model TEXT,
        created_at TEXT NOT NULL, message_count INTEGER DEFAULT 0
      )`),
      db.prepare(`CREATE TABLE IF NOT EXISTS request_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
        session_id TEXT, provider TEXT, status INTEGER,
        input_tokens INTEGER, output_tokens INTEGER, error TEXT
      )`),
      db.prepare('CREATE INDEX IF NOT EXISTS idx_pii_session ON pii_entities(session_id)'),
      db.prepare('CREATE INDEX IF NOT EXISTS idx_pii_type ON pii_entities(entity_type)'),
      db.prepare('CREATE INDEX IF NOT EXISTS idx_request_log_ts ON request_log(timestamp)'),
    ]);
  }
}

// ─── PII Detection & Dehydration ───────────────────────────────────────

function detectNames(text) {
  const names = [];
  const seen = new Set();
  NAME_REGEX.lastIndex = 0;
  let m;
  while ((m = NAME_REGEX.exec(text)) !== null) {
    const candidate = m[1];
    const words = candidate.split(/\s+/);
    if (words.every(w => !COMMON_NON_NAMES.has(w)) && !seen.has(candidate)) {
      seen.add(candidate);
      names.push(candidate);
    }
  }
  return names;
}

function dehydrate(text, db, sessionId) {
  const mappings = {};
  let result = text;
  const counters = {};
  const now = new Date().toISOString();
  const d1Binds = [];

  // Detect names first (before other patterns consume parts)
  const names = detectNames(text);
  for (const name of names) {
    const type = 'NAME';
    counters[type] = (counters[type] || 0) + 1;
    const placeholder = `${type}_${counters[type]}`;
    const id = crypto.randomUUID();
    mappings[placeholder] = name;
    result = result.split(name).join(placeholder);
    d1Binds.push([id, type, name, placeholder, sessionId, now, now]);
  }

  // Standard PII patterns
  for (const { type, regex } of PII_PATTERNS) {
    regex.lastIndex = 0;
    const matches = text.match(regex);
    if (!matches) continue;
    const unique = [...new Set(matches)];
    for (const match of unique) {
      counters[type] = (counters[type] || 0) + 1;
      const placeholder = `${type}_${counters[type]}`;
      const id = crypto.randomUUID();
      mappings[placeholder] = match;
      result = result.split(match).join(placeholder);
      d1Binds.push([id, type, match, placeholder, sessionId, now, now]);
    }
  }

  return { dehydrated: result, mappings, d1Binds };
}

async function storeMappings(db, d1Binds) {
  if (!d1Binds.length) return;
  const stmt = db.prepare(
    'INSERT OR REPLACE INTO pii_entities (id, entity_type, real_value, placeholder, session_id, created_at, last_used_at) VALUES (?,?,?,?,?,?,?)'
  );
  await db.batch(d1Binds.map(b => stmt.bind(...b)));
}

function rehydrate(text, mappings) {
  let result = text;
  for (const [placeholder, original] of Object.entries(mappings)) {
    result = result.split(placeholder).join(original);
  }
  return result;
}

// ─── Proxy Handler ──────────────────────────────────────────────────────

async function handleProxy(request, env, corsHeaders) {
  if (!env.LOG_VAULT) {
    const e = error('NO_DATABASE', 'D1 database binding (DB) not configured', 500);
    return json(e.body, corsHeaders, e.status);
  }
  if (!env.API_KEY) {
    const e = error('NO_API_KEY', 'API_KEY environment variable not configured', 500);
    return json(e.body, corsHeaders, e.status);
  }

  let body;
  try { body = await request.json(); }
  catch { const e = error('INVALID_JSON', 'Request body is not valid JSON'); return json(e.body, corsHeaders, e.status); }

  if (!body.messages || !Array.isArray(body.messages)) {
    const e = error('INVALID_MESSAGES', 'Missing or invalid "messages" array');
    return json(e.body, corsHeaders, e.status);
  }

  await ensureD1Schema(env.LOG_VAULT);

  const sessionId = crypto.randomUUID();
  const endpoint = env.PROVIDER_ENDPOINT || 'https://api.openai.com/v1/chat/completions';
  const provider = new URL(endpoint).hostname;
  const model = body.model || 'unknown';

  // Create session in D1
  await env.LOG_VAULT.prepare('INSERT INTO sessions (id, provider, model, created_at, message_count) VALUES (?,?,?,?,?)')
    .bind(sessionId, provider, model, new Date().toISOString(), body.messages.length).run();

  // Dehydrate all messages
  let allMappings = {};
  let allD1Binds = [];
  for (const msg of body.messages) {
    if (typeof msg.content === 'string') {
      const { dehydrated, mappings, d1Binds } = dehydrate(msg.content, env.LOG_VAULT, sessionId);
      msg.content = dehydrated;
      Object.assign(allMappings, mappings);
      allD1Binds.push(...d1Binds);
    }
  }

  // Store in D1 (primary) and KV (fast lookup for rehydration)
  await storeMappings(env.LOG_VAULT, allD1Binds);
  if (env.PII_MAP) {
    await env.PII_MAP.put(sessionId, JSON.stringify(allMappings), { expirationTtl: 3600 });
  }

  // Also store latest for /rehydrate endpoint
  if (env.PII_MAP) {
    await env.PII_MAP.put('__latest', JSON.stringify(allMappings), { expirationTtl: 3600 });
  }

  const stream = body.stream === true;

  try {
    const resp = await fetch(endpoint, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${env.API_KEY}`,
      },
      body: JSON.stringify(body),
    });

    // Log request
    const inputTokens = parseInt(resp.headers.get('x-request-id') || '0') || null; // placeholder
    const respStatus = resp.status;

    if (!resp.ok) {
      const errText = await resp.text();
      await env.LOG_VAULT.prepare('INSERT INTO request_log (timestamp, session_id, provider, status, error) VALUES (?,?,?,?,?)')
        .bind(new Date().toISOString(), sessionId, provider, respStatus, errText.slice(0, 500)).run();
      const e = error('UPSTREAM_ERROR', `Upstream responded with ${respStatus}`, respStatus);
      return json({ ...e.body, detail: errText.slice(0, 1000) }, corsHeaders, e.status);
    }

    // Try to extract token usage from response
    let inputTokensUsed = null, outputTokensUsed = null;

    if (stream) {
      const response = handleStream(resp, allMappings, sessionId, env, corsHeaders);
      // Log async (fire and forget for streaming)
      env.LOG_VAULT.prepare('INSERT INTO request_log (timestamp, session_id, provider, status) VALUES (?,?,?,?)')
        .bind(new Date().toISOString(), sessionId, provider, respStatus).run();
      return response;
    }

    // Non-streaming
    const data = await resp.json();
    if (data.usage) {
      inputTokensUsed = data.usage.prompt_tokens || null;
      outputTokensUsed = data.usage.completion_tokens || null;
    }
    if (data.choices?.[0]?.message?.content) {
      data.choices[0].message.content = rehydrate(data.choices[0].message.content, allMappings);
    }

    await env.LOG_VAULT.prepare(
      'INSERT INTO request_log (timestamp, session_id, provider, status, input_tokens, output_tokens) VALUES (?,?,?,?,?,?)'
    ).bind(new Date().toISOString(), sessionId, provider, respStatus, inputTokensUsed, outputTokensUsed).run();

    return json(data, corsHeaders);
  } catch (err) {
    await env.LOG_VAULT.prepare('INSERT INTO request_log (timestamp, session_id, provider, status, error) VALUES (?,?,?,?,?)')
      .bind(new Date().toISOString(), sessionId, provider, 0, err.message.slice(0, 500)).run();
    const e = error('PROVIDER_ERROR', `Provider request failed: ${err.message}`, 502);
    return json(e.body, corsHeaders, e.status);
  }
}

async function handleStream(upstream, mappings, sessionId, env, corsHeaders) {
  const { readable, writable } = new TransformStream();
  const writer = writable.getWriter();
  const decoder = new TextDecoder();
  const encoder = new TextEncoder();

  (async () => {
    try {
      const reader = upstream.body.getReader();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed || !trimmed.startsWith('data: ')) {
            await writer.write(encoder.encode(line + '\n'));
            continue;
          }
          const payload = trimmed.slice(6);
          if (payload === '[DONE]') {
            await writer.write(encoder.encode('data: [DONE]\n\n'));
            continue;
          }
          try {
            const parsed = JSON.parse(payload);
            const delta = parsed.choices?.[0]?.delta?.content;
            if (delta) {
              parsed.choices[0].delta.content = rehydrate(delta, mappings);
            }
            await writer.write(encoder.encode(`data: ${JSON.stringify(parsed)}\n\n`));
          } catch {
            await writer.write(encoder.encode(line + '\n\n'));
          }
        }
      }
    } catch (err) {
      // Swallow stream errors
    } finally {
      await writer.close();
      if (env.PII_MAP) {
        await env.PII_MAP.put(sessionId, JSON.stringify(mappings), { expirationTtl: 3600 });
      }
    }
  })();

  return new Response(readable, {
    headers: {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      'Connection': 'keep-alive',
      ...corsHeaders,
    },
  });
}

// ─── Stats Handler ──────────────────────────────────────────────────────

async function handleStats(env, corsHeaders) {
  if (!env.LOG_VAULT) {
    const e = error('NO_DATABASE', 'D1 database binding not configured', 500);
    return json(e.body, corsHeaders, e.status);
  }
  await ensureD1Schema(env.LOG_VAULT);

  const [entities, sessions, recent] = await Promise.all([
    env.LOG_VAULT.prepare('SELECT COUNT(*) as cnt FROM pii_entities').first(),
    env.LOG_VAULT.prepare('SELECT COUNT(*) as cnt FROM sessions').first(),
    env.LOG_VAULT.prepare('SELECT COUNT(*) as cnt FROM request_log WHERE timestamp > datetime(\'now\', \'-24 hours\')').first(),
  ]);

  const byType = await env.LOG_VAULT.prepare('SELECT entity_type, COUNT(*) as cnt FROM pii_entities GROUP BY entity_type').all();

  return json({
    pii_entities: entities?.cnt || 0,
    sessions: sessions?.cnt || 0,
    requests_24h: recent?.cnt || 0,
    by_type: Object.fromEntries((byType.results || []).map(r => [r.entity_type, r.cnt])),
    ts: Date.now(),
  }, corsHeaders);
}

// ─── Helpers ────────────────────────────────────────────────────────────

function json(data, extraHeaders = {}, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json', ...extraHeaders },
  });
}

// ─── Router ─────────────────────────────────────────────────────────────

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const corsHeaders = {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type, Authorization',
    };

    if (request.method === 'OPTIONS') return new Response(null, { headers: corsHeaders });

    // Health check
    if (url.pathname === '/' && request.method === 'GET') {
      return json({ status: 'ok', service: 'log-mcp-vault', version: '2.0.0-d1', ts: Date.now() }, corsHeaders);
    }

    // Stats
    if (url.pathname === '/stats' && request.method === 'GET') {
      return handleStats(env, corsHeaders);
    }

    // Test dehydrate
    if (url.pathname === '/dehydrate' && request.method === 'GET') {
      if (!env.LOG_VAULT) { const e = error('NO_DATABASE', 'D1 not configured', 500); return json(e.body, corsHeaders, e.status); }
      await ensureD1Schema(env.LOG_VAULT);
      const text = url.searchParams.get('text') || '';
      const sessionId = crypto.randomUUID();
      const { dehydrated, mappings, d1Binds } = dehydrate(text, env.LOG_VAULT, sessionId);
      await storeMappings(env.LOG_VAULT, d1Binds);
      return json({ original: text, dehydrated, mappings, id: sessionId }, corsHeaders);
    }

    // Test rehydrate
    if (url.pathname === '/rehydrate' && request.method === 'GET') {
      const text = url.searchParams.get('text') || '';
      const raw = await env.PII_MAP.get('__latest');
      const mappings = raw ? JSON.parse(raw) : {};
      return json({ dehydrated: text, rehydrated: rehydrate(text, mappings) }, corsHeaders);
    }

    // Main proxy
    if (url.pathname === '/v1/chat/completions' && request.method === 'POST') {
      return handleProxy(request, env, corsHeaders);
    }

    const e = error('NOT_FOUND', `Unknown route: ${request.method} ${url.pathname}`, 404);
    return json(e.body, corsHeaders, e.status);
  },
};
