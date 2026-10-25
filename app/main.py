import json
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, Header, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from app.config import settings
from app.db import db, IdempotencyConflictError, TaskConflictError
from app.models import (
    MEDIA_A2A_JSON,
    MEDIA_BATCH_INPUT,
    MEDIA_PROPOSALS,
    MEDIA_RESULTS_INPUT,
    MEDIA_RECEIPTS,
    TASK_STATE_INPUT_REQUIRED,
    TASK_STATE_COMPLETED,
    TASK_STATE_CANCELED,
)
from app.ai import decide_invoice_batch
from app.utils import hash_message, generate_task_id, generate_context_id

app = FastAPI(title="A2A Invoice Agent", version="1.0.0")

def get_base_url(request: Request) -> str:
    """Return configured base URL or infer from incoming request."""
    if settings.BASE_URL:
        base = settings.BASE_URL.strip()
        if not base.endswith("/"):
            base += "/"
        return base
    # Inferred from request host
    scheme = request.url.scheme
    # Render and cloud proxies forward x-forwarded-proto
    f_proto = request.headers.get("x-forwarded-proto")
    if f_proto:
        scheme = f_proto
    host = request.headers.get("host", str(request.url.netloc))
    return f"{scheme}://{host}/a2a/"

def a2a_response(data: Any, status_code: int = 200) -> Response:
    """Serialize response with application/a2a+json and verify size stays <= 512 KiB."""
    content = json.dumps(data)
    encoded = content.encode("utf-8")
    if len(encoded) > settings.MAX_PAYLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Response exceeds maximum allowed size of 512 KiB",
        )
    return Response(
        content=encoded,
        status_code=status_code,
        media_type=MEDIA_A2A_JSON,
    )

