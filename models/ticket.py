from pydantic import BaseModel
from typing import Optional
from enum import Enum


class Severity(str, Enum):
    LOW      = "Low"
    MEDIUM   = "Medium"
    HIGH     = "High"
    CRITICAL = "Critical"


class TicketSource(str, Enum):
    SERVICENOW = "ServiceNow"
    JIRA       = "Jira"
    MANUAL     = "Manual"


class Ticket(BaseModel):
    ticket_id:         str
    source:            TicketSource = TicketSource.MANUAL
    title:             str
    description:       str
    severity:          Severity = Severity.MEDIUM
    affected_system:   Optional[str] = None   # MARSHA / MINT / ACRS
    affected_hotel:    Optional[str] = None   # hotel code e.g. PARBA
    affected_location: Optional[str] = None   # location code e.g. HTDV7N
    affected_eid:      Optional[str] = None   # user EID e.g. NMKOS046
    reported_by:       Optional[str] = None
    jira_issue_key:    Optional[str] = None   # set when ticket originates from Jira webhook
