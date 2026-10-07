"""The agent loop: call the model -> run the tools it asks for -> repeat until it answers.

Run one case:  python -m agent.loop "Hi, I want a refund for order 1"
"""

import json
import sys
from datetime import date

from agent import llm, tools

MAX_MODEL_CALLS = 10  # simple safety cap for now; full step limit + loop detection come in step 6

SYSTEM_PROMPT = f"""You are ResolveAI, the refund assistant for an online shop. Today is {date.today().isoformat()}.

Always use your tools to look up facts. Never guess order details.
Right now you can only look up orders. You cannot issue refunds yet, so do not promise one.
When you have the facts, reply to the customer with a short summary of what you found
and tell them a team member will review their request."""

DEFAULT_CASE = "Hi, I want a refund for order 1, the headphones stopped working."


def run_case(message: str, verbose: bool = True) -> dict:
    """Work one customer message to the end. Always returns a result dict, never raises."""
    say = print if verbose else (lambda *a, **k: None)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": message},
    ]
    retried_invalid_args = False

    for step in range(1, MAX_MODEL_CALLS + 1):
        try:
            reply = llm.chat(messages, tools.TOOL_SCHEMAS)
        except llm.ModelUnavailable as e:
            return escalate("model unavailable", step, say, detail=str(e))

        # No tool calls means the model is done: its text is the final answer
        if not reply.tool_calls:
            say(f"[{step}] answer:\n{reply.content}")
            return {"outcome": "answered", "answer": reply.content, "model_calls": step}

        # Keep the model's tool request in the conversation, so it remembers what it asked for
        messages.append({
            "role": "assistant",
            "content": reply.content or "",
            "tool_calls": [
                {"id": c.id, "type": "function",
                 "function": {"name": c.function.name, "arguments": c.function.arguments}}
                for c in reply.tool_calls
            ],
        })

        for call in reply.tool_calls:
            say(f"[{step}] tool call: {call.function.name}({call.function.arguments})")
            try:
                output = tools.run_tool(call.function.name, call.function.arguments)
            except tools.InvalidToolCall as e:
                if retried_invalid_args:
                    return escalate("invalid tool arguments", step, say, detail=str(e))
                retried_invalid_args = True  # one chance to fix it
                output = {"error": f"{e}. Fix the arguments and call the tool again."}
            say(f"     result: {output}")
            messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(output)})

    return escalate("step limit reached", MAX_MODEL_CALLS, say)


def escalate(reason, step, say, detail=None):
    say(f"[{step}] ESCALATED: {reason}" + (f" ({detail})" if detail else ""))
    return {"outcome": "escalated", "reason": reason, "detail": detail, "model_calls": step}


if __name__ == "__main__":
    # Windows consoles default to cp1252, which can't print some characters models use (e.g. '‑')
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    case =" ".join(sys.argv[1:]) or DEFAULT_CASE
    print(f"Customer: {case}\n")
    result = run_case(case)
    print(f"\nResult: {result['outcome']}")
    print(f"Model stats: {llm.stats}")
