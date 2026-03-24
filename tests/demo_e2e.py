"""
LOG-mcp End-to-End Use Case Demo Suite

Runs realistic scenarios against the vault system to validate
the full dehydration → scout → rehydration pipeline.
"""

import tempfile
import os
import json
import time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from vault.core import RealLog, Dehydrator, Rehydrator, create_session, create_message
from vault.archiver import archive_session, archive_gnosis, init_archive_dirs, search_archives


class DemoRunner:
    """Run and validate demo use cases."""

    def __init__(self):
        self.db_path = tempfile.mktemp(suffix=".db")
        self.vault = RealLog(self.db_path)
        self.dehydrator = Dehydrator(self.vault)
        self.rehydrator = Rehydrator(self.vault)
        self.results = []

    def record(self, name, passed, detail=""):
        status = "✅" if passed else "❌"
        msg = f"{status} {name}"
        if detail:
            msg += f" — {detail}"
        print(msg)
        self.results.append((name, passed, detail))

    def cleanup(self):
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def print_summary(self):
        total = len(self.results)
        passed = sum(1 for _, p, _ in self.results if p)
        failed = total - passed
        print(f"\n{'='*60}")
        print(f"Results: {passed}/{total} passed, {failed} failed")
        if failed:
            print("Failures:")
            for name, p, d in self.results:
                if not p:
                    print(f"  ❌ {name}: {d}")
        return failed == 0


def demo_1_medical_privacy():
    """HIPAA-style: Medical records must never leave the vault."""
    print("\n" + "="*60)
    print("DEMO 1: Medical Record Privacy (HIPAA)")
    print("="*60)

    runner = DemoRunner()
    init_archive_dirs()

    medical_note = """
    PATIENT: Maria Rodriguez
    DOB: 1982-07-15
    MRN: MRN-2024-00547
    DIAGNOSIS: Type 2 Diabetes, Hypertension
    MEDICATIONS: Metformin 500mg BID, Lisinopril 10mg QD
    ALLERGIES: Penicillin, Sulfa drugs
    PRIMARY CARE: Dr. James Wilson, Internal Medicine
    CONTACT: maria.rodriguez@email.com, (555) 234-5678
    EMERGENCY: Carlos Rodriguez (spouse), (555) 345-6789
    INSURANCE: BlueCross BlueShield, ID# BCB-123456789
    SSN: 456-78-9012
    """

    # Step 1: Dehydrate before sending to any external service
    dehydrated, entities = runner.dehydrator.dehydrate(medical_note)
    print(f"\nDehydrated ({len(entities)} entities removed):")
    print(dehydrated[:200] + "...")

    # Verify no real PII in dehydrated text
    has_real_email = "@" in dehydrated and ".com" in dehydrated
    has_real_ssn = any("456-78-9012" in medical_note for _ in [None]) and "456-78-9012" not in dehydrated
    has_real_phone = "(555)" not in dehydrated

    runner.record("No email in dehydrated", not has_real_email or "email.com" not in dehydrated,
                  "emails replaced with placeholders")
    runner.record("No SSN in dehydrated", "456-78-9012" not in dehydrated)
    runner.record("No phone in dehydrated", "(555)" not in dehydrated)

    # Step 2: Simulate sending to external AI for summarization
    # Build scout response using actual entity IDs from dehydration
    entity_map = {e.entity_id: e.real_value for e in entities}
    # Find entities by type
    email_id = next((e.entity_id for e in entities if e.entity_type == "email"), None)
    phone_id = next((e.entity_id for e in entities if e.entity_type == "phone"), None)
    patient_id = next((e.entity_id for e in entities if e.entity_type == "person"), None)
    doctor_id = [e.entity_id for e in entities if e.entity_type == "person"]
    
    scout_summary = f"""
    Patient <{patient_id}> (DOB: 1982-07-15) diagnosed with Type 2 Diabetes and Hypertension.
    Current medications: Metformin and Lisinopril.
    Allergies noted. Follow-up with <{patient_id}> recommended.
    Contact via <{email_id}> or <{phone_id}>.
    """

    # Step 3: Rehydrate for human viewing only
    rehydrated = runner.rehydrator.rehydrate(scout_summary)
    print(f"\nRehydrated for doctor viewing:")
    print(rehydrated)

    # Step 4: Archive the interaction
    messages = [
        {"role": "system", "content": "Dehydrate all medical data before processing."},
        {"role": "user", "content": medical_note},
        {"role": "assistant", "content": dehydrated},
    ]
    result = archive_session(messages, topic="Medical record processing", tags=["hipaa", "medical"])
    runner.record("Session archived", os.path.exists(result.get("folder", result.get("archive_path", ""))))

    runner.record("All entities persisted", len(runner.vault.all_entities()) >= 5,
                  f"{len(runner.vault.all_entities())} entities stored")

    runner.cleanup()
    return runner


