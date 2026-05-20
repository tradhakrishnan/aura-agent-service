# AURA Agent Service

7-agent LangGraph orchestration service — the AI brain of AURA. Ingests support tickets, runs multi-agent analysis, pauses for human approval, executes fixes, and closes tickets autonomously.

---

## Overview

| Property | Value |
|---|---|
| Service Name | `aura-agent-service` |
| Port | **8090** |
| Protocol | HTTP REST (JSON) |
| Stack | FastAPI · Python 3.11 · LangGraph · LangChain · Anthropic Claude |
| LLM Model | `claude-sonnet-4-6` (default) |
| Role | Orchestrator of all 7 AI agents in the AURA pipeline |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     aura-agent-service :8090                     │
│                                                                   │
│  FastAPI REST Layer                                               │
│  ├── POST /agent/run              Submit ticket → background run  │
│  ├── GET  /agent/run/{id}         Poll run state                  │
│  ├── POST /agent/run/{id}/approve Human override → EA/VA/SDA     │
│  ├── GET  /agent/runs             List all runs                   │
│  └── GET  /health                 Health check                    │
│                                                                   │
│  LangGraph StateGraph (aura_graph)                                │
│                                                                   │
│  SDA-open → SPA ⇄ SME (max 3 iterations) → RAT → AUTHORIZER      │
│                                              │                    │
│                                    pending_approval = True        │
│                                    (workflow pauses here)         │
│                                              │                    │
│                         human approval via /approve endpoint      │
│                                              │                    │
│                                         EA → VA → SDA-close       │
│                                                                   │
│  In-memory run store (dict) — per-process, reset on restart       │
└─────────────────────────────────────────────────────────────────┘

Agent Tools:
  QUERY_TOOLS  → calls tap-query-service :8081
  UPDATER_TOOLS → calls tap-updater-service :8082
               → or client services :8083-:8086
```

---

## Agent Pipeline

### Full Flow

```
Ticket IN
    │
    ▼
[1] SDA-open (Service Desk Analyst)
    • Reads ticket fields
    • Queries TAP for hotel/location/user context
    • Produces: sda_summary
    │
    ▼
[2] SPA (Software Programmer Agent)
    • Deep-dives into TAP data relationships
    • Runs full query suite (hotels ↔ locations ↔ users)
    • Produces: spa_findings, increments iteration counter
    │
    ▼
[3] SME (Subject Matter Expert)
    • Reviews SDA + SPA findings
    • Confirms root cause OR requests more research
    • RESOLVED → continue  |  NEEDS_MORE_RESEARCH → back to SPA
    • Max 3 SPA↔SME iterations
    • Produces: sme_verdict (with FIX PLAN if RESOLVED)
    │
    ▼
[4] RAT (Risk Assessment Tool)
    • Classifies fix risk: Immediate / Normal / Escalated
    • Produces: risk_level
    │
    ▼
[5] AUTHORIZER
    • Reviews full case + fix plan + risk
    • Gives RECOMMENDATION: APPROVE | REJECT
    • Sets pending_approval = True (workflow pauses)
    • Sets authorized = False (human must confirm)
    │
    ▼  ← Human clicks "Approve & Execute" in UI
    │    POST /agent/run/{id}/approve
    │
    ▼
[6] EA (Executing Agent)
    • Reads fix plan from sme_verdict
    • Uses updater tools to apply changes step by step
    • Logs each action → execution_log
    │
    ▼
[7] VA (Validation Agent)
    • Queries TAP to verify all changes were applied
    • Runs cross-validation checks (user ↔ location ↔ hotel)
    • Sets validation_report: { status: VALIDATED | FAILED }
    │
    ▼
[1b] SDA-close (Service Desk Analyst — closure)
    • Produces professional closure summary
    • Sets ticket_status = Closed
    │
    ▼
