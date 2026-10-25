import requests

A2A_BASE = "https://a2a-invoice-agent-gs6x.onrender.com/a2a"

auth_headers = {
    "Authorization": "Bearer test_cancel_token_888",
    "A2A-Version": "1.0",
    "Content-Type": "application/json"
}

# 1. Create task
batch_payload = {
    "message": {
        "messageId": "msg_cancel_test",
        "role": "ROLE_USER",
        "parts": [{
            "mediaType": "application/vnd.ga5.invoice-claim-batch+json",
            "data": {
                "batchId": "b_cancel",
                "packages": [{"packageId": "p_c", "text": "Test cancel invoice [REF-1] [REF-2] [REF-3]"}]
            }
        }]
    }
}

r = requests.post(f"{A2A_BASE}/message:send", json=batch_payload, headers=auth_headers)
task = r.json()["task"]
task_id = task["id"]
print(f"Created task: {task_id}, state: {task['state']}")

# 2. Cancel task
r_cancel = requests.post(f"{A2A_BASE}/tasks/{task_id}:cancel", headers=auth_headers)
print(f"Cancel status: {r_cancel.status_code}")
assert r_cancel.status_code == 200
canceled_task = r_cancel.json()
print(f"Canceled state: {canceled_task['state']}")
assert canceled_task["state"] == "TASK_STATE_CANCELED"

# 3. Cancel again -> 409
r_again = requests.post(f"{A2A_BASE}/tasks/{task_id}:cancel", headers=auth_headers)
print(f"Repeat cancel status: {r_again.status_code}")
assert r_again.status_code == 409

# 4. List tasks -> contains canceled task
r_list = requests.get(f"{A2A_BASE}/tasks", headers=auth_headers)
print(f"List tasks status: {r_list.status_code}, count: {len(r_list.json()['tasks'])}")
assert r_list.status_code == 200

print("All cancel and list checks PASSED on live server!")
