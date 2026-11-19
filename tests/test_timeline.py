import wave

import pytest

from subshort.providers import ProviderError, Segment, build_timeline, timeline_alignment


def pcm_clip(tmp_path, name, frames, *, channels=1, rate=24000, width=2):
    path = tmp_path / name
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(channels)
        audio.setframerate(rate)
        audio.setsampwidth(width)
        audio.writeframes(b"\0" * frames * channels * width)
    return path

def test_source_turns_keep_separate_starts(tmp_path):
    segments = [Segment(start, start + 1, str(i)) for i, start in enumerate((0, 5.40, 12.86))]
    clips = [pcm_clip(tmp_path, f"turn{i}.wav", 24000) for i in range(3)]
    timeline = build_timeline(segments, clips, 0.7, source_duration=20)
    assert [row["actual_start_seconds"] for row in timeline] == [round(s.start / 0.7 * 24000) / 24000 for s in segments]
    assert all(row["delay_seconds"] == 0 for row in timeline)

def test_overlong_clip_never_moves_next_turn(tmp_path):
    clips = [pcm_clip(tmp_path, "long.wav", 48001), pcm_clip(tmp_path, "next.wav", 24000)]
    with pytest.raises(ValueError, match="alignment"):
        build_timeline([Segment(0, 1, "first"), Segment(2, 3, "next")], clips, source_duration=4)



def test_timeline_measures_pcm_without_moving_starts(tmp_path):
    clips = [pcm_clip(tmp_path, "a.wav", 48000), pcm_clip(tmp_path, "b.wav", 12000)]
    timeline = build_timeline([Segment(1, 2, "A"), Segment(4, 5, "B")], clips, source_duration=6)
    assert [row["actual_start_seconds"] for row in timeline] == [1, 4]
    assert [row["actual_end_seconds"] for row in timeline] == [3, 4.5]


def test_sample_grid_exact_boundary(tmp_path):
    clips = [pcm_clip(tmp_path, "a.wav", 7), pcm_clip(tmp_path, "b.wav", 7)]
    timeline = build_timeline([Segment(0, 7 / 24000, "A"), Segment(7 / 24000, 14 / 24000, "B")], clips)
    assert timeline[1]["actual_start_seconds"] == 7 / 24000
    assert timeline[1]["actual_end_seconds"] == 14 / 24000


def test_nonunit_speeds_keep_source_duration(tmp_path):
    clips = [pcm_clip(tmp_path, "a.wav", 24000), pcm_clip(tmp_path, "b.wav", 24000)]
    timeline = build_timeline([Segment(0, .5, "A"), Segment(1, 2, "B")], clips, .5, source_duration=5)
    result = timeline_alignment(timeline, 5, .5, 1.25)
    assert timeline[1]["actual_start_seconds"] == 2
    assert result["picture_speed"] == .625
    assert result["final_duration_seconds"] == 8
    assert result["freeze_seconds"] == 0


@pytest.mark.parametrize("rows", [
    [Segment(0, 1, "A"), Segment(0, 2, "B")],
    [Segment(0, 2, "A"), Segment(1, 3, "B")],
])
def test_source_windows_reject_overlap(tmp_path, rows):
    clips = [pcm_clip(tmp_path, f"{i}.wav", 1) for i in range(2)]
    with pytest.raises(ValueError, match="alignment"):
        build_timeline(rows, clips, source_duration=4)


def test_empty_timeline_retains_slow_source_duration():
    assert build_timeline([], []) == []
    assert timeline_alignment([], 5, 0.5, 2) == {
        "source_duration_seconds": 5, "slow_duration_seconds": 10,
        "video_speed": 1, "picture_speed": 1, "target_duration_seconds": 10,
        "final_duration_seconds": 5, "freeze_seconds": 0,
    }


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), float("-inf"), True, "1", None])
def test_timeline_rejects_invalid_speed(value):
    with pytest.raises(ValueError):
        build_timeline([], [], value)


@pytest.mark.parametrize("field", [0, 1, 2])
@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), True, "1", None])
def test_alignment_rejects_invalid_duration_or_speed(field, value):
    args = [10, 1, 1]
    args[field] = value
    with pytest.raises(ValueError):
        timeline_alignment([], *args)


def test_timeline_requires_matching_clips():
    with pytest.raises(ValueError):
        build_timeline([Segment(0, 1, "A")], [])


@pytest.mark.parametrize("kwargs", [{"channels": 2}, {"rate": 16000}, {"width": 1}])
def test_timeline_requires_normalized_pcm(tmp_path, kwargs):
    clip = pcm_clip(tmp_path, "bad.wav", 10, **kwargs)
    with pytest.raises(ValueError):
        build_timeline([Segment(0, 1, "A")], [clip])


def test_timeline_rejects_empty_pcm(tmp_path):
    clip = pcm_clip(tmp_path, "empty.wav", 0)
    with pytest.raises(ValueError):
        build_timeline([Segment(0, 1, "A")], [clip])


def test_timeline_rejects_truncated_pcm(tmp_path):
    clip = pcm_clip(tmp_path, "short.wav", 100)
    clip.write_bytes(clip.read_bytes()[:-2])
    with pytest.raises(ValueError):
        build_timeline([Segment(0, 1, "A")], [clip])


@pytest.mark.parametrize("start,end", [(-1, 1), (0, 0), (float("nan"), 1), (0, float("inf"))])
def test_timeline_rejects_invalid_source_bounds(tmp_path, start, end):
    clip = pcm_clip(tmp_path, "a.wav", 1)
    with pytest.raises(ProviderError):
        build_timeline([Segment(start, end, "A")], [clip])


@pytest.mark.parametrize("start,end", [(-1, 1), (0, 0), (1, 0.5), (float("nan"), 1), (0, float("inf")), (True, 2)])
def test_alignment_rejects_invalid_bounds(start, end):
    with pytest.raises(ValueError):
        timeline_alignment([{"actual_start_seconds": start, "actual_end_seconds": end}], 10, 1, 1)


def test_alignment_rejects_overlapping_rows():
    with pytest.raises(ValueError):
        timeline_alignment([
            {"actual_start_seconds": 0, "actual_end_seconds": 2},
            {"actual_start_seconds": 1, "actual_end_seconds": 3},
        ], 10, 1, 1)


@pytest.mark.parametrize("args", [(1e308, 1e-308, 1), (1, 1e308, 1e308), (1e308, 1, 1e-308)])
def test_alignment_rejects_nonfinite_derived_values(args):
    with pytest.raises(ValueError):
        timeline_alignment([], *args)
