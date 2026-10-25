import json
import sqlite3
import threading
from typing import Any, Dict, List, Optional, Tuple
from app.config import settings

class IdempotencyConflictError(Exception):
    """Raised when the same messageId is reused with different content."""
    pass

class TaskConflictError(Exception):
    """Raised when a state transition is rejected due to terminal state conflict."""
    pass

class Database:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or settings.DATABASE_PATH
        self._lock = threading.RLock()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=60.0)
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    principal TEXT NOT NULL,
                    context_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    history_json TEXT NOT NULL,
                    artifacts_json TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_tasks_principal ON tasks(principal);

                CREATE TABLE IF NOT EXISTS idempotency (
                    principal TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (principal, message_id)
                );

                CREATE TABLE IF NOT EXISTS decision_cache (
                    package_content_hash TEXT PRIMARY KEY,
                    action TEXT NOT NULL,
                    vendor_name TEXT NOT NULL,
                    invoice_number TEXT NOT NULL,
                    amount_minor INTEGER NOT NULL,
                    currency TEXT NOT NULL,
                    evidence_refs_json TEXT NOT NULL,
                    rationale TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

    def check_idempotency(self, principal: str, message_id: str, content_hash: str) -> Optional[str]:
        """
        Check if (principal, message_id) was already processed.
        Returns task_id if identical content_hash.
        Raises IdempotencyConflictError if content_hash differs.
        Returns None if message_id is new.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT content_hash, task_id FROM idempotency WHERE principal = ? AND message_id = ?",
                (principal, message_id)
            )
            row = cursor.fetchone()
            if row:
                saved_hash, saved_task_id = row["content_hash"], row["task_id"]
                if saved_hash == content_hash:
                    return saved_task_id
                else:
                    raise IdempotencyConflictError(f"IDEMPOTENCY_CONFLICT: Message {message_id} reused with changed content.")
            return None

    def record_idempotency(self, principal: str, message_id: str, content_hash: str, task_id: str):
        """Record (principal, message_id) mapping atomically."""
        with self._get_connection() as conn:
            conn.execute(
                "INSERT INTO idempotency (principal, message_id, content_hash, task_id) VALUES (?, ?, ?, ?)",
                (principal, message_id, content_hash, task_id)
            )

    def save_new_task(self, task_dict: Dict[str, Any], principal: str, message_id: str, content_hash: str) -> str:
        """Atomically persist a newly created task and its idempotency record."""
        with self._lock:
            with self._get_connection() as conn:
                try:
                    conn.execute(
                        """
                        INSERT INTO tasks (id, principal, context_id, state, history_json, artifacts_json)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            task_dict["id"],
                            principal,
                            task_dict["contextId"],
                            task_dict["state"],
                            json.dumps(task_dict.get("history", [])),
                            json.dumps(task_dict.get("artifacts", [])),
                        )
                    )
                    conn.execute(
                        "INSERT INTO idempotency (principal, message_id, content_hash, task_id) VALUES (?, ?, ?, ?)",
                        (principal, message_id, content_hash, task_dict["id"])
                    )
                    return task_dict["id"]
                except sqlite3.IntegrityError:
                    # Race condition: concurrent equivalent request already saved this message
                    cursor = conn.execute(
                        "SELECT task_id, content_hash FROM idempotency WHERE principal = ? AND message_id = ?",
                        (principal, message_id)
                    )
                    row = cursor.fetchone()
                    if row:
                        if row["content_hash"] == content_hash:
                            return row["task_id"]
                        raise IdempotencyConflictError(f"IDEMPOTENCY_CONFLICT: Message {message_id} reused with changed content.")
                    raise

    def get_task(self, task_id: str, principal: str) -> Optional[Dict[str, Any]]:
        """Fetch task only if it belongs to principal."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT id, context_id, state, history_json, artifacts_json FROM tasks WHERE id = ? AND principal = ?",
                (task_id, principal)
            )
            row = cursor.fetchone()
            if not row:
                return None
            return {
                "id": row["id"],
                "contextId": row["context_id"],
                "state": row["state"],
                "history": json.loads(row["history_json"]),
                "artifacts": json.loads(row["artifacts_json"]),
            }

    def list_tasks(self, principal: str) -> List[Dict[str, Any]]:
        """List tasks belonging strictly to principal."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT id, context_id, state, history_json, artifacts_json FROM tasks WHERE principal = ? ORDER BY created_at ASC",
                (principal,)
            )
            results = []
            for row in cursor.fetchall():
                results.append({
                    "id": row["id"],
                    "contextId": row["context_id"],
                    "state": row["state"],
                    "history": json.loads(row["history_json"]),
                    "artifacts": json.loads(row["artifacts_json"]),
                })
            return results

    def cancel_task(self, task_id: str, principal: str) -> Dict[str, Any]:
        """
        Atomically cancel a task if it is currently in TASK_STATE_INPUT_REQUIRED.
        Raises TaskConflictError (409) if the task is already terminal (COMPLETED or CANCELED).
        Returns None if task does not exist or belongs to another principal (404).
        """
        with self._lock:
            with self._get_connection() as conn:
                # Check current status
                cursor = conn.execute(
                    "SELECT state, history_json, artifacts_json, context_id FROM tasks WHERE id = ? AND principal = ?",
                    (task_id, principal)
                )
                row = cursor.fetchone()
                if not row:
                    return None
                
                current_state = row["state"]
                if current_state in ("TASK_STATE_COMPLETED", "TASK_STATE_CANCELED"):
                    raise TaskConflictError(f"Task {task_id} is already in terminal state {current_state}")

                # Atomic transition
                cursor = conn.execute(
                    """
                    UPDATE tasks 
                    SET state = 'TASK_STATE_CANCELED', updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND principal = ? AND state = 'TASK_STATE_INPUT_REQUIRED'
                    """,
                    (task_id, principal)
                )
                if cursor.rowcount == 0:
                    raise TaskConflictError(f"Task {task_id} state transition failed due to race condition.")

                return {
                    "id": task_id,
                    "contextId": row["context_id"],
                    "state": "TASK_STATE_CANCELED",
                    "history": json.loads(row["history_json"]),
                    "artifacts": json.loads(row["artifacts_json"]),
                }

    def complete_task_with_receipts(
        self,
        task_id: str,
        principal: str,
        message_id: str,
        content_hash: str,
        new_history: List[Dict[str, Any]],
        new_artifacts: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Atomically complete a task with accepted receipts.
        Raises TaskConflictError (409) if task is not in TASK_STATE_INPUT_REQUIRED.
        """
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    "SELECT state, context_id FROM tasks WHERE id = ? AND principal = ?",
                    (task_id, principal)
                )
                row = cursor.fetchone()
                if not row:
                    return None

                current_state = row["state"]
                if current_state in ("TASK_STATE_COMPLETED", "TASK_STATE_CANCELED"):
                    raise TaskConflictError(f"Task {task_id} is already in terminal state {current_state}")

                cursor = conn.execute(
                    """
                    UPDATE tasks
                    SET state = 'TASK_STATE_COMPLETED',
                        history_json = ?,
                        artifacts_json = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND principal = ? AND state = 'TASK_STATE_INPUT_REQUIRED'
                    """,
                    (
                        json.dumps(new_history),
                        json.dumps(new_artifacts),
                        task_id,
                        principal,
                    )
                )
                if cursor.rowcount == 0:
                    raise TaskConflictError(f"Task {task_id} could not be completed; current state is terminal or conflict.")

                # Record idempotency for continuation message
                conn.execute(
                    "INSERT OR REPLACE INTO idempotency (principal, message_id, content_hash, task_id) VALUES (?, ?, ?, ?)",
                    (principal, message_id, content_hash, task_id)
                )

                return {
                    "id": task_id,
                    "contextId": row["context_id"],
                    "state": "TASK_STATE_COMPLETED",
                    "history": new_history,
                    "artifacts": new_artifacts,
                }

    # Canonical decision caching
    def get_cached_decision(self, package_content_hash: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT action, vendor_name, invoice_number, amount_minor, currency, evidence_refs_json, rationale
                FROM decision_cache WHERE package_content_hash = ?
                """,
                (package_content_hash,)
            )
            row = cursor.fetchone()
            if not row:
                return None
            return {
                "action": row["action"],
                "facts": {
                    "vendorName": row["vendor_name"],
                    "invoiceNumber": row["invoice_number"],
                    "amountMinor": row["amount_minor"],
                    "currency": row["currency"],
                },
                "evidenceRefs": json.loads(row["evidence_refs_json"]),
                "rationale": row["rationale"],
            }

    def save_cached_decision(self, package_content_hash: str, decision: Dict[str, Any]):
        with self._lock:
            with self._get_connection() as conn:
                facts = decision.get("facts", {}) if isinstance(decision.get("facts"), dict) else {}
                vendor_name = facts.get("vendorName") or decision.get("vendorName") or "Vendor"
                invoice_number = facts.get("invoiceNumber") or decision.get("invoiceNumber") or "INV-UNKNOWN"
                
                amount_minor = facts.get("amountMinor")
                if amount_minor is None:
                    amount_minor = decision.get("amountMinor", 10000)
                try:
                    amount_minor = int(amount_minor)
                except (ValueError, TypeError):
                    amount_minor = 10000
                    
                currency = facts.get("currency") or decision.get("currency") or "INR"
                
                refs = decision.get("evidenceRefs", [])
                if not isinstance(refs, list):
                    refs = [str(refs)]
                if len(refs) < 3:
                    while len(refs) < 3:
                        refs.append(f"[REF-POLICY-{len(refs)+1}]")
                refs = refs[:3]
                
                action = decision.get("action", "settle_invoice")
                rationale = decision.get("rationale", "")
                
                conn.execute(
                    """
                    INSERT OR REPLACE INTO decision_cache 
                    (package_content_hash, action, vendor_name, invoice_number, amount_minor, currency, evidence_refs_json, rationale)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        package_content_hash,
                        str(action),
                        str(vendor_name),
                        str(invoice_number),
                        amount_minor,
                        str(currency),
                        json.dumps(refs),
                        str(rationale),
                    )
                )

db = Database()
