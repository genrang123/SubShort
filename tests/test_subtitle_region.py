"""Local contracts only: no credential reads or network calls."""
import json
from types import SimpleNamespace

import pytest

from subshort.models import Settings
from subshort import subtitle_region as region
from subshort.providers import FatalProviderError, ProviderError


def frame(tmp_path):
    width, height = 100, 60
    pixels = bytearray(width * height)
    for y in range(40, 46):
        for x in range(30, 70):
            pixels[y * width + x] = 255 if x % 4 < 2 else 0
    path = tmp_path / "frame.png"
    path.write_bytes(region._png(width, height, pixels))
    return {"timestamp": 1, "path": path, "width": width, "height": height, "pixels": pixels}


def test_defaults_manual_and_off_never_sample(tmp_path, monkeypatch):
    settings = Settings(tmp_path)
    assert settings.old_subtitle_mode == "off"
    monkeypatch.setattr(region, "sample_frames", lambda *a: pytest.fail("sampling outside auto mode"))
    assert region.resolve_region(settings, None, None, {}, tmp_path)[0] is None
    settings.old_subtitle_mode, settings.old_subtitle_region = "manual", "[0.1,0.7,0.8,0.2]"
    assert region.resolve_region(settings, None, None, {}, tmp_path)[0] == (.1, .7, .8, .2)


def test_auto_samples_and_calls_provider_without_extra_permission(tmp_path, monkeypatch):
    settings = Settings(tmp_path, old_subtitle_mode="auto")
    calls = []
    frames = [frame(tmp_path)]
    def sample(*args):
        calls.append("sample")
        return frames
    def detect(sampled, selected):
        assert sampled is frames and selected is settings
        calls.append("detect")
        return None
    monkeypatch.setattr(region, "sample_frames", sample)
    monkeypatch.setattr(region, "detect", detect)
    selected, info = region.resolve_region(settings, None, None, {}, tmp_path)
    assert selected is None and info["result"] == "no_subtitles"
    assert calls == ["sample", "detect"]


@pytest.mark.parametrize("content", ['{}', 'null', '{"has_subtitles":true,"box":null}',
    '{"has_subtitles":false,"box":[0,0,1,1]}', '{"has_subtitles":true,"box":[0,0,NaN,1]}',
    '{"has_subtitles":true,"box":[0,0,2,1]}', '{"has_subtitles":1,"box":null}'])
def test_malformed_never_invents_box(content):
    with pytest.raises(ProviderError):
        region.parse_detection(content)


def test_no_subtitle_and_real_pixel_refinement(tmp_path):
    assert region.parse_detection('{"has_subtitles":false,"box":null}') is None
    proposed = (.1, .5, .8, .4)
    refined = region.refine_region([frame(tmp_path)], proposed)
    assert refined != proposed
    x, y, w, h = refined
    assert x <= .30 and x + w >= .70 and y <= 40 / 60 and y + h >= 46 / 60
    blank = {**frame(tmp_path), "pixels": bytes(6000)}
    with pytest.raises(ProviderError, match="pixel"):
        region.refine_region([blank], proposed)


def test_auto_diagnostics_and_no_box(tmp_path, monkeypatch):
    settings = Settings(tmp_path, old_subtitle_mode="auto")
    monkeypatch.setattr(region, "sample_frames", lambda *a: [frame(tmp_path)])
    monkeypatch.setattr(region, "detect", lambda *a: (.1, .5, .8, .4))
    selected, info = region.resolve_region(settings, None, None, {}, tmp_path)
    assert selected == info["refined_box"] and info["proposed_box"] != selected
    assert info["sampled_frames"][0]["timestamp"] == 1
    assert json.loads((tmp_path / "subtitle_region.json").read_text())["region"] == list(selected)
    monkeypatch.setattr(region, "detect", lambda *a: None)
    selected, info = region.resolve_region(settings, None, None, {}, tmp_path)
    assert selected is None and info["result"] == "no_subtitles"


@pytest.mark.parametrize("provider,host", [("openai", "api.openai.com"), ("gemini", "generativelanguage.googleapis.com")])
def test_selected_transport_model_retry_and_safe_errors(tmp_path, monkeypatch, provider, host):
    settings = Settings(tmp_path, vision_provider=provider, vision_model="chosen-vision", openai_api_key="fake", gemini_api_key="fake")
    requests = []
    def worker(request):
        request = json.loads(request)
        requests.append(request)
        if len(requests) == 1:
            return json.dumps({"ok": False, "status": 429, "body": "secret response", "retry_after": 0})
        return json.dumps({"ok": True, "body": json.dumps({"choices": [{"message": {"content": '{"has_subtitles":false,"box":null}'}}]})})
    monkeypatch.setattr(region.providers, "_run_worker", worker)
    assert region.detect([frame(tmp_path)], settings) is None
    assert len(requests) == 2 and host in requests[0]["url"]
    payload = json.loads(requests[0]["payload"])
    assert payload["model"] == "chosen-vision"
    assert payload["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    monkeypatch.setattr(region.providers, "_run_worker", lambda *a: '{"ok":false,"status":401,"body":"secret response"}')
    with pytest.raises(FatalProviderError) as error:
        region.detect([frame(tmp_path)], settings)
    assert "secret response" not in str(error.value)


def test_sampling_is_deterministic_and_bounded(tmp_path, monkeypatch):
    commands = []
    def run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout=bytes(360 * 640))
    monkeypatch.setattr(region, "run_process", run)
    frames = region.sample_frames(SimpleNamespace(ffmpeg="ffmpeg"), "source.mp4", {"width": 1920, "height": 1080, "duration": 10}, tmp_path)
    assert [f["timestamp"] for f in frames] == [2, 5, 8]
    assert len(commands) == 3 and all(f["width"] == 640 and f["height"] == 360 for f in frames)


def test_cancelled_before_transport(tmp_path, monkeypatch):
    from subshort.execution import Cancelled
    monkeypatch.setattr(region, "check_cancelled", lambda: (_ for _ in ()).throw(Cancelled()))
    monkeypatch.setattr(region.providers, "_run_worker", lambda *a: pytest.fail("transport after cancellation"))
    with pytest.raises(Cancelled):
        region.detect([frame(tmp_path)], Settings(tmp_path, openai_api_key="fake"))

def test_real_ffmpeg_sampling_and_pixel_refinement(tmp_path):
    import shutil
    import subprocess
    from subshort.media import MediaAdapter
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg not installed")
    video = tmp_path / "burned-region.mp4"
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i",
                    "color=black:s=320x180:d=1,drawgrid=x=80:y=130:w=8:h=12:t=2:c=white",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)], check=True, capture_output=True)
    frames = region.sample_frames(MediaAdapter(ffmpeg), video, {"width": 320, "height": 180, "duration": 1}, tmp_path)
    refined = region.refine_region(frames, (.1, .65, .8, .25))
    assert all(f["path"].read_bytes().startswith(b"\x89PNG") for f in frames)
    assert region.validate_region(refined) == refined
