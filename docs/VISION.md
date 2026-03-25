# VISION.md — The Personal AI Gateway

> *"Your data. Your hardware. Your intelligence. Nobody else's."*

*Last updated: 2026-03-24. This is a living document.*

---

## Identity

LOG-mcp is becoming something bigger than a PII stripper. It's becoming the **personal AI gateway** — a self-hosted intelligence layer that sits between you and every AI service you use. It protects your privacy, remembers what matters, and gradually replaces cloud APIs with local inference that gets better the more you use it.

**Working name:** "LOG-mcp" (Latent Orchestration Gateway) is technically accurate but cryptic. We should consider something more approachable — *Gatekeeper*, *Mynd*, *Loom*, or keep the acronym but lead with the tagline. TBD. The code can stay; the brand can evolve.

**Pitch:** *Fork a repo, deploy to Cloudflare, point it at your hardware — and get a private AI assistant that protects your data, remembers your context, and gets smarter without ever phoning home.*

---

## Principles

**Privacy isn't a feature — it's the architecture.** "Private" means PII never leaves your machine in plaintext. Cloud APIs receive dehydrated messages. Your vault is local SQLite, encrypted at rest. No telemetry, no phone-home, no exceptions.

**Self-improvement is local.** When the local model fails and a cloud API succeeds, the response (rehydrated, never raw) becomes training signal. Fine-tuning happens on-device. The system gets better specifically for *you*, not for some averaged user.

**You own everything.** Your data, your model weights, your conversation history, your system. Fork it, modify it, run it however you want. No SaaS lock-in, no license servers, no "we reserve the right."

---

## The User Journey

**Day 1:** Fork → `npx wrangler deploy` → install Docker on local machine → configure tunnel token → done. You have a private AI endpoint. It works immediately with regex PII stripping and cloud API fallback.

**Day 30:** Local LLM is handling 60-70% of requests. The system has learned your name, your projects, your preferences. Cloud API calls are for hard cases only.

**Day 365:** 90%+ local inference. Your assistant knows your patterns, your relationships, your work. Cloud APIs are a safety net you almost never touch.

**What the user never does:** Train a model. Configure neural architectures. Manage GPU memory. Think about MLOps. The system handles all of this.

---

## Architecture

```
┌─────────────────────────┐
│   Edge: Cloudflare      │  ← Public endpoint, dehydrates PII
│   Worker (Tunnel)       │  ← Routes to local hardware
└───────────┬─────────────┘
            │ encrypted tunnel
┌───────────▼─────────────┐
│   Local: Your Hardware  │  ← Vault (SQLite), LOG-mcp core, agent
│   (Jetson/PC/whatever)  │  ← Ollama for local inference
└───────────┬─────────────┘
            │
┌───────────▼─────────────┐
│   Model: Ollama         │  ← Fine-tuned models, trained locally
│   + Cloud API fallback  │  ← Claude/GPT for edge cases only
└─────────────────────────┘
```

**Data flow:** User → Worker (PII stripped) → Tunnel → Local vault → Local LLM tries first → Cloud API if needed → Response rehydrated → User.

**PII lifecycle:** Detected at the edge. Replaced with tokens. The mapping (real value → token) exists *only* in your local SQLite vault. Cloud APIs never see it. Tokens are session-scoped and expire.

---

## What Makes This Different

**Why not ChatGPT?** Your data lives on their servers. You can't audit it, can't delete it reliably, can't control training. You're the product.

**Why not just Ollama?** Ollama is a model runner, not a system. No PII protection, no memory management, no cloud fallback, no self-improvement loop. It's an engine, not a car.

**Why not just run a local LLM with a proxy?** That's closer — but you're still building the memory, the PII logic, the training pipeline, the deployment yourself. LOG-mcp packages all of it into something you can fork and run in an hour.

**What we get right:** The *gateway* abstraction. You don't think about which model handles which request. The system routes intelligently. Local first, cloud as fallback, and the gap shrinks over time.

---

## Success Metrics

- **Local inference rate:** What % of requests are handled without cloud APIs? (Track weekly.)
- **PII leakage events:** Should be zero. If one occurs, it's a critical bug.
- **Latency:** Local responses <500ms. Cloud fallback <2s.
- **Graduation:** The day a user can disconnect their cloud API key and not notice. That's the goal.

---

## Risks and Open Questions

**Security:** Tunnelling to a home machine is inherently risky. We need to harden the tunnel (mTLS, not just a token), limit blast radius if the local machine is compromised, and make sure the vault is useless without the encryption key.

**Onboarding:** "Fork, deploy, configure a tunnel" is still too much for most people. We need a one-click path — maybe a Terraform module, maybe a script that handles the whole thing.

**Hardware:** Jetson Orin is great but expensive ($500-1500). Can we make this work well on a $200 mini PC? What's the minimum viable hardware?

**Model improvement:** Fine-tuning locally is hard. How do we do it without the user noticing (background jobs)? How do we handle catastrophic forgetting? What's the training data pipeline actually look like?

**We don't know:** The right balance between privacy and capability. How much local context to keep. Whether people actually want this or if they'll just keep using ChatGPT despite the privacy cost. That's what shipping will teach us.