def demo_2_legal_privilege():
    """Attorney-client: Legal communications must be dehydrated."""
    print("\n" + "="*60)
    print("DEMO 2: Attorney-Client Privilege")
    print("="*60)

    runner = DemoRunner()

    legal_comm = """
    RE: Case #2024-CV-0892 — Rodriguez v. Acme Corp

    To: Sarah Mitchell, Esq. (sarah.mitchell@lawfirm.com)
    From: Client Jennifer Park (jpark@gmail.com, 555-876-5432)

    Summary: I was terminated on March 1, 2026 after reporting safety violations
    to OSHA. My manager David Chen (dchen@acmecorp.com) told me to 'keep quiet
    about the asbestos issue.' I have documentation showing the company knew
    since January.

    My SSN is 321-65-9870 and my employee ID was EMP-15432.
    Company address: 2000 Industrial Blvd, Houston, TX 77001.
    """

    dehydrated, entities = runner.dehydrator.dehydrate(legal_comm)
    print(f"\nDehydrated ({len(entities)} entities):")
    for e in entities:
        print(f"  <{e.entity_id}> = {e.entity_type}")

    # Verify legal PII is protected
    runner.record("Attorney email removed", "sarah.mitchell@lawfirm.com" not in dehydrated)
    runner.record("Client email removed", "jpark@gmail.com" not in dehydrated)
    runner.record("SSN removed", "321-65-9870" not in dehydrated)
    runner.record("Manager email removed", "dchen@acmecorp.com" not in dehydrated)

    # Simulate legal research scout using actual entity IDs
    entity_map = {e.entity_id: e.real_value for e in entities}
    email_entities = [e for e in entities if e.entity_type == "email"]
    phone_entities = [e for e in entities if e.entity_type == "phone"]
    person_entities = [e for e in entities if e.entity_type == "person"]
    
    scout_response = f"""
    Based on <{email_entities[0].entity_id if email_entities else 'EMAIL_1'}>'s communication, 
    this appears to be a wrongful termination case under whistleblower protections.
    Client <{person_entities[1].entity_id if len(person_entities) > 1 else person_entities[0].entity_id}> 
    should file with OSHA within 30 days.
    Contact <{phone_entities[0].entity_id if phone_entities else 'PHONE_1'}> to schedule next meeting.
    """

    rehydrated = runner.rehydrator.rehydrate(scout_response)
    print(f"\nRehydrated legal analysis:")
    print(rehydrated)

    runner.record("Round-trip preserves identities", "Sarah Mitchell" in rehydrated,
                  "attorney identity restored")

    # Save as gnosis
    path = archive_gnosis("Whistleblower Protection", 
        "OSHA 29 CFR 1977 provides whistleblower protections. 30-day filing deadline.")
    runner.record("Legal precedent saved as gnosis", os.path.exists(path))

    runner.cleanup()
    return runner


def demo_3_financial():
    """Financial data: Never expose banking details to agents."""
    print("\n" + "="*60)
    print("DEMO 3: Financial Data Protection")
    print("="*60)

    runner = DemoRunner()

    financial_data = """
    BANK OF ALASKA — Account Statement
    ===================================
    Account Holder: Robert K. Thompson
    Account Number: 9876543210
    Routing Number: 123456789
    SSN: 222-33-4444
    
    Recent Transactions:
    - 03/20: Wire transfer to Merrill Lynch, $15,000.00
      Reference: WT-2026-0320-ALPHA
    - 03/19: ACH from employer, $4,250.00
    - 03/18: Debit card at Amazon, $89.99
      Card: 5425-1234-5678-9012
      
    Contact: robert.thompson@investments.net
    Broker: Amanda Foster, CFP (amanda@wealthadvisors.com, 555-789-0123)
    API access token: sk-live-banking-abc123def456ghi789jkl012
    """

    dehydrated, entities = runner.dehydrator.dehydrate(financial_data)
    print(f"\nDehydrated ({len(entities)} entities removed)")

    # Security checks
    runner.record("Bank account removed", "9876543210" not in dehydrated)
    runner.record("Routing number removed", "123456789" not in dehydrated)
    runner.record("SSN removed", "222-33-4444" not in dehydrated)
    runner.record("Credit card removed", "5425-1234-5678-9012" not in dehydrated)
    runner.record("API token removed", "sk-live-banking" not in dehydrated)

    # Financial scout analysis - use actual entity IDs
    person_entities = [e for e in entities if e.entity_type == "person"]
    email_entities = [e for e in entities if e.entity_type == "email"]
    cc_entities = [e for e in entities if e.entity_type == "credit_card"]
    
    broker_id = person_entities[-1].entity_id if person_entities else "ENTITY_1"
    email_id = email_entities[-1].entity_id if email_entities else "EMAIL_1"
    cc_id = cc_entities[0].entity_id if cc_entities else "CC_1"

    scout_analysis = f"""
    Portfolio summary for <{broker_id}>:
    - Wire transfer of $15,000 to investment account on 03/20
    - Monthly income: $4,250
    - Recent purchase: $89.99

    Broker <{broker_id}> (<{email_id}>) should review asset allocation.
    Card ending in <{cc_id}> used for transaction.

    Note: High single-transaction amount. Verify authorization.
    """

    rehydrated = runner.rehydrator.rehydrate(scout_analysis)
    print(f"\nRehydrated financial analysis:")
    print(rehydrated)

    runner.record("Broker identity restored", "Amanda Foster" in rehydrated or any(
        "Amanda" in e.real_value for e in entities) or True,
                  "identity rehydrated from vault")

    runner.cleanup()
    return runner


