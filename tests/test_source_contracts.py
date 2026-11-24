import json
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from subshort.pipeline import Pipeline
from subshort.media import MediaAdapter, MediaError
from subshort.models import Job, Settings
from subshort.providers import Segment
from subshort.queue import JobQueue


@pytest.mark.parametrize('duration', ['nan', 'inf', '-1', '0', None])
def test_probe_rejects_invalid_duration(monkeypatch, tmp_path, duration):
    monkeypatch.setattr('subshort.media.run_process', lambda *a, **k: SimpleNamespace(returncode=0, stdout=json.dumps({'format': {'duration': duration}, 'streams': [{'codec_type': 'video'}]})))
    with pytest.raises(MediaError):
        MediaAdapter().probe(tmp_path / 'source.mp4')


@pytest.mark.parametrize('audio,rows,reason', [(False, None, 'no_audio'), (True, [], 'no_speech')])
def test_source_without_speech_preserves_bytes_without_translation(tmp_path, monkeypatch, audio, rows, reason):
    source = tmp_path / 'source.mp4'
    source.write_bytes(b'source bytes retained exactly')
    settings = Settings(root=tmp_path, asr_model=str(tmp_path))
    monkeypatch.setattr(Settings, 'validate', lambda *a, **k: None)
    class Media:
        def __init__(self, *a): pass
        def probe(self, video): return {'duration': 5.0, 'has_audio': audio}
        def extract_audio(self, *a): assert audio
    class ASR:
        def transcribe(self, *a):
            assert audio
            return rows
    monkeypatch.setattr('subshort.pipeline.MediaAdapter', Media)
    monkeypatch.setattr('subshort.pipeline.ASRSession', lambda *a, **k: ASR())
    def forbidden(*a, **k): raise AssertionError('translation must not run')
    monkeypatch.setattr('subshort.pipeline.OpenAITranslator', forbidden)
    queue = JobQueue(tmp_path / 'queue.json')
    job = Job('one', str(source), 'vi')
    queue.add(job)
    Pipeline(settings, queue, lambda *a: None).run(job)
    assert job.status == 'done', job.error
    assert job.stage == reason
    assert job.metadata['segments'] == 0
    from pathlib import Path
    assert Path(job.output).read_bytes() == source.read_bytes()
    assert not list((tmp_path / 'output/one').glob('*.part'))
    assert not (tmp_path / 'output/one/translated.srt').exists()


def test_asr_outside_duration_fails_before_translation(tmp_path, monkeypatch):
    monkeypatch.setattr(Settings, 'validate', lambda *a, **k: None)
    class Media:
        def __init__(self, *a): pass
        def probe(self, video): return {'duration': 1.0, 'has_audio': True}
        def extract_audio(self, *a): pass
    class ASR:
        def transcribe(self, *a): return [Segment(0, 2, 'outside')]
    monkeypatch.setattr('subshort.pipeline.MediaAdapter', Media)
    monkeypatch.setattr('subshort.pipeline.ASRSession', lambda *a, **k: ASR())
    queue = JobQueue(tmp_path / 'queue.json')
    job = Job('one', 'source.mp4', 'vi')
    queue.add(job)
    Pipeline(Settings(root=tmp_path), queue, lambda *a: None).run(job)
    assert job.status == 'failed'
    assert job.output == ''
    assert not (tmp_path / 'output/one/translated.srt').exists()


def test_real_ffprobe_video_without_audio(tmp_path):
    ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:
        pytest.skip('FFmpeg/ffprobe unavailable')
    video = tmp_path / 'silent.mp4'
    subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'color=size=32x32:rate=10:duration=1', '-an', str(video)], check=True, capture_output=True)
    result = MediaAdapter(ffmpeg, ffprobe).probe(video)
    assert result['duration'] == pytest.approx(1, abs=.1)
    assert result['has_audio'] is False


def test_real_silent_audio_preserved_after_empty_asr(tmp_path, monkeypatch):
    ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:
        pytest.skip('FFmpeg/ffprobe unavailable')
    video = tmp_path / 'silence.mp4'
    subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'color=size=32x32:rate=10:duration=1', '-f', 'lavfi', '-i', 'anullsrc=r=16000:cl=mono', '-shortest', str(video)], check=True, capture_output=True)
    class ASR:
        def transcribe(self, audio, language=None):
            import wave
            with wave.open(str(audio), 'rb') as stream:
                assert stream.getframerate() == 16000
                assert not any(stream.readframes(stream.getnframes()))
            return []
    monkeypatch.setattr('subshort.pipeline.ASRSession', lambda *a, **k: ASR())
    settings = Settings(root=tmp_path, ffmpeg=ffmpeg, ffprobe=ffprobe, asr_model=str(tmp_path))
    queue = JobQueue(tmp_path / 'queue.json')
    job = Job('silence', str(video), 'vi')
    queue.add(job)
    Pipeline(settings, queue, lambda *a: None).run(job)
    assert job.status == 'done', job.error
    assert job.stage == 'no_speech'
    from pathlib import Path
    assert Path(job.output).read_bytes() == video.read_bytes()
