"""
L.O.G. MCP Server — Agent-facing tool interface.

Implements the full LOG vocabulary for dehydration, rehydration,
archiving, distillation, and hysteresis management.

Agent Instructions (embed in system prompt):
  "You are the LOG-Orchestrator. Your primary directive is: Never transmit
   raw PII. If a tool requires data, you must first call log_dehydrate.
   If a response contains LOG_IDs, you must call log_rehydrate before
   showing it to the user. You are the wall between the Vault and the Scout."
"""

import json
import sys
from pathlib import Path
from typing import Any

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from vault.core import RealLog, PIIEntity, MemoryTier
from vault.archiver import (
    archive_session, search_archives, archive_gnosis,
    get_master_index, init_archive_dirs
)

reallog = RealLog()


# --- MCP Tool Definitions ---

TOOLS = [
    {
        "name": "log_dehydrate",
        "description": "Strip PII from text and replace with LOG_IDs. Always call this before sending data to external agents.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Raw text to dehydrate"},
                "force_entities": {
                    "type": "array",
                    "description": "Optional: manually specify entities as [{entity_type, real_value}]",
                    "items": {
                        "type": "object",
                        "properties": {
                            "entity_type": {"type": "string"},
                            "real_value": {"type": "string"}
                        }
                    }
                }
            },
            "required": ["text"]
        }
    },
    {
        "name": "log_rehydrate",
        "description": "Swap LOG_ID placeholders back to real values for human-readable output.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text containing LOG_IDs to rehydrate"}
            },
            "required": ["text"]
        }
    },
    {
        "name": "log_distill",
        "description": "Distill a conversation into a semantic summary (Working-Fiction). Strips fluff, extracts key context.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "messages": {"type": "array", "description": "Array of {role, content} messages to distill"},
                "focus": {"type": "string", "description": "Optional: what to focus the summary on"}
            },
            "required": ["messages"]
        }
    },
    {
        "name": "log_archive_session",
        "description": "Archive a conversation session to the Vault's local filesystem.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "messages": {"type": "array", "description": "Array of {role, content, timestamp} messages"},
                "topic": {"type": "string", "description": "Session topic"},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags for categorization"},
                "is_short": {"type": "boolean", "description": "Archive in shorts/ if single-topic"}
            },
            "required": ["messages"]
        }
    },
    {
        "name": "log_search_archives",
        "description": "Search the Vault's archive index by topic, summary, or tags.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "limit": {"type": "integer", "description": "Max results (default 10)"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "log_archive_gnosis",
        "description": "Save a permanent lesson learned to the gnosis directory.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Gnosis title"},
                "content": {"type": "string", "description": "Lesson content"},
                "source_session_id": {"type": "string", "description": "Optional: source session ID"}
            },
            "required": ["title", "content"]
        }
    },
    {
        "name": "log_prune_hysteresis",
        "description": "Run the garbage collector. View or modify memory tier status.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["status", "promote", "prune"], "description": "Action to perform"},
                "session_id": {"type": "string", "description": "Session ID (for promote)"},
                "new_tier": {"type": "string", "enum": ["hot", "warm", "cold", "ice"], "description": "Target tier (for promote)"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "log_vault_status",
        "description": "Get current Vault status: storage stats, entity count, tier distribution.",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    }
]


# --- Tool Handlers ---

def handle_log_dehydrate(arguments: dict) -> dict:
    text = arguments["text"]
    entities = None
    if arguments.get("force_entities"):
        entities = [
            PIIEntity(log_id="", real_value=e["real_value"], entity_type=e["entity_type"])
            for e in arguments["force_entities"]
        ]
    dehydrated, ents = reallog.dehydrate(text, entities)
    return {
        "dehydrated_text": dehydrated,
        "entities_detected": len(ents),
        "entities": [{"log_id": e.log_id, "type": e.entity_type} for e in ents]
    }


def handle_log_rehydrate(arguments: dict) -> dict:
    return {"rehydrated_text": reallog.rehydrate(arguments["text"])}


