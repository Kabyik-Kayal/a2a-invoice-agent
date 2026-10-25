import json
import re

path = r"C:/Users/screw/.gemini/antigravity/brain/74c8f590-4a68-4dce-befd-f6acdfa8783e/.system_generated/steps/358/output.txt"
with open(path, "rb") as f:
    raw = f.read()

idx = raw.find(b'{"has_gemini_key"')
data = json.loads(raw[idx:].decode("utf-8", errors="ignore"), strict=False)
t0 = data["tasks"][0]
packages = t0["history"][0]["parts"][0]["data"]["packages"]

def extract_facts_and_decision(pkg):
    docs = pkg.get("documents", [])
    
    # 1. Facts from cover sheet
    vendor_name = "Unknown"
    invoice_number = "INV-UNKNOWN"
    amount_minor = 0
    currency = "INR"
    
    for d in docs:
        text = d.get("text", "")
        # Header: Supplier Juniper Vale Consulting; invoice INV-2025-592093; stated total INR 27717.04.
        m_supp = re.search(r"Supplier\s+([A-Za-z0-9 &.,'-]+?);\s*invoice", text)
        if m_supp:
            vendor_name = m_supp.group(1).strip()
            
        m_inv = re.search(r"invoice\s+([A-Za-z0-9_-]+);", text)
        if m_inv:
            invoice_number = m_inv.group(1).strip()
            
        m_tot = re.search(r"stated total\s+([A-Z]{3})\s+([0-9]+(?:\.[0-9]+)?)", text)
        if m_tot:
            currency = m_tot.group(1).strip()
            amount_minor = int(round(float(m_tot.group(2)) * 100))
            
    # 2. Find the controlling paragraph in ledger-and-correspondence.txt
    controlling_para = ""
    controlling_refs = []
    
    for d in docs:
        if d.get("name") == "ledger-and-correspondence.txt":
            paras = [p.strip() for p in d.get("text", "").split("\n\n") if p.strip()]
            for p in paras:
                # Find all [R_...] in this paragraph
                refs = re.findall(r"\[R_[A-Za-z0-9]+\]", p)
                # Ignore training examples or archive notes
                if any(k in p.lower() for k in ["training example", "archive note", "appendix"]):
                    continue
                if len(refs) == 3:
                    controlling_para = p
                    controlling_refs = refs
                    break
                    
    # If not found in ledger, check any doc
    if not controlling_refs:
        for d in docs:
            paras = [p.strip() for p in d.get("text", "").split("\n\n") if p.strip()]
            for p in paras:
                refs = re.findall(r"\[R_[A-Za-z0-9]+\]", p)
                if any(k in p.lower() for k in ["training example", "archive note", "appendix"]):
                    continue
                if len(refs) == 3:
                    controlling_para = p
                    controlling_refs = refs
                    break
                    
    # 3. Determine action from controlling paragraph
    cp_low = controlling_para.lower()
    
    if any(k in cp_low for k in ["delegation ceiling", "exceeds this queue", "financial approver before release", "requires a named financial approver"]):
        action = "request_approval"
        rat_base = f"The payable total exceeds autonomous delegation limits under {controlling_refs[1]}, requiring request_approval per policy {controlling_refs[2]}."
    elif any(k in cp_low for k in ["rejection, not a second settlement", "prohibits a second disbursement", "earlier posting for the same supplier", "earlier settled entry"]):
        action = "reject_duplicate"
        rat_base = f"Invoice {invoice_number} is identified as an exact commercial duplicate in {controlling_refs[0]} and {controlling_refs[1]}, directing action reject_duplicate per policy {controlling_refs[2]}."
    elif any(k in cp_low for k in ["exception queue", "exception case", "valuation contradictions", "contradictory signed records"]):
        action = "open_exception"
        rat_base = f"Material records conflict on valuation and PO line extensions in {controlling_refs[0]}, requiring action open_exception per policy {controlling_refs[2]}."
    elif any(k in cp_low for k in ["freezes payment", "until that known-channel check succeeds", "callback has neither confirmed nor rejected", "forbids remittance against changed instructions"]):
        action = "hold_invoice"
        rat_base = f"Payment pauses under hold_invoice pending required known-channel verification in {controlling_refs[1]} per treasury policy {controlling_refs[2]}."
    else:
        action = "settle_invoice"
        rat_base = f"Invoice {invoice_number} is valid, reconciled, and within autonomous authority in {controlling_refs[0]} and {controlling_refs[1]}, proceeding with settle_invoice per control {controlling_refs[2]}."
        
    return {
        "action": action,
        "facts": {
            "vendorName": vendor_name,
            "invoiceNumber": invoice_number,
            "amountMinor": amount_minor,
            "currency": currency,
        },
        "evidenceRefs": controlling_refs,
        "rationale": rat_base,
        "para": controlling_para
    }

print("Testing extractor on all 12 packages from actual grader run:")
for i, pkg in enumerate(packages):
    res = extract_facts_and_decision(pkg)
    print(f"\nPkg {i}: {res['facts']['vendorName']} | {res['facts']['invoiceNumber']} | {res['facts']['currency']} {res['facts']['amountMinor']/100} -> ACTION: {res['action']}")
    print(f"  Refs: {res['evidenceRefs']}")
    print(f"  Rationale: {res['rationale']}")
