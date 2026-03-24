"""
L.O.G. Archiver — Session archival with annotated summaries.

Structure:
  ~/.log/vault/archives/
    shorts/                          # Single-topic, short sessions
      2026-03-23T16-00-setting-up-gh-auth/
        full.txt
        summary.md
        index.json
    sessions/                        # Multi-topic, longer sessions
      2026-03-23T12-00-building-log-system/
        full.txt
        summary.md
        index.json
        episodes/                    # Sub-topics within a session
          01-privacy-layer-design.md
          02-cloudflare-ghost-portal.md
    gnosis/                          # Permanent lessons learned
      jetson-docker-workaround.md
      cloudflare-tunnel-setup.md
    index.json                       # Master index of all archives
"""

from __future__ import annotations
import json
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Tuple, Dict, Any


ARCHIVE_ROOT = Path("~/.log/vault/archives").expanduser()
SHORTS_DIR = ARCHIVE_ROOT / "shorts"
SESSIONS_DIR = ARCHIVE_ROOT / "sessions"
GNOSIS_DIR = ARCHIVE_ROOT / "gnosis"
MASTER_INDEX = ARCHIVE_ROOT / "index.json"


@dataclass
class ArchiveEntry:
    """A single archive entry in the master index."""
    session_id: str
    folder: str
    topic: str
    started_at: str
    ended_at: str
    summary: str
    tags: list[str] = field(default_factory=list)
    tier: str = "hot"
    is_short: bool = False
    message_count: int = 0
    token_estimate: int = 0
    episodes: list[str] = field(default_factory=list)


def _safe_filename(topic: str, max_len: int = 60) -> str:
    """Create a filesystem-safe, descriptive filename from a topic."""
    # Remove non-alphanumeric except spaces and hyphens
    safe = re.sub(r'[^\w\s-]', '', topic.lower())
    # Collapse whitespace
    safe = re.sub(r'\s+', '-', safe.strip())
    # Remove leading/trailing hyphens
    safe = safe.strip('-')
    # Truncate at word boundary
    if len(safe) > max_len:
        safe = safe[:max_len].rsplit('-', 1)[0]
    return safe or "untitled"


def _generate_session_id() -> str:
    """Generate a unique session ID."""
    return hashlib.sha256(
        f"{datetime.now(timezone.utc).isoformat()}".encode()
    ).hexdigest()[:16]


def init_archive_dirs():
    """Create archive directory structure."""
    for d in [ARCHIVE_ROOT, SHORTS_DIR, SESSIONS_DIR, GNOSIS_DIR]:
        d.mkdir(parents=True, exist_ok=True)


def archive_session(
    messages: list[dict],
    topic: str = "",
    tags: list[str] | None = None,
    is_short: bool = False,
    existing_session_id: str | None = None,
) -> dict:
    """Archive a conversation session.

    Args:
        messages: List of {"role": str, "content": str, "timestamp": str} dicts.
        topic: Human-readable topic for the session.
        tags: Optional list of tags for categorization.
        is_short: If True, archive in shorts/ instead of sessions/.
        existing_session_id: Reuse an existing ID if provided.

    Returns:
        Archive metadata dict with paths and session info.
    """
    init_archive_dirs()

    session_id = existing_session_id or _generate_session_id()
    now = datetime.now(timezone.utc)

    if messages:
        first_ts = messages[0].get("timestamp", now.isoformat())
        last_ts = messages[-1].get("timestamp", now.isoformat())
    else:
        first_ts = last_ts = now.isoformat()

    # Determine topic if not provided
    if not topic and messages:
        topic = _auto_topic(messages)

    safe_name = _safe_filename(topic)
    time_prefix = now.strftime("%Y-%m-%dT%H-%M")
    folder_name = f"{time_prefix}-{safe_name}"

    if is_short:
        session_dir = SHORTS_DIR / folder_name
    else:
        session_dir = SESSIONS_DIR / folder_name
    session_dir.mkdir(parents=True, exist_ok=True)

    # Write full text
    full_text_path = session_dir / "full.txt"
    full_content = _format_full_text(messages)
    full_text_path.write_text(full_content, encoding="utf-8")

    # Write summary
    summary_path = session_dir / "summary.md"
    summary = _generate_summary(messages, topic)
    summary_path.write_text(summary, encoding="utf-8")

    # Write index.json for this session
    entry = ArchiveEntry(
        session_id=session_id,
        folder=str(session_dir),
        topic=topic,
        started_at=first_ts,
        ended_at=last_ts,
        summary=summary,
        tags=tags or [],
        tier="hot",
        is_short=is_short,
        message_count=len(messages),
        token_estimate=estimate_tokens(full_content),
    )

    index_path = session_dir / "index.json"
    index_path.write_text(json.dumps(asdict(entry), indent=2), encoding="utf-8")

    # Update master index
    _update_master_index(entry)

    return asdict(entry)


def _format_full_text(messages: list[dict]) -> str:
    """Format messages into a readable full-text log with line numbers."""
    lines = []
    for i, msg in enumerate(messages, 1):
        ts = msg.get("timestamp", "")
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        lines.append(f"[L{i:04d}] [{ts}] {role.upper()}:\n{content}\n")
    return "\n".join(lines)


