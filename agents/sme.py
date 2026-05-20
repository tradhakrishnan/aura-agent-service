import json
from langchain_core.messages import AIMessage
from agents.base import get_llm, run_agent
from tools.tap_query_tools import QUERY_TOOLS_FULL
from graph.state import AuraState


# ── Permission ticket — zero tool calls, pure confirmation ───────────────────

PROMPT_PERMISSION = """You are Agent 3 - Subject Matter Expert (SME).
SPA has already confirmed the permission gap with a direct API call.
No further investigation is needed.

Confirm the finding and produce the fix plan.

RESOLVED: {permission} is {gap_state} in {eid}'s {system} assignments.

FIX PLAN:
1. {action_verb} "{permission}" {preposition} {eid}'s {system} assignments list
2. Verify via get_user_by_eid_and_app({eid}, {system}) that the change took effect

Risk note: Permission assignment change — {system} standard procedure, Normal risk.
"""

# ── Hotel/location and generic — existing domain analysis ────────────────────

PROMPT_DEFAULT = """You are Agent 3 - Subject Matter Expert Agent (SME) for Marriott's AURA system.

Your responsibilities:
1. Review the SDA summary and SPA findings
2. Apply your deep domain expertise in Marriott's TAP systems (MARSHA, MINT, ACRS)
3. Confirm or challenge the root cause
4. If root cause is clear: produce a precise, executable fix plan
5. If more investigation is needed: specify exactly what to investigate

You MUST begin your response with one of:
  RESOLVED: <confirmed root cause>
  NEEDS_MORE_RESEARCH: <specific gaps to investigate>

If RESOLVED, follow with:
FIX PLAN:
1. [exact action with specific IDs/codes]
2. [exact action with specific IDs/codes]
...

If NEEDS_MORE_RESEARCH, follow with:
INVESTIGATE:
- [specific data point or query needed]
"""


def sme_node(state: AuraState) -> dict:
    ticket             = state["ticket"]
    sda_summary        = state.get("sda_summary", "")
    spa_findings       = state.get("spa_findings", "")
    iteration          = state.get("iteration", 0)
    ticket_type        = state.get("ticket_type", "generic")
    resolution_context = state.get("resolution_context", {})

    if ticket_type == "permission":
        rc         = resolution_context
        eid        = rc.get("eid",        ticket.get("affected_eid", ""))
        system     = rc.get("system",     ticket.get("affected_system", "MARSHA"))
        permission = rc.get("permission", "")
        action     = rc.get("action",     "add")

        # Derive natural-language phrasing
        gap_state   = "MISSING" if action == "add" else "PRESENT (excess)"
        action_verb = "Add"     if action == "add" else "Remove"
        preposition = "to"      if action == "add" else "from"

        prompt = PROMPT_PERMISSION.format(
            eid         = eid,
            system      = system,
            permission  = permission,
            action      = action,
            gap_state   = gap_state,
            action_verb = action_verb,
            preposition = preposition,
        )
        # No tools — SME just confirms and plans, SPA already has the data
        tools   = []
        context = f"SPA Findings:\n{spa_findings}"
    else:
        prompt  = PROMPT_DEFAULT
        tools   = QUERY_TOOLS_FULL
        context = f"""Ticket: {json.dumps(ticket, indent=2)}

SDA Summary:
{sda_summary}

SPA Findings (iteration {iteration}):
{spa_findings}

Review these findings and determine if the root cause is confirmed.
"""

    llm    = get_llm()
    result, _ = run_agent(llm, tools, prompt, context)

    return {
        "sme_verdict": result,
        "messages":    [AIMessage(content=f"[SME]\n{result}", name="SME")],
    }
