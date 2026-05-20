from langgraph.graph import StateGraph, END, START
from graph.state import AuraState
from agents.sda import sda_open_node, sda_close_node
from agents.spa import spa_node
from agents.sme import sme_node
from agents.rat import rat_node
from agents.authorizer import authorizer_node
from agents.ea import ea_node
from agents.va import va_node
from config import MAX_DISCUSSION_ITERATIONS


# ── Routing functions ────────────────────────────────────────────────────────

def route_after_sme(state: AuraState) -> str:
    verdict   = state.get("sme_verdict", "")
    iteration = state.get("iteration", 0)
    if "RESOLVED" in verdict or iteration >= MAX_DISCUSSION_ITERATIONS:
        return "rat"
    return "spa"


def route_after_authorizer(state: AuraState) -> str:
    if state.get("authorized", False):
        return "ea"
    # Both pending_approval and rejected route to END — human override handles the ea path
    return "end"


# ── Graph definition ─────────────────────────────────────────────────────────

def build_graph():
    graph = StateGraph(AuraState)

    graph.add_node("sda_open",   sda_open_node)
    graph.add_node("spa",        spa_node)
    graph.add_node("sme",        sme_node)
    graph.add_node("rat",        rat_node)
    graph.add_node("authorizer", authorizer_node)
    graph.add_node("ea",         ea_node)
    graph.add_node("va",         va_node)
    graph.add_node("sda_close",  sda_close_node)

    graph.add_edge(START,       "sda_open")
    graph.add_edge("sda_open",  "spa")
    graph.add_edge("spa",       "sme")
    graph.add_conditional_edges("sme", route_after_sme, {"spa": "spa", "rat": "rat"})
    graph.add_edge("rat",       "authorizer")
    graph.add_conditional_edges("authorizer", route_after_authorizer, {"ea": "ea", "end": END})
    graph.add_edge("ea",        "va")
    graph.add_edge("va",        "sda_close")
    graph.add_edge("sda_close", END)

    return graph.compile()


aura_graph = build_graph()
