import requests
import json
import sys

BASE = "https://a2a-invoice-agent-gs6x.onrender.com"
A2A_BASE = f"{BASE}/a2a"

print(f"Testing live server: {BASE}")

# 1. Agent card
r = requests.get(f"{BASE}/.well-known/agent-card.json", timeout=15)
assert r.status_code == 200, f"Agent card failed: {r.status_code}"
card = r.json()
assert card["supportedInterfaces"][0]["url"] == f"{A2A_BASE}/", f"Url mismatch: {card['supportedInterfaces']}"
print("[OK] Agent card verified")

# 2. Auth & Version
r = requests.post(f"{A2A_BASE}/message:send", json={}, headers={"A2A-Version": "1.0"}, timeout=15)
assert r.status_code == 401, f"Expected 401 for missing auth, got {r.status_code}"
print("[OK] 401 on missing auth verified")

r = requests.post(f"{A2A_BASE}/message:send", json={}, headers={"Authorization": "Bearer tok_test_123"}, timeout=15)
assert r.status_code == 400, f"Expected 400 for missing version, got {r.status_code}"
print("[OK] 400 on missing A2A-Version verified")

# 3. Initial batch
auth_headers = {
    "Authorization": "Bearer test_grader_token_777",
    "A2A-Version": "1.0",
    "Content-Type": "application/json",
    "Accept": "application/a2a+json"
}

batch_payload = {
    "message": {
        "messageId": "msg_live_test_01",
        "role": "ROLE_USER",
        "parts": [{
            "mediaType": "application/vnd.ga5.invoice-claim-batch+json",
            "data": {
                "batchId": "b_live_01",
                "policyRevision": "rev_2026_q1",
                "packages": [
                    {
                        "packageId": "pkg_live_1",
                        "text": "Vendor: Apex Solutions. Invoice: INV-881. Total: INR 12000. Reconciled and valid within delegated authority. [REF-PO-881] [REF-GRN-881] [REF-POL-881]"
                    }
                ]
            }
        }]
    }
}

r = requests.post(f"{A2A_BASE}/message:send", json=batch_payload, headers=auth_headers, timeout=25)
assert r.status_code == 200, f"Batch send failed: {r.status_code} {r.text}"
res = r.json()
task = res["task"]
assert task["state"] == "TASK_STATE_INPUT_REQUIRED", f"Unexpected state: {task['state']}"
task_id = task["id"]
context_id = task["contextId"]
proposals = task["artifacts"][0]["data"]["proposals"]
assert len(proposals) == 1
prop = proposals[0]
assert len(prop["actionId"]) >= 12
assert len(prop["evidenceRefs"]) == 3
print(f"[OK] Initial batch proposal verified (Action: {prop['action']}, State: {task['state']})")

# 4. Continuation Results
cont_payload = {
    "message": {
        "messageId": "msg_live_cont_01",
        "taskId": task_id,
        "contextId": context_id,
        "role": "ROLE_USER",
        "parts": [{
            "mediaType": "application/vnd.ga5.invoice-action-results+json",
            "data": {
                "batchId": "b_live_01",
                "results": [{
                    "packageId": prop["packageId"],
                    "actionId": prop["actionId"],
                    "action": prop["action"],
                    "outcome": "ACCEPTED",
                    "receiptNonce": "live_nonce_xyz123"
                }]
            }
        }]
    }
}

r = requests.post(f"{A2A_BASE}/message:send", json=cont_payload, headers=auth_headers, timeout=25)
assert r.status_code == 200, f"Continuation failed: {r.status_code} {r.text}"
completed_task = r.json()["task"]
assert completed_task["state"] == "TASK_STATE_COMPLETED"
assert len(completed_task["artifacts"]) == 2
executions = completed_task["artifacts"][1]["data"]["executions"]
assert len(executions) == 1
assert executions[0]["receiptNonce"] == "live_nonce_xyz123"
print(f"[OK] Continuation completed verified (State: {completed_task['state']}, Receipts: {len(executions)})")

# 5. User isolation
outsider_headers = {
    "Authorization": "Bearer outsider_token_999",
    "A2A-Version": "1.0"
}
r = requests.get(f"{A2A_BASE}/tasks/{task_id}", headers=outsider_headers, timeout=15)
assert r.status_code == 404, f"Expected 404 for outsider, got {r.status_code}"
print("[OK] User isolation verified: outsider received 404 Generic")

print("\nALL LIVE REMOTE TESTS PASSED! READY FOR GRADER.")
