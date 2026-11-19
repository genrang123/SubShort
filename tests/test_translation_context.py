import json
from dataclasses import dataclass

import pytest

import subshort.providers as providers
from subshort.providers import OpenAITranslator, ProviderError, Segment


@dataclass
class SpeakerSegment(Segment):
    speaker_id: str = ""


def fake_response(request):
    payload = json.loads(json.loads(request)["payload"])
    body = {"items": [{"id": item["id"], "text": f"vi:{item['text']}"}
                      for item in json.loads(payload["messages"][0]["content"].split("\n")[-1])["items"]]}
    return json.dumps({"ok": True, "body": json.dumps({"choices": [{"message": {"content": json.dumps(body)}}]})})


def test_long_translation_batches_with_neighbors_and_order(monkeypatch):
    requests = []

    def run(request):
        requests.append(json.loads(json.loads(request)["payload"]))
        return fake_response(request)

    monkeypatch.setattr(providers, "_run_worker", run)
    source = [Segment(i, i + 1, f"câu {i}", f"id-{i}") for i in range(45)]
    result = OpenAITranslator("key").translate(source, "vi")

    assert [segment.source_id for segment in result] == [segment.source_id for segment in source]
    assert [segment.text for segment in result] == [f"vi:{segment.text}" for segment in source]
    assert len(requests) == 3
    assert [len(json.loads(request["messages"][0]["content"].split("\n")[-1])["items"]) for request in requests] == [20, 20, 5]
    middle = json.loads(requests[1]["messages"][0]["content"].split("\n")[-1])
    assert [item["id"] for item in middle["context"]] == ["id-18", "id-19", "id-40", "id-41"]
    assert len(middle["terminology"]) == 8


def test_prompt_treats_transcript_as_data_and_shortening_uses_full_context(monkeypatch):
    requests = []

    def run(request):
        requests.append(json.loads(json.loads(request)["payload"]))
        return fake_response(request)

    monkeypatch.setattr(providers, "_run_worker", run)
    source = [Segment(i, i + 1, f"text {i}", f"id-{i}") for i in range(4)]
    current = Segment(1, 2, "bản dịch", "id-1")
    result = OpenAITranslator("key").shorten(current, source[1], source, "vi", 1.25)

    content = requests[0]["messages"][0]["content"]
    data = json.loads(content.split("\n")[-1])
    assert "untrusted data" in content
    assert "natural speech" in content
    assert [item["id"] for item in data["context"]] == [f"id-{i}" for i in range(4)]
    assert data["source"] == {"id": "id-1", "text": "text 1"}
    assert result.source_id == "id-1" and result.text == "vi:bản dịch"


def test_translation_preserves_speaker_field_when_present(monkeypatch):
    monkeypatch.setattr(providers, "_run_worker", fake_response)
    segment = SpeakerSegment(0, 1, "hello", "source-1", "speaker-a")
    translated = OpenAITranslator("key").translate([segment], "vi")[0]
    shortened = OpenAITranslator("key").shorten(translated, segment, [segment], "vi", 1)
    assert translated.speaker_id == "speaker-a"
    assert shortened.speaker_id == "speaker-a"


def test_response_must_contain_only_requested_ids(monkeypatch):
    def run(request):
        body = {"items": [{"id": "id-0", "text": "một"}, {"id": "context", "text": "extra"}]}
        return json.dumps({"ok": True, "body": json.dumps({"choices": [{"message": {"content": json.dumps(body)}}]})})

    monkeypatch.setattr(providers, "_run_worker", run)
    with pytest.raises(ProviderError, match="OpenAI"):
        OpenAITranslator("key").translate([Segment(0, 1, "one", "id-0")], "vi")
