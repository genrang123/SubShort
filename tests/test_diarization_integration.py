import struct
import wave

import pytest

from subshort.diarization import SpeakerTurn, align_segments, review_fingerprint
from subshort.media import MediaAdapter
from subshort.providers import Segment, build_timeline, timeline_alignment


def _wav(path, frames=2400, value=1000):
    with wave.open(str(path), "wb") as stream:
        stream.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        stream.writeframes((value.to_bytes(2, "little", signed=True)) * frames)


def test_speaker_lanes_delay_only_same_speaker_and_mix_overlaps(tmp_path):
    first, second, third, output = (tmp_path / name for name in ("a.wav", "b.wav", "c.wav", "mix.wav"))
    _wav(first, 24000, 100)
    _wav(second, 24000, 200)
    _wav(third, 24000, 400)
    segments = [Segment(0, .3, "A", "1", "speaker-1"), Segment(.4, .6, "B", "2", "speaker-2"),
                Segment(.8, 1, "C", "3", "speaker-1")]
    timeline = build_timeline(segments, [first, second, third], source_duration=1.5, multi_speaker=True)
    starts = [row["actual_start_seconds"] for row in timeline]
    assert starts == [0, .4, 1]
    assert [row["delay_seconds"] for row in timeline] == [0, 0, .2]
    alignment = timeline_alignment(timeline, 1.5, 1, 1, allow_overlap=True)
    assert alignment["target_duration_seconds"] == 2
    MediaAdapter().mix_timeline_audio([first, second, third], starts, output, alignment["target_duration_seconds"])
    with wave.open(str(output), "rb") as stream:
        samples = struct.unpack(f"<{stream.getnframes()}h", stream.readframes(stream.getnframes()))
    assert samples[2400] == 100
    assert samples[12000] == 300
    assert samples[28800] == 600
    assert samples[36000] == 400
    assert len(samples) == 48000


def test_speaker_lanes_require_a_speaker_id(tmp_path):
    clip = tmp_path / "a.wav"
    _wav(clip)
    with pytest.raises(ValueError, match="thiếu người nói"):
        build_timeline([Segment(0, 1, "A")], [clip], multi_speaker=True)


def test_review_fingerprint_changes_when_transcript_changes():
    turns = [SpeakerTurn(0, 1, "speaker-1", "resolved")]
    base = [Segment(0, 1, "hello", "1", "speaker-1")]
    changed = [Segment(0, 1, "edited", "1", "speaker-1")]
    assert review_fingerprint("source", base, turns, ["hello"]) != review_fingerprint("source", changed, turns, ["edited"])


def test_align_resplit_requires_single_speaker_child():
    source = [Segment(0, 2, "two speakers", "1")]
    turns = [SpeakerTurn(0, 1, "speaker-1"), SpeakerTurn(1, 2, "speaker-2")]
    children = lambda segment: [Segment(0, 1, "one", "1"), Segment(1, 2, "two", "1")]
    result = align_segments(source, turns, resplit=children)
    assert [item.speaker_id for item in result] == ["speaker-1", "speaker-2"]

def test_align_blocks_uncovered_segment():
    with pytest.raises(Exception, match="không xác định người nói"):
        align_segments([Segment(2, 3, "uncovered", "1")], [SpeakerTurn(0, 1, "speaker-1")])


def test_align_blocks_ambiguous_cue_without_resplit():
    turns = [SpeakerTurn(0, 1, "speaker-1"), SpeakerTurn(0.5, 2, "speaker-2")]
    with pytest.raises(Exception, match="nhiều người nói"):
        align_segments([Segment(0, 2, "ambiguous", "1")], turns)
