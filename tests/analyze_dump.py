import json

path = r"C:/Users/screw/.gemini/antigravity/brain/74c8f590-4a68-4dce-befd-f6acdfa8783e/.system_generated/steps/358/output.txt"
with open(path, "rb") as f:
    raw = f.read()

idx = raw.find(b'{"has_gemini_key"')
if idx != -1:
    data = json.loads(raw[idx:].decode("utf-8", errors="ignore"), strict=False)
    print("Has gemini key:", data["has_gemini_key"])
    print("AI provider:", data["ai_provider"])
    print("Tasks count:", len(data["tasks"]))
    
    t0 = data["tasks"][0]
    batch_data = t0["history"][0]["parts"][0]["data"]
    print("BatchId:", batch_data["batchId"])
    print("PolicyRev:", batch_data.get("policyRevision"))
    packages = batch_data.get("packages", [])
    print(f"Num packages in batch: {len(packages)}")
    
    # Inspect first package
    pkg0 = packages[0]
    print("\n--- Package 0 ---")
    print("PackageId:", pkg0.get("packageId"))
    for doc in pkg0.get("documents", []):
        print(f"\nDocument [{doc.get('name')}]:")
        print(doc.get("text")[:600] + "...")
else:
    print("Could not find JSON in dump")
