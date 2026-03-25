# cloudflare/

Cloudflare deployment (deprecated). The PII proxy that ran as a Cloudflare Worker before local dehydration was implemented.

## Structure

```
cloudflare/
├── worker/         # Cloudflare Worker (JavaScript)
│   ├── src/        # Worker source code
│   └── .wrangler/  # Wrangler build cache
└── pages/          # Cloudflare Pages (static frontend, deprecated)
```

## Status

**Deprecated.** The Worker intercepted requests to strip PII before forwarding to the AI provider. This is now handled locally by `vault/core.py`'s Dehydrator, which is:

- Faster (no network hop to Cloudflare)
- More private (PII never leaves the machine)
- More flexible (entity tokens maintain context across requests)

The Worker code remains for reference but is not actively maintained.
