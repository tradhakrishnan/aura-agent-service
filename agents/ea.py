import json
from langchain_core.messages import AIMessage
from agents.base import get_llm, run_agent
from tools.tap_updater_tools import UPDATER_TOOLS
from tools.tap_query_tools import QUERY_TOOLS_BASIC, QUERY_TOOLS_PERMISSION
from graph.state import AuraState


# ── Permission ticket prompt ──────────────────────────────────────────────────
# IMPORTANT: no "mental" intermediate step — both tool calls are listed as
# explicit mandatory calls so the LLM cannot satisfy the prompt with text alone.

PROMPT_PERMISSION = (
    "You are Agent 6 - Execution Agent (EA) for Marriott's AURA system.\n"
    "This is a PERMISSION ticket. You MUST make exactly TWO tool calls in sequence.\n\n"
    "MANDATORY TOOL CALL 1 — read current state:\n"
    "  get_user_by_eid_and_app(eid=\"{eid}\", app=\"{system}\")\n\n"
    "MANDATORY TOOL CALL 2 — write the fix (do this immediately after call 1 returns):\n"
    "  patch_user_assignments(\n"
    "    eid=\"{eid}\",\n"
    "    app=\"{system}\",\n"
    "    assignments=<take the assignments list from call 1, then {action_verb} \"{permission}\" {preposition} it>\n"
    "  )\n\n"
    "RULES:\n"
    "- patch_user_assignments REPLACES the full list — preserve all existing assignments.\n"
    "- Do NOT output an execution report until BOTH tool calls have returned responses.\n"
    "- The task is NOT complete until patch_user_assignments has been called and returned success.\n"
    "- Do not modify any other user, field, or system.\n\n"
    "After both calls complete, output:\n"
    "EXECUTION REPORT:\n"
    "Call 1 get_user_by_eid_and_app: [assignments found]\n"
    "Call 2 patch_user_assignments:  [success / error response]\n"
    "EXECUTION STATUS: COMPLETE|FAILED\n"
)

# ── Default prompt for hotel/location/generic tickets ────────────────────────

PROMPT_DEFAULT = (
    "You are Agent 6 - Executing Agent (EA) for Marriott's AURA system.\n\n"
    "Your responsibilities:\n"
    "1. Read the authorized fix plan from the SME verdict carefully\n"
    "2. Execute EACH step of the fix plan precisely using the available tools\n"
    "3. Use query tools first to verify current state before making changes\n"
    "4. Apply changes using updater tools\n"
    "5. Log every action taken with its result\n\n"
    "CRITICAL RULES:\n"
    "- Execute ONLY what is in the approved fix plan — do not improvise\n"
    "- Use exact IDs/codes from the fix plan\n"
    "- If a tool call fails, report it and continue with the remaining steps\n"
    "- Do not make changes outside the scope of the fix plan\n\n"
    "Output format:\n"
    "EXECUTION REPORT:\n"
    "Step 1: [action taken] -> Result: [success/failure + details]\n"
    "Step 2: [action taken] -> Result: [success/failure + details]\n"
    "...\n"
    "EXECUTION STATUS: COMPLETE|PARTIAL|FAILED\n"
)


def ea_node(state: AuraState) -> dict:
    sme_verdict        = state.get("sme_verdict", "")
    ticket             = state["ticket"]
    ticket_type        = state.get("ticket_type", "generic")
    resolution_context = state.get("resolution_context", {})

    if ticket_type == "permission":
        rc         = resolution_context
        eid        = rc.get("eid",        ticket.get("affected_eid", ""))
        system     = rc.get("system",     ticket.get("affected_system", "MARSHA"))
        permission = rc.get("permission", "")
        action     = rc.get("action",     "add")

        action_verb = "adding"   if action == "add" else "removing"
        preposition = "to"       if action == "add" else "from"

        prompt = PROMPT_PERMISSION.format(
            eid         = eid,
            system      = system,
            permission  = permission,
            action_verb = action_verb,
            preposition = preposition,
        )
        context = (
            "Authorized action: " + action + " \"" + permission + "\" for " + eid + " in " + system + ".\n\n"
            "SME Fix Plan:\n" + sme_verdict + "\n\n"
            "Execute the two mandatory tool calls now. Do not stop after the first call."
        )
        # Both read and write tools for permission — TAP direct + MARSHA client service as backup
        perm_write_tools = [
            t for t in UPDATER_TOOLS
            if t.name in ("patch_user_assignments", "marsha_patch_user_assignments")
        ]
        tools = QUERY_TOOLS_PERMISSION + perm_write_tools
    else:
        prompt  = PROMPT_DEFAULT
        context = (
            "Authorized Fix Plan from SME:\n" + sme_verdict + "\n\n"
            "Original Ticket:\n" + json.dumps(ticket, indent=2) + "\n\n"
            "Execute each step of the fix plan now."
        )
        tools = UPDATER_TOOLS + QUERY_TOOLS_BASIC

    llm    = get_llm()
    result, _ = run_agent(llm, tools, prompt, context, max_iters=10)

    execution_log = [{"agent": "EA", "report": result}]

    return {
        "execution_log": execution_log,
        "ticket_status": "Resolved",
        "messages":      [AIMessage(content="[EA]\n" + result, name="EA")],
    }
