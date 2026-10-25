import os
import pytest
from fastapi.testclient import TestClient

# Use temporary test database
os.environ["DATABASE_PATH"] = "test_invoice_agent.db"

from app.main import app
from app.db import db
from app.models import (
    MEDIA_A2A_JSON,
    MEDIA_BATCH_INPUT,
    MEDIA_PROPOSALS,
    MEDIA_RESULTS_INPUT,
    MEDIA_RECEIPTS,
    TASK_STATE_INPUT_REQUIRED,
    TASK_STATE_COMPLETED,
    TASK_STATE_CANCELED,
)

client = TestClient(app)

AUTH_HEADER_ALICE = {
    "Authorization": "Bearer token_alice_123",
    "A2A-Version": "1.0",
    "Content-Type": "application/a2a+json",
}
AUTH_HEADER_BOB = {
    "Authorization": "Bearer token_bob_456",
    "A2A-Version": "1.0",
    "Content-Type": "application/a2a+json",
}

@pytest.fixture(autouse=True)
def clean_db():
    # Clean tables before each test
    with db._get_connection() as conn:
        conn.execute("DELETE FROM idempotency;")
        conn.execute("DELETE FROM tasks;")
        conn.execute("DELETE FROM decision_cache;")
    yield

def test_agent_card():
    resp = client.get("/.well-known/agent-card.json")
    assert resp.status_code == 200
    card = resp.json()
    assert card["name"]
    assert card["description"]
    assert card["version"]
    assert isinstance(card["capabilities"], dict)
    assert MEDIA_BATCH_INPUT in card["defaultInputModes"]
    assert MEDIA_PROPOSALS in card["defaultOutputModes"]
    assert MEDIA_RECEIPTS in card["defaultOutputModes"]
    assert any(s["id"] == "invoice_action_agent" for s in card["skills"])
    assert len(card["supportedInterfaces"]) >= 1

def test_auth_and_version_checks():
    # Missing auth on POST /message:send
    resp = client.post("/a2a/message:send", json={}, headers={"A2A-Version": "1.0", "Content-Type": "application/a2a+json"})
    assert resp.status_code == 401

    # Missing auth on GET /tasks (LIST_AUTH_REQUIRED)
    resp_list_no_auth = client.get("/a2a/tasks")
    assert resp_list_no_auth.status_code == 401

    # Missing A2A-Version
    resp = client.post("/a2a/message:send", json={}, headers={"Authorization": "Bearer token123", "Content-Type": "application/a2a+json"})
    assert resp.status_code == 400

    # Wrong A2A-Version
    resp = client.post("/a2a/message:send", json={}, headers={"Authorization": "Bearer token123", "A2A-Version": "2.0", "Content-Type": "application/a2a+json"})
    assert resp.status_code == 400

    # Unsupported Media Type (A2A_MEDIA_TYPE_REJECTION)
    resp_wrong_ct = client.post("/a2a/message:send", json={}, headers={"Authorization": "Bearer token123", "A2A-Version": "1.0", "Content-Type": "application/json"})
    assert resp_wrong_ct.status_code == 415

    resp_xml = client.post("/a2a/message:send", data="<xml></xml>", headers={"Authorization": "Bearer token123", "A2A-Version": "1.0", "Content-Type": "application/xml"})
    assert resp_xml.status_code == 415


def test_initial_batch_submission_and_proposals():
    payload = {
        "message": {
            "messageId": "msg_init_001",
            "role": "ROLE_USER",
            "parts": [
                {
                    "mediaType": MEDIA_BATCH_INPUT,
                    "data": {
                        "batchId": "batch_test_001",
                        "policyRevision": "rev_2026_q1",
                        "packages": [
                            {
                                "packageId": "pkg_01",
                                "text": "Vendor: Global Supplies Ltd. Invoice: INV-1001. Amount: INR 5000. Reconciled and valid within limits. [REF-PO-1] [REF-GRN-1] [REF-POL-1]"
                            },
                            {
                                "packageId": "pkg_02",
                                "text": "Vendor: Heavy Machinery Corp. Invoice: INV-9999. Amount: INR 950000. Outside delegated authority limit. [REF-PO-2] [REF-AUTH-2] [REF-POL-2]"
                            }
                        ]
                    }
                }
            ]
        }
    }

    resp = client.post("/a2a/message:send", json=payload, headers=AUTH_HEADER_ALICE)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == MEDIA_A2A_JSON
    body = resp.json()
    assert "task" in body
    task = body["task"]
    assert task["state"] == TASK_STATE_INPUT_REQUIRED
    assert len(task["history"]) == 1
    assert len(task["artifacts"]) == 1

    art = task["artifacts"][0]
    assert art["mediaType"] == MEDIA_PROPOSALS
    proposals = art["data"]["proposals"]
    assert len(proposals) == 2

    # Check proposal properties
    for prop in proposals:
        assert len(prop["actionId"]) >= 12
        assert prop["action"] in ["settle_invoice", "request_approval", "hold_invoice", "reject_duplicate", "open_exception"]
        assert len(prop["evidenceRefs"]) == 3
        assert 60 <= len(prop["rationale"]) <= 1500
        assert prop["action"] in prop["rationale"]

