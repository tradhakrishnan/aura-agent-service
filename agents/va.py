import json
from langchain_core.messages import AIMessage
from agents.base import get_llm, run_agent
from tools.tap_query_tools import QUERY_TOOLS_FULL
from graph.state import AuraState

SYSTEM_PROMPT = """You are Agent 7 - Validation Agent (VA) for Marriott's AURA system.

Your responsibilities:
1. Query the current state of all data affected by the fix
2. Compare it against the EXPECTED state described in the SME fix plan
3. Verify each change was applied correctly
4. Cross-validate relationships (e.g. user in location → location has hotel → hotel is active)
5. Report VALIDATED if all checks pass, FAILED if any check fails

Use query tools to read current data — do not make any changes.

Output format:
VALIDATION REPORT:
Check 1 - [what was checked]: PASS|FAIL
  Expected: [expected value]
  Actual:   [actual value from TAP]

Check 2 - [what was checked]: PASS|FAIL
  Expected: [expected value]
  Actual:   [actual value from TAP]

...

VALIDATION STATUS: VALIDATED|FAILED
SUMMARY: [overall assessment]
"""


def va_node(state: AuraState) -> dict:
    sme_verdict   = state.get("sme_verdict", "")
    execution_log = state.get("execution_log", [])
    ticket        = state["ticket"]

    context = f"""Original Ticket: {json.dumps(ticket, indent=2)}

SME Fix Plan (expected state after fix):
{sme_verdict}

Execution Log (what was done):
{json.dumps(execution_log, indent=2)}

Now query TAP to verify all changes were applied correctly."""

    llm    = get_llm()
    result, _ = run_agent(llm, QUERY_TOOLS_FULL, SYSTEM_PROMPT, context)

    validation_passed = "VALIDATED" in result and "FAILED" not in result

    validation_report = {
        "status":  "VALIDATED" if validation_passed else "FAILED",
        "details": result,
    }

    return {
        "validation_report": validation_report,
        "messages":          [AIMessage(content=f"[VA]\n{result}", name="VA")],
    }
