import json
import subprocess
import sys
import types
import wave

import pytest

from subshort import providers
from subshort.execution import Cancelled, Execution
from subshort.providers import OpenAITranslator, ProviderError, Segment, validate_segments


@pytest.mark.parametrize("segments", [
    None, (), {}, [object()], [Segment(True, 1, "a")],
    [Segment(0, False, "a")], [Segment("0", 1, "a")],
    [Segment(0, float("nan"), "a")], [Segment(0, float("inf"), "a")],
    [Segment(-1, 1, "a")], [Segment(1, 1, "a")], [Segment(2, 1, "a")],
    [Segment(0, 1, None)], [Segment(0, 1, 123)], [Segment(0, 1, " \n")],
    [Segment(1, 2, "a"), Segment(0, 3, "b")],
    [Segment(0, 3, "a"), Segment(1, 2, "b")],
])
def test_validate_rejects_malformed_segments(segments):
    with pytest.raises(ProviderError):
        validate_segments(segments)


def test_validate_allows_overlap_and_empty_speech_and_strips_text():
    assert validate_segments([]) == []
    assert validate_segments([Segment(0, 2, " a "), Segment(1, 3, "b")], 3) == [
        Segment(0, 2, "a"), Segment(1, 3, "b")]
    with pytest.raises(ProviderError):
        validate_segments([Segment(0, 2, "a")], 1)


@pytest.mark.parametrize("duration", [True, "2", -1, float("nan"), float("inf")])
def test_validate_rejects_invalid_duration(duration):
    with pytest.raises(ProviderError):
        validate_segments([], duration)





SEGMENTS = [Segment(0, 2, "one"), Segment(1, 2.25, "two")]


def install_translation(monkeypatch, answer, on_response=None):
    requests = []

    def run(request):
        requests.append(json.loads(json.loads(request)["payload"]))
        if on_response:
            on_response()
        body = json.dumps({"choices": [{"message": {"content": json.dumps(answer)}}]})
        return json.dumps({"ok": True, "body": body})

    monkeypatch.setattr(providers, "_run_worker", run)
    return requests


def test_translation_ids_reorder_response_without_changing_times(monkeypatch):
    requests = install_translation(monkeypatch, {"items": [{"id": "1", "text": " hai "}, {"id": "0", "text": "một"}]})
    assert OpenAITranslator("key").translate(SEGMENTS, "vi") == [Segment(0, 2, "một"), Segment(1, 2.25, "hai")]
    payload = requests[0]
    assert payload["response_format"]["type"] == "json_schema"
    schema = payload["response_format"]["json_schema"]
    assert schema["strict"] is True
    assert schema["schema"]["additionalProperties"] is False
    item_schema = schema["schema"]["properties"]["items"]["items"]
    assert set(item_schema["properties"]) == set(item_schema["required"]) == {"id", "text"}
    assert item_schema["additionalProperties"] is False
    content = payload["messages"][0]["content"]
    assert json.loads(content.split("\n", 1)[1]) == {"items": [
        {"id": "0", "text": "one", "source_duration_seconds": 2},
        {"id": "1", "text": "two", "source_duration_seconds": 1.25},
    ]}


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
def test_translation_rejects_invalid_schema_ids_and_text(monkeypatch, answer):
    requests = install_translation(monkeypatch, answer)
    with pytest.raises(ProviderError, match="OpenAI"):
        OpenAITranslator("key").translate(SEGMENTS, "vi")
    assert len(requests) == 1


def test_translation_empty_input_needs_no_key_or_request(monkeypatch):
    requests = install_translation(monkeypatch, {})
    assert OpenAITranslator("").translate([], "vi") == []
    assert requests == []


def test_translation_cancellation_after_response(monkeypatch):
    execution = Execution()
    install_translation(monkeypatch, {"items": [{"id": "0", "text": "one"}, {"id": "1", "text": "two"}]}, execution.cancel)
    with execution.activate(), pytest.raises(Cancelled):
        OpenAITranslator("key").translate(SEGMENTS, "vi")


def test_translation_rejects_non_json_content(monkeypatch):
    body = json.dumps({"choices": [{"message": {"content": "secret not json"}}]})
    monkeypatch.setattr(providers, "_run_worker", lambda request: json.dumps({"ok": True, "body": body}))
    with pytest.raises(ProviderError, match="OpenAI") as error:
        OpenAITranslator("key").translate(SEGMENTS, "vi")
    assert "secret" not in str(error.value)


