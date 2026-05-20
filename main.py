import threading
import uuid
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from models.ticket import Ticket
from graph.workflow import aura_graph
from agents.ea import ea_node
from agents.va import va_node
from agents.sda import sda_close_node
import db.mongo as mongo
import integrations.jira_client as jira
from config import JIRA_URL, JIRA_PROJECT_KEY

# In-memory run store (fast access for polling)
runs: dict = {}


# ── Startup: rehydrate from MongoDB ──────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    docs = mongo.load_all_runs()
    for doc in docs:
        run_id = doc.get("run_id")
        if not run_id:
            continue
        # Any run still "running" when the server died can't be resumed — mark failed
        if doc.get("status") == "running":
            doc["status"] = "failed"
            doc["error"]  = "Service restarted while run was in progress"
            mongo.update_run_fields(run_id, {"status": "failed", "error": doc["error"]})
        runs[run_id] = doc
    yield


app = FastAPI(
    title="AURA Agent Service",
    description="Marriott Service Desk Agent Framework — 7-agent LangGraph orchestration",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

NODE_LABEL_MAP = {
    "sda_open":   "SDA",
    "spa":        "SPA",
    "sme":        "SME",
    "rat":        "RAT",
    "authorizer": "AUTHORIZER",
    "ea":         "EA",
    "va":         "VA",
    "sda_close":  "SDA",
}

# Jira browse URL helper
def _jira_browse_url(issue_key: str) -> str:
    return f"{JIRA_URL.rstrip('/')}/browse/{issue_key}" if JIRA_URL and issue_key else ""

# Extract a short summary string for Jira comments based on which node just ran
def _node_summary(node_name: str, run_state: dict) -> str:
    if node_name == "sda_open":
        return run_state.get("sda_summary", "")
    if node_name == "spa":
        return run_state.get("spa_findings", "")
    if node_name == "sme":
        return run_state.get("sme_verdict", "")
    if node_name == "rat":
        return f"Risk level: {run_state.get('risk_level', 'Unknown')}"
    if node_name == "authorizer":
        if run_state.get("pending_approval"):
            return "Awaiting human approval before execution."
        return "Authorisation check complete."
    if node_name == "ea":
        logs = run_state.get("execution_log", [])
        return "\n".join(e.get("report", "") for e in logs) if logs else "Execution complete."
    if node_name == "va":
        report = run_state.get("validation_report", {})
        return f"Validation status: {report.get('status', 'unknown')}. {report.get('details', '')}"
    if node_name == "sda_close":
        return f"Ticket status: {run_state.get('ticket_status', 'Resolved')}"
    return ""


def _build_initial_state(ticket: Ticket) -> dict:
    return {
        "ticket":            ticket.model_dump(),
        "tap_context":       {},
        "sda_summary":       "",
        "spa_findings":      "",
        "sme_verdict":       "",
        "risk_level":        "",
        "authorized":        False,
        "pending_approval":  False,
        "rejected":          False,
        "execution_log":     [],
        "validation_report": {},
        "ticket_status":     "Open",
        "messages":          [],
        "iteration":         0,
    }


def _stream_graph(run_id: str, initial_state: dict):
    """Run LangGraph with streaming — updates runs[run_id] after each node."""
    issue_key = runs[run_id].get("jira_issue_key", "")

    for event in aura_graph.stream(initial_state):
        for node_name, node_state in event.items():
            if node_name.startswith("__"):
                continue

            agents_done = runs[run_id].get("agents_completed", [])
            if node_name not in agents_done:
                agents_done = agents_done + [node_name]

            update = {k: v for k, v in node_state.items() if k != "messages"}
            update["agents_completed"] = agents_done

            conv = runs[run_id].get("agent_conversation", [])
            for m in node_state.get("messages", []):
                if hasattr(m, "name") and m.name:
                    conv = conv + [{"agent": m.name, "content": m.content, "node": node_name}]
            update["agent_conversation"] = conv

            runs[run_id].update(update)

            mongo.update_run_fields(run_id, {
                k: v for k, v in update.items()
                if k not in ("run_id", "ticket_id", "started_at")
            })

            # Jira: post per-agent comment
            if issue_key:
                summary = _node_summary(node_name, runs[run_id])
                jira.comment_agent_done(issue_key, node_name, summary)
                # When awaiting approval, post the approval prompt comment
                if node_name == "authorizer" and runs[run_id].get("pending_approval"):
                    jira.comment_awaiting_approval(issue_key, runs[run_id].get("risk_level", "Unknown"))


def _run_background(run_id: str, initial_state: dict):
    mongo.set_current_run_id(run_id)
    issue_key = runs[run_id].get("jira_issue_key", "")
    try:
        _stream_graph(run_id, initial_state)
        completed_at = datetime.now(timezone.utc).isoformat()
        runs[run_id]["completed_at"] = completed_at
        # If ended at authorizer (pending approval), status stays "running" so UI keeps polling
        if runs[run_id].get("pending_approval"):
            runs[run_id]["status"] = "running"
            mongo.update_run_fields(run_id, {"status": "running", "completed_at": completed_at})
        else:
            runs[run_id]["status"] = "completed"
            mongo.update_run_fields(run_id, {"status": "completed", "completed_at": completed_at})
    except Exception as e:
        runs[run_id]["status"] = "failed"
        runs[run_id]["error"]  = str(e)
        mongo.update_run_fields(run_id, {"status": "failed", "error": str(e)})


@app.post("/agent/run")
async def run_agent(ticket: Ticket):
    """Submit a ticket — returns run_id immediately, agents run in background."""
    run_id     = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc).isoformat()
    issue_key  = ticket.jira_issue_key or ""

    run_doc = {
        "run_id":             run_id,
        "status":             "running",
        "ticket_id":          ticket.ticket_id,
        "ticket":             ticket.model_dump(),
        "started_at":         started_at,
        "agents_completed":   [],
        "agent_conversation": [],
        "ticket_status":      "Open",
        "sda_summary":        "",
        "spa_findings":       "",
        "sme_verdict":        "",
        "risk_level":         "",
        "authorized":         False,
        "pending_approval":   False,
        "rejected":           False,
        "execution_log":      [],
        "validation_report":  {},
        "jira_issue_key":     issue_key,
        "jira_browse_url":    _jira_browse_url(issue_key),
    }

    runs[run_id] = run_doc.copy()
    mongo.save_run(run_doc)

    # Jira: transition to In Progress + comment
    if issue_key:
        jira.transition_in_progress(issue_key)
        jira.comment_picked_up(issue_key)

    t = threading.Thread(target=_run_background, args=(run_id, _build_initial_state(ticket)), daemon=True)
    t.start()

    return {"run_id": run_id, "status": "running", "ticket_id": ticket.ticket_id, "jira_browse_url": _jira_browse_url(issue_key)}


