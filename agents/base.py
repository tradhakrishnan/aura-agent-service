import time
import random
from datetime import datetime, timezone

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage, AIMessage
from config import ANTHROPIC_API_KEY, LLM_MODEL, LLM_MAX_TOKENS


def get_llm() -> ChatAnthropic:
    return ChatAnthropic(
        model=LLM_MODEL,
        anthropic_api_key=ANTHROPIC_API_KEY,
        max_tokens=LLM_MAX_TOKENS,
    )


def _invoke_with_retry(bound, msgs: list, max_retries: int = 4):
    """Invoke LLM with exponential backoff on 429 rate-limit errors."""
    delay = 15  # seconds — start conservative for rate limits
    for attempt in range(max_retries):
        try:
            return bound.invoke(msgs)
        except Exception as e:
            err = str(e)
            is_rate_limit = "429" in err or "rate_limit" in err.lower()
            is_last       = attempt == max_retries - 1
            if is_rate_limit and not is_last:
                jitter = random.uniform(0, 5)
                wait   = delay + jitter
                time.sleep(wait)
                delay *= 2  # 15s → 30s → 60s → 120s
            else:
                raise


def _serialize_msg(m) -> dict:
    """Convert a LangChain message object to a plain JSON-serializable dict."""
    if isinstance(m, SystemMessage):
        return {"role": "system", "content": m.content}
    if isinstance(m, HumanMessage):
        return {"role": "human", "content": m.content}
    if isinstance(m, ToolMessage):
        return {"role": "tool", "content": m.content, "tool_call_id": m.tool_call_id}
    if isinstance(m, AIMessage):
        entry: dict = {"role": "ai", "content": m.content}
        if m.tool_calls:
            entry["tool_calls"] = [
                {"name": tc["name"], "args": tc["args"], "id": tc["id"]}
                for tc in m.tool_calls
            ]
        return entry
    return {"role": "unknown", "content": str(m)}


def run_agent(llm, tools: list, system_prompt: str, context: str, max_iters: int = 8) -> tuple:
    """Run an agent with optional tool use. Returns (final_text, messages).

    After each invocation the full prompt + conversation is persisted to
    MongoDB (fire-and-forget — a DB outage never crashes an agent).
    """
    tool_map = {t.name: t for t in tools}
    bound    = llm.bind_tools(tools) if tools else llm
    msgs     = [SystemMessage(content=system_prompt), HumanMessage(content=context)]
    last     = None

    for _ in range(max_iters):
        response = _invoke_with_retry(bound, msgs)
        msgs.append(response)
        last = response

        tool_calls = getattr(response, "tool_calls", None)
        if not tool_calls:
            break

        for tc in tool_calls:
            try:
                result = tool_map[tc["name"]].invoke(tc["args"])
            except Exception as e:
                result = f"Tool error: {str(e)}"
            msgs.append(ToolMessage(content=str(result), tool_call_id=tc["id"]))

    content = getattr(last, "content", str(last)) if last else ""

    # ── Persist full conversation to MongoDB ──────────────────────────────────
    try:
        from db.mongo import get_current_run_id, append_prompt_entry
        run_id = get_current_run_id()
        if run_id:
            append_prompt_entry(run_id, {
                "system_prompt":  system_prompt,
                "context":        context,
                "messages":       [_serialize_msg(m) for m in msgs],
                "captured_at":    datetime.now(timezone.utc).isoformat(),
            })
    except Exception:
        pass  # never let DB errors surface to agents

    return content, msgs
