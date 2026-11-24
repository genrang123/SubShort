import json

import pytest

from subshort import providers


@pytest.mark.parametrize('content,reason', [
    ('not-json-secret', 'message.content không phải JSON'),
    ('[]', 'JSON gốc phải là object, nhận list'),
    ('{}', 'object thiếu trường items'),
    ('{"items": [], "extra": "secret"}', 'object có trường ngoài items'),
    ('{"items": {}}', 'items phải là array, nhận dict'),
    ('{"items": [{"id": "0", "text": "secret", "source_duration_seconds": 1}]}', 'phải chỉ có id và text'),
    ('{"items": [{"id": 0, "text": "secret"}]}', 'id không phải chuỗi'),
    ('{"items": [{"id": "unexpected-secret", "text": "secret"}]}', 'ID yêu cầu'),
    ('{"items": [{"id": "0", "text": " "}]}', 'text không phải chuỗi hoặc rỗng'),
    ('{"items": []}', 'thiếu 1/1'),
    ('{"items": [{"id": "0", "text": "a"}, {"id": "0", "text": "b"}]}', 'id bị trùng'),
])
def test_invalid_translation_reports_reason_without_content(monkeypatch, content, reason):
    body = json.dumps({'choices': [{'message': {'content': content}}]})
    monkeypatch.setattr(providers, '_run_worker', lambda request: json.dumps({'ok': True, 'body': body}))
    lines = []
    translator = providers.OpenAITranslator('key-secret', diagnostic=lines.append)
    segment = providers.Segment(0, 1, 'transcript-secret')
    with pytest.raises(providers.ProviderError, match=reason) as error:
        translator.translate([segment], 'vi')
    log = '\n'.join(lines)
    assert 'Dịch OpenAI' in log
    assert reason in log
    assert 'OK' not in log
    assert 'secret' not in log
    assert 'secret' not in str(error.value)


def test_success_logged_only_after_content_validation(monkeypatch):
    content = json.dumps({'items': [{'id': '0', 'text': 'translated'}]})
    body = json.dumps({'choices': [{'message': {'content': content}}]})
    monkeypatch.setattr(providers, '_run_worker', lambda request: json.dumps({'ok': True, 'body': body}))
    lines = []
    result = providers.OpenAITranslator('key', diagnostic=lines.append).translate([providers.Segment(0, 1, 'source')], 'vi')
    assert result[0].text == 'translated'
    assert 'OK' in lines[-1]
