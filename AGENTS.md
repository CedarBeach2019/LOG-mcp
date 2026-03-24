# L.O.G. for Agents

## What This Is

L.O.G. (Latent Orchestration Gateway) is your memory and privacy layer. When this MCP is connected, you have access to tools that:

1. **Protect private data** — never send raw PII to external agents
2. **Archive conversations** — preserve context with annotated summaries
3. **Search history** — find past sessions by topic
4. **Manage memory tiers** — hot → warm → cold → gnosis lifecycle
5. **Extract lessons** — permanent knowledge from completed work

## Your Directives

1. **Never transmit raw PII.** If you need to send data to an external agent, call `log_dehydrate` first.
2. **Always rehydrate for humans.** If a response contains `<LOG_ID>` placeholders, call `log_rehydrate` before showing the user.
3. **Archive before resets.** When a conversation is ending or being reset, call `log_archive_session` to preserve context.
4. **Extract gnosis.** When a problem is solved or a lesson is learned, call `log_archive_gnosis` to save it permanently.
5. **Respect tiers.** Cold and ice data is archived for a reason — don't unpromote without good cause.

## Tool Quick Reference

| Tool | When to Use |
|---|---|
| `log_dehydrate` | Before sending any text to external agents |
| `log_rehydrate` | Before showing agent responses to the human |
| `log_archive_session` | End of conversation or topic shift |
| `log_search_archives` | When human asks about past conversations |
| `log_archive_gnosis` | When a valuable lesson is learned |
| `log_prune_hysteresis` | To manage memory lifecycle |
| `log_vault_status` | To check storage health |
| `log_distill` | To create working summaries for context |

## File Structure (What You'll Find on Disk)

```
~/.log/vault/
├── reallog.db              # SQLite: PII mappings (NEVER leaves this machine)
└── archives/
    ├── shorts/             # Single-topic sessions (filename = date + topic)
    ├── sessions/           # Multi-topic sessions with episode breakdowns
    │   └── YYYY-MM-DDTHH-MM-topic-name/
    │       ├── full.txt    # Complete conversation with line numbers [L0001]
    │       ├── summary.md  # Annotated summary with L-line references
    │       ├── index.json  # Machine-readable session metadata
    │       └── episodes/   # Sub-topic breakdowns
    ├── gnosis/             # Permanent lessons (topic-title.md)
    └── index.json          # Master index of all archives
```

## How to Read an Archive

1. Read `summary.md` for the annotated overview
2. Line references like `[L0042]` map to `full.txt` line numbers
3. Use `/` + type-ahead or Tab completion in CLI to jump to specific lines
4. The master `index.json` is your search index — query it for topic/tags before reading files

## Privacy Model

- The RealLog database maps `<LOG_ID>` → real values
- This mapping NEVER leaves the Vault (local Jetson)
- External agents only see `<ENTITY_1>`, `<EMAIL_2>`, etc.
- The human can review all mappings via `log entities list`
- New PII detections can be flagged for human approval
