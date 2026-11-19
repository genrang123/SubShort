from pathlib import Path
import json
import wave

import pytest

from subshort.diarization import SpeakerTurn, align_segments, review_fingerprint, speaker_windows
from subshort.media import MediaAdapter
from subshort.providers import Segment, build_timeline, timeline_alignment


def _wav(path, frames=2400, value=1000):
    with wave.open(str(path), "wb") as stream:
        stream.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        stream.writeframes((value.to_bytes(2, "little", signed=True)) * frames)


def test_speaker_lanes_allow_overlap_and_mixer(tmp_path):
    first, second, output = (tmp_path / name for name in ("a.wav", "b.wav", "mix.wav"))
    _wav(first, 24000, 100)
    _wav(second, 24000, 200)
    segments = [Segment(0, 1, "A", "1", "speaker-1"), Segment(0.4, 1.4, "B", "2", "speaker-2")]
    windows = [(0, 24000), (9600, 33600)]
    timeline = build_timeline(segments, [first, second], 1, windows=windows)
    assert timeline[1]["actual_start_seconds"] < timeline[0]["actual_end_seconds"]
    alignment = timeline_alignment(timeline, 2, 1, 1, allow_overlap=True)
    MediaAdapter("ffmpeg", "ffprobe").mix_timeline_audio([first, second], [0, 0.4], output, alignment["target_duration_seconds"])
    import struct
    with wave.open(str(output), "rb") as stream:
        samples = struct.unpack(f"<{stream.getnframes()}h", stream.readframes(stream.getnframes()))
    assert samples[2400] == 100
    assert samples[12000] == 300
    assert samples[28800] == 200

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
