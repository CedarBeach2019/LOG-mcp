# web/

The chat UI — a single-file dark-theme SPA (`index.html`, ~1460 lines).

## Features

- **Dark theme** with CSS custom properties (no build step)
- **Passphrase login** — JWT stored in localStorage
- **Streaming responses** — tokens appear in real-time with blinking cursor
- **Draft mode** — 🎯 toggle or `/draft` prefix triggers 3-profile comparison with click-to-rank cards
- **Session history** — 📋 modal to browse and resume past conversations
- **New chat** — 🗑️ button clears and starts fresh
- **Feedback** — 👍👎 on every response with optional critique
- **Route badges** — ⚡ CACHED, 🔵 LOCAL, route action displayed per message
- **Settings panel** — Privacy toggle, custom profiles CRUD, preferences, local model load/unload, cache stats
- **Responsive** — works on mobile (34px breakpoint)

## Architecture

All HTML, CSS, and JavaScript are inline in `index.html`. No build step, no framework, no external dependencies. Uses the Fetch API for all communication.

### Key UI Components

```
┌──────────────────────────────┐
│ Header: LOG-mcp  🟢LOCAL  📋  │
├──────────────────────────────┤
│                              │
│  Chat messages area          │
│  (user/assistant/drafts)     │
│                              │
├──────────────────────────────┤
│ Input: [textarea] 🎯 🗑️ ➤  │
└──────────────────────────────┘
  └── Settings (slide panel)
  └── History (modal overlay)
```

### JavaScript Structure

- IIFE wraps all code (no globals except event listeners)
- `chatHistory[]` — message array sent to API
- `currentSessionId` — tracks active session for persistence
- `sendNormal()` — streaming fetch to `/v1/chat/completions`
- `sendDraft()` — fetch to `/v1/drafts`, renders ranking cards
- `restoreLastSession()` — loads messages from last session on login
- `pollLocalStatus()` — 30s interval checks local model status

### API Calls

All use `fetch()` with `Authorization: Bearer <token>`. Streaming uses `ReadableStream` reader for SSE parsing.

## Customization

- Colors: CSS custom properties (`--bg`, `--surface`, `--accent`, etc.) at the top of `<style>`
- Fonts: `--font` variable (defaults to system stack)
- Passphrase: set via `LOG_PASSPHRASE` env var
