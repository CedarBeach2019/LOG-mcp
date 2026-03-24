"""
L.O.G. CLI — Command-line interface for the Vault.

Usage:
    log vault init          Initialize the Vault
    log dehydrate <text>    Dehydrate text (strip PII)
    log rehydrate <text>    Rehydrate text (restore PII)
    log status              Show Vault status and storage stats
    log archive             Archive current session
    log search <query>      Search archives
    log gnosis <title>      Save a lesson learned
    log prune               Run hysteresis garbage collector
    log entities list       List all registered PII entities
    log entities add <type> <value>  Manually add a PII entity
"""

import argparse
import sys
import json
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from vault.core import RealLog, PIIEntity, MemoryTier
from vault.archiver import (
    archive_session, search_archives, archive_gnosis,
    get_master_index, init_archive_dirs, ARCHIVE_ROOT
)


def cmd_init(args):
    """Initialize the Vault."""
    log = RealLog(args.db_path)
    init_archive_dirs()
    print(f"✅ Vault initialized at {log.db_path}")
    print(f"📁 Archives at {ARCHIVE_ROOT}")
    stats = log.get_storage_stats()
    print(f"📊 Entities: {stats['entities']} | Sessions: {stats['sessions']}")


def cmd_dehydrate(args):
    """Dehydrate text — strip PII, replace with LOG_IDs."""
    log = RealLog(args.db_path)
    text = " ".join(args.text) if args.text else sys.stdin.read().strip()

    if not text:
        print("❌ No text provided", file=sys.stderr)
        sys.exit(1)

    dehydrated, entities = log.dehydrate(text)

    if args.json:
        output = {
            "dehydrated": dehydrated,
            "entities": [{"log_id": e.log_id, "type": e.entity_type} for e in entities]
        }
        print(json.dumps(output, indent=2))
    else:
        print(dehydrated)
        if entities:
            print(f"\n🔍 Detected {len(entities)} entities:")
            for e in entities:
                print(f"   <{e.log_id}> = [{e.entity_type}] ***")


def cmd_rehydrate(args):
    """Rehydrate text — swap LOG_IDs back to real values."""
    log = RealLog(args.db_path)
    text = " ".join(args.text) if args.text else sys.stdin.read().strip()

    if not text:
        print("❌ No text provided", file=sys.stderr)
        sys.exit(1)

    rehydrated = log.rehydrate(text)
    print(rehydrated)


def cmd_status(args):
    """Show Vault status."""
    log = RealLog(args.db_path)
    stats = log.get_storage_stats()
    db_size = log.db_size_mb()

    print("🪵 L.O.G. Vault Status")
    print(f"   Database:  {log.db_path} ({db_size:.2f} MB)")
    print(f"   Entities:  {stats['entities']}")
    print(f"   Sessions:  {stats['sessions']}")
    print(f"   Archives:  {stats['archives']}")
    print(f"   By Tier:")
    for tier, count in stats.get('by_tier', {}).items():
        emoji = {"hot": "🔥", "warm": "🌤️", "cold": "❄️", "ice": "🧊"}.get(tier, "❓")
        print(f"     {emoji} {tier}: {count}")

    # Archive directory sizes
    if ARCHIVE_ROOT.exists():
        total_size = sum(f.stat().st_size for f in ARCHIVE_ROOT.rglob("*") if f.is_file())
        print(f"   Archive Storage: {total_size / (1024*1024):.2f} MB")
        shorts = len(list((ARCHIVE_ROOT / "shorts").iterdir())) if (ARCHIVE_ROOT / "shorts").exists() else 0
        sessions = len(list((ARCHIVE_ROOT / "sessions").iterdir())) if (ARCHIVE_ROOT / "sessions").exists() else 0
        gnosis = len(list((ARCHIVE_ROOT / "gnosis").iterdir())) if (ARCHIVE_ROOT / "gnosis").exists() else 0
        print(f"   Shorts: {shorts} | Sessions: {sessions} | Gnosis: {gnosis}")


def cmd_archive(args):
    """Archive messages from stdin (JSON array of messages)."""
    init_archive_dirs()

    if args.input:
        raw = Path(args.input).read_text()
    else:
        raw = sys.stdin.read()

    try:
        messages = json.loads(raw)
    except json.JSONDecodeError:
        print("❌ Expected JSON array of messages", file=sys.stderr)
        sys.exit(1)

    result = archive_session(
        messages=messages,
        topic=args.topic or "",
        tags=args.tags.split(",") if args.tags else None,
        is_short=args.short,
    )

    print(f"✅ Archived session: {result['session_id']}")
    print(f"   Topic: {result['topic']}")
    print(f"   Folder: {result['folder']}")
    print(f"   Messages: {result['message_count']}")


def cmd_search(args):
    """Search archives."""
    results = search_archives(args.query, limit=args.limit)
    if not results:
        print(f"🔍 No results for '{args.query}'")
        return

    print(f"🔍 Found {len(results)} results for '{args.query}':\n")
    for r in results:
        tier_emoji = {"hot": "🔥", "warm": "🌤️", "cold": "❄️"}.get(r.get("tier", "hot"), "")
        print(f"  {tier_emoji} {r['topic']}")
        print(f"     ID: {r['session_id']} | {r['started_at']}")
        print(f"     {r['summary'][:100]}...")
        if r.get("tags"):
            print(f"     Tags: {', '.join(r['tags'])}")
        print()


