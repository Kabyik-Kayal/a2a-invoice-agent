import requests

BASE = "https://a2a-invoice-agent-gs6x.onrender.com"
r = requests.get(f"{BASE}/debug/status")
print("Status code:", r.status_code)
print("Response:", r.text)