def _auto_topic(messages: list[dict]) -> str:
    """Generate a topic from the first few messages (to be enhanced with LLM)."""
    if not messages:
        return "untitled"
    # Simple heuristic: first user message, truncated
    for msg in messages:
        if msg.get("role") == "user":
            content = msg["content"].strip()
            # Take first sentence or first 80 chars
            sentence = re.split(r'[.!?]', content)[0].strip()
            if len(sentence) > 80:
                sentence = sentence[:77] + "..."
            return sentence
    return "untitled"


def _generate_summary(messages: list[dict], topic: str) -> str:
    """Generate an annotated summary with line references to full text.

    Format designed for both human reading and agent parsing.
    """
    lines = []
    lines.append(f"# {topic}")
    lines.append(f"Messages: {len(messages)}")
    lines.append("")

    if not messages:
        return "\n".join(lines)

    # Topic breakdown / episode detection
    episodes = _detect_episodes(messages)

    if len(episodes) > 1:
        lines.append("## Episodes")
        for i, (ep_start, ep_end, ep_topic) in enumerate(episodes, 1):
            lines.append(f"### Episode {i}: {ep_topic}")
            lines.append(f"- Lines: L{ep_start:04d}–L{ep_end:04d} in full.txt")
            lines.append(f"- Messages: {ep_end - ep_start + 1}")
            lines.append("")

    # Key points extraction
    lines.append("## Key Points")
    for i, msg in enumerate(messages):
        if msg.get("role") == "assistant" and len(msg.get("content", "")) > 100:
            # First meaningful sentence from assistant
            content = msg["content"]
            first_line = content.split("\n")[0][:120]
            lines.append(f"- L{i+1:04d}: {first_line}")
    lines.append("")

    # Decisions and outcomes
    lines.append("## Summary")
    # Concatenate last few messages for a quick recap
    recent = messages[-min(3, len(messages)):]
    for msg in recent:
        role = msg.get("role", "").upper()
        content = msg.get("content", "")[:200]
        lines.append(f"- **{role}**: {content}...")
    lines.append("")

    return "\n".join(lines)


def _detect_episodes(messages: list[dict]) -> list[tuple[int, int, str]]:
    """Detect topic shifts in a conversation (simple heuristic).

    Returns list of (start_idx, end_idx, topic) tuples.
    Enhanced later with embedding similarity.
    """
    if len(messages) <= 3:
        return [(0, max(0, len(messages) - 1), _auto_topic(messages))]

    episodes = []
    current_start = 0

    for i in range(1, len(messages)):
        prev = messages[i - 1].get("content", "")
        curr = messages[i].get("content", "")

        # Simple topic shift detection: if current message starts
        # a new subject (heuristic: short user message after long exchange)
        role = messages[i].get("role", "")
        if role == "user" and len(curr) < 60 and i - current_start > 2:
            # Check if it looks like a new topic
            prev_words = set(re.findall(r'\w+', prev.lower()))
            curr_words = set(re.findall(r'\w+', curr.lower()))
            new_topic_words = curr_words - prev_words
            if len(new_topic_words) > 2:  # Reduced threshold for better detection
                episodes.append((current_start, i - 1, _auto_topic(messages[current_start:i])))
                current_start = i

    # Final episode
    if current_start <= len(messages) - 1:
        episodes.append((current_start, len(messages) - 1, _auto_topic(messages[current_start:])))

    # If no episodes were detected, create a single episode
    if not episodes:
        episodes.append((0, len(messages) - 1, _auto_topic(messages)))

    return episodes


def estimate_tokens(text: str) -> int:
    """Rough token estimate (1 token ≈ 4 chars for English)."""
    return len(text) // 4


def _update_master_index(entry: ArchiveEntry):
    """Append entry to master index file."""
    master = []
    if MASTER_INDEX.exists():
        master = json.loads(MASTER_INDEX.read_text())

    # Check for duplicates
    existing_ids = {e["session_id"] for e in master}
    if entry.session_id not in existing_ids:
        master.append(asdict(entry))

    MASTER_INDEX.write_text(json.dumps(master, indent=2), encoding="utf-8")


def get_master_index() -> list[dict]:
    """Read the master archive index."""
    if MASTER_INDEX.exists():
        return json.loads(MASTER_INDEX.read_text())
    return []


def search_archives(query: str, limit: int = 10) -> list[dict]:
    """Search archives by topic/tags."""
    index = get_master_index()
    query_lower = query.lower()
    results = []
    for entry in index:
        score = 0
        if query_lower in entry["topic"].lower():
            score += 2
        if query_lower in entry["summary"].lower():
            score += 1
        for tag in entry.get("tags", []):
            if query_lower in tag.lower():
                score += 1
        if score > 0:
            results.append((score, entry))
    results.sort(key=lambda x: x[0], reverse=True)
    return [entry for _, entry in results[:limit]]


def archive_gnosis(title: str, content: str, source_session_id: str = "") -> str:
    """Save a permanent lesson learned to the gnosis directory."""
    init_archive_dirs()
    safe_name = _safe_filename(title)
    path = GNOSIS_DIR / f"{safe_name}.md"
    timestamp = datetime.now(timezone.utc).isoformat()
    header = f"# {title}\n\n> Extracted: {timestamp}"
    if source_session_id:
        header += f"\n> Source: {source_session_id}\n"
    header += "\n"
    path.write_text(header + content, encoding="utf-8")
    return str(path)