def _run_override(run_id: str):
    """Run ea → va → sda_close after human approval."""
    mongo.set_current_run_id(run_id)
    issue_key = runs[run_id].get("jira_issue_key", "")
    state = {
        "ticket":            runs[run_id].get("ticket", {}),
        "tap_context":       runs[run_id].get("tap_context", {}),
        "sda_summary":       runs[run_id].get("sda_summary", ""),
        "spa_findings":      runs[run_id].get("spa_findings", ""),
        "sme_verdict":       runs[run_id].get("sme_verdict", ""),
        "risk_level":        runs[run_id].get("risk_level", ""),
        "authorized":        True,
        "rejected":          False,
        "execution_log":     runs[run_id].get("execution_log", []),
        "validation_report": runs[run_id].get("validation_report", {}),
        "ticket_status":     runs[run_id].get("ticket_status", "Open"),
        "messages":          [],
        "iteration":         runs[run_id].get("iteration", 0),
    }
    try:
        for node_fn, node_name in [(ea_node, "ea"), (va_node, "va"), (sda_close_node, "sda_close")]:
            node_state = node_fn(state)
            state.update({k: v for k, v in node_state.items() if k != "messages"})

            agents_done = runs[run_id].get("agents_completed", [])
            if node_name not in agents_done:
                agents_done = agents_done + [node_name]

            conv = runs[run_id].get("agent_conversation", [])
            for m in node_state.get("messages", []):
                if hasattr(m, "name") and m.name:
                    conv = conv + [{"agent": m.name, "content": m.content, "node": node_name}]

            update = {k: v for k, v in node_state.items() if k != "messages"}
            update["agents_completed"]   = agents_done
            update["agent_conversation"] = conv

            runs[run_id].update(update)
            mongo.update_run_fields(run_id, {
                k: v for k, v in update.items()
                if k not in ("run_id", "ticket_id", "started_at")
            })

            if issue_key:
                jira.comment_agent_done(issue_key, node_name, _node_summary(node_name, runs[run_id]))

        completed_at = datetime.now(timezone.utc).isoformat()
        runs[run_id]["status"]       = "completed"
        runs[run_id]["completed_at"] = completed_at
        mongo.update_run_fields(run_id, {"status": "completed", "completed_at": completed_at})

        # Jira: resolution comment + transition to Done
        if issue_key:
            resolution = runs[run_id].get("sme_verdict", "") or runs[run_id].get("sda_summary", "")
            jira.comment_resolved(issue_key, resolution)
            jira.transition_done(issue_key)
    except Exception as e:
        runs[run_id]["status"] = "failed"
        runs[run_id]["error"]  = str(e)
        mongo.update_run_fields(run_id, {"status": "failed", "error": str(e)})


