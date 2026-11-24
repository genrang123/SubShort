"""Gemini uses the same isolated HTTP worker and strict translation contract."""
import json
import subprocess

import pytest

from subshort import providers
from subshort.execution import Cancelled, Execution
from subshort.providers import FatalProviderError, GeminiTranslator, OpenAITranslator, ProviderError, Segment


SEGMENTS = [Segment(0, 2, "one"), Segment(1, 2.25, "two")]
KEY = "fake-gemini-secret"
ANSWER = {"items": [{"id": "1", "text": " hai "}, {"id": "0", "text": "một"}]}


def success(answer=ANSWER):
    body = json.dumps({"choices": [{"message": {"content": json.dumps(answer)}}]})
    return {"ok": True, "body": body}


@pytest.fixture
def fake_post(monkeypatch):
    """Replace the worker process, retaining the complete stdin request envelope."""
    def install(*responses):
        requests = []
        pending = iter(responses)

        def run(args, **kwargs):
            assert KEY not in str(args)
            assert kwargs["timeout"] == 120
            requests.append(json.loads(kwargs["input"]))
            response = next(pending)
            if isinstance(response, Exception):
                raise response
            return subprocess.CompletedProcess(args, 0, json.dumps(response), "")

        monkeypatch.setattr(providers, "run_process", run)
        return requests

    return install


def test_gemini_preserves_auth_schema_ids_and_timing(fake_post):
    requests = fake_post(success())
    assert GeminiTranslator(KEY).translate(SEGMENTS, "vi") == [Segment(0, 2, "một"), Segment(1, 2.25, "hai")]
    request = requests[0]
    assert request["url"] == "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    assert request["headers"] == {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
    assert request["timeout"] == 120
    payload = json.loads(request["payload"])
    assert payload["model"] == "gemini-2.5-flash"
    schema = payload["response_format"]["json_schema"]
    assert schema["strict"] is True
    assert schema["schema"]["additionalProperties"] is False
    item_schema = schema["schema"]["properties"]["items"]["items"]
    assert item_schema["required"] == ["id", "text"]
    assert item_schema["additionalProperties"] is False
    prompt = payload["messages"][0]["content"]
    assert json.loads(prompt.split("\n", 1)[1]) == {"items": [
        {"id": "0", "text": "one", "source_duration_seconds": 2},
        {"id": "1", "text": "two", "source_duration_seconds": 1.25},
    ]}


@pytest.mark.parametrize("translator,model", [(GeminiTranslator, "gemini-custom"), (OpenAITranslator, "gpt-custom")])
def test_model_is_configurable_without_changing_endpoint(fake_post, translator, model):
    requests = fake_post(success())
    translator(KEY, model=model).translate(SEGMENTS, "vi")
    assert json.loads(requests[0]["payload"])["model"] == model
    assert requests[0]["url"] == translator.endpoint


@pytest.mark.parametrize("answer", [
    None, [], "one\ntwo", {}, {"items": {}}, {"items": [], "extra": True},
    {"items": [{"id": "0", "text": "one"}]},
    {"items": [{"id": "0", "text": "one"}, {"id": "0", "text": "two"}]},
    {"items": [{"id": "0", "text": "one"}, {"id": "2", "text": "two"}]},
    {"items": [{"id": "0", "text": "one"}, {"id": "1", "text": "two"}, {"id": "2", "text": "extra"}]},
    {"items": [{"id": 0, "text": "one"}, {"id": "1", "text": "two"}]},
    *[{"items": [{"id": "0", "text": text}, {"id": "1", "text": "two"}]} for text in [None, 12, " "]],
    {"items": [{"id": "0", "text": "one", "extra": 1}, {"id": "1", "text": "two"}]},
    {"items": [None]},
])
def test_gemini_rejects_invalid_schema_without_retry(fake_post, answer):
    requests = fake_post(success(answer))
    with pytest.raises(ProviderError, match="Gemini"):
        GeminiTranslator(KEY).translate(SEGMENTS, "vi")
    assert len(requests) == 1


@pytest.mark.parametrize("body", ["not json", '{"choices": []}', '{"choices":[{"message":{"content":null}}]}'])
def test_gemini_rejects_malformed_body(fake_post, body):
    requests = fake_post({"ok": True, "body": body})
    with pytest.raises(ProviderError, match="Gemini"):
        GeminiTranslator(KEY).translate(SEGMENTS, "vi")
    assert len(requests) == 1


@pytest.mark.parametrize("response,wait", [
    ({"ok": False, "status": 429, "body": "", "retry_after": "120"}, 30.0),
    ({"ok": False, "status": 503, "body": ""}, 0.5),
    ({"ok": False, "error": "network"}, 0.5),
    (subprocess.TimeoutExpired(["worker"], 30), 0.5),
])
def test_gemini_reuses_retry_policy(fake_post, monkeypatch, response, wait):
    requests = fake_post(response, success())
    waits = []
    monkeypatch.setattr(providers, "_wait", waits.append)
    GeminiTranslator(KEY).translate(SEGMENTS, "vi")
    assert waits == [wait]
    assert len(requests) == 2 and requests[0] == requests[1]


@pytest.mark.parametrize("status", [401, 403, 400])
def test_gemini_http_errors_never_fallback_or_leak_body(fake_post, status):
    requests = fake_post({"ok": False, "status": status, "body": KEY})
    error_type = FatalProviderError if status in (401, 403) else ProviderError
    with pytest.raises(error_type, match=f"Gemini.*{status}") as error:
        GeminiTranslator(KEY).translate(SEGMENTS, "vi")
    assert KEY not in str(error.value)
    assert len(requests) == 1


def test_gemini_exhausts_retries_without_fallback(fake_post, monkeypatch):
    requests = fake_post(*[{"ok": False, "status": 503, "body": KEY}] * 3)
    waits = []
    monkeypatch.setattr(providers, "_wait", waits.append)
    with pytest.raises(ProviderError, match="Gemini.*3") as error:
        GeminiTranslator(KEY).translate(SEGMENTS, "vi")
    assert KEY not in str(error.value)
    assert len(requests) == 3 and waits == [0.5, 1.0]
    assert all(request["url"] == GeminiTranslator.endpoint for request in requests)


def test_gemini_empty_input_and_missing_key_never_spawn(fake_post):
    requests = fake_post()
    assert GeminiTranslator("").translate([], "vi") == []
    with pytest.raises(FatalProviderError, match="GEMINI_API_KEY"):
        GeminiTranslator("").translate(SEGMENTS, "vi")
    assert requests == []


def test_gemini_cancellation_prevents_request(fake_post):
    requests = fake_post()
    execution = Execution()
    execution.cancel()
    with execution.activate(), pytest.raises(Cancelled):
        GeminiTranslator(KEY).translate(SEGMENTS, "vi")
    assert requests == []
