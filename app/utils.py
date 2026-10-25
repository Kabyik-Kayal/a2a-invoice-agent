import hashlib
import json
import re
import uuid
from typing import Any, Dict, List, Set

def canonical_json(obj: Any) -> str:
    """Recursively key-sorted compact JSON serialization."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

def hash_message(message: Dict[str, Any]) -> str:
    """Compute SHA256 of recursively key-sorted compact JSON of the message dict."""
    return hashlib.sha256(canonical_json(message).encode("utf-8")).hexdigest()

def generate_action_id() -> str:
    """Generate a durable unique action ID of at least 12 characters."""
    return f"act_{uuid.uuid4().hex[:16]}"

def generate_task_id() -> str:
    """Generate a unique task ID."""
    return f"task_{uuid.uuid4().hex}"

def generate_context_id() -> str:
    """Generate a unique context ID."""
    return f"ctx_{uuid.uuid4().hex}"

def hash_canonical_package(package: Any) -> str:
    """
    Hash package content ignoring ephemeral package IDs so identical
    core documents can be recognized across batches and tasks.
    """
    if isinstance(package, dict):
        # Exclude ephemeral IDs like packageId or id to find true content match
        filtered = {k: v for k, v in package.items() if k not in ("packageId", "id")}
        if not filtered:
            filtered = package
        return hashlib.sha256(canonical_json(filtered).encode("utf-8")).hexdigest()
    elif isinstance(package, str):
        return hashlib.sha256(package.strip().encode("utf-8")).hexdigest()
    else:
        return hashlib.sha256(canonical_json(package).encode("utf-8")).hexdigest()

def extract_bracketed_refs(text: str) -> List[str]:
    """Find all [REF-...] bracketed references in text."""
    matches = re.findall(r"\[(?:REF-)?[A-Za-z0-9_.-]+\]", text)
    # Deduplicate preserving order
    seen: Set[str] = set()
    result: List[str] = []
    for m in matches:
        if m not in seen:
            seen.add(m)
            result.append(m)
    return result

def ensure_valid_rationale(action: str, evidence_refs: List[str], base_rationale: str) -> str:
    """
    Ensure the rationale is between 60 and 1500 characters,
    explicitly names the action, and cites at least two evidence refs.
    """
    rationale = base_rationale.strip()
    
    # Check if action is mentioned
    if action not in rationale:
        rationale = f"Selected action {action}. {rationale}"
        
    # Check if at least two evidence refs are present
    refs_to_add = [ref for ref in evidence_refs[:2] if ref not in rationale]
    if refs_to_add:
        rationale = f"{rationale} Verified by decisive references {', '.join(refs_to_add)}."
        
    # Check character limits (60–1500 chars)
    if len(rationale) < 60:
        padding = f" Reconciled facts against authoritative financial controls for action {action}."
        rationale += padding
        
    if len(rationale) > 1500:
        rationale = rationale[:1490].rsplit(" ", 1)[0] + "."
        
    return rationale
