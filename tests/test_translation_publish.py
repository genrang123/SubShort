import json
from types import SimpleNamespace

import pytest

from subshort.app import Pipeline
from subshort.execution import Cancelled
from subshort.models import Job, Settings
from subshort.providers import Segment
from subshort.queue import JobQueue


@pytest.mark.parametrize('cancel', [False, True])
def test_invalid_or_cancelled_translation_never_publishes(tmp_path, monkeypatch, cancel):
    monkeypatch.setattr(Settings, 'validate', lambda *a, **k: None)
    class Media:
        def __init__(self, *a): pass
        def probe(self, video): return {'duration': 3.0, 'has_audio': True}
        def extract_audio(self, *a): pass
        def render(self, *a): raise AssertionError('must not render')
    class ASR:
        def transcribe(self, *a): return [Segment(0, 1, 'source')]
    monkeypatch.setattr('subshort.app.MediaAdapter', Media)
    monkeypatch.setattr('subshort.app.ASRSession', lambda *a, **k: ASR())
    queue = JobQueue(tmp_path / 'queue.json')
    job = Job('one', 'source.mp4', 'vi')
    queue.add(job)
    pipeline = Pipeline(Settings(root=tmp_path, openai_api_key='fake'), queue, lambda *a: None)
    def worker(*a, **k):
        if cancel:
            pipeline.cancel()
            raise Cancelled('cancel')
        body = json.dumps({'choices': [{'message': {'content': json.dumps({'items': []})}}]})
        return SimpleNamespace(returncode=0, stdout=json.dumps({'ok': True, 'body': body}))
    monkeypatch.setattr('subshort.providers.run_process', worker)
    pipeline.run(job)
    assert job.status == ('cancelled' if cancel else 'failed')
    assert job.output == ''
    assert not (tmp_path / 'output_artifacts/one/translated.srt').exists()
    assert not (tmp_path / 'output_artifacts/one/timeline.json').exists()
    assert not list((tmp_path / 'output').glob('*.mp4'))
