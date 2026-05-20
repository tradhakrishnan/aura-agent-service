import json
from langchain_core.messages import AIMessage
from agents.base import get_llm, run_agent
from graph.state import AuraState

SYSTEM_PROMPT = """You are Agent 5 - Authorizing Agent for Marriott's AURA system.

Your responsibilities:
1. Review the full case: ticket, root cause, fix plan, and risk level
2. Assess whether the fix plan is safe, precise, and proportionate to the issue
3. Provide your RECOMMENDATION — a human will make the final approval decision

You MUST begin your response with:
RECOMMENDATION: APPROVE|REJECT

Then provide:
ASSESSMENT NOTES:
- Fix Plan Review: [assessment of the fix steps]
- Risk Acceptance: [why risk level is acceptable or not]
- Conditions: [any conditions or constraints for execution]

Note: This is a recommendation only. A human operator will make the final authorization decision.
"""


def authorizer_node(state: AuraState) -> dict:
    ticket      = state["ticket"]
    sme_verdict = state.get("sme_verdict", "")
    risk_level  = state.get("risk_level", "Normal")

    context = f"""Ticket: {json.dumps(ticket, indent=2)}

SME Root Cause and Fix Plan:
{sme_verdict}

Risk Assessment: {risk_level}

Assess this fix and provide your recommendation. A human will make the final call."""

    llm    = get_llm()
    result, _ = run_agent(llm, [], SYSTEM_PROMPT, context)

    recommendation = "APPROVE" if "APPROVE" in result else "REJECT"

    return {
        "authorized":       False,          # always False until human approves
        "pending_approval": True,           # signals UI to show Approve/Reject buttons
        "messages":         [AIMessage(content=f"[AUTHORIZER]\nRECOMMENDATION: {recommendation}\n\n{result}", name="AUTHORIZER")],
    }