Ticket CLOSED
```

### Agent Roles

| # | Agent | Code | Key Output |
|---|---|---|---|
| 1a | Service Desk Analyst (open) | `sda_open_node` | `sda_summary`, `ticket_type`, `resolution_context` |
| 2 | Software Programmer Agent | `spa_node` | `spa_findings` |
| 3 | Subject Matter Expert | `sme_node` | `sme_verdict` (RESOLVED / NEEDS_MORE_RESEARCH) |
| 4 | Risk Assessment Tool | `rat_node` | `risk_level` (Immediate / Normal / Escalated) |
| 5 | Authorizer | `authorizer_node` | `pending_approval = True` |
| 6 | Executing Agent | `ea_node` | `execution_log` |
| 7 | Validation Agent | `va_node` | `validation_report` |
| 1b | Service Desk Analyst (close) | `sda_close_node` | `ticket_status = Closed`, overwrites `sda_summary` |

---

## Ticket Classification

Before any LLM call, `sda_open_node` runs a zero-token heuristic classifier that determines the ticket type and extracts the minimal resolution context from structured fields or regex on free text.

### Ticket Types

| Type | Trigger | Resolution Context |
|---|---|---|
| `permission` | EID present + permission keyword in title/description | `{eid, permission, system, action}` |
| `hotel_location` | `affected_hotel` or `affected_location` set | `{hotel_code, location_code, system}` |
| `user_access` | EID + system both set, no permission keyword | `{eid, system}` |
| `generic` | None of the above | `{}` |

### Permission Ticket Routing

**When system is known** (from structured fields or keyword in text):
- SDA calls `get_user_by_eid_and_app(eid, app)` — 1 targeted call
- SPA calls `get_user_by_eid_and_app` — 1 call
- SME confirms with zero tool calls (SPA data is sufficient)
- EA calls `get_user_by_eid_and_app` then `patch_user_assignments` — 2 mandatory calls

**When system is unknown** (Jira tickets with `affected_system: null` and no system keyword):
- SDA calls `get_user_by_eid(eid)` to discover which app the user belongs to
- SDA outputs `SYSTEM_CONFIRMED: <MARSHA|ACRS|MINT>` in its response
- The confirmed system is parsed and stored in `resolution_context["system"]`
- All downstream agents (SPA → SME → EA) use the discovered system automatically

### Jira Ticket Guidelines

For Jira tickets to resolve correctly without system discovery overhead, the title should follow this pattern:

```
User EID {EID} is missing {SYSTEM} permission '{Permission Name}'
```

**Examples:**
```
User EID WITSF960 is missing ACRS permission 'Revenue Manager'
User EID SYIPL670 is missing MARSHA permission 'Hotel Reservation Manager'
```

**Rules:**
- EID: 4-7 uppercase letters + 2-4 digits (e.g. `WITSF960`)
- System: one of `MARSHA`, `ACRS`, `MINT` (any case)
- Permission name: Title Case, inside single or double quotes

For remove/revoke tickets, add `remove`, `revoke`, or `delete` anywhere in the title.

---

## State Schema (`AuraState`)

```python
class AuraState(TypedDict):
    ticket:             dict       # original ticket payload
    ticket_type:        str        # 'permission' | 'hotel_location' | 'user_access' | 'generic'
    resolution_context: dict       # {eid, permission, system, action} or {hotel_code, location_code}
    tap_context:        dict       # TAP data gathered by SDA
    sda_summary:        str        # SDA open summary (overwritten by SDA-close at end)
    spa_findings:       str        # SPA root cause analysis
    sme_verdict:        str        # SME verdict + fix plan
    risk_level:         str        # Immediate / Normal / Escalated
    authorized:         bool       # True only after human approves
    pending_approval:   bool       # True = workflow paused for human
    execution_log:      List[dict] # EA step-by-step log
    validation_report:  dict       # { status, details }
    ticket_status:      str        # Open / In Progress / Resolved / Closed
    messages:           list       # LangChain message history
    iteration:          int        # SPA↔SME loop counter
