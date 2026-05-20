import re
import json
from langchain_core.messages import AIMessage
from agents.base import get_llm, run_agent
from tools.tap_query_tools import QUERY_TOOLS_BASIC, QUERY_TOOLS_PERMISSION, get_user_by_eid
from graph.state import AuraState


# ── Ticket type classifier (heuristic, zero LLM tokens) ─────────────────────

PERM_KEYWORDS = [
    "permission", "role", "missing role", "missing permission",
    "not authorized", "access", "unauthorized", "privilege",
    "hotel reservation manager", "revenue manager",
]

# EID: 4-7 uppercase letters + 2-4 digits  e.g. WITSF960, SYIPL670, GPBGY085
EID_RE = re.compile(r'\b([A-Z]{4,7}\d{2,4})\b')

# Quoted permission name (straight quotes only)
QUOTE_RE = re.compile(r'["\']([^"\']+)["\']')

# Unquoted permission after keywords — inline (?i:...) keeps keywords case-insensitive
# while [A-Z] stays uppercase-only so lowercase words like "for" are NOT captured.
# Searched against title and description separately (not combined) to stay within {3,40}.
PERM_AFTER_RE = re.compile(
    r'(?i:permission|role|access to(?:\s+permission)?)\s+([A-Z][A-Za-z ]{3,40}?)(?:\s*[.,\-]|\s*$)',
)

# Parse SYSTEM_CONFIRMED line from SDA discovery output
SYSTEM_CONFIRMED_RE = re.compile(r'SYSTEM_CONFIRMED:\s*(MARSHA|ACRS|MINT)', re.IGNORECASE)


def classify_ticket(ticket):
    """
    Determine ticket type and extract the minimal resolution context.
    Falls back to regex when structured fields (affected_eid etc.) are empty,
    which is common for Jira-sourced tickets where EID lives in free text.
    system is left as "" when unknown — sda_open_node will discover it via API.
    Returns (ticket_type, resolution_context).
    """
    title_raw    = ticket.get("title", "") or ""
    desc_raw     = ticket.get("description", "") or ""
    title        = title_raw.lower()
    description  = desc_raw.lower()
    combined_raw = title_raw + " " + desc_raw

    eid      = (ticket.get("affected_eid", "")      or "").strip()
    hotel    = (ticket.get("affected_hotel", "")    or "").strip()
    location = (ticket.get("affected_location", "") or "").strip()
    system   = (ticket.get("affected_system", "")   or "").strip()

    # Extract EID from free text when structured field is empty (Jira tickets)
    if not eid:
        m = EID_RE.search(combined_raw)
        if m:
            eid = m.group(1)

    is_permission = bool(eid) and any(k in title or k in description for k in PERM_KEYWORDS)

    if is_permission:
        # 1. Try quoted permission name (straight quotes only)
        qm = QUOTE_RE.search(combined_raw)
        permission_name = qm.group(1).strip() if qm else ""

        # 2. Fall back: search title first (short), then description separately
        #    so the {3,40} cap is not blown by trailing description text.
        if not permission_name:
            pm = PERM_AFTER_RE.search(title_raw) or PERM_AFTER_RE.search(desc_raw[:300])
            if pm:
                permission_name = pm.group(1).strip()

        # Infer system from free text — leave as "" when unknown (API discovery later)
        if not system:
            for sys in ("MARSHA", "ACRS", "MINT"):
                if sys.lower() in title or sys.lower() in description:
                    system = sys
                    break
            # Do NOT default to MARSHA — sda_open_node discovers via get_user_by_eid

        action = "remove" if any(k in title + description for k in ["remove", "revoke", "delete", "excess"]) else "add"
        return "permission", {
            "eid":        eid,
            "permission": permission_name,
            "system":     system,   # may be "" — discovery required
            "action":     action,
        }

    if hotel or location:
        ctx = {}
        if hotel:    ctx["hotel_code"]    = hotel
        if location: ctx["location_code"] = location
        ctx["system"] = system or "MARSHA"
        return "hotel_location", ctx

    if eid and system:
        return "user_access", {"eid": eid, "system": system}

    return "generic", {}


