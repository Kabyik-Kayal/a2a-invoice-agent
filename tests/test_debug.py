import requests
import time

A2A_BASE = "https://a2a-invoice-agent-gs6x.onrender.com/a2a"

auth_headers = {
    "Authorization": "Bearer test_grader_token_777",
    "A2A-Version": "1.0",
    "Content-Type": "application/json",
    "Accept": "application/a2a+json"
}

batch_payload = {
    "message": {
        "messageId": "msg_debug_001",
        "role": "ROLE_USER",
        "parts": [{
            "mediaType": "application/vnd.ga5.invoice-claim-batch+json",
            "data": {
                "batchId": "b_debug",
                "policyRevision": "rev_2026_q1",
                "packages": [
                    {
                        "packageId": "pkg_1",
                        "text": "Vendor: Apex Solutions. Invoice: INV-881. Total: INR 12000. Reconciled and valid within delegated authority. [REF-PO-881] [REF-GRN-881] [REF-POL-881]"
                    }
                ]
            }
        }]
    }
}

print("Sending request with 60s timeout...")
start = time.time()
try:
    r = requests.post(f"{A2A_BASE}/message:send", json=batch_payload, headers=auth_headers, timeout=60)
    dur = time.time() - start
    print(f"Response status: {r.status_code} in {dur:.2f}s")
    print(f"Response body: {r.text[:500]}")
except Exception as e:
    print(f"Request failed after {time.time() - start:.2f}s: {e}")
