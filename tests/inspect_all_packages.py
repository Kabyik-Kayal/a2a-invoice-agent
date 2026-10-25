import json

path = r"C:/Users/screw/.gemini/antigravity/brain/74c8f590-4a68-4dce-befd-f6acdfa8783e/.system_generated/steps/358/output.txt"
with open(path, "rb") as f:
    raw = f.read()

idx = raw.find(b'{"has_gemini_key"')
data = json.loads(raw[idx:].decode("utf-8", errors="ignore"), strict=False)
t0 = data["tasks"][0]
packages = t0["history"][0]["parts"][0]["data"]["packages"]

for i, pkg in enumerate(packages):
    pkg_id = pkg["packageId"]
    print(f"\n==================== PACKAGE {i}: {pkg_id} ====================")
    for doc in pkg.get("documents", []):
        dname = doc.get("name")
        lines = [line.strip() for line in doc.get("text", "").split("\n") if line.strip()]
        for line in lines:
            # If line has multiple references or keywords
            refs = [w for w in line.split() if w.startswith("[R_")]
            if len(refs) >= 2 or any(k in line.lower() for k in ["reconciled", "duplicate", "exception", "approval", "hold", "authority"]):
                print(f"[{dname}] {line}\n")
