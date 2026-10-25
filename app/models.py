from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field

# ProtoJSON constants
ROLE_USER = "ROLE_USER"
ROLE_AGENT = "ROLE_AGENT"

TASK_STATE_SUBMITTED = "TASK_STATE_SUBMITTED"
TASK_STATE_WORKING = "TASK_STATE_WORKING"
TASK_STATE_INPUT_REQUIRED = "TASK_STATE_INPUT_REQUIRED"
TASK_STATE_COMPLETED = "TASK_STATE_COMPLETED"
TASK_STATE_CANCELED = "TASK_STATE_CANCELED"

# Media types
MEDIA_A2A_JSON = "application/a2a+json"
MEDIA_BATCH_INPUT = "application/vnd.ga5.invoice-claim-batch+json"
MEDIA_PROPOSALS = "application/vnd.ga5.invoice-action-proposals+json"
MEDIA_RESULTS_INPUT = "application/vnd.ga5.invoice-action-results+json"
MEDIA_RECEIPTS = "application/vnd.ga5.invoice-action-receipts+json"

# Allowed Actions
ActionType = Literal[
    "settle_invoice",
    "request_approval",
    "hold_invoice",
    "reject_duplicate",
    "open_exception",
]

ALLOWED_ACTIONS: set[str] = {
    "settle_invoice",
    "request_approval",
    "hold_invoice",
    "reject_duplicate",
    "open_exception",
}

class Part(BaseModel):
    mediaType: str
    data: Dict[str, Any]

class Message(BaseModel):
    messageId: str
    role: str = ROLE_USER
    taskId: Optional[str] = None
    contextId: Optional[str] = None
    parts: List[Part] = Field(default_factory=list)

class TaskConfiguration(BaseModel):
    returnImmediately: Optional[bool] = False
    historyLength: Optional[int] = 20
    acceptedOutputModes: Optional[List[str]] = None

class MessageSendRequest(BaseModel):
    message: Message
    configuration: Optional[TaskConfiguration] = None

class Facts(BaseModel):
    vendorName: str
    invoiceNumber: str
    amountMinor: int
    currency: str

class Proposal(BaseModel):
    packageId: str
    actionId: str
    action: ActionType
    facts: Facts
    evidenceRefs: List[str]
    rationale: str

class ProposalsArtifactData(BaseModel):
    batchId: str
    proposals: List[Proposal]

class ResultItem(BaseModel):
    packageId: str
    actionId: str
    action: str
    outcome: Literal["ACCEPTED", "REJECTED"]
    receiptNonce: str

class ResultsArtifactData(BaseModel):
    batchId: str
    results: List[ResultItem]

class ExecutionItem(BaseModel):
    packageId: str
    actionId: str
    action: str
    receiptNonce: str
    facts: Facts
    evidenceRefs: List[str]

class ReceiptsArtifactData(BaseModel):
    batchId: str
    executions: List[ExecutionItem]

class Task(BaseModel):
    id: str
    contextId: str
    state: str
    history: List[Dict[str, Any]] = Field(default_factory=list)
    artifacts: List[Dict[str, Any]] = Field(default_factory=list)

class TaskEnvelope(BaseModel):
    task: Task

class TasksEnvelope(BaseModel):
    tasks: List[Task]