def test_idempotency_replay_and_conflict():
    payload1 = {
        "message": {
            "messageId": "msg_idemp_01",
            "role": "ROLE_USER",
            "parts": [
                {
                    "mediaType": MEDIA_BATCH_INPUT,
                    "data": {
                        "batchId": "batch_idemp",
                        "packages": [{"packageId": "p1", "text": "Invoice test [REF-1] [REF-2] [REF-3]"}]
                    }
                }
            ]
        },
        "configuration": {"returnImmediately": False}
    }

    # First request
    resp1 = client.post("/a2a/message:send", json=payload1, headers=AUTH_HEADER_ALICE)
    assert resp1.status_code == 200
    task1 = resp1.json()["task"]

    # Replay with altered configuration & reordered keys (same message)
    payload_replay = {
        "configuration": {"returnImmediately": True},
        "message": {
            "parts": payload1["message"]["parts"],
            "role": "ROLE_USER",
            "messageId": "msg_idemp_01"
        }
    }
    resp2 = client.post("/a2a/message:send", json=payload_replay, headers=AUTH_HEADER_ALICE)
    assert resp2.status_code == 200
    task2 = resp2.json()["task"]
    assert task1["id"] == task2["id"]

    # Reusing messageId with modified content should return 409 IDEMPOTENCY_CONFLICT
    payload_conflict = {
        "message": {
            "messageId": "msg_idemp_01",
            "role": "ROLE_USER",
            "parts": [
                {
                    "mediaType": MEDIA_BATCH_INPUT,
                    "data": {
                        "batchId": "batch_idemp_changed",
                        "packages": [{"packageId": "p_different", "text": "Different invoice"}]
                    }
                }
            ]
        }
    }
    resp3 = client.post("/a2a/message:send", json=payload_conflict, headers=AUTH_HEADER_ALICE)
    assert resp3.status_code == 409
    assert "IDEMPOTENCY_CONFLICT" in resp3.text

def test_continuation_lifecycle_and_execution_receipts():
    # 1. Propose
    init_payload = {
        "message": {
            "messageId": "msg_batch_100",
            "role": "ROLE_USER",
            "parts": [
                {
                    "mediaType": MEDIA_BATCH_INPUT,
                    "data": {
                        "batchId": "b_100",
                        "packages": [
                            {"packageId": "pkg_A", "text": "Invoice A [REF-A1] [REF-A2] [REF-A3]"},
                            {"packageId": "pkg_B", "text": "Invoice B [REF-B1] [REF-B2] [REF-B3]"}
                        ]
                    }
                }
            ]
        }
    }
    resp1 = client.post("/a2a/message:send", json=init_payload, headers=AUTH_HEADER_ALICE)
    assert resp1.status_code == 200
    task = resp1.json()["task"]
    task_id = task["id"]
    ctx_id = task["contextId"]
    proposals = task["artifacts"][0]["data"]["proposals"]
    prop_a = proposals[0]
    prop_b = proposals[1]

    # 2. Continue with results (A accepted, B rejected)
    continuation_payload = {
        "message": {
            "messageId": "msg_results_100",
            "taskId": task_id,
            "contextId": ctx_id,
            "role": "ROLE_USER",
            "parts": [
                {
                    "mediaType": MEDIA_RESULTS_INPUT,
                    "data": {
                        "batchId": "b_100",
                        "results": [
                            {
                                "packageId": prop_a["packageId"],
                                "actionId": prop_a["actionId"],
                                "action": prop_a["action"],
                                "outcome": "ACCEPTED",
                                "receiptNonce": "nonce_alice_999"
                            },
                            {
                                "packageId": prop_b["packageId"],
                                "actionId": prop_b["actionId"],
                                "action": prop_b["action"],
                                "outcome": "REJECTED",
                                "receiptNonce": "nonce_ignored"
                            }
                        ]
                    }
                }
            ]
        }
    }

    resp2 = client.post("/a2a/message:send", json=continuation_payload, headers=AUTH_HEADER_ALICE)
    assert resp2.status_code == 200
    completed_task = resp2.json()["task"]
    assert completed_task["state"] == TASK_STATE_COMPLETED
    assert len(completed_task["artifacts"]) == 2

    # Preserves proposal artifact
    assert completed_task["artifacts"][0]["mediaType"] == MEDIA_PROPOSALS
    # Adds receipts artifact
    receipts_art = completed_task["artifacts"][1]
    assert receipts_art["mediaType"] == MEDIA_RECEIPTS
    executions = receipts_art["data"]["executions"]
    assert len(executions) == 1
    assert executions[0]["packageId"] == prop_a["packageId"]
    assert executions[0]["receiptNonce"] == "nonce_alice_999"