@app.post("/agent/run/{run_id}/approve")
async def approve_run(run_id: str):
    """Human override — force-approve and run EA → VA → SDA-close."""
    if run_id not in runs:
        raise HTTPException(status_code=404, detail="Run not found")
    if runs[run_id].get("authorized"):
        return {"message": "Already authorized"}
    if runs[run_id].get("rejected"):
        return {"message": "Run was rejected — cannot approve after rejection"}
    runs[run_id]["authorized"]       = True
    runs[run_id]["pending_approval"] = False
    runs[run_id]["rejected"]         = False
    runs[run_id]["status"]           = "running"
    mongo.update_run_fields(run_id, {
        "authorized": True, "pending_approval": False, "rejected": False, "status": "running"
    })
    issue_key = runs[run_id].get("jira_issue_key", "")
    if issue_key:
        jira.comment_approved(issue_key)
    t = threading.Thread(target=_run_override, args=(run_id,), daemon=True)
    t.start()
    return {"run_id": run_id, "message": "Override approved — EA/VA/SDA-close running"}


@app.post("/agent/run/{run_id}/retry")
async def retry_run(run_id: str):
    """Retry a failed run — creates a fresh run using the same ticket."""
    if run_id not in runs:
        raise HTTPException(status_code=404, detail="Run not found")
    if runs[run_id].get("status") not in ("failed",):
        return {"message": "Only failed runs can be retried"}
    ticket_data = runs[run_id].get("ticket")
    if not ticket_data:
        raise HTTPException(status_code=400, detail="No ticket data found in run")
    ticket = Ticket(**ticket_data)
    return await run_agent(ticket)


@app.post("/agent/run/{run_id}/reject")
async def reject_run(run_id: str):
    """Human rejection — mark run as rejected, no execution will happen."""
    if run_id not in runs:
        raise HTTPException(status_code=404, detail="Run not found")
    if runs[run_id].get("authorized"):
        return {"message": "Already authorized and executing — cannot reject"}
    if runs[run_id].get("rejected"):
        return {"message": "Already rejected"}
    rejected_at = datetime.now(timezone.utc).isoformat()
    runs[run_id]["rejected"]         = True
    runs[run_id]["pending_approval"] = False
    runs[run_id]["authorized"]       = False
    runs[run_id]["status"]           = "completed"
    runs[run_id]["ticket_status"]    = "Rejected"
    runs[run_id]["completed_at"]     = rejected_at
    mongo.update_run_fields(run_id, {
        "rejected": True, "pending_approval": False, "authorized": False,
        "status": "completed", "ticket_status": "Rejected", "completed_at": rejected_at,
    })
    issue_key = runs[run_id].get("jira_issue_key", "")
    if issue_key:
        jira.comment_rejected(issue_key)
    return {"run_id": run_id, "message": "Run rejected — no execution will proceed"}


