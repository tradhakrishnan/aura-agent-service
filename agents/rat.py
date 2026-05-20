import json
from langchain_core.messages import AIMessage
from agents.base import get_llm, run_agent
from graph.state import AuraState

SYSTEM_PROMPT = """You are Agent 4 - Risk Assessment Tool (RAT) for Marriott's AURA system.

Your responsibilities:
1. Review the confirmed root cause and proposed fix plan from the SME
2. Assess the risk and urgency of the fix
3. Classify the resolution into exactly one category

Classification criteria:
- Immediate: Critical system impact, affects many hotels/users, revenue at risk, needs action within hours
- Normal:    Moderate impact, affects limited scope, can be resolved in standard timeline (1-2 days)
- Escalated: Complex change, requires senior review, cross-system impact, or regulatory concern

You MUST begin your response with:
RISK_LEVEL: Immediate|Normal|Escalated

Then provide:
ASSESSMENT:
- Impact Scope: [how many hotels/locations/users affected]
- Business Impact: [revenue, operations, compliance risk]
- Fix Complexity: [simple/moderate/complex]
- Reasoning: [why this classification]
"""


def rat_node(state: AuraState) -> dict:
    ticket      = state["ticket"]
    sme_verdict = state.get("sme_verdict", "")
    iteration   = state.get("iteration", 0)

    context = f"""Ticket: {json.dumps(ticket, indent=2)}

SME Verdict and Fix Plan:
{sme_verdict}

Discussion iterations completed: {iteration}

Assess the risk level for this fix."""

    llm    = get_llm()
    result, _ = run_agent(llm, [], SYSTEM_PROMPT, context)

    # Extract risk level from response
    risk_level = "Normal"
    for level in ["Immediate", "Escalated", "Normal"]:
        if level in result:
            risk_level = level
            break

    return {
        "risk_level": risk_level,
        "messages":   [AIMessage(content=f"[RAT]\n{result}", name="RAT")],
    }