def test_continuation_mismatch_rejection():
    # Submit initial
    init_payload = {
        "message": {
            "messageId": "msg_mismatch",
            "role": "ROLE_USER",
            "parts": [{
                "mediaType": MEDIA_BATCH_INPUT,
                "data": {
                    "batchId": "b_orig",
                    "packages": [{"packageId": "pkg_1", "text": "Invoice test [REF-1] [REF-2] [REF-3]"}]
                }
            }]
        }
    }
    task = client.post("/a2a/message:send", json=init_payload, headers=AUTH_HEADER_ALICE).json()["task"]

    # Send continuation with wrong actionId
    bad_continuation = {
        "message": {
            "messageId": "msg_bad_cont",
            "taskId": task["id"],
            "contextId": task["contextId"],
            "role": "ROLE_USER",
            "parts": [{
                "mediaType": MEDIA_RESULTS_INPUT,
                "data": {
                    "batchId": "b_orig",
                    "results": [{
                        "packageId": "pkg_1",
                        "actionId": "wrong_action_id_123",
                        "action": "settle_invoice",
                        "outcome": "ACCEPTED",
                        "receiptNonce": "nonce_123"
                    }]
                }
            }]
        }
    }
    resp = client.post("/a2a/message:send", json=bad_continuation, headers=AUTH_HEADER_ALICE)
    assert resp.status_code == 400

def test_cancel_versus_result_conflict():
    # Submit task
    init_payload = {
        "message": {
            "messageId": "msg_race",
            "role": "ROLE_USER",
            "parts": [{
                "mediaType": MEDIA_BATCH_INPUT,
                "data": {
                    "batchId": "b_race",
                    "packages": [{"packageId": "pkg_r", "text": "Invoice [REF-1] [REF-2] [REF-3]"}]
                }
            }]
        }
    }
    task = client.post("/a2a/message:send", json=init_payload, headers=AUTH_HEADER_ALICE).json()["task"]
    task_id = task["id"]

    # Cancel task
    cancel_resp = client.post(f"/a2a/tasks/{task_id}:cancel", headers=AUTH_HEADER_ALICE)
    assert cancel_resp.status_code == 200
    canceled_task = cancel_resp.json()
    assert canceled_task["state"] == TASK_STATE_CANCELED

    # Subsequent cancel returns 409
    cancel_again = client.post(f"/a2a/tasks/{task_id}:cancel", headers=AUTH_HEADER_ALICE)
    assert cancel_again.status_code == 409

    # Result continuation on canceled task returns 409
    prop = task["artifacts"][0]["data"]["proposals"][0]
    cont = {
        "message": {
            "messageId": "msg_cont_late",
            "taskId": task_id,
            "contextId": task["contextId"],
            "role": "ROLE_USER",
            "parts": [{
                "mediaType": MEDIA_RESULTS_INPUT,
                "data": {
                    "batchId": "b_race",
                    "results": [{
                        "packageId": prop["packageId"],
                        "actionId": prop["actionId"],
                        "action": prop["action"],
                        "outcome": "ACCEPTED",
                        "receiptNonce": "n1"
                    }]
                }
            }]
        }
    }
    cont_resp = client.post("/a2a/message:send", json=cont, headers=AUTH_HEADER_ALICE)
    assert cont_resp.status_code == 409