def extract_principal(authorization: Optional[str]) -> str:
    """Validate and extract Bearer token identity."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization Bearer header",
        )
    principal = authorization[7:].strip()
    if not principal:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Empty Bearer token",
        )
    return principal

def verify_a2a_version(a2a_version: Optional[str]):
    """Verify A2A-Version: 1.0 header."""
    if not a2a_version or a2a_version.strip() != "1.0":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported or missing A2A-Version: '{a2a_version}'. Expected '1.0'",
        )

# ==========================================
# 1. Origin-level Discovery: Agent Card
# ==========================================
@app.get("/.well-known/agent-card.json")
def get_agent_card(request: Request):
    base_url = get_base_url(request)
    return {
        "name": "Invoice Safe Action Agent",
        "description": "Evidence-based A2A invoice agent with safe multi-tenant lifecycle",
        "version": "1.0.0",
        "capabilities": {},
        "defaultInputModes": [MEDIA_BATCH_INPUT],
        "defaultOutputModes": [MEDIA_PROPOSALS, MEDIA_RECEIPTS],
        "skills": [
            {
                "id": "invoice_action_agent",
                "name": "Invoice Action Agent",
                "description": "Reads invoice batches, chooses decisive evidence-based proposals, and executes approved receipts safely.",
                "tags": ["finance", "invoices", "reconciliation", "safety", "a2a"],
            }
        ],
        "supportedInterfaces": [
            {
                "url": base_url,
                "protocolBinding": "HTTP+JSON",
                "protocolVersion": "1.0",
            }
        ],
    }

# ==========================================
# 2. Core A2A Route Logic
# ==========================================
async def handle_message_send(request: Request, authorization: Optional[str], a2a_version: Optional[str]):
    verify_a2a_version(a2a_version)
    principal = extract_principal(authorization)

    body = await request.json()
    message = body.get("message")
    if not message or not isinstance(message, dict):
        raise HTTPException(status_code=400, detail="Missing or invalid 'message' field")

    message_id = message.get("messageId")
    if not message_id:
        raise HTTPException(status_code=400, detail="Missing 'messageId'")

    # Hash recursively key-sorted compact JSON of message only (ignoring configuration)
    content_hash = hash_message(message)

    # Check idempotency
    try:
        existing_task_id = db.check_idempotency(principal, message_id, content_hash)
        if existing_task_id:
            saved_task = db.get_task(existing_task_id, principal)
            if saved_task:
                return a2a_response({"task": saved_task})
    except IdempotencyConflictError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"IDEMPOTENCY_CONFLICT: {str(e)}",
        )

    parts = message.get("parts", [])
    if not parts or not isinstance(parts, list):
        raise HTTPException(status_code=400, detail="Message 'parts' must be a non-empty list")

    first_part = parts[0]
    media_type = first_part.get("mediaType")
    part_data = first_part.get("data", {})

    # ----------------------------------------------------
    # Case A: Initial Invoice Claim Batch -> Make Proposals
    # ----------------------------------------------------
    if media_type == MEDIA_BATCH_INPUT:
        batch_id = part_data.get("batchId", "batch_default")
        policy_rev = part_data.get("policyRevision", "")
        packages = part_data.get("packages", [])

        # Evaluate packages (cached + batched AI call)
        proposals = await decide_invoice_batch(packages, policy_rev)

        task_id = generate_task_id()
        context_id = generate_context_id()

        proposal_artifact = {
            "mediaType": MEDIA_PROPOSALS,
            "data": {
                "batchId": batch_id,
                "proposals": [p.model_dump() for p in proposals],
            },
        }

        task_dict = {
            "id": task_id,
            "contextId": context_id,
            "state": TASK_STATE_INPUT_REQUIRED,
            "history": [message],
            "artifacts": [proposal_artifact],
        }

        db.save_new_task(task_dict, principal, message_id, content_hash)
        return a2a_response({"task": task_dict})

    # ----------------------------------------------------
    # Case B: Continuation Results -> Execute Receipts
    # ----------------------------------------------------
    elif media_type == MEDIA_RESULTS_INPUT:
        task_id = message.get("taskId")
        context_id = message.get("contextId")
        if not task_id or not context_id:
            raise HTTPException(status_code=400, detail="Continuation requires 'taskId' and 'contextId'")

        # User isolation: only fetch if owned by principal
        current_task = db.get_task(task_id, principal)
        if not current_task:
            raise HTTPException(status_code=404, detail="Task not found")

        if current_task["contextId"] != context_id:
            raise HTTPException(status_code=400, detail="Mismatched contextId for task")

        if current_task["state"] in (TASK_STATE_COMPLETED, TASK_STATE_CANCELED):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Task {task_id} is already in terminal state {current_task['state']}",
            )

        # Locate proposals artifact
        proposal_artifact = None
        for art in current_task.get("artifacts", []):
            if art.get("mediaType") == MEDIA_PROPOSALS:
                proposal_artifact = art
                break

        if not proposal_artifact:
            raise HTTPException(status_code=400, detail="Missing original proposal artifact in task")

        stored_batch_id = proposal_artifact.get("data", {}).get("batchId")
        continuation_batch_id = part_data.get("batchId")
        if stored_batch_id != continuation_batch_id:
            raise HTTPException(status_code=400, detail="Mismatched batchId in continuation")

        stored_proposals = proposal_artifact.get("data", {}).get("proposals", [])
        stored_props_by_pkg: Dict[str, Dict[str, Any]] = {p["packageId"]: p for p in stored_proposals}

        results = part_data.get("results", [])
        executions = []

        # Validate every result item
        for res in results:
            pkg_id = res.get("packageId")
            prop = stored_props_by_pkg.get(pkg_id)
            if not prop:
                raise HTTPException(status_code=400, detail=f"Unknown packageId {pkg_id} in results")

            # Must match actionId and action character-for-character
            if res.get("actionId") != prop.get("actionId"):
                raise HTTPException(status_code=400, detail=f"Mismatched actionId for package {pkg_id}")
            if res.get("action") != prop.get("action"):
                raise HTTPException(status_code=400, detail=f"Mismatched action for package {pkg_id}")

            outcome = res.get("outcome")
            if outcome == "ACCEPTED":
                nonce = res.get("receiptNonce")
                if not nonce:
                    raise HTTPException(status_code=400, detail=f"Missing receiptNonce for accepted package {pkg_id}")
                executions.append({
                    "packageId": pkg_id,
                    "actionId": prop["actionId"],
                    "action": prop["action"],
                    "receiptNonce": nonce,
                    "facts": prop["facts"],
                    "evidenceRefs": prop["evidenceRefs"],
                })

        # Build receipts artifact
        receipts_artifact = {
            "mediaType": MEDIA_RECEIPTS,
            "data": {
                "batchId": stored_batch_id,
                "executions": executions,
            },
        }

        # Keep original proposals artifact, add receipts artifact
        new_artifacts = [proposal_artifact, receipts_artifact]
        new_history = current_task.get("history", []) + [message]

        try:
            completed_task = db.complete_task_with_receipts(
                task_id=task_id,
                principal=principal,
                message_id=message_id,
                content_hash=content_hash,
                new_history=new_history,
                new_artifacts=new_artifacts,
            )
            if not completed_task:
                raise HTTPException(status_code=404, detail="Task not found")
            return a2a_response({"task": completed_task})
        except TaskConflictError as e:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    else:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported input mediaType: '{media_type}'",
        )

def handle_get_task(task_id: str, authorization: Optional[str], a2a_version: Optional[str]):
    verify_a2a_version(a2a_version)
    principal = extract_principal(authorization)
    task = db.get_task(task_id, principal)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return a2a_response(task)

def handle_list_tasks(authorization: Optional[str], a2a_version: Optional[str]):
    verify_a2a_version(a2a_version)
    principal = extract_principal(authorization)
    tasks = db.list_tasks(principal)
    return a2a_response({"tasks": tasks})

def handle_cancel_task(task_id: str, authorization: Optional[str], a2a_version: Optional[str]):
    verify_a2a_version(a2a_version)
    principal = extract_principal(authorization)
    try:
        task = db.cancel_task(task_id, principal)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        return a2a_response(task)
    except TaskConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

# ==========================================
# 3. Mount routes at both /a2a/* and /*
# ==========================================
@app.post("/a2a/message:send")
async def send_message_a2a(
    request: Request,
    authorization: Optional[str] = Header(None),
    a2a_version: Optional[str] = Header(None, alias="A2A-Version"),
):
    return await handle_message_send(request, authorization, a2a_version)

@app.post("/message:send")
async def send_message_root(
    request: Request,
    authorization: Optional[str] = Header(None),
    a2a_version: Optional[str] = Header(None, alias="A2A-Version"),
):
    return await handle_message_send(request, authorization, a2a_version)

@app.get("/a2a/tasks/{task_id}")
def get_task_a2a(
    task_id: str,
    authorization: Optional[str] = Header(None),
    a2a_version: Optional[str] = Header(None, alias="A2A-Version"),
):
    return handle_get_task(task_id, authorization, a2a_version)

@app.get("/tasks/{task_id}")
def get_task_root(
    task_id: str,
    authorization: Optional[str] = Header(None),
    a2a_version: Optional[str] = Header(None, alias="A2A-Version"),
):
    return handle_get_task(task_id, authorization, a2a_version)

@app.get("/a2a/tasks")
def list_tasks_a2a(
    authorization: Optional[str] = Header(None),
    a2a_version: Optional[str] = Header(None, alias="A2A-Version"),
):
    return handle_list_tasks(authorization, a2a_version)

@app.get("/tasks")
def list_tasks_root(
    authorization: Optional[str] = Header(None),
    a2a_version: Optional[str] = Header(None, alias="A2A-Version"),
):
    return handle_list_tasks(authorization, a2a_version)

@app.post("/a2a/tasks/{task_id}:cancel")
def cancel_task_a2a(
    task_id: str,
    authorization: Optional[str] = Header(None),
    a2a_version: Optional[str] = Header(None, alias="A2A-Version"),
):
    return handle_cancel_task(task_id, authorization, a2a_version)

@app.post("/tasks/{task_id}:cancel")
def cancel_task_root(
    task_id: str,
    authorization: Optional[str] = Header(None),
    a2a_version: Optional[str] = Header(None, alias="A2A-Version"),
):
    return handle_cancel_task(task_id, authorization, a2a_version)