```

---

## API Reference

### Submit a ticket
```
POST /agent/run
Content-Type: application/json
```
**Request body:**
```json
{
  "ticket_id": "INC000201",
  "source": "ServiceNow",
  "title": "Missing supervisor assignment for user NMKOS046",
  "description": "User NMKOS046 in MARSHA app has no supervisor EID set in location HTDV7N. Reported by Revenue Management team.",
  "severity": "High",
  "affected_system": "MARSHA",
  "affected_hotel": "PARBA",
  "affected_location": "HTDV7N",
  "affected_eid": "NMKOS046",
  "reported_by": "JBROW012"
}
```

**Field constraints:**

| Field | Required | Valid Values |
|---|---|---|
| `ticket_id` | Yes | Any string (e.g. `INC000201`) |
| `source` | Yes | `ServiceNow`, `Jira`, `Manual` |
| `title` | Yes | String |
| `description` | Yes | String |
| `severity` | Yes | `Critical`, `High`, `Medium`, `Low` |
| `affected_system` | No | `MARSHA`, `MINT`, `ACRS`, `VDS`, `TAP` |
| `affected_hotel` | No | Hotel code (e.g. `PARBA`) |
| `affected_location` | No | Location code (e.g. `HTDV7N`) |
| `affected_eid` | No | Employee ID (e.g. `NMKOS046`) |
| `reported_by` | No | Reporter EID |

**Response (immediate — agents run in background):**
```json
{
  "run_id": "f4a8c2d1-1234-5678-abcd-ef9012345678",
  "status": "running",
  "ticket_id": "INC000201"
}
```

**Example:**
```bash
curl -X POST http://localhost:8090/agent/run \
  -H "Content-Type: application/json" \
  -d '{
    "ticket_id": "INC000201",
    "source": "ServiceNow",
    "title": "Missing supervisor assignment for user NMKOS046",
    "description": "User NMKOS046 in MARSHA app has no supervisor EID set in location HTDV7N.",
    "severity": "High",
    "affected_system": "MARSHA",
    "affected_location": "HTDV7N",
    "affected_eid": "NMKOS046",
    "reported_by": "JBROW012"
  }'
```

---

### Poll run state
```
GET /agent/run/{run_id}
```
**Example:**
```bash
curl http://localhost:8090/agent/run/f4a8c2d1-1234-5678-abcd-ef9012345678
```
**Response (while running):**
```json
{
  "run_id": "f4a8c2d1-1234-5678-abcd-ef9012345678",
  "status": "running",
  "ticket_id": "INC000201",
  "ticket": { ... },
  "started_at": "2024-05-15T10:00:00.000000",
  "completed_at": null,
  "agents_completed": ["sda_open", "spa", "sme", "rat"],
  "agent_conversation": [
    { "agent": "SDA", "content": "[SDA-OPEN]\nTICKET SUMMARY:\n...", "node": "sda_open" },
    { "agent": "SPA", "content": "[SPA - iteration 1]\nROOT CAUSE ANALYSIS:\n...", "node": "spa" }
  ],
  "ticket_status": "In Progress",
  "sda_summary": "TICKET SUMMARY:\n- Issue: ...",
  "spa_findings": "ROOT CAUSE ANALYSIS:\n...",
  "sme_verdict": "RESOLVED: Missing supervisor assignment\nFIX PLAN:\n1. ...",
  "risk_level": "Normal",
  "authorized": false,
  "pending_approval": true,
  "execution_log": [],
  "validation_report": {}
}
```

**Response (completed):**
```json
{
  "run_id": "f4a8c2d1-1234-5678-abcd-ef9012345678",
  "status": "completed",
  "ticket_status": "Closed",
  "authorized": true,
  "pending_approval": false,
  "risk_level": "Normal",
  "validation_report": {
    "status": "VALIDATED",
    "details": "VALIDATION REPORT:\nCheck 1 - User assignments: PASS\n..."
  },
  "execution_log": [
    {
      "agent": "EA",
      "report": "EXECUTION REPORT:\nStep 1: Patched supervisor EIDs → Result: success\n..."
    }
  ],
  "completed_at": "2024-05-15T10:04:32.000000"
}
```

---

### Approve and execute (human override)
```
POST /agent/run/{run_id}/approve
```
**Triggers:** EA → VA → SDA-close agents run in background with `authorized = True`.

**Example:**
```bash
curl -X POST http://localhost:8090/agent/run/f4a8c2d1-1234-5678-abcd-ef9012345678/approve
```
**Response:**
```json
{
  "run_id": "f4a8c2d1-1234-5678-abcd-ef9012345678",
  "message": "Override approved — EA/VA/SDA-close running"
}
```

**Error (already authorized):**
```json
{ "message": "Already authorized" }
```

---

### List all runs
```
GET /agent/runs
```
**Example:**
```bash
curl http://localhost:8090/agent/runs
```
**Response:**
```json
[
  {
    "run_id": "f4a8c2d1-1234-5678-abcd-ef9012345678",
    "status": "completed",
    "ticket_id": "INC000201",
    "started_at": "2024-05-15T10:00:00.000000"
  },
  {
    "run_id": "a1b2c3d4-5678-90ab-cdef-123456789012",
    "status": "running",
    "ticket_id": "INC000202",
    "started_at": "2024-05-15T10:05:00.000000"
  }
]
```

---

### Health check
```
GET /health
```
**Response:**
```json
{
  "status": "UP",
  "service": "aura-agent-service"
}
```

---

## Error Responses

| HTTP Status | Scenario |
|---|---|
| `200 OK` | Success |
| `404 Not Found` | `run_id` not in memory store |
| `422 Unprocessable Entity` | Invalid ticket payload (check severity/source enum values) |
| `500 Internal Server Error` | Agent execution error (check `error` field in run state) |

---

## Configuration

### `config.py`
```python
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
LLM_MODEL         = os.getenv("LLM_MODEL", "claude-sonnet-4-6")
LLM_MAX_TOKENS    = int(os.getenv("LLM_MAX_TOKENS", "2048"))

