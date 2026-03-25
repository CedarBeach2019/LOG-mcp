"""
Extended tests for LOG-mcp vault components.

Covers: CLI integration, MCP server, cross-session entities,
        edge cases, performance baselines, and full pipeline tests.
"""

import pytest
import tempfile
import os
import json
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from vault.core import (
    RealLog, Dehydrator, Rehydrator, PIIEntity, MemoryTier,
    create_session, create_message
)


@pytest.fixture
def db_path():
    """Provide a temporary database path."""
    path = tempfile.mktemp(suffix=".db")
    yield path
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def vault(db_path):
    """Provide a fresh RealLog instance."""
    return RealLog(db_path)


@pytest.fixture
def dehydrator(vault):
    return Dehydrator(vault)


@pytest.fixture
def rehydrator(vault):
    return Rehydrator(vault)


class TestPIIDetection:
    """Test PII detection patterns."""

    def test_email_detection(self, dehydrator):
        emails = [
            "user@example.com",
            "first.last@company.co.uk",
            "admin+test@sub.domain.org",
            "a@b.cc",
        ]
        for email in emails:
            text = f"Contact at {email}"
            _, entities = dehydrator.dehydrate(text)
            types = [e.entity_type for e in entities]
            assert "email" in types, f"Failed to detect email: {email}"

    def test_phone_variants(self, dehydrator):
        phones = [
            "555-123-4567",
            "(555) 123-4567",
            "555.123.4567",
            "+1-555-123-4567",
            "+1 (555) 123-4567",
        ]
        for phone in phones:
            text = f"Call me at {phone} please"
            _, entities = dehydrator.dehydrate(text)
            types = [e.entity_type for e in entities]
            assert "phone" in types, f"Failed to detect phone: {phone}"

    def test_ssn_detection(self, dehydrator):
        text = "SSN: 123-45-6789 and 987-65-4321"
        _, entities = dehydrator.dehydrate(text)
        ssns = [e for e in entities if e.entity_type == "ssn"]
        assert len(ssns) == 2

    def test_credit_card(self, dehydrator):
        text = "Card: 4532-1234-5678-9012 and 5425-0000-1111-2222"
        _, entities = dehydrator.dehydrate(text)
        cards = [e for e in entities if e.entity_type == "credit_card"]
        assert len(cards) == 2

    def test_credit_card_not_phone(self, dehydrator):
        """Credit card numbers should not be partially matched as phone numbers."""
        text = "card 4111222233334444"
        dehydrated, entities = dehydrator.dehydrate(text)
        phones = [e for e in entities if e.entity_type == "phone"]
        cards = [e for e in entities if e.entity_type == "credit_card"]
        assert len(cards) == 1, f"Expected 1 credit card, got {len(cards)}"
        assert len(phones) == 0, f"Credit card matched as phone: {phones}"
        assert "CREDIT_CARD_A" in dehydrated or "CC_A" in dehydrated, f"Expected CC token in dehydrated text, got: {dehydrated}"
        assert "PHONE" not in dehydrated, f"PHONE found in dehydrated text: {dehydrated}"

    def test_api_key(self, dehydrator):
        keys = [
            "sk-abc123def456ghi789jkl0mno",
            "sk-live-abc123def456ghi789jkl012mno",
            "key_12345_abcdefghijklmnopqrstuvwxyz",
        ]
        for key in keys:
            text = f"Use this key: {key}"
            _, entities = dehydrator.dehydrate(text)
            types = [e.entity_type for e in entities]
            assert "api_key" in types, f"Failed to detect API key: {key}"

    def test_no_false_positives_on_normal_text(self, dehydrator):
        text = "The quick brown fox jumps over the lazy dog. Python is great for coding."
        _, entities = dehydrator.dehydrate(text)
        # Should detect very few (maybe some names) but no emails/phones/ssns
        pii_types = [e.entity_type for e in entities]
        assert "email" not in pii_types
        assert "phone" not in pii_types
        assert "ssn" not in pii_types
    
    def test_name_detection_improvements(self, dehydrator):
        # Test that 'Email John Smith' doesn't get detected as a name
        text = "Email John Smith about the project"
        _, entities = dehydrator.dehydrate(text)
        # 'Email' should not be part of a detected name
        person_entities = [e for e in entities if e.entity_type == 'person']
        # 'John Smith' might be detected, but 'Email John Smith' shouldn't be
        for entity in person_entities:
            assert 'Email' not in entity.real_value
        
        # Test that actual names are still detected
        text2 = "Contact John Smith at john@example.com"
        _, entities2 = dehydrator.dehydrate(text2)
        person_entities2 = [e for e in entities2 if e.entity_type == 'person']
        # 'John Smith' should be detected
        assert any('John Smith' in e.real_value for e in person_entities2)
        
        # Test with common non-name words
        text3 = "Send the document to The Office"
        _, entities3 = dehydrator.dehydrate(text3)
        person_entities3 = [e for e in entities3 if e.entity_type == 'person']
        # 'The Office' should not be detected as a person
        assert not any('The Office' in e.real_value for e in person_entities3)

    def test_address_detection(self, dehydrator):
        text = "Ship to 742 Evergreen Terrace, Springfield and 1600 Pennsylvania Avenue NW"
        _, entities = dehydrator.dehydrate(text)
        addrs = [e for e in entities if e.entity_type == "address"]
        assert len(addrs) >= 1