# ── System prompts (ASCII strings — no smart quotes) ────────────────────────

PROMPT_PERMISSION_KNOWN = (
    "You are Agent 1 - Service Desk Analyst (SDA) for Marriott's AURA system.\n"
    "This is a PERMISSION ticket.\n\n"
    "Your ONLY task:\n"
    "1. Call get_user_by_eid_and_app(eid=\"{eid}\", app=\"{system}\") - one call only.\n"
    "2. List the user's current assignments array from the response.\n"
    "3. Check if \"{permission}\" is in that list.\n\n"
    "Output format:\n"
    "TICKET SUMMARY:\n"
    "- Issue: [one line describing the permission complaint]\n"
    "- Affected User: {eid} / {system}\n"
    "- Current Assignments: [list from API response]\n"
    "- Permission Gap: [\"{permission}\" is PRESENT or MISSING]\n"
    "- Required Action: {action} \"{permission}\"\n\n"
    "Do NOT query hotels, locations, roles from other systems, or any other data.\n"
)

PROMPT_PERMISSION_DISCOVER = (
    "You are Agent 1 - Service Desk Analyst (SDA) for Marriott's AURA system.\n"
    "This is a PERMISSION ticket. The system (MARSHA/ACRS/MINT) is NOT known from the ticket.\n\n"
    "Your ONLY tasks:\n"
    "1. Call get_user_by_eid(eid=\"{eid}\") to find which apps this user is registered in.\n"
    "2. From the response, identify which app contains this user's record (MARSHA, ACRS, or MINT).\n"
    "   - If the user appears in multiple apps, pick the one where the permission makes sense.\n"
    "3. Output SYSTEM_CONFIRMED: <app> on its own line (e.g. SYSTEM_CONFIRMED: ACRS).\n"
    "4. List the user's current assignments from that app's record.\n"
    "5. Check if \"{permission}\" is in that list.\n\n"
    "Output format (use this exactly):\n"
    "SYSTEM_CONFIRMED: [MARSHA|ACRS|MINT]\n"
    "TICKET SUMMARY:\n"
    "- Issue: [one line describing the permission complaint]\n"
    "- Affected User: {eid} / [confirmed system]\n"
    "- Current Assignments: [list from API response for the confirmed app]\n"
    "- Permission Gap: [\"{permission}\" is PRESENT or MISSING]\n"
    "- Required Action: {action} \"{permission}\"\n\n"
    "Do NOT query hotels, locations, roles, or any other data.\n"
)

PROMPT_HOTEL_LOCATION = (
    "You are Agent 1 - Service Desk Analyst (SDA) for Marriott's AURA system.\n"
    "This is a HOTEL / LOCATION configuration ticket.\n\n"
    "Your tasks:\n"
    "1. Get the hotel record if a hotel code is present (get_hotel_by_id).\n"
    "2. Get the location record if a location code is present (get_location_by_id).\n"
    "3. Call get_location_hotel_summary for the location.\n"
    "4. Identify the specific configuration mismatch.\n\n"
    "Output format:\n"
    "TICKET SUMMARY:\n"
    "- Issue: [clear description]\n"
    "- Affected Hotel: [code + name + status]\n"
    "- Affected Location: [code + controlled hotels summary]\n"
    "- Configuration Gap: [specific mismatch found]\n"
    "- Initial Assessment: [what needs to be fixed]\n"
)

PROMPT_GENERIC = (
    "You are Agent 1 - Service Desk Analyst (SDA) for Marriott's AURA system.\n\n"
    "Your responsibilities:\n"
    "1. Read the ticket carefully.\n"
    "2. Use available tools to gather relevant context.\n"
    "3. Produce a clear structured summary.\n\n"
    "Output:\n"
    "TICKET SUMMARY:\n"
    "- Issue: [description]\n"
    "- Affected System: [system]\n"
    "- TAP Context: [key data retrieved]\n"
    "- Initial Assessment: [your read on what is wrong]\n"
)

