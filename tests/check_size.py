import json

with open("C:/Users/screw/.gemini/antigravity/brain/74c8f590-4a68-4dce-befd-f6acdfa8783e/.system_generated/steps/358/output.txt", "rb") as f:
    raw = f.read()

idx = raw.find(b'{"has_gemini_key"')
data = json.loads(raw[idx:].decode("utf-8", errors="ignore"), strict=False)
t0 = data["tasks"][0]
envelope = {"task": t0}

s1 = json.dumps(envelope)
s2 = json.dumps(envelope, separators=(",", ":"))

print(f"Default json.dumps size: {len(s1.encode('utf-8'))} bytes ({len(s1.encode('utf-8'))/1024:.2f} KiB)")
print(f"Compact json.dumps size: {len(s2.encode('utf-8'))} bytes ({len(s2.encode('utf-8'))/1024:.2f} KiB)")
print(f"Max allowed (512 KiB):   {512 * 1024} bytes (512.00 KiB)")
