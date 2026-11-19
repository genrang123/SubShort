import json
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import pytest

from subshort.app import Pipeline
from subshort.media import MediaAdapter, MediaError
from subshort.models import Job, Settings
from subshort.providers import Segment
from subshort.queue import JobQueue


@pytest.mark.parametrize('failure', [None, 'render', 'validate', 'cancel'])
@pytest.mark.parametrize('mode', ['subtitle_dub', 'subtitle', 'dub'])
def test_real_pipeline_duration_and_publish(tmp_path, monkeypatch, failure, mode):
    ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:
        pytest.skip('FFmpeg unavailable')
    video = tmp_path / 'source.mp4'
    subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'color=size=64x64:rate=25:duration=2', '-f', 'lavfi', '-i', 'anullsrc=r=16000:cl=mono', '-shortest', str(video)], check=True, capture_output=True)
    class ASR:
        def transcribe(self, *a): return [Segment(0, .3, 'first'), Segment(.4, .7, 'second')]
    class Translator:
        def __init__(self, *a, **k): pass
        def translate(self, rows, target): return rows
    class TTS:
        def synthesize(self, text, output, **kwargs):
            with wave.open(str(output), 'wb') as stream:
                stream.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
                stream.writeframes((b'\x00\x10' * 6000))
    monkeypatch.setattr('subshort.app.TtsSession', lambda *a, **k: TTS())
    monkeypatch.setattr('subshort.app.ASRSession', lambda *a, **k: ASR())
    monkeypatch.setattr('subshort.app.OpenAITranslator', Translator)
    monkeypatch.setattr('subshort.app.TtsSession', lambda *a, **k: TTS())
    def captions(voice, references, duration):
        assert voice.name == 'voice_final.wav'
        return references, {'source': 'reference'}
    monkeypatch.setattr('subshort.captions.build_captions', captions)
    def separate(source, work, model_dir, python):
        with wave.open(str(source), 'rb') as stream:
            assert stream.getnchannels() == 2
            assert stream.getframerate() == 44100
            assert stream.getnframes() > 0
        return source
    monkeypatch.setattr('subshort.app.separate_background', separate)
    queue = JobQueue(tmp_path / 'queue.json')
    settings = Settings(root=tmp_path, ffmpeg=ffmpeg, ffprobe=ffprobe, asr_model=str(tmp_path), tts_provider='vieneu', tts_model=str(tmp_path), tts_hf_cache=str(tmp_path), tts_python=sys.executable, slow_speed=.8, final_speed=1.25)
    settings.keep_original_audio = settings.separate_background = True
    settings.separator_model_dir = str(tmp_path)
    job = Job('timing', str(video), 'vi', mode=mode)
    queue.add(job)
    events = []
    pipeline = Pipeline(settings, queue, lambda job, message="": events.append((job.stage, job.progress)) if job is not None else None)
    render = MediaAdapter.render
    def controlled_render(media, video, audio, subtitles, output, *args, **kwargs):
        assert (subtitles is None) == (mode == 'dub')
        if failure in ('render', 'cancel'):
            output.write_bytes(b'partial')
            if failure == 'cancel':
                pipeline.cancel()
                from subshort.execution import check_cancelled
                check_cancelled()
            raise MediaError('render failed')
        return render(media, video, audio, subtitles, output, *args)
    monkeypatch.setattr(MediaAdapter, 'render', controlled_render)
    if failure == 'validate':
        def reject(*a): raise MediaError('validation failed')
        monkeypatch.setattr(MediaAdapter, 'validate_output', reject)
    pipeline.run(job)
    work = tmp_path / 'output_artifacts/timing'
    final = tmp_path / 'output/source_timing_translated.mp4'
    assert not list(work.glob('*.pending.mp4'))
    if failure:
        assert job.status == ('cancelled' if failure == 'cancel' else 'failed')
        assert job.output == '' and not final.exists()
    else:
        assert job.status == 'done', job.error
        assert Path(job.output) == final
        timing = json.loads((work / 'pipeline_timing.json').read_text())
        assert timing['final_duration_seconds'] == pytest.approx(2)
        assert MediaAdapter(ffmpeg, ffprobe).probe(final)['duration'] == pytest.approx(2, abs=.15)
        stages = job.metadata['stages']
        assert ('tts' in stages) == (mode != 'subtitle')
        assert ('caption_asr' in stages) == (mode == 'subtitle_dub')
        assert ('caption' in stages) == (mode != 'dub')
        assert ('mix_background' in stages) == (mode != 'subtitle')
        assert ('separate_background' in stages) == (mode != 'subtitle')
        assert events and events[-1] == ("done", 1.0)
        assert [progress for _, progress in events] == sorted(progress for _, progress in events)
        assert {"probe", "asr", "translate", "render", "done"} <= {stage for stage, _ in events}
        if mode != 'subtitle':
            timeline = json.loads((work / 'timeline.json').read_text())
            assert timeline[1]['actual_start_seconds'] >= timeline[0]['actual_end_seconds']
        if mode != 'dub':
            srt = (work / 'translated.srt').read_text(encoding='utf-8-sig')
            expected = '00:00:00,400 --> 00:00:00,700' if mode == 'subtitle' else '00:00:00,400 --> 00:00:00,600'
            assert expected in srt
        else:
            assert not (work / 'translated.srt').exists()
