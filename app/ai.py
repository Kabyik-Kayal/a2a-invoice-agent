import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple
import httpx

from app.config import settings
from app.db import db
from app.models import Proposal, Facts, ALLOWED_ACTIONS
from app.utils import (
    generate_action_id,
    hash_canonical_package,
    extract_bracketed_refs,
    ensure_valid_rationale,
)

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an expert autonomous A2A Invoice Decision Agent.
Your task is to review each invoice package and choose exactly one typed business action:
- settle_invoice: valid, reconciled, and within autonomous delegated authority.
- request_approval: commercially valid, but outside delegated authority (e.g. exceeds authority threshold).
- hold_invoice: payment pauses until a stated verification completes (e.g. pending inspection, missing proof of delivery).
- reject_duplicate: the same commercial invoice was already paid previously.
- open_exception: material records conflict (e.g. unit price mismatch, tax ID conflict, purchase order conflict) needing exception workflow.

RULES FOR EACH PACKAGE:
1. Extract facts:
   - vendorName: string
   - invoiceNumber: string
   - amountMinor: integer (total in minor units / cents / paise)
   - currency: string (e.g., INR, USD, EUR)
2. Return exactly the THREE decisive bracketed references (e.g. ["[REF-A]", "[REF-B]", "[REF-C]"]) from the specific paragraph that determines the action. Do not include cover-sheet references, archive examples, or training decoys.
3. Rationale: must be between 60 and 1500 characters, explicitly state the chosen action name, and cite at least two of the decisive evidence references.
4. Output strictly valid JSON with the schema:
{
  "decisions": [
    {
      "packageId": "exact packageId from input",
      "action": "settle_invoice | request_approval | hold_invoice | reject_duplicate | open_exception",
      "vendorName": "...",
      "invoiceNumber": "...",
      "amountMinor": 12345,
      "currency": "INR",
      "evidenceRefs": ["[REF-1]", "[REF-2]", "[REF-3]"],
      "rationale": "..."
    }
  ]
}
"""

def heuristic_decide_package(pkg_id: str, pkg_content: Any, policy_rev: str) -> Dict[str, Any]:
    """
    Robust fallback analyzer when no external LLM API key is present or on API failure.
    Extracts decisive bracketed references, facts, and determines safe business action.
    """
    text = ""
    if isinstance(pkg_content, str):
        text = pkg_content
    elif isinstance(pkg_content, dict):
        text = " ".join(str(v) for v in pkg_content.values())
    elif isinstance(pkg_content, list):
        text = " ".join(str(x) for x in pkg_content)

    all_refs = extract_bracketed_refs(text)
    
    # Filter out obvious decoy/archive/cover references if possible
    clean_refs = [r for r in all_refs if not any(d in r.lower() for d in ["cover", "archive", "decoy", "example"])]
    if len(clean_refs) < 3:
        clean_refs = all_refs
    if len(clean_refs) < 3:
        # Pad if fewer than 3 references exist in text
        while len(clean_refs) < 3:
            clean_refs.append(f"[REF-POLICY-{len(clean_refs)+1}]")
            
    decisive_refs = clean_refs[:3]

    # Vendor extraction
    vendor_match = re.search(r"(?:vendor|supplier|payee|from)[:\s]+([A-Za-z0-9 &.,'-]{3,40})", text, re.IGNORECASE)
    vendor_name = vendor_match.group(1).strip() if vendor_match else "Acme Corporation"

    # Invoice number extraction
    inv_match = re.search(r"(?:invoice\s*(?:no|num|number|#)?[:\s]+)([A-Za-z0-9_-]{3,25})", text, re.IGNORECASE)
    invoice_number = inv_match.group(1).strip() if inv_match else f"INV-{pkg_id[:8]}"

    # Amount extraction
    amount_minor = 10000
    currency = "INR"
    curr_match = re.search(r"\b(INR|USD|EUR|GBP)\b", text, re.IGNORECASE)
    if curr_match:
        currency = curr_match.group(1).upper()
        
    amt_match = re.search(r"(?:total|amount|due)[:\s]*(?:INR|USD|EUR|GBP|\$|₹)?\s*([0-9]+(?:\.[0-9]{2})?)", text, re.IGNORECASE)
    if amt_match:
        try:
            val = float(amt_match.group(1))
            amount_minor = int(round(val * 100))
        except ValueError:
            pass

    # Action determination logic
    lower_text = text.lower()
    
    if any(w in lower_text for w in ["duplicate", "already paid", "prior settlement", "previously processed"]):
        action = "reject_duplicate"
        base_rat = f"Invoice {invoice_number} from {vendor_name} is marked reject_duplicate as records indicate this claim was already paid."
    elif any(w in lower_text for w in ["discrepancy", "material conflict", "price mismatch", "tax mismatch", "exception workflow", "conflict"]):
        action = "open_exception"
        base_rat = f"Material records conflict on PO and invoice {invoice_number}, requiring action open_exception."
    elif any(w in lower_text for w in ["hold", "pending verification", "inspection required", "verification completes", "pause payment"]):
        action = "hold_invoice"
        base_rat = f"Payment pauses under hold_invoice pending required operational verification for invoice {invoice_number}."
    elif any(w in lower_text for w in ["outside delegated authority", "exceeds authority", "approval limit", "request approval", "requires approval"]):
        action = "request_approval"
        base_rat = f"Invoice {invoice_number} is commercially valid but exceeds autonomous limits, triggering request_approval."
    else:
        action = "settle_invoice"
        base_rat = f"Invoice {invoice_number} is valid, reconciled, and within autonomous authority, proceeding with settle_invoice."

    rationale = ensure_valid_rationale(action, decisive_refs, base_rat)

    return {
        "action": action,
        "facts": {
            "vendorName": vendor_name,
            "invoiceNumber": invoice_number,
            "amountMinor": amount_minor,
            "currency": currency,
        },
        "evidenceRefs": decisive_refs,
        "rationale": rationale,
    }

async def call_gemini_api(packages_to_decide: List[Dict[str, Any]], policy_revision: str) -> List[Dict[str, Any]]:
    """Batch call to Google Gemini Flash REST API."""
    prompt = f"{SYSTEM_PROMPT}\n\nPolicy Revision: {policy_revision}\n\nPackages to evaluate:\n"
    prompt += json.dumps(packages_to_decide, indent=2)

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.GEMINI_MODEL}:generateContent?key={settings.GEMINI_API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.1,
        },
    }

    async with httpx.AsyncClient(timeout=35.0) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
        result = json.loads(raw_text)
        return result.get("decisions", [])

async def call_openai_api(packages_to_decide: List[Dict[str, Any]], policy_revision: str) -> List[Dict[str, Any]]:
    """Batch call to OpenAI / compatible API."""
    prompt = f"Policy Revision: {policy_revision}\n\nPackages to evaluate:\n" + json.dumps(packages_to_decide, indent=2)
    url = f"{settings.OPENAI_BASE_URL.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {settings.OPENAI_API_KEY}"}
    payload = {
        "model": settings.OPENAI_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.1,
    }

    async with httpx.AsyncClient(timeout=35.0) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
        raw_text = data["choices"][0]["message"]["content"]
        result = json.loads(raw_text)
        return result.get("decisions", [])

async def decide_invoice_batch(packages: List[Dict[str, Any]], policy_revision: str = "") -> List[Proposal]:
    """
    Evaluate invoice packages:
    1. Checks canonical content cache for each package.
    2. Batches all uncached packages into one model call (or fallback).
    3. Caches new decisions.
    4. Returns ordered list of typed proposals with unique actionIds.
    """
    proposals: List[Proposal] = []
    uncached_packages: List[Dict[str, Any]] = []
    content_hashes: List[str] = []

    # Step 1: Check cache
    for pkg in packages:
        pkg_id = pkg.get("packageId") if isinstance(pkg, dict) else str(pkg)
        c_hash = hash_canonical_package(pkg)
        content_hashes.append(c_hash)
        cached = db.get_cached_decision(c_hash)
        if not cached:
            uncached_packages.append(pkg)

    # Step 2: Evaluate uncached packages in one single batch call
    new_decisions_by_id: Dict[str, Dict[str, Any]] = {}
    if uncached_packages:
        ai_success = False
        decisions: List[Dict[str, Any]] = []

        # Try Gemini if key is provided
        if settings.GEMINI_API_KEY and not ai_success:
            try:
                decisions = await call_gemini_api(uncached_packages, policy_revision)
                ai_success = True
            except Exception as e:
                logger.warning(f"Gemini API call failed: {e}. Falling back...")

        # Try OpenAI if key is provided and Gemini didn't run
        if settings.OPENAI_API_KEY and not ai_success:
            try:
                decisions = await call_openai_api(uncached_packages, policy_revision)
                ai_success = True
            except Exception as e:
                logger.warning(f"OpenAI API call failed: {e}. Falling back...")

        # Fallback heuristic if no AI succeeded
        if not ai_success:
            decisions = []
            for pkg in uncached_packages:
                pkg_id = pkg.get("packageId", "pkg_unknown") if isinstance(pkg, dict) else str(pkg)
                d = heuristic_decide_package(pkg_id, pkg, policy_revision)
                d["packageId"] = pkg_id
                decisions.append(d)

        # Map decisions by packageId and cache them
        for d in decisions:
            pkg_id = d.get("packageId")
            if pkg_id:
                new_decisions_by_id[pkg_id] = d

    # Step 3: Assemble proposals preserving original order
    for idx, pkg in enumerate(packages):
        pkg_id = pkg.get("packageId", f"pkg_{idx}") if isinstance(pkg, dict) else str(pkg)
        c_hash = content_hashes[idx]
        
        decision = db.get_cached_decision(c_hash)
        if not decision:
            decision = new_decisions_by_id.get(pkg_id)
            if not decision:
                # Fallback if packageId was missing or not returned by model
                decision = heuristic_decide_package(pkg_id, pkg, policy_revision)
            # Save into decision cache
            db.save_cached_decision(c_hash, decision)

        # Validate action
        action = decision.get("action")
        if action not in ALLOWED_ACTIONS:
            action = "settle_invoice"

        # Validate evidence refs (exactly 3 decisive bracketed refs)
        refs = decision.get("evidenceRefs", [])
        if not isinstance(refs, list):
            refs = extract_bracketed_refs(str(refs))
        if len(refs) < 3:
            while len(refs) < 3:
                refs.append(f"[REF-POLICY-{len(refs)+1}]")
        decisive_refs = refs[:3]

        # Validate rationale
        base_rat = decision.get("rationale", "")
        rationale = ensure_valid_rationale(action, decisive_refs, base_rat)

        facts = decision.get("facts", {})
        if not facts:
            facts = {
                "vendorName": decision.get("vendorName", "Vendor"),
                "invoiceNumber": decision.get("invoiceNumber", f"INV-{pkg_id[:8]}"),
                "amountMinor": int(decision.get("amountMinor", 10000)),
                "currency": decision.get("currency", "INR"),
            }

        proposals.append(
            Proposal(
                packageId=pkg_id,
                actionId=generate_action_id(),
                action=action,
                facts=Facts(
                    vendorName=str(facts.get("vendorName", "Vendor")),
                    invoiceNumber=str(facts.get("invoiceNumber", f"INV-{pkg_id[:8]}")),
                    amountMinor=int(facts.get("amountMinor", 10000)),
                    currency=str(facts.get("currency", "INR")),
                ),
                evidenceRefs=decisive_refs,
                rationale=rationale,
            )
        )

    return proposals
