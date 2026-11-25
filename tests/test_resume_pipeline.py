import json
import sys
from types import SimpleNamespace
import wave
from pathlib import Path

import pytest

from subshort.pipeline import Pipeline
from subshort.execution import check_cancelled
from subshort.models import Settings
from subshort.providers import Segment


def harness(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video bytes")
    counts = {"asr": 0, "translate": 0, "tts": 0, "normalize": 0}
    control = {"cancel_after": None, "pipeline": None}

    class Media:
        last_encoding = {}
        def __init__(self, *args): pass
        def probe(self, path): return {"has_audio": True, "duration": control.get("duration", 4), "width": 32, "height": 32}
        def output_dimensions(self, width, height, layout=None): return width, height
        def extract_audio(self, source, output): output.write_bytes(b"audio")
        def extract_audio_window(self, source, output, start, end):
            output.write_bytes(b"window")
        def normalize_clip(self, raw, output, *, speed=1.0):
            counts["normalize"] += 1
            output.write_bytes(raw.read_bytes())
        def timeline_audio(self, clips, starts, output, duration): output.write_bytes(b"timeline")
        mix_timeline_audio = timeline_audio
        def prepare_audio(self, source, output, *args): output.write_bytes(b"final")
        def render(self, source, audio, subtitles, output, *args, **kwargs): output.write_bytes(b"render")
        def validate_output(self, *args): pass
    monkeypatch.setattr("subshort.pipeline.MediaAdapter", Media)

    class ASR:
        def transcribe(self, *args):
            counts["asr"] += 1
            if control.get("resplit") and Path(args[0]).parent.name == "review":
                return [Segment(0, 1, "child")]
            return control.get("segments", [Segment(0, 1, "first"), Segment(2, 3, "second")])

    class Translator:
        def __init__(self, *args, **kwargs): pass
        def translate(self, segments, language):
            counts["translate"] += 1
            return [Segment(s.start, s.end, s.text + language, s.source_id) for s in segments]
    monkeypatch.setattr("subshort.pipeline.ASRSession", lambda *args, **kwargs: ASR())
    monkeypatch.setattr("subshort.pipeline.OpenAITranslator", Translator)

    class TTS:
        def voices(self): return [{"id": "Hải Đăng"}, {"id": "Trúc Ly"}]
        def synthesize(self, text, output, **kwargs):
            control.setdefault("tts_calls", []).append((text, kwargs))
            counts["tts"] += 1
            if counts["tts"] == control["cancel_after"]:
                control["pipeline"].cancel()
                check_cancelled()
            with wave.open(str(output), "wb") as stream:
                stream.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
                stream.writeframes(b"\0\0" * control.get("frames", 2400))

    monkeypatch.setattr("subshort.pipeline.TtsSession", lambda *args, **kwargs: TTS())
    asr_model = tmp_path / "asr-model"; asr_model.mkdir()
    tts_model = tmp_path / "tts-model"; tts_model.mkdir()
    tts_cache = tmp_path / "tts-cache"; tts_cache.mkdir()
    settings = Settings(root=tmp_path, asr_model=str(asr_model), mode="dub",
                        tts_provider="vieneu", tts_model=str(tts_model), tts_hf_cache=str(tts_cache),
                        tts_python=sys.executable)
    job = SimpleNamespace(id="resume", source=str(source), mode="dub", metadata={},
                          prepare=lambda _: settings, output="", error="")
    queue = SimpleNamespace(update=lambda job: None)

    def run():
        pipeline = Pipeline(settings, control.get("queue", queue), lambda *args: None)
        control["pipeline"] = pipeline
        current = control.get("job", job)
        pipeline.run(current)
        return current

    return settings, counts, control, run


def test_pipeline_preserves_three_source_turns(tmp_path, monkeypatch):
    settings, counts, control, run = harness(tmp_path, monkeypatch)
    control["duration"] = 20
    control["segments"] = [Segment(0, 1, "A"), Segment(5.40, 6, "B"), Segment(12.86, 14, "C")]
    settings.slow_speed = .7
    settings.final_speed = 1.25
    job = run()
    assert job.status == "done", job.error
    rows = json.loads((tmp_path / "output_artifacts/resume/timeline.json").read_text())
    assert [row["source_id"] for row in rows] == ["0", "1", "2"]
    assert [row["actual_start_seconds"] for row in rows] == [round(s.start / .7 * 24000) / 24000 for s in control["segments"]]


def test_pipeline_delays_next_cue_without_overlap(tmp_path, monkeypatch):
    settings, _, control, run = harness(tmp_path, monkeypatch)
    settings.slow_speed = 1
    control["segments"] = [Segment(0, .075, "first"), Segment(.075, .2, "second")]
    control["frames"] = 3600

    job = run()
    assert job.status == "done", job.error
    rows = json.loads((tmp_path / "output_artifacts/resume/timeline.json").read_text())
    assert rows[0]["actual_end_seconds"] == rows[1]["actual_start_seconds"] == .15
    assert rows[1]["delay_seconds"] == .075
    assert job.metadata["timing"]["final_duration_seconds"] == 4


def test_overflow_resume_keeps_reviewed_text_without_extra_translation(tmp_path, monkeypatch):
    settings, counts, control, run = harness(tmp_path, monkeypatch)
    settings.slow_speed = 1
    settings.final_speed = 1.25
    control["frames"] = 72000
    first = run()
    assert first.status == "done", first.error
    first.config = {"review_translation": {"0": "Lời đã duyệt, không rút gọn"}}
    reviewed = run()
    assert reviewed.status == "done", reviewed.error

    work = tmp_path / "output_artifacts/resume"
    translation = work / "translation_full.srt"
    original_srt = translation.read_bytes()
    rows = json.loads((work / "timeline.json").read_text(encoding="utf-8"))
    expected_text = ["Lời đã duyệt, không rút gọn", "secondvi"]
    assert [row["text"] for row in rows] == expected_text
    assert [text for text, _ in control["tts_calls"][-2:]] == ["secondvi", expected_text[0]]
    assert expected_text[0] in original_srt.decode("utf-8-sig")
    assert [row["duration_seconds"] for row in rows] == [3, 3]
    assert rows[0]["actual_end_seconds"] == rows[1]["actual_start_seconds"] == 3
    assert rows[1]["actual_end_seconds"] == 6
    assert reviewed.metadata["timing"]["final_duration_seconds"] == 4.8
    assert reviewed.metadata["timing"]["freeze_seconds"] == 1.6
    assert counts["translate"] == 1 and counts["tts"] == 3

    resumed = run()
    assert resumed.status == "done", resumed.error
    assert counts["translate"] == 1 and counts["tts"] == 3
    assert {"translate", "tts"} <= set(resumed.metadata["reused_stages"])
    assert [row["text"] for row in resumed.metadata["review_translation"]] == expected_text
    assert translation.read_bytes() == original_srt
    assert json.loads((work / "timeline.json").read_text(encoding="utf-8")) == rows


def test_cancel_resume_reuses_completed_stages_and_clip(tmp_path, monkeypatch):
    settings, counts, control, run = harness(tmp_path, monkeypatch)
    control["cancel_after"] = 2
    cancelled = run()
    assert cancelled.status == "cancelled", cancelled.error
    assert cancelled.stage == "tts"
    assert cancelled.progress == pytest.approx(.70)
    control["cancel_after"] = None
    job = run()
    assert job.status == "done", job.error
    assert counts == {"asr": 1, "translate": 1, "tts": 3, "normalize": 3}
    assert "asr" not in job.metadata["stages"]
    assert "translate" not in job.metadata["stages"]
    assert {"asr", "translate", "tts_clip_1"} <= set(job.metadata["reused_stages"])
    assert "tts" in job.metadata["stages"]
    assert Path(job.output).parent == tmp_path / "output"
    assert Path(job.output).read_bytes() == b"render"
    assert (tmp_path / "output_artifacts/resume/transcript_raw.srt").is_file()
    assert (tmp_path / "output_artifacts/resume/timeline.json").is_file()


def test_speed_reuses_voice_change_and_text_invalidate_clips(tmp_path, monkeypatch):
    settings, counts, control, run = harness(tmp_path, monkeypatch)
    assert run().status == "done", run().error
    settings.slow_speed = .8
    settings.final_speed = 1.25
    job = run()
    assert job.status == "done", job.error
    assert counts["tts"] == 2
    assert {"asr", "translate", "tts"} <= set(job.metadata["reused_stages"])
    assert not {"asr", "translate", "tts"} & set(job.metadata["stages"])
    assert counts["normalize"] == 4
    settings.tts_voice = "different-voice"
    assert run().status == "done"
    assert counts["tts"] == 4 and counts["asr"] == counts["translate"] == 1
    settings.target_language = "en"
    assert run().status == "done"
    assert counts["translate"] == 2 and counts["tts"] == 6 and counts["asr"] == 1


def test_source_and_model_bytes_invalidate_asr(tmp_path, monkeypatch):
    settings, counts, control, run = harness(tmp_path, monkeypatch)
    model = tmp_path / "model"
    model.mkdir()
    (model / "weights").write_bytes(b"one")
    settings.asr_model = str(model)
    assert run().status == "done"
    (model / "weights").write_bytes(b"two")
    assert run().status == "done"
    assert counts["asr"] == 2
    (tmp_path / "source.mp4").write_bytes(b"other bytes")
    assert run().status == "done"
    assert counts["asr"] == 3


def test_cached_translation_resumes_without_extra_permission(tmp_path, monkeypatch):
    settings, counts, control, run = harness(tmp_path, monkeypatch)
    assert run().status == "done"
    assert run().status == "done"
    assert counts["translate"] == 1

def test_review_edits_resume_from_completed_job(tmp_path, monkeypatch):
    settings, counts, control, run = harness(tmp_path, monkeypatch)
    job = run()
    assert job.status == "done", job.error
    assert job.metadata["review_transcript"][0]["text"] == "first"
    job.config = {"review_translation": {"0": "Lời đã duyệt"}}
    assert run().status == "done", job.error
    assert counts["asr"] == 1 and counts["translate"] == 1 and counts["tts"] == 3
    assert "Lời đã duyệt" in (tmp_path / "output_artifacts/resume/translation_full.srt").read_text(encoding="utf-8")
    job.config = {"review_transcript": {"0": "corrected"}}
    assert run().status == "done", job.error
    assert counts["asr"] == 1 and counts["translate"] == 2 and counts["tts"] == 4
    assert "corrected" in job.metadata["review_translation"][0]["text"]
    assert "first" in (tmp_path / "output_artifacts/resume/transcript_raw.srt").read_text(encoding="utf-8")


@pytest.mark.parametrize("resplit,unknown_voice", [(False, False), (True, False), (False, True)])
def test_persisted_speaker_review_dispatches_two_preset_voices(tmp_path, monkeypatch, resplit, unknown_voice):
    from subshort.diarization import SpeakerTurn, review_fingerprint
    from subshort.models import Job
    from subshort.providers import FatalProviderError
    from subshort.queue import JobQueue

    settings, counts, control, run = harness(tmp_path, monkeypatch)
    settings.multi_speaker = True
    settings.diarization_model = settings.asr_model
    settings.ffmpeg = settings.ffprobe = sys.executable
    control["resplit"] = resplit
    if resplit:
        control["segments"] = [Segment(0, 3, "two speakers")]
    turns = [SpeakerTurn(0, 1, "speaker-1"), SpeakerTurn(2, 3, "speaker-2")]
    monkeypatch.setattr(Pipeline, "_diarization", lambda *args: SimpleNamespace(diarize=lambda *args: turns))
    queue = JobQueue(tmp_path / "queue.json")
    job = Job("resume", str(tmp_path / "source.mp4"), "vi", mode="dub")
    queue.add(job)
    control.update(job=job, queue=queue)
    with pytest.raises(FatalProviderError, match="Cần duyệt"):
        run()
    job = JobQueue(queue.path).snapshot()[0]
    assert job.metadata["speaker_review_required"] is True
    assert counts["tts"] == 0
    reviewed = [SpeakerTurn(**{**turn, "status": "resolved"}) for turn in job.metadata["diarization"]]
    transcript_edits = {"0": "corrected"}
    review_segments = [Segment(**{**segment, "text": transcript_edits.get(segment["source_id"], segment["text"])})
                       for segment in job.metadata["review_transcript"]]
    voice_map = {"speaker-1": "Hải Đăng", "speaker-2": "Trúc Ly"}
    if unknown_voice:
        voice_map["speaker-2"] = "not-a-preset"
    translation_edits = {"0.0" if resplit else "0": "Lời đã duyệt"}
    job.config.update(review_transcript=transcript_edits, review_translation=translation_edits,
                      speaker_review={"approved": True, "turns": [turn.__dict__ for turn in reviewed],
                                      "voice_map": voice_map,
                                      "fingerprint": review_fingerprint(job.metadata["review_source_hash"], review_segments, reviewed,
                                                                        [segment.text for segment in review_segments])})
    queue.update(job)
    queue = JobQueue(queue.path)
    control.update(job=queue.snapshot()[0], queue=queue)
    if unknown_voice:
        with pytest.raises(FatalProviderError, match="Không tìm thấy voice"):
            run()
        assert counts["tts"] == 0
        return
    job = run()
    assert job.status == "done", job.error
    persisted = JobQueue(queue.path).snapshot()[0]
    assert persisted.config["speaker_review"]["voice_map"] == voice_map
    assert persisted.config["review_transcript"] == transcript_edits
    assert persisted.config["review_translation"] == translation_edits
    assert [options for _, options in control["tts_calls"]] == [{"voice": "Hải Đăng"}, {"voice": "Trúc Ly"}]
    assert control["tts_calls"][0][0] == "Lời đã duyệt"
    timeline = json.loads((tmp_path / "output_artifacts/resume/timeline.json").read_text(encoding="utf-8"))
    assert [row["speaker_id"] for row in timeline] == ["speaker-1", "speaker-2"]
    assert [row["source_id"] for row in timeline] == (["0.0", "0.1"] if resplit else ["0", "1"])
    assert [row["source_start_seconds"] for row in timeline] == [0, 2]
    assert [row["actual_start_seconds"] for row in timeline] == [0, round(2 / settings.slow_speed * 24000) / 24000]
    assert [row["duration_seconds"] for row in timeline] == [.1, .1]
    assert [row["delay_seconds"] for row in timeline] == [0, 0]
    assert persisted.metadata["segments"] == 2
    raw = (tmp_path / "output_artifacts/resume/transcript_raw.srt").read_text(encoding="utf-8")
    assert ("two speakers" if resplit else "first") in raw
    assert "corrected" not in raw


@pytest.mark.parametrize("provider", ["vieneu", "omnivoice"])
def test_custom_voice_reference_arguments_match_provider(tmp_path, monkeypatch, provider):
    settings, counts, control, run = harness(tmp_path, monkeypatch)
    settings.tts_provider = provider
    settings.tts_custom_voice = "custom"
    reference = {"provider": provider, "path": str(tmp_path / "reference.wav"), "ref_text": "reference words"}
    monkeypatch.setattr("subshort.pipeline.VoiceRegistry", lambda *args: SimpleNamespace(resolve=lambda _: reference))
    job = run()
    assert job.status == "done", job.error
    expected = {"ref_audio": reference["path"]}
    if provider == "omnivoice":
        expected["ref_text"] = reference["ref_text"]
    assert [options for _, options in control["tts_calls"]] == [expected, expected]
