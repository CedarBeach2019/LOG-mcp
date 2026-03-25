# Phase 5.4: Prompt Engineering Pipeline — Agent 4 Journey

## Phase 1: Codebase Analysis

### prompt_intelligence.py (ready to wire)
- `DEFAULT_SYSTEM_PROMPTS`: 7 templates (default, code, creative, teacher, analyst, concise, debug)
- `render_template()`: fills {name}, {date}, {time} variables
- `ContextWindow`: truncates messages to fit token budget (default 4096), estimates at 4 chars/token
- `FewShotInjector`: keyword-based matching, scores by overlap + feedback boost, max 3 examples, injects after system prompt

### routes.py — Integration Points
- System prompt built at "Build system message with preferences" section
- Uses `Dehydrator.build_preamble()` + user preferences as system message
- Route action comes from `classify()` → actions: cheap, escalation, local, draft, compare, manual_override
- Session messages available via `reallog.get_session_messages(session_id)`

### routing_script.py — Action → Template Mapping
- debug/fix/broken → escalation action → debug template
- write/implement/create code → escalation → code template
- factual/simple → cheap → concise template
- Default → default template

### Integration Strategy
1. Map route action to template name
2. After dehydration, before model call, replace the system message with template-based one
3. Apply ContextWindow truncation to session history
4. Inject few-shot examples from positively-rated interactions
5. Backward compatible: if no template matches, keep existing preamble behavior

## Implementation Plan
1. Create `gateway/prompt_pipeline.py` — orchestrates template selection + context window + few-shot
2. Wire into `chat_completions` after routing, before model calls
3. Add 3 API endpoints for template management
4. Add prompt templates DB table for customizations
5. Minimal UI changes (dropdown + indicators)
6. Tests for pipeline
