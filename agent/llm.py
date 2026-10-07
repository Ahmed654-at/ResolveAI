"""The ONLY file that talks to the model.

Adds what the free Nemotron route needs: a timeout, 3 attempts with backoff,
treating empty replies as failures, and counters so the evals can report reliability.
"""

import time

import openai
from openai import OpenAI

from agent import config

TIMEOUT_SECONDS = 60
ATTEMPTS = 3
BACKOFF_SECONDS = 1  # waits 1s, then 2s, between attempts

# max_retries=0: the SDK has its own hidden retries; we turn them off so ours are the only ones
client = OpenAI(api_key=config.OPENROUTER_API_KEY, base_url=config.BASE_URL,
                timeout=TIMEOUT_SECONDS, max_retries=0)

stats = {"calls": 0, "failures": 0, "empty": 0}


class ModelUnavailable(Exception):
    """All attempts failed. The loop turns this into an escalation, never a crash."""


def reset_stats():
    for key in stats:
        stats[key] = 0


def chat(messages, tools=None):
    """Send the conversation to the model and return its reply message.

    The reply has `.content` (text) and/or `.tool_calls` (tools the model wants to run).
    """
    kwargs = {
        "model": config.MODEL,
        "messages": messages,
        "extra_body": {"reasoning": {"enabled": False}},  # off for speed; an eval experiment later
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    last_error = None
    for attempt in range(ATTEMPTS):
        stats["calls"] += 1
        try:
            response = client.chat.completions.create(**kwargs)
            message = response.choices[0].message if response.choices else None
            if message and (message.content or message.tool_calls):
                return message
            stats["empty"] += 1
            last_error = "empty reply"
        except openai.OpenAIError as e:  # timeouts, rate limits (429), server errors
            stats["failures"] += 1
            last_error = f"{type(e).__name__}: {e}"

        if attempt < ATTEMPTS - 1:
            time.sleep(BACKOFF_SECONDS * 2 ** attempt)

    raise ModelUnavailable(last_error)