def cmd_gnosis(args):
    """Save a lesson learned."""
    content = " ".join(args.content) if args.content else sys.stdin.read().strip()
    if not content:
        print("❌ No content provided", file=sys.stderr)
        sys.exit(1)

    path = archive_gnosis(args.title, content)
    print(f"✅ Gnosis saved: {path}")


def cmd_prune(args):
    """Run hysteresis garbage collector."""
    log = RealLog(args.db_path)
    stats = log.get_storage_stats()
    db_size = log.db_size_mb()

    # Check thresholds
    warn_mb = args.warn_mb or 1024  # Default 1GB warning
    if db_size > warn_mb:
        print(f"⚠️  Database size ({db_size:.2f} MB) exceeds {warn_mb} MB threshold!")
        print("   Consider running full pruning or migrating old data.")

    # Demote cold sessions
    cold_sessions = log.get_sessions(tier=MemoryTier.COLD, limit=100)
    if cold_sessions and args.auto:
        for s in cold_sessions:
            log.promote_session(s.session_id, MemoryTier.ICE)
            print(f"  🧊 Pruned: {s.topic[:50]}")

    print(f"\n📊 Current stats:")
    cmd_status(args)


def cmd_entities(args):
    """Manage PII entities."""
    log = RealLog(args.db_path)

    if args.action == "list":
        entities = log.all_entities()
        if not entities:
            print("📋 No entities registered")
            return
        print(f"📋 {len(entities)} registered entities:\n")
        for e in entities:
            status = "✅" if e.approved else "⏳"
            print(f"  {status} <{e.log_id}> [{e.entity_type}]")
            print(f"     Context: {e.context or 'N/A'}")

    elif args.action == "add":
        if len(args.values) < 2:
            print("❌ Usage: entities add <type> <value>", file=sys.stderr)
            sys.exit(1)
        entity_type = args.values[0]
        real_value = " ".join(args.values[1:])
        log_id = log.next_log_id(entity_type)
        entity = PIIEntity(log_id=log_id, real_value=real_value, entity_type=entity_type)
        log.register_entity(entity)
        print(f"✅ Registered <{log_id}> = [{entity_type}] ***")


def main():
    parser = argparse.ArgumentParser(prog="log", description="🪵 L.O.G. — Latent Orchestration Gateway CLI")
    parser.add_argument("--db-path", default="~/.log/vault/reallog.db", help="Path to RealLog database")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # vault init
    p_init = subparsers.add_parser("init", help="Initialize the Vault")
    p_init.set_defaults(func=cmd_init)

    # dehydrate
    p_deh = subparsers.add_parser("dehydrate", help="Strip PII from text")
    p_deh.add_argument("text", nargs="*", help="Text to dehydrate (or pipe via stdin)")
    p_deh.add_argument("--json", action="store_true", help="Output as JSON")
    p_deh.set_defaults(func=cmd_dehydrate)

    # rehydrate
    p_reh = subparsers.add_parser("rehydrate", help="Restore real values from LOG_IDs")
    p_reh.add_argument("text", nargs="*", help="Text to rehydrate (or pipe via stdin)")
    p_reh.set_defaults(func=cmd_rehydrate)

    # status
    p_status = subparsers.add_parser("status", help="Show Vault status")
    p_status.set_defaults(func=cmd_status)

    # archive
    p_arch = subparsers.add_parser("archive", help="Archive a session")
    p_arch.add_argument("--input", "-i", help="JSON file with messages (or pipe via stdin)")
    p_arch.add_argument("--topic", "-t", help="Session topic")
    p_arch.add_argument("--tags", help="Comma-separated tags")
    p_arch.add_argument("--short", action="store_true", help="Archive as short session")
    p_arch.set_defaults(func=cmd_archive)

    # search
    p_search = subparsers.add_parser("search", help="Search archives")
    p_search.add_argument("query", help="Search query")
    p_search.add_argument("--limit", "-n", type=int, default=10)
    p_search.set_defaults(func=cmd_search)

    # gnosis
    p_gn = subparsers.add_parser("gnosis", help="Save a lesson learned")
    p_gn.add_argument("title", help="Gnosis title")
    p_gn.add_argument("content", nargs="*", help="Content (or pipe via stdin)")
    p_gn.set_defaults(func=cmd_gnosis)

    # prune
    p_prune = subparsers.add_parser("prune", help="Run garbage collector")
    p_prune.add_argument("--auto", action="store_true", help="Auto-promote cold sessions")
    p_prune.add_argument("--warn-mb", type=float, help="Size warning threshold in MB")
    p_prune.set_defaults(func=cmd_prune)

    # entities
    p_ent = subparsers.add_parser("entities", help="Manage PII entities")
    p_ent.add_argument("action", choices=["list", "add"], help="Action to perform")
    p_ent.add_argument("values", nargs="*", help="Type and value for 'add'")
    p_ent.set_defaults(func=cmd_entities)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
