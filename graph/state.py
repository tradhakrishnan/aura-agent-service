from typing import TypedDict, List, Annotated
from langgraph.graph.message import add_messages


class AuraState(TypedDict):
    ticket:             dict
    tap_context:        dict
    ticket_type:        str   # 'permission' | 'hotel_location' | 'user_access' | 'generic'
    resolution_context: dict  # focused fields extracted at SDA: {eid, permission, system, action} etc.
    sda_summary:        str
    spa_findings:       str
    sme_verdict:        str
    risk_level:         str   # Immediate / Normal / Escalated
    authorized:         bool
    pending_approval:   bool  # True = waiting for human to approve/reject
    rejected:           bool  # True = human explicitly rejected execution
    execution_log:      List[dict]
    validation_report:  dict
    ticket_status:      str   # Open / In Progress / Resolved / Closed
    messages:           Annotated[list, add_messages]
    iteration:          int
