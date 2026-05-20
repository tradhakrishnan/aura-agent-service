import os
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
LLM_MODEL         = os.getenv("LLM_MODEL", "claude-sonnet-4-6")
LLM_MAX_TOKENS    = int(os.getenv("LLM_MAX_TOKENS", "2048"))

TAP_QUERY_URL    = os.getenv("TAP_QUERY_URL",    "http://localhost:8081")
TAP_UPDATER_URL  = os.getenv("TAP_UPDATER_URL",  "http://localhost:8082")
MARSHA_URL       = os.getenv("MARSHA_URL",       "http://localhost:8083")
MINT_URL         = os.getenv("MINT_URL",         "http://localhost:8084")
ACRS_URL         = os.getenv("ACRS_URL",         "http://localhost:8085")
VDS_URL          = os.getenv("VDS_URL",          "http://localhost:8086")

MAX_DISCUSSION_ITERATIONS = int(os.getenv("MAX_DISCUSSION_ITERATIONS", "3"))

MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27017")
MONGO_DB  = os.getenv("MONGO_DB",  "aura_db")

JIRA_URL           = os.getenv("JIRA_URL",           "")
JIRA_EMAIL         = os.getenv("JIRA_EMAIL",         "")
JIRA_API_TOKEN     = os.getenv("JIRA_API_TOKEN",     "")
JIRA_PROJECT_KEY   = os.getenv("JIRA_PROJECT_KEY",   "")