PROMPT_CLOSE = (
    "You are Agent 1 - Service Desk Analyst Agent (SDA).\n"
    "The fix has been applied and validated. Close the ticket with a professional summary.\n\n"
    "Output format:\n"
    "TICKET CLOSED\n"
    "- Ticket ID: [id]\n"
    "- Resolution: [what was fixed]\n"
    "- Actions Taken: [summary of changes made]\n"
    "- Validation Status: [passed/failed + details]\n"
    "- Closed By: AURA Autonomous Agent\n"
)

# Tool sets for discovery vs known-system permission flows
QUERY_TOOLS_PERMISSION_DISCOVER = [get_user_by_eid]
QUERY_TOOLS_PERMISSION_KNOWN    = QUERY_TOOLS_PERMISSION   # [get_user_by_eid_and_app]


def sda_open_node(state):
    ticket = state["ticket"].copy()
    if ticket.get("description"):
        ticket["description"] = ticket["description"][:500]

    # Classify ticket type before any LLM call — zero tokens spent on classification
    ticket_type, resolution_context = classify_ticket(ticket)

    if ticket_type == "permission":
        rc     = resolution_context
        system = rc.get("system", "")

        if not system:
            # System unknown — call get_user_by_eid to discover which app this user lives in
            tools  = QUERY_TOOLS_PERMISSION_DISCOVER
            prompt = PROMPT_PERMISSION_DISCOVER.format(
                eid        = rc.get("eid", ""),
                permission = rc.get("permission", ""),
                action     = rc.get("action", "add"),
            )
        else:
            # System already known from ticket fields or text
            tools  = QUERY_TOOLS_PERMISSION_KNOWN
            prompt = PROMPT_PERMISSION_KNOWN.format(
                eid        = rc.get("eid", ""),
                system     = system,
                permission = rc.get("permission", ""),
                action     = rc.get("action", "add"),
            )

    elif ticket_type == "hotel_location":
        tools  = QUERY_TOOLS_BASIC
        prompt = PROMPT_HOTEL_LOCATION
    else:
        tools  = QUERY_TOOLS_BASIC
        prompt = PROMPT_GENERIC

    context = "New support ticket received:\n" + json.dumps(ticket, indent=2)
    if resolution_context:
        context += "\n\nExtracted resolution context:\n" + json.dumps(resolution_context, indent=2)

    llm    = get_llm()
    result, _ = run_agent(llm, tools, prompt, context)

    # For permission tickets where system was unknown, parse SYSTEM_CONFIRMED from LLM output
    if ticket_type == "permission" and not resolution_context.get("system"):
        m = SYSTEM_CONFIRMED_RE.search(result)
        if m:
            resolution_context = dict(resolution_context)
            resolution_context["system"] = m.group(1).upper()

    return {
        "ticket_type":        ticket_type,
        "resolution_context": resolution_context,
        "sda_summary":        result,
        "ticket_status":      "In Progress",
        "messages":           [AIMessage(content="[SDA-OPEN]\n" + result, name="SDA")],
    }


def sda_close_node(state):
    ticket            = state["ticket"]
    validation_report = state.get("validation_report", {})
    execution_log     = state.get("execution_log", [])

    context = (
        "Ticket: " + json.dumps(ticket, indent=2) + "\n\n"
        "Validation Report: " + json.dumps(validation_report, indent=2) + "\n\n"
        "Execution Log: " + json.dumps(execution_log, indent=2) + "\n\n"
        "All changes have been validated. Please produce the final ticket closure summary."
    )

    llm    = get_llm()
    result, _ = run_agent(llm, [], PROMPT_CLOSE, context)

    return {
        "sda_summary":   result,   # overwrites open summary so UI shows close output
        "ticket_status": "Closed",
        "messages":      [AIMessage(content="[SDA-CLOSE]\n" + result, name="SDA")],
    }