class TestDehydrationRehydration:
    """Test dehydration and rehydration round-trips."""

    def test_single_entity_roundtrip(self, dehydrator, rehydrator):
        text = "Email test@example.com for help"
        dehydrated, _ = dehydrator.dehydrate(text)
        rehydrated = rehydrator.rehydrate(dehydrated)
        assert rehydrated == text

    def test_multiple_entity_roundtrip(self, dehydrator, rehydrator):
        text = "Contact John Smith at john@smith.com or 555-123-4567. SSN: 111-22-3333."
        dehydrated, _ = dehydrator.dehydrate(text)
        rehydrated = rehydrator.rehydrate(dehydrated)
        assert rehydrated == text

    def test_same_pii_same_id(self, dehydrator):
        text1 = "Email alice@example.com"
        text2 = "Also alice@example.com"
        d1, _ = dehydrator.dehydrate(text1)
        d2, _ = dehydrator.dehydrate(text2)
        # Same email should map to same entity ID
        import re
        id1 = re.findall(r'\[[A-Z]+_[A-Z]\]', d1)
        id2 = re.findall(r'\[[A-Z]+_[A-Z]\]', d2)
        assert len(id1) == 1 and len(id2) == 1
        assert id1[0] == id2[0]

    def test_different_values_different_ids(self, dehydrator):
        text1 = "Email alice@example.com"
        text2 = "Email bob@example.com"
        d1, _ = dehydrator.dehydrate(text1)
        d2, _ = dehydrator.dehydrate(text2)
        assert d1 != d2


class TestCrossSession:
    """Test entity sharing across sessions."""

    def test_entity_persists_across_sessions(self, vault, dehydrator, rehydrator):
        # Session 1
        text1 = "Contact alice@example.com"
        d1, _ = dehydrator.dehydrate(text1)

        # Session 2 - same email
        text2 = "Follow up with alice@example.com"
        d2, _ = dehydrator.dehydrate(text2)

        # Same entity ID should be used
        import re
        id1 = re.findall(r'\[[A-Z]+_[A-Z]\]', d1)
        id2 = re.findall(r'\[[A-Z]+_[A-Z]\]', d2)
        assert id1[0] == id2[0]

        # Rehydrate from session 2 data
        rehydrated = rehydrator.rehydrate(d2)
        assert "alice@example.com" in rehydrated

    def test_entity_count_grows_with_new_data(self, vault, dehydrator):
        dehydrator.dehydrate("Email alice@example.com")
        dehydrator.dehydrate("Email bob@example.com")
        dehydrator.dehydrate("Email alice@example.com")  # duplicate
        entities = vault.all_entities()
        # Should have exactly 2 unique email entities
        emails = [e for e in entities if e.entity_type == "email"]
        assert len(emails) == 2


