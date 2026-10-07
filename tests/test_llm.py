"""Tests retries and counters in llm.py with a fake client, so no network is used."""

from types import SimpleNamespace

import openai
import pytest

from agent import llm


def response(content):
    message = SimpleNamespace(content=content, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


@pytest.fixture
def fake_client(monkeypatch):
    """Returns a function that installs a scripted fake client and records each request."""
    monkeypatch.setattr(llm, "BACKOFF_SECONDS", 0)
    llm.reset_stats()

    def install(script):
        requests = []

        def create(**kwargs):
            requests.append(kwargs)
            item = script.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        monkeypatch.setattr(llm, "client", client)
        return requests

    return install


def test_retries_until_success(fake_client):
    fake_client([openai.OpenAIError("boom"), response(None), response("hello")])
    assert llm.chat([{"role": "user", "content": "hi"}]).content == "hello"
    assert llm.stats == {"calls": 3, "failures": 1, "empty": 1}


def test_gives_up_after_three_attempts(fake_client):
    fake_client([openai.OpenAIError("boom")] * 3)
    with pytest.raises(llm.ModelUnavailable):
        llm.chat([{"role": "user", "content": "hi"}])
    assert llm.stats["failures"] == 3


def test_tools_only_sent_when_given(fake_client):
    requests = fake_client([response("a"), response("b")])
    llm.chat([], tools=None)
    llm.chat([], tools=[{"type": "function"}])
    assert "tools" not in requests[0]
    assert requests[1]["tool_choice"] == "auto"


def test_error_inside_a_200_response_counts_as_failure(fake_client):
    # What OpenRouter actually sends when NVIDIA is overloaded: no choices, an error field
    overloaded = SimpleNamespace(choices=None, error={"message": "Service temporarily overloaded", "code": 503})
    fake_client([overloaded, response("hello")])
    assert llm.chat([]).content == "hello"
    assert llm.stats == {"calls": 2, "failures": 1, "empty": 0}
