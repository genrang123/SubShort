import array
import json
import math
import shutil
import subprocess
import wave
from types import SimpleNamespace

import pytest

from subshort.execution import Cancelled, Execution
from subshort.media import MediaAdapter, MediaError


def pcm(path, frames, value=1000, channels=1, rate=24000):
    with wave.open(str(path), 'wb') as stream:
        stream.setparams((channels, 2, rate, 0, 'NONE', 'not compressed'))
        stream.writeframes(array.array('h', [value] * frames * channels).tobytes())
    return path


def samples(path):
    with wave.open(str(path), 'rb') as stream:
        data = array.array('h')
        data.frombytes(stream.readframes(stream.getnframes()))
        return stream.getparams(), data


@pytest.fixture
def media():
    ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:
        pytest.skip('FFmpeg and ffprobe required')
    return MediaAdapter(ffmpeg, ffprobe)


def test_timeline_exact_frames_gaps_padding_and_no_voice_truncation(tmp_path):
    first = pcm(tmp_path / 'first.wav', 7, 1200)
    second = pcm(tmp_path / 'second.wav', 5, -1800)
    output = tmp_path / 'timeline.wav'
    adapter = MediaAdapter()
    adapter.timeline_audio([first, second], [3 / 24000, 15 / 24000], output, 25 / 24000)
    params, data = samples(output)
    assert (params.nchannels, params.sampwidth, params.framerate) == (1, 2, 24000)
    assert list(data) == [0] * 3 + [1200] * 7 + [0] * 5 + [-1800] * 5 + [0] * 5
    adapter.timeline_audio([first], [0], output, 2 / 24000)
    assert list(samples(output)[1]) == [1200] * 7


@pytest.mark.parametrize('starts', [[0, 0.001], [0.01, 0], [-1, 1], [math.nan, 1], [math.inf, 1], [True, 1], [0]])
def test_timeline_rejects_invalid_or_overlapping_starts(tmp_path, starts):
    clip = pcm(tmp_path / 'clip.wav', 240)
    with pytest.raises(MediaError):
        MediaAdapter().timeline_audio([clip, clip], starts, tmp_path / 'bad.wav')


@pytest.mark.parametrize('channels,rate', [(2, 24000), (1, 48000)])
def test_timeline_rejects_non_normalized_pcm(tmp_path, channels, rate):
    clip = pcm(tmp_path / 'clip.wav', 10, channels=channels, rate=rate)
    with pytest.raises(MediaError, match='mono PCM16'):
        MediaAdapter().timeline_audio([clip], [0], tmp_path / 'bad.wav')


def test_timeline_streams_one_clip_in_bounded_blocks(tmp_path, monkeypatch):
    clips = [pcm(tmp_path / f'{i}.wav', 40000) for i in range(3)]
    original = wave.open
    active = 0
    reads = []

    class Reader:
        def __init__(self, reader):
            self.reader = reader
        def __getattr__(self, name):
            return getattr(self.reader, name)
        def __enter__(self):
            nonlocal active
            active += 1
            assert active == 1
            return self
        def __exit__(self, *args):
            nonlocal active
            self.reader.close()
            active -= 1
        def readframes(self, count):
            reads.append(count)
            assert count <= 16384
            return self.reader.readframes(count)

    def tracked(path, mode):
        stream = original(path, mode)
        return Reader(stream) if mode == 'rb' else stream

    monkeypatch.setattr('subshort.media.wave.open', tracked)
    MediaAdapter().timeline_audio(clips, [0, 2, 4], tmp_path / 'timeline.wav')
    assert len(reads) == 9
    assert active == 0


@pytest.mark.parametrize('start', [0, 5])
def test_timeline_cancellation_inside_audio_and_silence_blocks(tmp_path, monkeypatch, start):
    clip = pcm(tmp_path / 'clip.wav', 80000)
    execution = Execution()
    checks = 0

    def check():
        nonlocal checks
        checks += 1
        if checks == 5:
            execution.cancel()
        execution.check()

    monkeypatch.setattr('subshort.media.check_cancelled', check)
    with execution.activate(), pytest.raises(Cancelled):
        MediaAdapter().timeline_audio([clip], [start], tmp_path / 'cancelled.wav')
    assert checks == 5