class TestRealLog:
    """Test RealLog database operations."""

    def test_session_crud(self, vault):
        s = create_session("s1", "Test session")
        vault.add_session(s)
        retrieved = vault.get_session("s1")
        assert retrieved is not None
        assert retrieved.id == "s1"
        assert retrieved.summary == "Test session"

    def test_message_storage(self, vault):
        vault.add_session(create_session("s1", "Test"))
        msg = create_message("s1", "user", "Hello world")
        msg_id = vault.add_message(msg)
        assert msg_id > 0

    def test_storage_stats(self, vault):
        stats = vault.get_storage_stats()
        assert "entities" in stats
        assert "sessions" in stats
        assert "messages" in stats

    def test_all_entities(self, vault, dehydrator):
        dehydrator.dehydrate("Email test@example.com and call 555-123-4567")
        entities = vault.all_entities()
        assert len(entities) >= 2


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_empty_text(self, dehydrator):
        dehydrated, entities = dehydrator.dehydrate("")
        assert dehydrated == ""
        assert len(entities) == 0

    def test_no_pii(self, dehydrator, rehydrator):
        text = "Just a normal sentence with no personal information."
        dehydrated, entities = dehydrator.dehydrate(text)
        assert len(entities) == 0
        assert dehydrated == text

    def test_unknown_entity_rehydration(self, rehydrator):
        text = "Contact <FAKE_ENTITY_99> and <NONEXISTENT_5>"
        result = rehydrator.rehydrate(text)
        # Should leave unknown IDs as-is or warn gracefully
        assert isinstance(result, str)

    def test_unicode_text(self, dehydrator, rehydrator):
        text = "Email test@example.com — café résumé naïve"
        dehydrated, _ = dehydrator.dehydrate(text)
        rehydrated = rehydrator.rehydrate(dehydrated)
        assert "test@example.com" in rehydrated
        assert "café" in rehydrated

    def test_very_long_text(self, dehydrator):
        names = ["Alice", "Bob", "Carol", "Dave", "Eve"]
        long_text = " ".join(f"Email {n.lower()}@example.com" for n in names * 100)
        dehydrated, entities = dehydrator.dehydrate(long_text)
        assert len(entities) >= 5  # At least the 5 unique emails

    def test_special_characters_in_pii(self, dehydrator, rehydrator):
        text = "Email user+tag@example.com and call 555-123-4567"
        dehydrated, entities = dehydrator.dehydrate(text)
        rehydrated = rehydrator.rehydrate(dehydrated)
        assert "user+tag@example.com" in rehydrated


class TestMCPInterface:
    """Test MCP server tool handling."""

    def test_tool_list(self):
        from mcp.server import TOOLS
        assert len(TOOLS) >= 7
        names = [t["name"] for t in TOOLS]
        assert "log_dehydrate" in names
        assert "log_rehydrate" in names
        assert "log_vault_status" in names

    def test_jsonrpc_initialize(self):
        from mcp.server import handle_request
        req = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        resp = handle_request(req)
        assert resp["result"]["serverInfo"]["name"] == "log-mcp"

    def test_jsonrpc_tools_list(self):
        from mcp.server import handle_request
        req = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        resp = handle_request(req)
        assert "tools" in resp["result"]

    def test_jsonrpc_unknown_tool(self):
        from mcp.server import handle_request
        req = {
            "jsonrpc": "2.0", "id": 1,
            "method": "tools/call",
            "params": {"name": "nonexistent_tool", "arguments": {}}
        }
        resp = handle_request(req)
        assert "error" in resp


class TestArchiver:
    """Test archiver module."""

    def test_gnosis_archive(self):
        from vault.archiver import archive_gnosis, init_archive_dirs
        init_archive_dirs()
        path = archive_gnosis("Test Lesson", "Always dehydrate before sending to scouts.")
        assert os.path.exists(path)
        content = Path(path).read_text()
        assert "Test Lesson" in content
        assert "Always dehydrate" in content

    def test_session_archive(self):
        from vault.archiver import archive_session, init_archive_dirs
        init_archive_dirs()
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        result = archive_session(messages, topic="greeting test")
        assert "session_id" in result
        assert result["message_count"] == 2
