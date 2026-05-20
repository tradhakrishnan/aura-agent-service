import json
from langchain_core.messages import AIMessage
from agents.base import get_llm, run_agent
from tools.tap_query_tools import QUERY_TOOLS_FULL, QUERY_TOOLS_PERMISSION
from graph.state import AuraState


# ── Permission ticket — focused 3-step analysis ──────────────────────────────

PROMPT_PERMISSION = """You are Agent 2 - Software Programmer Agent (SPA).
This is a PERMISSION ticket. Follow these exact steps — no more.

Step 1: Call get_user_by_eid_and_app(eid="{eid}", app="{system}") once.
Step 2: Check if "{permission}" is in the assignments list returned.
Step 3: Output your findings.

ROOT CAUSE ANALYSIS:
- Current Assignments: [exact list from API]
- Permission "{permission}": PRESENT / MISSING
- Recommended Fix: {action} "{permission}" to {eid}'s {system} assignments
- Data Verified: get_user_by_eid_and_app({eid}, {system}) → [assignment count] assignments found

Do NOT call any other tools. Do NOT investigate hotels, locations, roles, or other systems.
"""

# ── Hotel/location and generic tickets — existing broad analysis ─────────────

PROMPT_DEFAULT = """You are Agent 2 - Software Programmer Agent (SPA) for Marriott's AURA system.

Your responsibilities:
1. Review the Service Desk Analyst's summary
2. Dig deeper into the TAP data using available query tools
3. Investigate relationships: hotel ↔ location ↔ user
4. Verify data consistency (e.g. user in location? location has hotel? status correct?)
5. Identify the specific technical root cause

Output your findings in this format:
ROOT CAUSE ANALYSIS:
- Technical Findings: [what you found in TAP data]
- Data Inconsistency: [specific mismatch or problem found]
- Probable Root Cause: [your technical assessment]
- Recommended Fix: [specific steps to resolve — be precise with IDs/codes]
- Data Verified: [list of queries you ran and what they returned]
"""


def spa_node(state: AuraState) -> dict:
    sda_summary        = state.get("sda_summary", "")
    sme_verdict        = state.get("sme_verdict", "")
    iteration          = state.get("iteration", 0)
    ticket             = state["ticket"]
    ticket_type        = state.get("ticket_type", "generic")
    resolution_context = state.get("resolution_context", {})

    context = f"""Ticket: {json.dumps(ticket, indent=2)}

SDA Summary:
{sda_summary}
"""
    if sme_verdict and "NEEDS_MORE_RESEARCH" in sme_verdict:
        context += f"\nSME feedback requiring further investigation:\n{sme_verdict}"

    if ticket_type == "permission":
        rc     = resolution_context
        prompt = PROMPT_PERMISSION.format(
            eid        = rc.get("eid", ticket.get("affected_eid", "")),
            system     = rc.get("system", ticket.get("affected_system", "MARSHA")),
            permission = rc.get("permission", ""),
            action     = rc.get("action", "add"),
        )
        tools = QUERY_TOOLS_PERMISSION   # only get_user_by_eid_and_app
    else:
        prompt = PROMPT_DEFAULT
        tools  = QUERY_TOOLS_FULL

    llm    = get_llm()
    result, _ = run_agent(llm, tools, prompt, context)

    return {
        "spa_findings": result,
        "iteration":    iteration + 1,
        "messages":     [AIMessage(content=f"[SPA - iteration {iteration + 1}]\n{result}", name="SPA")],
    }
