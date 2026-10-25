import json

with open("C:/Users/screw/.gemini/antigravity/brain/74c8f590-4a68-4dce-befd-f6acdfa8783e/.system_generated/steps/358/output.txt", "rb") as f:
    raw = f.read()

idx = raw.find(b'{"has_gemini_key"')
data = json.loads(raw[idx:].decode("utf-8", errors="ignore"), strict=False)

print(f"Total tasks in database: {len(data['tasks'])}")
for i, t in enumerate(data['tasks']):
    msg_id = t['history'][0].get('messageId', 'unknown') if t['history'] else 'none'
    num_parts = len(t['history'][0].get('parts', [])) if t['history'] else 0
    first_media = t['history'][0]['parts'][0]['mediaType'] if num_parts > 0 else 'none'
    artifacts = [a.get('mediaType') for a in t.get('artifacts', [])]
    print(f"Task {i}: id={t['id']} state={t['state']} created_at={t.get('created_at')} msgId={msg_id} media={first_media} artifacts={artifacts}")
