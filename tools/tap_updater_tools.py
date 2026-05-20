import json
import httpx
from langchain_core.tools import tool
from config import TAP_UPDATER_URL, MARSHA_URL, MINT_URL, ACRS_URL, VDS_URL


def _patch(url: str, body: dict) -> str:
    try:
        resp = httpx.patch(url, json=body, timeout=10.0)
        return json.dumps(resp.json(), indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


def _post(url: str, body: dict) -> str:
    try:
        resp = httpx.post(url, json=body, timeout=10.0)
        return json.dumps(resp.json(), indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


def _put(url: str, body: dict) -> str:
    try:
        resp = httpx.put(url, json=body, timeout=10.0)
        return json.dumps(resp.json(), indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── Hotel updater ────────────────────────────────────────────────────────────

@tool
def patch_hotel_status(hotel_id: str, status: str, updated_by: str) -> str:
    """Update the status of a hotel. status must be 'Active' or 'Inactive'."""
    return _patch(f"{TAP_UPDATER_URL}/api/hotels/{hotel_id}/status",
                  {"status": status, "updatedBy": updated_by})


# ── Location updater ─────────────────────────────────────────────────────────

@tool
def patch_location_status(location_id: str, status: str, updated_by: str) -> str:
    """Update the status of a control-location. status must be 'Active' or 'Inactive'."""
    return _patch(f"{TAP_UPDATER_URL}/api/locations/{location_id}/status",
                  {"status": status, "updatedBy": updated_by})


@tool
def patch_location_hotels(location_id: str, add: list, remove: list) -> str:
    """Add or remove hotel codes from a control-location's controlledHotels array.
    add: list of hotel codes to add. remove: list of hotel codes to remove."""
    return _patch(f"{TAP_UPDATER_URL}/api/locations/{location_id}/hotels",
                  {"add": add, "remove": remove})


@tool
def patch_location_supervisors(location_id: str, add: list, remove: list) -> str:
    """Add or remove EIDs from a control-location's supervisorEids array.
    add: list of EIDs to add. remove: list of EIDs to remove."""
    return _patch(f"{TAP_UPDATER_URL}/api/locations/{location_id}/supervisors",
                  {"add": add, "remove": remove})


# ── User EID updater ─────────────────────────────────────────────────────────

@tool
def patch_user_status(eid: str, app: str, status: str) -> str:
    """Update the status of a user-eid record. app must be MARSHA, MINT, or ACRS."""
    return _patch(f"{TAP_UPDATER_URL}/api/users/{eid}/{app}/status",
                  {"status": status})


@tool
def patch_user_assignments(eid: str, app: str, assignments: list) -> str:
    """Replace the full assignments list for a user-eid record.
    app must be MARSHA, MINT, or ACRS. assignments is the complete new list."""
    return _patch(f"{TAP_UPDATER_URL}/api/users/{eid}/{app}/assignments",
                  {"assignments": assignments})


@tool
def patch_user_locations(eid: str, app: str, add: list, remove: list) -> str:
    """Add or remove location codes from a user-eid's locations array.
    app must be MARSHA, MINT, or ACRS."""
    return _patch(f"{TAP_UPDATER_URL}/api/users/{eid}/{app}/locations",
                  {"add": add, "remove": remove})


# ── Client service updaters ───────────────────────────────────────────────────

@tool
def marsha_patch_location_status(location_id: str, status: str, updated_by: str = "AURA-EA") -> str:
    """Update a MARSHA control-location status via the MARSHA client service."""
    return _patch(f"{MARSHA_URL}/marsha/locations/{location_id}/status",
                  {"status": status, "updatedBy": updated_by})


@tool
def marsha_patch_user_assignments(eid: str, assignments: list) -> str:
    """Replace assignments for a MARSHA user-eid via the MARSHA client service."""
    return _patch(f"{MARSHA_URL}/marsha/users/{eid}/assignments",
                  {"assignments": assignments})


@tool
def marsha_patch_user_locations(eid: str, add: list, remove: list) -> str:
    """Add or remove locations for a MARSHA user-eid via the MARSHA client service."""
    return _patch(f"{MARSHA_URL}/marsha/users/{eid}/locations",
                  {"add": add, "remove": remove})


@tool
def acrs_patch_user_assignments(eid: str, assignments: list) -> str:
    """Replace assignments for an ACRS user-eid via the ACRS client service."""
    return _patch(f"{ACRS_URL}/acrs/users/{eid}/assignments",
                  {"assignments": assignments})


UPDATER_TOOLS = [
    patch_hotel_status,
    patch_location_status, patch_location_hotels, patch_location_supervisors,
    patch_user_status, patch_user_assignments, patch_user_locations,
    marsha_patch_location_status, marsha_patch_user_assignments, marsha_patch_user_locations,
    acrs_patch_user_assignments,
]