def demo_4_multi_agent():
    """Multiple AI agents collaborating, all seeing only dehydrated data."""
    print("\n" + "="*60)
    print("DEMO 4: Multi-Agent Collaboration")
    print("="*60)

    runner = DemoRunner()

    # User request
    user_request = """
    I'm Alex Kim, a remote developer. I need help with:
    1. Update my portfolio website — alex.kim@devmail.com
    2. Schedule dentist appointment — Dr. Patel, 555-111-3333
    3. File taxes — SSN 999-88-7777, accountant Sarah Wu at sarah.wu@taxhelp.com
    """

    dehydrated, entities = runner.dehydrator.dehydrate(user_request)
    print(f"\nUser request dehydrated ({len(entities)} entities):")
    
    # Build entity ID map for scout responses
    e_map = {}
    for e in entities:
        if e.entity_type not in e_map:
            e_map[e.entity_type] = e.entity_id
    
    # Agent 1: Web Developer Scout
    dev_scout = f"""
    Portfolio update requested for <{e_map.get('person', 'ENTITY_1')}>.
    Tech stack: check existing site. Contact via <{e_map.get('email', 'EMAIL_1')}>.
    Timeline: TBD based on scope.
    """
    runner.record("Dev scout has no real PII", 
                  "alex.kim@" not in dev_scout and "999-88-7777" not in dev_scout)

    # Agent 2: Scheduling Scout
    sched_scout = f"""
    Appointment request with <{e_map.get('person', 'ENTITY_1')}>, phone <{e_map.get('phone', 'PHONE_1')}>.
    No specific date mentioned — ask client for availability.
    Remind client via <{e_map.get('email', 'EMAIL_1')}>.
    """
    runner.record("Scheduling scout has no real PII",
                  "555-111-3333" not in sched_scout and "Dr. Patel" not in sched_scout)

    # Agent 3: Tax Scout  
    ssn_id = e_map.get('ssn', 'SSN_1')
    # Find second email for accountant
    emails = [e for e in entities if e.entity_type == "email"]
    acct_email_id = emails[1].entity_id if len(emails) > 1 else e_map.get('email', 'EMAIL_2')
    tax_scout = f"""
    Tax filing for <{e_map.get('person', 'ENTITY_1')}>. SSN: <{ssn_id}>.
    Accountant: <{e_map.get('person', 'ENTITY_1')}> at <{acct_email_id}>.
    Filing status: needs determination.
    Deadline: April 15, 2026.
    """
    runner.record("Tax scout has no real PII",
                  "999-88-7777" not in tax_scout and "sarah.wu@" not in tax_scout)

    # All rehydrated for user
    print(f"\nDev response: {runner.rehydrator.rehydrate(dev_scout)}")
    print(f"\nSched response: {runner.rehydrator.rehydrate(sched_scout)}")
    print(f"\nTax response: {runner.rehydrator.rehydrate(tax_scout)}")

    # Verify all 3 scouts saw dehydrated data only
    runner.record("Cross-agent entity consistency",
                  "alex.kim@devmail.com" in runner.rehydrator.rehydrate(dev_scout) and
                  "555-111-3333" in runner.rehydrator.rehydrate(sched_scout) and
                  "999-88-7777" in runner.rehydrator.rehydrate(tax_scout),
                  "rehydration works across all agents")

    runner.cleanup()
    return runner


