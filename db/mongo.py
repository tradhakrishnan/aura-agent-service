"""
MongoDB persistence layer for AURA agent runs.

Uses a thread-local variable so background agent threads can write their
prompt history without needing run_id threaded through every call site.

Collections (all in MONGO_DB, default: aura_db):
  agent_runs  — one document per run; includes full prompt_history with
                every LLM call made by every agent (system prompt, user
                context, tool calls, and model responses).
"""

import threading
from datetime import datetime, timezone
from pymongo import MongoClient, ReturnDocument
from pymongo.errors import PyMongoError
from config import MONGO_URL, MONGO_DB

# ── Lazy singleton connection ─────────────────────────────────────────────────

_client: MongoClient | None = None
_db = None
_lock = threading.Lock()

def _get_db():
    global _client, _db
    if _db is None:
        with _lock:
            if _db is None:
                _client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=3000)
                _db = _client[MONGO_DB]
    return _db


# ── Thread-local run_id ───────────────────────────────────────────────────────

_local = threading.local()


def set_current_run_id(run_id: str) -> None:
    """Call once at the start of each background agent thread."""
    _local.run_id = run_id


def get_current_run_id() -> str | None:
    return getattr(_local, "run_id", None)


# ── Run-level operations ──────────────────────────────────────────────────────

def save_run(run_data: dict) -> None:
    """Insert or replace a run document (upsert by run_id)."""
    try:
        db = _get_db()
        doc = {**run_data, "_id": run_data["run_id"], "prompt_history": []}
        db.agent_runs.replace_one({"_id": doc["_id"]}, doc, upsert=True)
    except PyMongoError:
        pass  # never crash agents because DB is unavailable


def update_run_fields(run_id: str, fields: dict) -> None:
    """$set specific fields on an existing run document."""
    try:
        db = _get_db()
        db.agent_runs.update_one({"_id": run_id}, {"$set": fields})
    except PyMongoError:
        pass


def append_prompt_entry(run_id: str, entry: dict) -> None:
    """$push one prompt-history entry into agent_runs.prompt_history."""
    try:
        db = _get_db()
        db.agent_runs.update_one({"_id": run_id}, {"$push": {"prompt_history": entry}})
    except PyMongoError:
        pass


def get_run(run_id: str) -> dict | None:
    """Fetch the full run document (including prompt_history) from MongoDB."""
    try:
        db = _get_db()
        doc = db.agent_runs.find_one({"_id": run_id})
        if doc:
            doc.pop("_id", None)
        return doc
    except PyMongoError:
        return None


def list_runs(limit: int = 100) -> list[dict]:
    """Return lightweight run summaries, newest first."""
    try:
        db = _get_db()
        cursor = db.agent_runs.find(
            {},
            {"run_id": 1, "status": 1, "ticket_id": 1, "started_at": 1, "completed_at": 1, "_id": 0},
        ).sort("started_at", -1).limit(limit)
        return list(cursor)
    except PyMongoError:
        return []


def load_all_runs() -> list[dict]:
    """Load all run documents for in-memory rehydration on service startup.
    prompt_history is excluded to keep memory footprint small."""
    try:
        db = _get_db()
        cursor = db.agent_runs.find({}, {"prompt_history": 0, "_id": 0})
        return list(cursor)
    except PyMongoError:
        return []