def test_user_isolation():
    # Alice creates a task
    init_payload = {
        "message": {
            "messageId": "msg_alice_priv",
            "role": "ROLE_USER",
            "parts": [{
                "mediaType": MEDIA_BATCH_INPUT,
                "data": {
                    "batchId": "b_priv",
                    "packages": [{"packageId": "p_alice", "text": "Private Alice [REF-1] [REF-2] [REF-3]"}]
                }
            }]
        }
    }
    alice_task = client.post("/a2a/message:send", json=init_payload, headers=AUTH_HEADER_ALICE).json()["task"]
    alice_task_id = alice_task["id"]

    # Bob attempts to get Alice's task -> 404 (generic)
    bob_get = client.get(f"/a2a/tasks/{alice_task_id}", headers=AUTH_HEADER_BOB)
    assert bob_get.status_code == 404
    assert "Task not found" in bob_get.text

    # Bob attempts to cancel Alice's task -> 404
    bob_cancel = client.post(f"/a2a/tasks/{alice_task_id}:cancel", headers=AUTH_HEADER_BOB)
    assert bob_cancel.status_code == 404

    # Bob lists tasks -> empty
    bob_list = client.get("/a2a/tasks", headers=AUTH_HEADER_BOB)
    assert bob_list.status_code == 200
    assert bob_list.json() == {"tasks": []}

    # Alice lists tasks -> contains her task
    alice_list = client.get("/a2a/tasks", headers=AUTH_HEADER_ALICE)
    assert alice_list.status_code == 200
    assert len(alice_list.json()["tasks"]) == 1
    assert alice_list.json()["tasks"][0]["id"] == alice_task_id

def test_canonical_decision_caching():
    # Same canonical package text submitted under two completely different batchId, messageId, packageId namespaces
    payload1 = {
        "message": {
            "messageId": "msg_run1",
            "role": "ROLE_USER",
            "parts": [{
                "mediaType": MEDIA_BATCH_INPUT,
                "data": {
                    "batchId": "b_run1",
                    "packages": [{"packageId": "pkg_orig_1", "text": "Vendor: Acme Corp. Duplicate payment record already paid. [REF-DUP-1] [REF-DUP-2] [REF-DUP-3]"}]
                }
            }]
        }
    }
    resp1 = client.post("/a2a/message:send", json=payload1, headers=AUTH_HEADER_ALICE)
    assert resp1.status_code == 200
    prop1 = resp1.json()["task"]["artifacts"][0]["data"]["proposals"][0]
    assert prop1["action"] == "reject_duplicate"

    # Second run with different messageId, batchId, and packageId, but identical package text
    payload2 = {
        "message": {
            "messageId": "msg_run2",
            "role": "ROLE_USER",
            "parts": [{
                "mediaType": MEDIA_BATCH_INPUT,
                "data": {
                    "batchId": "b_run2",
                    "packages": [{"packageId": "pkg_namespaced_2", "text": "Vendor: Acme Corp. Duplicate payment record already paid. [REF-DUP-1] [REF-DUP-2] [REF-DUP-3]"}]
                }
            }]
        }
    }
    resp2 = client.post("/a2a/message:send", json=payload2, headers=AUTH_HEADER_ALICE)
    assert resp2.status_code == 200
    prop2 = resp2.json()["task"]["artifacts"][0]["data"]["proposals"][0]
    
    # Decisions match from cache
    assert prop2["action"] == prop1["action"]
    assert prop2["facts"] == prop1["facts"]
    assert prop2["evidenceRefs"] == prop1["evidenceRefs"]
    # But durable actionId and packageId reflect current run
    assert prop2["packageId"] == "pkg_namespaced_2"
    assert prop2["actionId"] != prop1["actionId"]

def test_flat_decision_normalization_and_caching():
    # Directly test db.save_cached_decision with flat format (like Groq/LLM responses)
    flat_decision = {
        "action": "open_exception",
        "vendorName": "Flat Vendor LLC",
        "invoiceNumber": "INV-FLAT-99",
        "amountMinor": 88000,
        "currency": "USD",
        "evidenceRefs": ["[REF-E1]", "[REF-E2]", "[REF-E3]"],
        "rationale": "Flat decision test open_exception [REF-E1] [REF-E2] verified against financial controls."
    }
    test_hash = "test_flat_hash_12345"
    db.save_cached_decision(test_hash, flat_decision)
    cached = db.get_cached_decision(test_hash)
    assert cached is not None
    assert cached["action"] == "open_exception"
    assert cached["facts"]["vendorName"] == "Flat Vendor LLC"
    assert cached["facts"]["invoiceNumber"] == "INV-FLAT-99"
    assert cached["facts"]["amountMinor"] == 88000
    assert cached["facts"]["currency"] == "USD"
    assert len(cached["evidenceRefs"]) == 3