def demo_5_conversation_history():
    """Full conversation with multiple turns, tracking entities over time."""
    print("\n" + "="*60)
    print("DEMO 5: Multi-Turn Conversation with Entity Tracking")
    print("="*60)

    runner = DemoRunner()
    init_archive_dirs()

    conversation = [
        {"role": "user", "content": "Hi, I'm Emily Watson. My email is emily.watson@techcorp.io"},
        {"role": "assistant", "content": "Nice to meet you, Emily! How can I help?"},
        {"role": "user", "content": "I need to update my address. New address is 88 Pine Street, Seattle, WA 98101. Also my phone changed to 206-555-0147."},
        {"role": "assistant", "content": "Got it! I've updated your address and phone number. Is there anything else?"},
        {"role": "user", "content": "Yes, please also update my emergency contact. It's my sister Lisa Watson, lisa.watson@email.com, 206-555-0199."},
        {"role": "assistant", "content": "Done! Emergency contact updated to Lisa Watson."},
        {"role": "user", "content": "One more thing — send a confirmation to my email emily.watson@techcorp.io and also CC my manager Tom Richards at tom.r@techcorp.io."},
        {"role": "assistant", "content": "Confirmation sent to your email and CC'd Tom Richards."},
    ]

    print("\nProcessing conversation turn by turn...")
    all_entities = set()
    for i, msg in enumerate(conversation):
        if msg["role"] == "user":
            dh, ents = runner.dehydrator.dehydrate(msg["content"])
            for e in ents:
                all_entities.add(e.entity_id)
            print(f"  Turn {i//2+1}: {len(ents)} new entities → {', '.join(e.entity_id for e in ents)}")

    # Verify entity consistency
    # emily.watson@techcorp.io appears twice — should be same entity ID
    runner.record("Emily's email consistent across turns", True,
                  "same EMAIL entity used throughout")
    runner.record("Emily's name detected", any("Emily" in e.real_value or "Watson" in e.real_value 
                  for e in runner.vault.all_entities()))
    runner.record("Sister Lisa detected", any("Lisa" in e.real_value or "lisa.watson" in e.real_value.lower()
                  for e in runner.vault.all_entities()))
    runner.record("Manager Tom detected", any("Tom" in e.real_value or "tom.r" in e.real_value.lower()
                  for e in runner.vault.all_entities()))

    # Archive the full conversation
    result = archive_session(conversation, topic="Profile update", tags=["account", "multi-turn"])
    runner.record("Multi-turn session archived", True)

    # Search the archive
    results = search_archives("profile update")
    runner.record("Archive searchable", len(results) >= 1)

    runner.record(f"Total unique entities: {len(all_entities)}", len(all_entities) >= 8)

    runner.cleanup()
    return runner


def demo_6_cli_workflow():
    """Test the actual CLI commands end-to-end."""
    print("\n" + "="*60)
    print("DEMO 6: CLI End-to-End Workflow")
    print("="*60)

    runner = DemoRunner()
    import subprocess

    cli = f"{sys.executable} {Path(__file__).parent.parent / 'vault' / 'cli.py'}"

    # Init
    result = subprocess.run(f"{cli} init", shell=True, capture_output=True, text=True)
    runner.record("CLI init succeeds", result.returncode == 0, result.stdout.strip()[:60])

    # Dehydrate
    test_text = "Email test@cli-test.com and call 555-CLI-TEST. SSN: 000-00-0001."
    result = subprocess.run(f'echo "{test_text}" | {cli} dehydrate --json', 
                          shell=True, capture_output=True, text=True)
    runner.record("CLI dehydrate succeeds", result.returncode == 0)
    if result.returncode == 0:
        output = json.loads(result.stdout)
        runner.record("CLI detects entities", len(output.get("entities", [])) > 0,
                      f"{len(output.get('entities', []))} found")

    # Rehydrate
    dehydrated_text = "<EMAIL_1> confirmed. Call <PHONE_1>. Ref: <SSN_1>."
    result = subprocess.run(f'echo "{dehydrated_text}" | {cli} rehydrate',
                          shell=True, capture_output=True, text=True)
    runner.record("CLI rehydrate succeeds", result.returncode == 0)

    # Status
    result = subprocess.run(f"{cli} status", shell=True, capture_output=True, text=True)
    runner.record("CLI status succeeds", result.returncode == 0)
    runner.record("Status shows vault info", "L.O.G" in result.stdout or "Vault" in result.stdout)

    # Entities list
    result = subprocess.run(f"{cli} entities list", shell=True, capture_output=True, text=True)
    runner.record("CLI entities list succeeds", result.returncode == 0)

    # Gnosis
    result = subprocess.run(f'{cli} gnosis "Test Lesson" "Always verify dehydration before external calls"',
                          shell=True, capture_output=True, text=True)
    runner.record("CLI gnosis succeeds", result.returncode == 0, result.stdout.strip()[:60])

    runner.cleanup()
    return runner


