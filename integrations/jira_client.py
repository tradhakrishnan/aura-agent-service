"""
Jira Cloud integration for AURA.

Lifecycle per ticket:
  1. Webhook arrives  → transition issue to In Progress + opening comment
  2. After each agent → add a condensed progress comment
  3. Awaiting approval → comment asking human to approve in AURA UI
  4. Approved + validated → transition to Done + resolution comment
  5. Rejected          → add rejection comment (issue stays open for manual triage)

All public functions are fire-and-forget (exceptions are swallowed) so a
Jira outage never blocks the agent pipeline.
"""

import threading
from config import JIRA_URL, JIRA_EMAIL, JIRA_API_TOKEN

_client = None
_lock   = threading.Lock()


# ── Client singleton ──────────────────────────────────────────────────────────

def _get_client():
    global _client
    if _client is not None:
        return _client
    if not (JIRA_URL and JIRA_EMAIL and JIRA_API_TOKEN):
        return None
    with _lock:
        if _client is None:
            from jira import JIRA
            _client = JIRA(server=JIRA_URL, basic_auth=(JIRA_EMAIL, JIRA_API_TOKEN))
    return _client


def is_configured() -> bool:
    return bool(JIRA_URL and JIRA_EMAIL and JIRA_API_TOKEN)


# ── ADF → plain text ──────────────────────────────────────────────────────────