def handle_log_distill(arguments: dict) -> dict:
    messages = arguments["messages"]
    focus = arguments.get("focus", "")

    # Extract key information from messages
    topics = set()
    entities_mentioned = set()
    user_expertise = []

    for msg in messages:
        content = msg.get("content", "").lower()
        role = msg.get("role", "")
        if role == "user":
            # Simple keyword extraction (enhanced with local LLM later)
            words = content.split()
            if focus:
                topics.add(focus)
            if len(words) > 5:
                # First meaningful clause
                topics.add(content[:80])

    # Generate Working-Fiction summary
    summary_parts = []
    if topics:
        summary_parts.append(f"Context: {'; '.join(list(topics)[:5])}")
    if user_expertise:
        summary_parts.append(f"Expertise: {', '.join(user_expertise[:5])}")
    summary_parts.append(f"Exchange length: {len(messages)} messages")

    return {
        "distilled_context": " | ".join(summary_parts) if summary_parts else "No distillable content",
        "message_count": len(messages),
        "note": "Enhanced distillation available with local LLM integration"
    }


def handle_log_archive_session(arguments: dict) -> dict:
    result = archive_session(
        messages=arguments["messages"],
        topic=arguments.get("topic", ""),
        tags=arguments.get("tags"),
        is_short=arguments.get("is_short", False),
    )
    return result


def handle_log_search_archives(arguments: dict) -> dict:
    results = search_archives(arguments["query"], limit=arguments.get("limit", 10))
    return {"results": results, "count": len(results)}


def handle_log_archive_gnosis(arguments: dict) -> dict:
    path = archive_gnosis(
        title=arguments["title"],
        content=arguments["content"],
        source_session_id=arguments.get("source_session_id", ""),
    )
    return {"gnosis_path": path, "status": "saved"}


def handle_log_prune_hysteresis(arguments: dict) -> dict:
    action = arguments["action"]
    if action == "status":
        stats = reallog.get_storage_stats()
        stats["db_size_mb"] = reallog.db_size_mb()
        return stats
    elif action == "promote":
        sid = arguments.get("session_id")
        tier_str = arguments.get("new_tier", "warm")
        if not sid:
            return {"error": "session_id required for promote"}
        tier = MemoryTier(tier_str)
        reallog.promote_session(sid, tier)
        return {"status": "promoted", "session_id": sid, "new_tier": tier_str}
    elif action == "prune":
        # Auto-prune cold sessions
        cold = reallog.get_sessions(tier=MemoryTier.COLD, limit=100)
        pruned = 0
        for s in cold:
            reallog.promote_session(s.session_id, MemoryTier.ICE)
            pruned += 1
        return {"status": "pruned", "sessions_iced": pruned}
    return {"error": "unknown action"}


def handle_log_vault_status(arguments: dict) -> dict:
    stats = reallog.get_storage_stats()
    stats["db_size_mb"] = reallog.db_size_mb()
    return stats


HANDLERS = {
    "log_dehydrate": handle_log_dehydrate,
    "log_rehydrate": handle_log_rehydrate,
    "log_distill": handle_log_distill,
    "log_archive_session": handle_log_archive_session,
    "log_search_archives": handle_log_search_archives,
    "log_archive_gnosis": handle_log_archive_gnosis,
    "log_prune_hysteresis": handle_log_prune_hysteresis,
    "log_vault_status": handle_log_vault_status,
}


# --- MCP JSON-RPC Transport (stdio) ---

def handle_request(request: dict) -> dict:
    """Handle a single JSON-RPC request."""
    method = request.get("method", "")
    req_id = request.get("id")
    params = request.get("params", {})

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": "log-mcp",
                    "version": "0.1.0"
                }
            }
        }

    elif method == "notifications/initialized":
        return None  # No response for notifications

    elif method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}

    elif method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})
        handler = HANDLERS.get(tool_name)
        if handler:
            try:
                result = handler(arguments)
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": json.dumps(result, indent=2)}]
                    }
                }
            except Exception as e:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32603, "message": str(e)}
                }
        else:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"}
            }

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Unknown method: {method}"}
    }


def main():
    """Run MCP server on stdio (JSON-RPC)."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            response = handle_request(request)
            if response is not None:
                print(json.dumps(response), flush=True)
        except json.JSONDecodeError:
            print(json.dumps({
                "jsonrpc": "2.0", "id": None,
                "error": {"code": -32700, "message": "Parse error"}
            }), flush=True)


if __name__ == "__main__":
    main()
