/**
 * LOG-mcp Vault — Cloudflare Worker
 * Privacy-first AI proxy: strips PII before forwarding to AI providers,
 * rehydrates responses so users never notice.
 */

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
      return json({ status: 'ok', service: 'log-mcp-vault', ts: Date.now() }, corsHeaders);
    }

    // Test endpoints
    if (url.pathname === '/dehydrate' && request.method === 'GET') {
      const text = url.searchParams.get('text') || '';
      const { dehydrated, id } = dehydrate(text);
      await env.PII_MAP.put(id, JSON.stringify({ mappings: dehydrateMap }));
      return json({ original: text, dehydrated, id }, corsHeaders);
    }

    if (url.pathname === '/rehydrate' && request.method === 'GET') {
      const text = url.searchParams.get('text') || '';
      const raw = await env.PII_MAP.get('__latest');
      const mappings = raw ? JSON.parse(raw) : {};
      return json({ dehydrated: text, rehydrated: rehydrate(text, mappings) }, corsHeaders);
    }

    // Main proxy endpoint
    if (url.pathname === '/v1/chat/completions' && request.method === 'POST') {
      return handleProxy(request, env, corsHeaders);
    }

    return json({ error: 'Not found' }, corsHeaders, 404);
  },
};

// ─── PII Detection & Dehydration ───────────────────────────────────────

let dehydrateMap = {};

const PII_PATTERNS = [
  { type: 'EMAIL', regex: /[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/g },
  { type: 'PHONE', regex: /(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}/g },
  { type: 'SSN', regex: /\b\d{3}-\d{2}-\d{4}\b/g },
  { type: 'CC', regex: /\b(?:4\d{12}(?:\d{3})?|5[1-5]\d{14}|3[47]\d{13}|6(?:011|5\d{2})\d{12})\b/g },
  { type: 'API_KEY', regex: /\b(?:sk|pk|api[_-]?key|secret)[_-][a-zA-Z0-9]{20,}\b/gi },
  { type: 'ADDRESS', regex: /\d+\s+[\w\s]+(?:Street|St|Avenue|Ave|Boulevard|Blvd|Drive|Dr|Lane|Ln|Road|Rd|Court|Ct)\b(?:[.,]?\s*[\w\s]+)?(?:\d{5})?/gi },
  { type: 'IPV4', regex: /\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b/g },
];

function dehydrate(text) {
  dehydrateMap = {};
  let result = text;
  const counters = {};

  for (const { type, regex } of PII_PATTERNS) {
    const matches = text.match(regex);
    if (!matches) continue;

    // Deduplicate within type
    const unique = [...new Set(matches)];
    for (const match of unique) {
      counters[type] = (counters[type] || 0) + 1;
      const placeholder = `${type}_${counters[type]}`;
      dehydrateMap[placeholder] = match;
      result = result.split(match).join(placeholder);
    }
  }

  return { dehydrated: result, id: crypto.randomUUID() };
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
  if (!env.API_KEY) {
    return json({ error: 'API_KEY not configured' }, corsHeaders, 500);
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: 'Invalid JSON body' }, corsHeaders, 400);
  }

  if (!body.messages || !Array.isArray(body.messages)) {
    return json({ error: 'Missing or invalid "messages" field' }, corsHeaders, 400);
  }

  const endpoint = env.PROVIDER_ENDPOINT || 'https://api.openai.com/v1/chat/completions';

  // Dehydrate all message contents
  dehydrateMap = {};
  for (const msg of body.messages) {
    if (typeof msg.content === 'string') {
      const { dehydrated } = dehydrate(msg.content);
      msg.content = dehydrated;
    }
  }

  const sessionId = crypto.randomUUID();
  await env.PII_MAP.put(sessionId, JSON.stringify(dehydrateMap));

  // Detect streaming
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

    if (!resp.ok) {
      const errText = await resp.text();
      return json({ error: `Upstream ${resp.status}`, detail: errText }, corsHeaders, resp.status);
    }

    if (stream) {
      return handleStream(resp, dehydrateMap, sessionId, env, corsHeaders);
    }

    // Non-streaming
    const data = await resp.json();
    if (data.choices?.[0]?.message?.content) {
      data.choices[0].message.content = rehydrate(data.choices[0].message.content, dehydrateMap);
    }
    // Cleanup after short TTL (KV auto-expiry set below)
    await env.PII_MAP.put(sessionId, JSON.stringify(dehydrateMap), { expirationTtl: 3600 });

    return json(data, corsHeaders);
  } catch (err) {
    return json({ error: 'Provider request failed', detail: err.message }, corsHeaders, 502);
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
            // Pass through malformed SSE chunks
            await writer.write(encoder.encode(line + '\n\n'));
          }
        }
      }
    } catch (err) {
      // Swallow stream errors to avoid crashes
    } finally {
      await writer.close();
      await env.PII_MAP.put(sessionId, JSON.stringify(mappings), { expirationTtl: 3600 });
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

// ─── Helpers ────────────────────────────────────────────────────────────

function json(data, extraHeaders = {}, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json', ...extraHeaders },
  });
}