@app.get("/agent/run/{run_id}")
async def get_run(run_id: str):
    """Poll for current run state."""
    if run_id not in runs:
        raise HTTPException(status_code=404, detail="Run not found")
    return runs[run_id]


@app.get("/agent/run/{run_id}/prompts")
async def get_run_prompts(run_id: str):
    """Return full LLM prompt history for a run from MongoDB."""
    if run_id not in runs:
        raise HTTPException(status_code=404, detail="Run not found")
    doc = mongo.get_run(run_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Run not found in database")
    return {
        "run_id":         run_id,
        "ticket_id":      doc.get("ticket_id"),
        "status":         doc.get("status"),
        "started_at":     doc.get("started_at"),
        "completed_at":   doc.get("completed_at"),
        "prompt_history": doc.get("prompt_history", []),
    }


@app.get("/agent/runs")
async def list_runs():
    return [
        {
            "run_id":           k,
            "status":           v["status"],
            "ticket_id":        v.get("ticket_id"),
            "ticket_title":     v.get("ticket", {}).get("title", ""),
            "ticket_severity":  v.get("ticket", {}).get("severity", ""),
            "ticket_status":    v.get("ticket_status", "Open"),
            "risk_level":       v.get("risk_level", ""),
            "agents_completed": len(v.get("agents_completed", [])),
            "pending_approval": v.get("pending_approval", False),
            "rejected":         v.get("rejected", False),
            "started_at":       v.get("started_at"),
            "completed_at":     v.get("completed_at"),
            "jira_issue_key":   v.get("jira_issue_key", ""),
            "jira_browse_url":  v.get("jira_browse_url", ""),
        }
        for k, v in reversed(list(runs.items()))
    ]


# ── Jira webhook ──────────────────────────────────────────────────────────────

@app.post("/agent/webhook/jira")
async def jira_webhook(request: Request):
    """
    Receive Jira Cloud webhook — acknowledge only.
    Issues are displayed in the Jira Issues page and run manually by the operator.
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    event     = payload.get("webhookEvent", "")
    issue     = payload.get("issue", {})
    issue_key = issue.get("key", "") if issue else ""

    return {
        "message":   "Webhook received — open Jira Issues in AURA to run agents manually",
        "event":     event,
        "issue_key": issue_key,
    }


@app.get("/agent/jira/issues")
async def list_jira_issues():
    """Fetch open Jira issues from the configured project."""
    if not jira.is_configured():
        return {"configured": False, "issues": [], "project_key": ""}
    issues = jira.get_project_issues(JIRA_PROJECT_KEY)
    # Annotate each issue with whether AURA has already processed it
    processed_keys = {
        v.get("ticket_id") for v in runs.values()
        if v.get("jira_issue_key") or v.get("ticket", {}).get("source") == "Jira"
    } | {
        v.get("jira_issue_key") for v in runs.values() if v.get("jira_issue_key")
    }
    for issue in issues:
        issue["aura_run_id"] = next(
            (k for k, v in runs.items() if v.get("jira_issue_key") == issue["key"] or v.get("ticket_id") == issue["key"]),
            None,
        )
    return {"configured": True, "issues": issues, "project_key": JIRA_PROJECT_KEY}


@app.get("/agent/webhook/jira/status")
async def jira_webhook_status():
    """Returns whether Jira integration is configured."""
    return {"configured": jira.is_configured(), "jira_url": JIRA_URL or None}


@app.get("/health")
async def health():
    return {"status": "UP", "service": "aura-agent-service"}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8090, reload=False)
