import json
from subshort.models import Job
from subshort.providers import Segment, write_srt
from subshort.queue import JobQueue


def test_write_srt(tmp_path):
    path = tmp_path / "out.srt"
    write_srt([Segment(0, 1.25, "Xin chào")], path)
    text = path.read_text(encoding="utf-8-sig")
    assert "00:00:00,000 --> 00:00:01,250" in text
    assert "Xin chào" in text


def test_queue_roundtrip(tmp_path):
    path = tmp_path / "queue.json"
    queue = JobQueue(path)
    queue.add(Job("abc", "clip.mp4", "vi"))
    loaded = JobQueue(path)
    assert loaded.jobs[0].id == "abc"
    assert json.loads(path.read_text(encoding="utf-8"))[0]["status"] == "queued"
