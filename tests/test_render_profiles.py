import json
import math
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

from subshort.execution import run_process
from subshort.media import MediaAdapter, MediaError


@pytest.fixture
def media():
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("FFmpeg and FFprobe required")
    return MediaAdapter(ffmpeg, ffprobe)


@pytest.mark.parametrize("layout,expected", [("original", (320, 180)), ("portrait", (1080, 1920)), ("landscape", (1920, 1080))])
def test_output_dimensions(layout, expected):
    assert MediaAdapter.output_dimensions(321, 181, layout) == expected
    assert MediaAdapter.output_dimensions(*expected, layout) == expected


@pytest.mark.parametrize("region", [(), (0, 0, 0, 1), (-0.1, 0, 1, 1), (0.5, 0, 0.6, 1), (0, 0, 1, math.nan), (False, 0, 1, 1), "0,0,1,1"])
def test_invalid_region_rejected_before_io(tmp_path, region):
    with pytest.raises(MediaError, match="Blur region"):
        MediaAdapter().render(tmp_path / "v", tmp_path / "a", None, tmp_path / "out", duration=1, blur_region=region)


@pytest.mark.parametrize("bitrate", [0, 0.01, 101, True, math.inf])
def test_invalid_bitrate(tmp_path, monkeypatch, bitrate):
    adapter = MediaAdapter()
    monkeypatch.setattr(adapter, "check", lambda: None)
    with pytest.raises(MediaError, match="Bitrate"):
        adapter.render(tmp_path / "v", tmp_path / "a", None, tmp_path / "out", duration=1, bitrate_mbps=bitrate)


def test_auto_nvenc_unavailable_falls_back_and_explicit_fails(tmp_path, monkeypatch):
    adapter = MediaAdapter()
    probes, renders = [], []
    monkeypatch.setattr(adapter, "check", lambda: None)

    def unavailable(args, **kwargs):
        probes.append(args)
        return SimpleNamespace(returncode=1, stderr="No capable devices found")

    monkeypatch.setattr("subshort.media.run_process", unavailable)
    monkeypatch.setattr(adapter, "run", lambda args, **kwargs: renders.append(args))
    adapter.render(tmp_path / "v", tmp_path / "a", None, tmp_path / "out", duration=1, encoder="auto", bitrate_mbps=3)
    assert adapter.last_encoding == {"requested": "auto", "selected": "cpu", "codec": "libx264", "reason": "NVENC không khả dụng: No capable devices found", "bitrate_mbps": 3.0}
    assert "lavfi" in probes[0] and probes[0][-2:] == ["null", "-"]
    assert renders[0][renders[0].index("-c:v") + 1] == "libx264"
    with pytest.raises(MediaError, match="NVENC"):
        adapter.render(tmp_path / "v", tmp_path / "a", None, tmp_path / "out", duration=1, encoder="nvenc")
    assert len(renders) == 1


@pytest.mark.parametrize("layout", ["original", "portrait", "landscape"])
def test_real_cpu_profiles_with_source_blur_and_freeze(media, tmp_path, layout):
    video, audio, output = (tmp_path / name for name in ("source.mp4", "voice.wav", "output.mp4"))
    media.run([media.ffmpeg, "-y", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=10", "-t", "0.1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)])
    media.run([media.ffmpeg, "-y", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "0.1", str(audio)])
    media.render(video, audio, None, output, duration=0.3, layout=layout, encoder="cpu", bitrate_mbps=2, blur_region=(0.1, 0.1, 0.5, 0.5))
    result = run_process([media.ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(output)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    picture = next(stream for stream in data["streams"] if stream["codec_type"] == "video")
    sound = next(stream for stream in data["streams"] if stream["codec_type"] == "audio")
    assert (picture["width"], picture["height"]) == media.output_dimensions(160, 90, layout)
    assert picture["codec_name"] == "h264" and picture["pix_fmt"] == "yuv420p"
    assert int(picture["nb_frames"]) == 3
    assert sound["codec_name"] == "aac" and float(sound["duration"]) == pytest.approx(0.3, abs=0.025)
    assert float(data["format"]["duration"]) == pytest.approx(0.3, abs=0.025)
    assert media.last_encoding["selected"] == "cpu"
    content = output.read_bytes()
    assert content.index(b"moov") < content.index(b"mdat")


def test_ass_filename_preserved(tmp_path, monkeypatch):
    adapter = MediaAdapter()
    captions = tmp_path / "styled.ASS"
    captions.write_text("[Script Info]\n", encoding="utf-8")
    monkeypatch.setattr(adapter, "check", lambda: None)
    seen = []

    def render(args, cwd):
        assert (Path(cwd) / "captions.ass").read_text(encoding="utf-8") == "[Script Info]\n"
        seen.append(args[args.index("-filter_complex") + 1])

    monkeypatch.setattr(adapter, "run", render)
    adapter.render(tmp_path / "v", tmp_path / "a", captions, tmp_path / "out", duration=1)
    assert "subtitles=filename=captions.ass" in seen[0]
