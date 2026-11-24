import json
import sys
from pathlib import Path
import shutil
import wave
import pytest

from subshort.pipeline import Pipeline
from subshort.execution import run_process
from subshort.media import MediaAdapter
from subshort.models import Job, Settings
from subshort.providers import Segment
from subshort.queue import JobQueue


@pytest.mark.parametrize("mode,hook_enabled", [("subtitle", False), ("subtitle_dub", True), ("dub", True), ("dub", False)])
def test_real_visual_pipeline_ass_hook_and_dub(tmp_path, monkeypatch, mode, hook_enabled):
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("FFmpeg unavailable")
    video = tmp_path / "source.mp4"
    media = MediaAdapter(ffmpeg, ffprobe)
    media.run([ffmpeg, "-y", "-f", "lavfi", "-i", "color=size=160x90:rate=10:duration=1", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-shortest", str(video)])

    class ASR:
        def transcribe(self, *args):
            return [Segment(0, 0.5, "A surprising translated opening")]

    class Translator:
        def __init__(self, *args):
            pass

        def translate(self, rows, target):
            return rows

    class TTS:
        def synthesize(self, text, output, **kwargs):
            with wave.open(str(output), "wb") as stream:
                stream.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
                stream.writeframes(b"\0\0" * 12000)

    monkeypatch.setattr("subshort.pipeline.ASRSession", lambda *a, **k: ASR())
    monkeypatch.setattr("subshort.pipeline.OpenAITranslator", Translator)
    monkeypatch.setattr("subshort.pipeline.TtsSession", lambda *a, **k: TTS())
    monkeypatch.setattr("subshort.captions.build_captions", lambda voice, references, duration: (references, {"source": "reference"}))
    queue = JobQueue(tmp_path / "queue.json")
    settings = Settings(root=tmp_path, ffmpeg=ffmpeg, ffprobe=ffprobe, asr_model=str(tmp_path), tts_provider="vieneu", tts_model=str(tmp_path), tts_hf_cache=str(tmp_path), tts_python=sys.executable, slow_speed=1, final_speed=1, hook_enabled=hook_enabled, hook_duration=1)
    job = Job("visual", str(video), "vi", mode=mode)
    queue.add(job)
    Pipeline(settings, queue, lambda *args: None).run(job)
    assert job.status == "done", job.error
    output = Path(job.output)
    assert output.parent == tmp_path / "output"
    assert output.name == f"source_{job.id}_translated.mp4"
    assert list(output.parent.iterdir()) == [output]
    assert not (output.parent / job.id).exists()
    work = tmp_path / "output_artifacts" / job.id
    assert (work / "translated.srt").exists() == (mode != "dub")
    assert (work / "translated.ass").exists() == (mode != "dub" or hook_enabled)
    if mode != "dub" or hook_enabled:
        ass = (work / "translated.ass").read_text(encoding="utf-8")
        assert "PlayResX: 160" in ass and "PlayResY: 90" in ass
        assert ("Dialogue: 10," in ass) == hook_enabled
        assert ("Dialogue: 0," in ass) == (mode != "dub")
    if hook_enabled:
        hook = json.loads((work / "hook.json").read_text(encoding="utf-8"))
        assert hook == job.metadata["hook"]
        assert hook["text"] in "A surprising translated opening"
    else:
        assert not (work / "hook.json").exists()
        assert job.metadata["hook"] is None
    assert job.metadata["encoding"]["codec"] == "libx264"
    assert job.metadata["output_dimensions"] == [160, 90]
    probe = media.probe(Path(job.output))
    assert (probe["width"], probe["height"]) == (160, 90)
    assert probe["duration"] == pytest.approx(1, abs=0.1)
    # Decode the produced video, so successful publication alone cannot hide filter errors.
    result = run_process([ffmpeg, "-v", "error", "-i", job.output, "-f", "null", "-"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