TAP_QUERY_URL    = os.getenv("TAP_QUERY_URL",    "http://localhost:8081")
TAP_UPDATER_URL  = os.getenv("TAP_UPDATER_URL",  "http://localhost:8082")
MARSHA_URL       = os.getenv("MARSHA_URL",       "http://localhost:8083")
MINT_URL         = os.getenv("MINT_URL",         "http://localhost:8084")
ACRS_URL         = os.getenv("ACRS_URL",         "http://localhost:8085")
VDS_URL          = os.getenv("VDS_URL",          "http://localhost:8086")

MAX_DISCUSSION_ITERATIONS = int(os.getenv("MAX_DISCUSSION_ITERATIONS", "3"))
```

### `.env` file
```env
ANTHROPIC_API_KEY=sk-ant-...
LLM_MODEL=claude-sonnet-4-6
LLM_MAX_TOKENS=2048
TAP_QUERY_URL=http://localhost:8081
TAP_UPDATER_URL=http://localhost:8082
MARSHA_URL=http://localhost:8083
MINT_URL=http://localhost:8084
ACRS_URL=http://localhost:8085
VDS_URL=http://localhost:8086
MAX_DISCUSSION_ITERATIONS=3
```

---

## Setup & Run

### Prerequisites
- Python 3.11 (not 3.14 — pre-release causes dependency failures)
- Anthropic API key
- All TAP services running (ports 8081-8086)

### Create virtual environment
```bash
cd aura-agent-service
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Run from terminal
```bash
source .venv/bin/activate
uvicorn main:app --host 0.0.0.0 --port 8090 --reload
```

### Verify running
```bash
curl http://localhost:8090/health
```

---

## Human-in-the-Loop Flow

The Authorizer agent always sets `pending_approval = True` and `authorized = False`. The workflow pauses at the `END` node in LangGraph because the conditional edge from Authorizer routes to `END` when `authorized = False`.

When the human clicks **Approve & Execute** in the UI:
1. `POST /agent/run/{id}/approve` is called
2. `authorized = True`, `pending_approval = False` are set in the run store
3. A new background thread starts `_run_override()` which calls `ea_node → va_node → sda_close_node` directly
4. UI continues polling `GET /agent/run/{id}` and shows agent progress

---

## Non-Functional Requirements

| Requirement | Target |
|---|---|
| Submission latency | < 100ms (returns run_id immediately; agents run in background thread) |
| Agent execution time | 2-8 minutes depending on LLM response time and iterations |
| Human approval gate | Always enforced — no autonomous execution without human confirm |
| Max discussion rounds | 3 (SPA ↔ SME iterations) — configurable via `MAX_DISCUSSION_ITERATIONS` |
| Run store | In-memory (resets on service restart — intended for POC/demo) |
| CORS | `*` allowed (UI at port 3000 proxies to this service) |

---

## Dependencies

| Service | Direction | Purpose |
|---|---|---|
| `aura-tap-query-service` :8081 | Downstream | SDA/SPA/SME/VA query agents |
| `aura-tap-updater-service` :8082 | Downstream | EA executes fixes directly |
| `aura-marsha-client` :8083 | Downstream | EA can use MARSHA-scoped mutations |
| `aura-mint-client` :8084 | Downstream | EA can use MINT-scoped mutations |
| `aura-acrs-client` :8085 | Downstream | EA can use ACRS-scoped mutations |
| `aura-vds-client` :8086 | Downstream | EA can use VDS-scoped identity mutations |
| `aura-ui` :3000 | Consumer | React UI polls this service; all agent APIs proxied via Vite |