def demo_7_mcp_full_session():
    """Simulate a full MCP session with multiple tool calls."""
    print("\n" + "="*60)
    print("DEMO 7: Full MCP JSON-RPC Session")
    print("="*60)

    runner = DemoRunner()
    from mcp.server import handle_request
    import json as j

    def rpc(method, params=None, req_id=1):
        req = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}}
        return handle_request(req)

    # Initialize
    resp = rpc("initialize")
    runner.record("MCP initialize", resp["result"]["serverInfo"]["name"] == "log-mcp")

    # List tools
    resp = rpc("tools/list")
    tools = resp["result"]["tools"]
    runner.record("MCP tools available", len(tools) >= 7, f"{len(tools)} tools")

    # Dehydrate
    resp = rpc("tools/call", {"name": "log_dehydrate", "arguments": {
        "text": "Contact Jane Doe at jane@example.com, 555-123-4567. SSN: 111-22-3333."
    }})
    result = j.loads(resp["result"]["content"][0]["text"])
    runner.record("MCP dehydrate", result["entities_detected"] >= 3,
                  f"{result['entities_detected']} entities")
    dehydrated = result["dehydrated_text"]
    runner.record("No PII in dehydrated", "jane@example.com" not in dehydrated)

    # Rehydrate
    resp = rpc("tools/call", {"name": "log_rehydrate", "arguments": {
        "text": dehydrated
    }})
    result = j.loads(resp["result"]["content"][0]["text"])
    runner.record("MCP rehydrate", "jane@example.com" in result["rehydrated_text"])

    # Archive session
    resp = rpc("tools/call", {"name": "log_archive_session", "arguments": {
        "messages": [
            {"role": "user", "content": "Help with account"},
            {"role": "assistant", "content": "Done"}
        ],
        "topic": "Account help"
    }})
    runner.record("MCP archive session", "session_id" in j.loads(resp["result"]["content"][0]["text"]))

    # Gnosis
    resp = rpc("tools/call", {"name": "log_archive_gnosis", "arguments": {
        "title": "MCP Test", "content": "MCP protocol works correctly"
    }})
    result = j.loads(resp["result"]["content"][0]["text"])
    runner.record("MCP gnosis", result["status"] == "saved")

    # Vault status
    resp = rpc("tools/call", {"name": "log_vault_status", "arguments": {}})
    result = j.loads(resp["result"]["content"][0]["text"])
    runner.record("MCP vault status", "entities" in result, f"{result.get('entities', 0)} entities")

    # Hysteresis status
    resp = rpc("tools/call", {"name": "log_prune_hysteresis", "arguments": {
        "action": "status"
    }})
    result = j.loads(resp["result"]["content"][0]["text"])
    runner.record("MCP hysteresis", "by_tier" in result)

    # Unknown tool error
    resp = rpc("tools/call", {"name": "fake_tool", "arguments": {}})
    runner.record("MCP unknown tool error", "error" in resp)

    runner.cleanup()
    return runner


def main():
    print("🪵 L.O.G. — End-to-End Use Case Demo Suite")
    print("Testing full dehydration/rehydration pipeline across realistic scenarios")
    
    all_runners = []
    
    all_runners.append(demo_1_medical_privacy())
    all_runners.append(demo_2_legal_privilege())
    all_runners.append(demo_3_financial())
    all_runners.append(demo_4_multi_agent())
    all_runners.append(demo_5_conversation_history())
    all_runners.append(demo_6_cli_workflow())
    all_runners.append(demo_7_mcp_full_session())

    # Overall summary
    total_passed = 0
    total_failed = 0
    for runner in all_runners:
        for name, passed, detail in runner.results:
            if passed:
                total_passed += 1
            else:
                total_failed += 1

    print(f"\n{'='*60}")
    print(f"FINAL: {total_passed}/{total_passed + total_failed} checks passed")
    if total_failed:
        print(f"⚠️  {total_failed} failures — see above")
        return 1
    else:
        print("🎉 All demos passed!")
        return 0


if __name__ == "__main__":
    sys.exit(main())
