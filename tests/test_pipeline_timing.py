import array
import json
import math
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import pytest

from subshort.pipeline import Pipeline
from subshort.media import MediaAdapter, MediaError
from subshort.models import Job, Settings
from subshort.providers import Segment
from subshort.queue import JobQueue


@pytest.mark.parametrize('failure,mode,overflow', [
    (failure, mode, False)
    for mode in ('subtitle_dub', 'subtitle', 'dub')
    for failure in (None, 'render', 'validate', 'cancel')
] + [pytest.param(None, 'dub', True, id='dubbing-overflow')])
def test_real_pipeline_duration_and_publish(tmp_path, monkeypatch, failure, mode, overflow):
    ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:
        pytest.skip('FFmpeg unavailable')
    video = tmp_path / 'source.mp4'
    subprocess.run([
        ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'color=c=red:size=64x64:rate=25:duration=2',
        '-f', 'lavfi', '-i', 'anullsrc=r=16000:cl=mono',
        '-vf', "drawbox=color=blue:t=fill:enable='gte(t,1.5)'", '-t', '2', str(video),
    ], check=True, capture_output=True)
    translated = []
    class ASR:
        def transcribe(self, *a): return [Segment(0, .3, 'first'), Segment(.4, .7, 'second')]
    class Translator:
        def __init__(self, *a, **k): pass
        def translate(self, rows, target):
            translated.append(target)
            return rows
    class TTS:
        def synthesize(self, text, output, **kwargs):
            with wave.open(str(output), 'wb') as stream:
                stream.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
                frames = 48000 if overflow else 6000
                tone = array.array('h', (round(8000 * math.sin(2 * math.pi * 440 * i / 24000)) for i in range(frames)))
                stream.writeframes(tone.tobytes())
    monkeypatch.setattr('subshort.pipeline.TtsSession', lambda *a, **k: TTS())
    monkeypatch.setattr('subshort.pipeline.ASRSession', lambda *a, **k: ASR())
    monkeypatch.setattr('subshort.pipeline.OpenAITranslator', Translator)
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
    monkeypatch.setattr('subshort.pipeline.separate_background', separate)
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
        return render(media, video, audio, subtitles, output, *args, **kwargs)
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
        duration = 3.2 if overflow else 2
        assert timing['version'] == 4
        assert timing['policy'] == ('source-windows-v1' if mode == 'subtitle' else 'unbounded-dubbing-v1')
        assert timing['picture_speed'] == pytest.approx(1)
        assert timing['final_duration_seconds'] == pytest.approx(duration)
        assert timing['freeze_seconds'] == pytest.approx(1.2 if overflow else 0)
        assert MediaAdapter(ffmpeg, ffprobe).probe(final)['duration'] == pytest.approx(duration, abs=.15)
        assert translated == ['vi']
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
            if overflow:
                assert [row['text'] for row in timeline] == ['first', 'second']
                assert [row['duration_seconds'] for row in timeline] == [2, 2]
                assert timeline[1]['requested_start_seconds'] == .5
                assert timeline[1]['actual_start_seconds'] == 2
                assert timeline[1]['actual_end_seconds'] == 4
                assert timing['target_duration_seconds'] == 4

                # The picture should still change on schedule, then hold its final color.
                def pixel_at(seconds):
                    pixel = subprocess.run([
                        ffmpeg, '-v', 'error', '-ss', seconds, '-i', str(final), '-an',
                        '-vf', 'scale=2:2', '-frames:v', '1', '-pix_fmt', 'rgb24', '-threads:v', '1', '-f', 'rawvideo', '-',
                    ], check=True, capture_output=True).stdout
                    assert len(pixel) == 12
                    return pixel[:3]

                red, _, blue = pixel_at('1.2')
                assert red > 200 and blue < 50
                for position in ('1.8', '2.8'):
                    red, _, blue = pixel_at(position)
                    assert blue > 200 and red < 50

                tail = subprocess.run([
                    ffmpeg, '-v', 'error', '-ss', '2.7', '-i', str(final), '-t', '0.3',
                    '-vn', '-ac', '1', '-ar', '24000', '-f', 's16le', '-',
                ], check=True, capture_output=True).stdout
                energy = array.array('h')
                energy.frombytes(tail)
                assert len(energy) >= 6000
                assert sum(abs(value) for value in energy) / len(energy) > 500
        if mode != 'dub':
            srt = (work / 'translated.srt').read_text(encoding='utf-8-sig')
            expected = '00:00:00,400 --> 00:00:00,700' if mode == 'subtitle' else '00:00:00,400 --> 00:00:00,600'
            assert expected in srt
        else:
            assert not (work / 'translated.srt').exists()
