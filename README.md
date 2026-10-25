# A2A Invoice Safe Action Agent

An autonomous, multi-tenant, evidence-based invoice decision agent implementing the **Agent-to-Agent (A2A) 1.0 HTTP+JSON Protocol**.

The agent inspects invoice batches, selects typed business actions, cites exact decisive evidence references, safeguards against duplicate payments, and guarantees safe two-phase state lifecycles with user isolation.

---

## 🌟 Architecture & Features

- **A2A 1.0 Protocol Surface**:
  - `GET /.well-known/agent-card.json`: Public discovery card with capabilities, skills (`invoice_action_agent`), and supported interfaces.
  - `POST {base}/message:send`: Handles initial invoice batches (`application/vnd.ga5.invoice-claim-batch+json`) and continuation results (`application/vnd.ga5.invoice-action-results+json`).
  - `GET {base}/tasks/{id}`: Retrieves task state for the authenticated owner.
  - `GET {base}/tasks`: Lists all tasks belonging to the authenticated owner.
  - `POST {base}/tasks/{id}:cancel`: Safely cancels an active task.
- **Two-Phase Action Gate**:
  - Proposes actions in `TASK_STATE_INPUT_REQUIRED`. A proposal alone is never permission to act.
  - Executes accepted proposals only after unpredictable grader `receiptNonce` continuation arrives, transitioning to `TASK_STATE_COMPLETED`.
- **Deduplication & Idempotency**:
  - Deduplicated by `(Bearer principal, messageId)`.
  - Content hash computed from recursively key-sorted compact JSON of `message` only (ignoring `configuration`).
  - Reusing a `messageId` with changed content returns `409 Conflict` with `IDEMPOTENCY_CONFLICT`.
- **Concurrency & Race Condition Handling**:
  - Atomic transitions in SQLite (WAL mode).
  - If cancellation and receipt arrive simultaneously, exactly one returns `200` and the other returns `409 Conflict`.
  - Once terminal (`COMPLETED` or `CANCELED`), state is immutable.
- **Strict User Isolation (Multi-Tenancy)**:
  - Each Bearer token represents an isolated principal.
  - A user cannot read, cancel, or list another user's tasks. Outsider requests return generic `404` errors without leaking task existence.
- **Decision Caching**:
  - Decisions are cached by canonical package content hash so repeated core tasks consume zero extra model tokens and execute in milliseconds.
- **AI Brain**:
  - Supports **Google Gemini** (e.g. `gemini-2.0-flash` or `gemini-1.5-flash`), **OpenAI**, or an intelligent built-in fallback parser.

---

## 📁 Project Structure

```
a2a-invoice-agent/
├── app/
│   ├── __init__.py
│   ├── config.py             # Environment configurations
│   ├── db.py                 # SQLite WAL persistence & atomic state transitions
│   ├── models.py             # Pydantic models for A2A schemas & ProtoJSON strings
│   ├── ai.py                 # Batch AI decision engine & content-addressable cache
│   ├── utils.py              # Canonical JSON hashing, ref extraction, actionId generator
│   └── main.py               # FastAPI server, protocol validation, route handlers
├── tests/
│   ├── __init__.py
│   └── test_agent.py         # Full test suite covering all graded requirements
├── Dockerfile                # Production Docker container
├── Procfile                  # Procfile for Render / Heroku
├── render.yaml               # Render Blueprint configuration
├── requirements.txt          # Python dependencies
├── .gitignore
└── README.md
```

---

## 🚀 Local Development & Testing

### 1. Setup Virtual Environment

```bash
# Clone or navigate to the repository
cd a2a-invoice-agent

# Create virtual environment
python -m venv venv

# Activate virtual environment
# On Windows:
.\venv\Scripts\activate
# On Linux/macOS:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Environment Variables (Optional)

Create a `.env` file or export variables:
```bash
# Optional: Set Gemini API key for live AI decisions
export GEMINI_API_KEY="your-gemini-api-key"
export GEMINI_MODEL="gemini-2.0-flash"

# Optional: Submit URL override (defaults to request host)
export BASE_URL="https://your-service.onrender.com/a2a/"
```
*(If no API key is provided, the agent runs a deterministic heuristic analyzer that satisfies all structural schema and evidence requirements).*

### 3. Run the Test Suite

Run the comprehensive integration test suite:
```bash
pytest tests/test_agent.py -v
```

All 7 test cases covering the Agent Card, authentication, versioning, proposal schema, idempotency, continuation receipts, cancel races, and user isolation should pass.

### 4. Start the Server Locally

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Test discovery card:
```bash
curl http://localhost:8000/.well-known/agent-card.json
```

---

## ☁️ Deploying on Render

### Step 1: Push Code to GitHub

1. Create a new repository on [GitHub](https://github.com/new) (e.g., `a2a-invoice-agent`).
2. In your local terminal:
```bash
cd a2a-invoice-agent
git add .
git commit -m "Initial commit of A2A Invoice Safe Action Agent"
git branch -M main
git remote add origin https://github.com/<your-username>/a2a-invoice-agent.git
git push -u origin main
```

---

### Step 2: Deploy as Web Service on Render

1. Log in to [Render Dashboard](https://dashboard.render.com/).
2. Click **New +** $\to$ **Web Service**.
3. Connect your GitHub repository (`a2a-invoice-agent`).
4. Set the following settings:
   - **Name**: `a2a-invoice-agent` (or your choice)
   - **Runtime**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   - **Instance Type**: `Free`
5. Click **Advanced** and add **Environment Variables**:
   - `PYTHON_VERSION`: `3.12.3`
   - `GEMINI_API_KEY`: *(Your Google AI Studio API key - get one for free at aistudio.google.com)*
   - `GEMINI_MODEL`: `gemini-2.0-flash`
   - `BASE_URL`: `https://<your-service-name>.onrender.com/a2a/`
6. Click **Create Web Service**.

Render will build and start your application. Once deployed, note down your Render public HTTPS URL:
`https://<your-service-name>.onrender.com`

---

## 🎯 Verification & Submitting to the Grader

### 1. Verify Deployment Live

Test the public Agent Card in your browser or curl:
```bash
curl -i https://<your-service-name>.onrender.com/.well-known/agent-card.json
```
Verify that `supportedInterfaces` lists `https://<your-service-name>.onrender.com/a2a/`.

### 2. Base URL to Submit

Submit the base URL to the grading portal:
```
https://<your-service-name>.onrender.com/a2a/
```

> [!IMPORTANT]
> - The checking limit is **10 checks per 24 hours**.
> - The 10th check is reserved for **Save**.
> - Make sure you verify `/.well-known/agent-card.json` manually before clicking **Check** on the grader!
