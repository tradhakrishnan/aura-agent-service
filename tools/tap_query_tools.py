import json
import requests
from langchain_core.tools import tool
from config import TAP_QUERY_URL

# Use requests (not httpx) — requests handles Docker IPv4-only networks reliably
_session = requests.Session()
_TIMEOUT = 10.0


_ARRAY_FIELDS = ("controlledHotels", "locations", "supervisorEids")

def _compact(data):
    """Replace large arrays with summary counts to reduce token usage."""
    if isinstance(data, dict):
        out = {}
        for k, v in data.items():
            if k in _ARRAY_FIELDS and isinstance(v, list):
                out[k + "_count"] = len(v)
                out[k + "_sample"] = v[:5]  # keep first 5 for context
            else:
                out[k] = _compact(v)
        return out
    if isinstance(data, list):
        return [_compact(i) for i in data]
    return data


def _get(path: str, params: dict = None) -> str:
    try:
        resp = _session.get(f"{TAP_QUERY_URL}{path}", params=params, timeout=_TIMEOUT)
        return json.dumps(_compact(resp.json()), indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@tool
def get_hotel_by_id(hotel_id: str) -> str:
    """Get a hotel record by its 5-character hotel code (e.g. PARBA, HTDV7N)."""
    return _get(f"/api/hotels/{hotel_id}")


@tool
def search_hotels(query: str, page: int = 0, size: int = 10) -> str:
    """Search hotels by name keyword. Use for partial name lookups."""
    return _get("/api/hotels/search", {"q": query, "page": page, "size": size})


@tool
def get_hotels_by_status(status: str, page: int = 0, size: int = 20) -> str:
    """Get hotels filtered by status. status must be 'Active' or 'Inactive'."""
    return _get(f"/api/hotels/by-status/{status}", {"page": page, "size": size})


@tool
def get_hotels_by_crs(crs_system: str, page: int = 0, size: int = 20) -> str:
    """Get hotels by CRS system. crs_system is 'MARSHA' or 'ACRS'."""
    return _get(f"/api/hotels/by-crs/{crs_system}", {"page": page, "size": size})


@tool
def count_hotels(status: str = None, crs_system: str = None) -> str:
    """Count hotels, optionally filtered by status and/or crs_system."""
    params = {}
    if status:     params["status"]    = status
    if crs_system: params["crsSystem"] = crs_system
    return _get("/api/hotels/count", params)


@tool
def get_location_by_id(location_id: str) -> str:
    """Get a control-location record by its 6-character location code (e.g. HTDV7N)."""
    return _get(f"/api/locations/{location_id}")


@tool
def find_locations_by_hotel(hotel_code: str, page: int = 0, size: int = 20) -> str:
    """Find all control-locations that contain a specific hotel code."""
    return _get(f"/api/locations/by-hotel/{hotel_code}", {"page": page, "size": size})


@tool
def find_locations_by_supervisor(eid: str, page: int = 0, size: int = 20) -> str:
    """Find all control-locations where the given EID is a supervisor."""
    return _get(f"/api/locations/by-supervisor/{eid}", {"page": page, "size": size})


@tool
def find_locations_by_app(app: str, status: str = None, page: int = 0, size: int = 20) -> str:
    """Find control-locations by app (MARSHA or MINT), optionally filtered by status."""
    params = {"page": page, "size": size}
    if status: params["status"] = status
    return _get(f"/api/locations/by-app/{app}", params)


@tool
def count_locations(app: str = None, status: str = None) -> str:
    """Count control-locations, optionally filtered by app and/or status."""
    params = {}
    if app:    params["app"]    = app
    if status: params["status"] = status
    return _get("/api/locations/count", params)


@tool
def get_location_hotel_summary(location_id: str) -> str:
    """Get a status breakdown of the controlled hotels for a location.
    Returns totalInArray (raw count in the array), active count, inactive count,
    unknown count (codes not found in hotel registry), plus the list of inactive
    and unknown hotel codes.
    ALWAYS call this when: a ticket mentions hotel count, a location has many
    controlled hotels, or the fix involves adding/removing hotels from a location.
    It reveals inactive hotels hiding inside the controlledHotels array that
    plain get_location_by_id does not surface."""
    return _get(f"/api/locations/{location_id}/hotel-summary")


@tool
def get_user_by_eid(eid: str) -> str:
    """Get all user-eid records for a given EID across all apps (MARSHA, MINT, ACRS)."""
    return _get(f"/api/users/{eid}")


@tool
def get_user_by_eid_and_app(eid: str, app: str) -> str:
    """Get a specific user-eid record for a given EID and app (MARSHA, MINT, or ACRS)."""
    return _get(f"/api/users/{eid}/{app}")


@tool
def find_users_by_location(location_code: str, page: int = 0, size: int = 20) -> str:
    """Find all users assigned to a specific control-location code."""
    return _get(f"/api/users/by-location/{location_code}", {"page": page, "size": size})


@tool
def find_users_by_assignment(persona: str, page: int = 0, size: int = 20) -> str:
    """Find users by their assignment/persona (e.g. 'Hotel Revenue Manager')."""
    return _get("/api/users/by-assignment", {"persona": persona, "page": page, "size": size})


@tool
def find_users_by_app(app: str, status: str = None, page: int = 0, size: int = 20) -> str:
    """Find user-eid records by app (MARSHA, MINT, or ACRS), optionally filtered by status."""
    params = {"page": page, "size": size}
    if status: params["status"] = status
    return _get(f"/api/users/by-app/{app}", params)


@tool
def count_users(app: str = None, status: str = None) -> str:
    """Count user-eid records, optionally filtered by app and/or status."""
    params = {}
    if app:    params["app"]    = app
    if status: params["status"] = status
    return _get("/api/users/count", params)


# Grouped tool lists for each agent role
QUERY_TOOLS_BASIC   = [get_hotel_by_id, get_location_by_id, get_location_hotel_summary,
                        get_user_by_eid, get_user_by_eid_and_app, find_users_by_location]

QUERY_TOOLS_FULL    = [get_hotel_by_id, search_hotels, get_hotels_by_status,
                        get_hotels_by_crs, count_hotels,
                        get_location_by_id, get_location_hotel_summary, find_locations_by_hotel,
                        find_locations_by_supervisor, find_locations_by_app, count_locations,
                        get_user_by_eid, get_user_by_eid_and_app, find_users_by_location,
                        find_users_by_assignment, find_users_by_app, count_users]

# Focused tool sets for specific ticket types — avoids token bloat from unnecessary queries
QUERY_TOOLS_PERMISSION  = [get_user_by_eid_and_app]          # permission tickets: 1 call only
QUERY_TOOLS_USER_ACCESS = [get_user_by_eid, get_user_by_eid_and_app, find_users_by_location]