def _adf_to_text(node) -> str:
    """Recursively extract plain text from Atlassian Document Format JSON."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        t = node.get("type", "")
        if t == "text":
            return node.get("text", "")
        if t == "hardBreak":
            return "\n"
        children = node.get("content", [])
        sep = "\n" if t in ("paragraph", "bulletList", "orderedList", "listItem", "heading") else ""
        return sep.join(_adf_to_text(c) for c in children)
    if isinstance(node, list):
        return "".join(_adf_to_text(c) for c in node)
    return str(node)


# ── Issue read ────────────────────────────────────────────────────────────────

def get_project_issues(project_key: str, max_results: int = 50) -> list:
    """Return all issues in a project, newest first.
    Uses /rest/api/3/search/jql directly (Atlassian deprecated /rest/api/3/search in 2025).
    """
    if not (JIRA_URL and JIRA_EMAIL and JIRA_API_TOKEN and project_key):
        return []
    try:
        import requests as _requests
        jql = f'project = "{project_key}" ORDER BY created DESC'
        resp = _requests.post(
            f"{JIRA_URL.rstrip('/')}/rest/api/3/search/jql",
            auth=(JIRA_EMAIL, JIRA_API_TOKEN),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            json={
                "jql": jql,
                "maxResults": max_results,
                "fields": ["summary", "description", "priority", "status",
                           "issuetype", "assignee", "reporter", "created", "updated"],
            },
            timeout=15,
        )
        resp.raise_for_status()
        raw_issues = resp.json().get("issues", [])
        result = []
        for issue in raw_issues:
            f         = issue.get("fields", {})
            priority  = f.get("priority")  or {}
            status    = f.get("status")    or {}
            issuetype = f.get("issuetype") or {}
            assignee  = f.get("assignee")  or {}
            reporter  = f.get("reporter")  or {}
            raw_desc  = f.get("description") or ""
            desc      = _adf_to_text(raw_desc) if isinstance(raw_desc, dict) else str(raw_desc)
            result.append({
                "key":         issue.get("key", ""),
                "summary":     f.get("summary") or "",
                "description": desc[:600],
                "priority":    priority.get("name",  "Medium"),
                "status":      status.get("name",    "Open"),
                "issuetype":   issuetype.get("name", "Bug"),
                "assignee":    assignee.get("displayName"),
                "reporter":    reporter.get("displayName"),
                "created":     f.get("created"),
                "updated":     f.get("updated"),
                "browse_url":  f"{JIRA_URL.rstrip('/')}/browse/{issue.get('key', '')}",
            })
        return result
    except Exception:
        return []


def get_issue(issue_key: str) -> dict:
    """Return issue fields as plain-text dict for agent consumption."""
    client = _get_client()
    if not client:
        return {}
    try:
        issue   = client.issue(issue_key)
        f       = issue.fields
        raw_desc = f.description or ""
        desc    = _adf_to_text(raw_desc) if isinstance(raw_desc, dict) else str(raw_desc)
        priority = getattr(f, "priority", None)
        return {
            "key":         issue.key,
            "summary":     f.summary or "",
            "description": desc,
            "priority":    priority.name if priority else "Medium",
            "status":      f.status.name if f.status else "",
            "issuetype":   f.issuetype.name if f.issuetype else "Bug",
        }
    except Exception:
        return {}


# ── Comments ──────────────────────────────────────────────────────────────────

def add_comment(issue_key: str, body: str) -> None:
    client = _get_client()
    if not client or not issue_key:
        return
    try:
        client.add_comment(issue_key, body)
    except Exception:
        pass


# ── Transitions ───────────────────────────────────────────────────────────────

def _transition(issue_key: str, *name_candidates: str) -> None:
    """Transition an issue by trying candidate transition names (case-insensitive)."""
    client = _get_client()
    if not client or not issue_key:
        return
    try:
        available = client.transitions(issue_key)
        for t in available:
            if t["name"].lower() in [n.lower() for n in name_candidates]:
                client.transition_issue(issue_key, t["id"])
                return
    except Exception:
        pass


def transition_in_progress(issue_key: str) -> None:
    _transition(issue_key, "In Progress", "Start Progress", "Start", "In Development")


def transition_done(issue_key: str) -> None:
    _transition(issue_key, "Done", "Resolve Issue", "Close Issue", "Resolved", "Complete")


# ── Lifecycle comments ────────────────────────────────────────────────────────

AGENT_LABELS = {
    "sda_open":   "Service Desk Analyst (SDA)",
    "spa":        "Software Programmer Agent (SPA)",
    "sme":        "Subject Matter Expert (SME)",
    "rat":        "Risk Assessment Team (RAT)",
    "authorizer": "Authorizer",
    "ea":         "Execution Agent (EA)",
    "va":         "Validation Agent (VA)",
    "sda_close":  "Service Desk Analyst — Close (SDA)",
}


def comment_picked_up(issue_key: str) -> None:
    add_comment(issue_key,
        "🤖 *AURA* has picked up this issue and started autonomous analysis.\n"
        "Follow progress in the AURA dashboard."
    )


def comment_agent_done(issue_key: str, node_name: str, summary: str) -> None:
    label = AGENT_LABELS.get(node_name, node_name.upper())
    # Keep comments short — truncate long agent output
    body = summary[:800] + ("…" if len(summary) > 800 else "")
    add_comment(issue_key, f"*[AURA · {label}]*\n\n{body}")


def comment_awaiting_approval(issue_key: str, risk_level: str) -> None:
    add_comment(issue_key,
        f"⏳ *AURA is awaiting human approval.*\n\n"
        f"Risk level: *{risk_level}*\n\n"
        f"An authorised operator must approve execution in the AURA dashboard before any changes are applied."
    )


def comment_approved(issue_key: str) -> None:
    add_comment(issue_key, "✅ *Approved by human operator.* AURA is now executing the fix.")


def comment_rejected(issue_key: str) -> None:
    add_comment(issue_key,
        "❌ *Execution rejected by human operator.*\n\n"
        "The fix plan was reviewed and rejected. Manual intervention may be required."
    )


def comment_resolved(issue_key: str, resolution: str) -> None:
    body = resolution[:1200] + ("…" if len(resolution) > 1200 else "")
    add_comment(issue_key,
        f"✅ *AURA has resolved this issue.*\n\n{body}\n\n"
        "_Closed automatically by AURA autonomous agent._"
    )