def test_real_normalize_stereo_and_prepare_exact_duration(media, tmp_path):
    stereo = pcm(tmp_path / 'stereo.wav', 4410, channels=2, rate=44100)
    normalized = tmp_path / 'normalized.wav'
    media.normalize_clip(stereo, normalized)
    params, data = samples(normalized)
    assert (params.nchannels, params.sampwidth, params.framerate, params.nframes) == (1, 2, 24000, 2400)
    assert max(data) > 0
    for speed in (0.125, 1, 8):
        output = tmp_path / f'prepared-{speed}.wav'
        media.prepare_audio(normalized, output, speed, 0.413)
        params, _ = samples(output)
        assert (params.framerate, params.sampwidth, params.nframes) == (48000, 2, round(0.413 * 48000))
    media.prepare_background(normalized, tmp_path / 'background.wav', 1, 0.413)
    assert samples(tmp_path / 'background.wav')[0].nframes == round(0.413 * 48000)


@pytest.mark.parametrize('speed', [0, -1, math.inf, math.nan, True])
def test_prepare_and_render_reject_invalid_speed(tmp_path, speed):
    adapter = MediaAdapter()
    with pytest.raises(MediaError):
        adapter.prepare_audio(tmp_path / 'a', tmp_path / 'b', speed, 1)
    with pytest.raises(MediaError):
        adapter.render(tmp_path / 'a', tmp_path / 'b', tmp_path / 'c', tmp_path / 'd', speed, 1)


def test_render_requires_duration(tmp_path):
    with pytest.raises(MediaError):
        MediaAdapter().render(*(tmp_path / name for name in ('v', 'a', 's', 'out')))


@pytest.mark.parametrize('speed', [0.5, 2])
def test_real_render_speed_subtitle_punctuation_duration_and_audio_tail(media, tmp_path, speed):
    video = tmp_path / 'source.mp4'
    subprocess.run([media.ffmpeg, '-y', '-f', 'lavfi', '-i', 'color=c=blue:s=160x120:r=25:d=0.6', '-c:v', 'libx264', str(video)], check=True, capture_output=True)
    voice = tmp_path / 'voice.wav'
    with wave.open(str(voice), 'wb') as stream:
        stream.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
        stream.writeframes(array.array('h', (round(5000 * math.sin(2 * math.pi * 440 * i / 24000)) for i in range(24000))).tobytes())
    audio = tmp_path / 'prepared.wav'
    media.prepare_audio(voice, audio, 1, 1)
    directory = tmp_path / "captions [a,b]; it's complicated"
    directory.mkdir()
    subtitles = directory / "text's [one],two.srt"
    subtitles.write_text('1\n00:00:00,000 --> 00:00:00,900\nHello\n', encoding='utf-8')
    output = tmp_path / 'rendered.mp4'
    media.render(video, audio, subtitles.resolve(), output, speed, 1)
    media.validate_output(output, 1)
    result = subprocess.run([media.ffprobe, '-v', 'error', '-show_streams', '-of', 'json', str(output)], check=True, capture_output=True, text=True)
    streams = json.loads(result.stdout)['streams']
    assert {s['codec_type'] for s in streams} == {'video', 'audio'}
    assert all(abs(float(s['duration']) - 1) <= 0.2 for s in streams)
    tail = subprocess.run([media.ffmpeg, '-v', 'error', '-ss', '0.8', '-i', str(output), '-vn', '-f', 's16le', '-ac', '1', '-ar', '24000', '-'], check=True, capture_output=True).stdout
    energy = array.array('h')
    energy.frombytes(tail)
    assert len(energy) >= 2400
    assert sum(abs(value) for value in energy) / len(energy) > 500
    with pytest.raises(MediaError):
        media.validate_output(output, 2)


@pytest.mark.parametrize('streams', [[], [{'codec_type': 'video', 'duration': '1'}], [{'codec_type': 'video', 'duration': 'nan'}, {'codec_type': 'audio', 'duration': '1'}], [{'codec_type': 'video', 'duration': '1'}, {'codec_type': 'audio', 'duration': '0.5'}]])
def test_validate_rejects_missing_or_invalid_stream_durations(tmp_path, monkeypatch, streams):
    output = tmp_path / 'output.mp4'
    output.write_bytes(b'nonempty')
    monkeypatch.setattr('subshort.media.run_process', lambda *a, **k: SimpleNamespace(returncode=0, stdout=json.dumps({'format': {'duration': '1'}, 'streams': streams})))
    with pytest.raises(MediaError):
        MediaAdapter().validate_output(output, 1)


def test_validate_rejects_empty_output(tmp_path):
    output = tmp_path / 'empty.mp4'
    output.touch()
    with pytest.raises(MediaError):
        MediaAdapter().validate_output(output, 1)
