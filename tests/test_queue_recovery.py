import json
import threading

import pytest

from subshort.models import Job
from subshort.queue import JobQueue, QueueError


def job(job_id="j1", status="queued", **kwargs):
    return Job(job_id, "clip.mp4", "vi", status=status, **kwargs)


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def leftovers(path):
    return [p.name for p in path.parent.iterdir() if p.suffix == ".tmp"]


def test_missing_file_loads_empty(tmp_path):
    queue = JobQueue(tmp_path / "queue.json")
    assert queue.jobs == [] and queue.load_error == ""
    assert queue.pending() == []


def test_replace_failure_preserves_bytes_and_rolls_back(tmp_path, monkeypatch):
    path = tmp_path / "queue.json"
    queue = JobQueue(path)
    queue.add(job("a", status="failed", output="old.mp4", error="old"))
    before = path.read_bytes()

    def boom(src, dst):
        raise OSError("disk full")
    monkeypatch.setattr("subshort.queue.os.replace", boom)

    with pytest.raises(QueueError):
        queue.add(job("b"))
    assert [j.id for j in queue.jobs] == ["a"]
    assert path.read_bytes() == before

    with pytest.raises(QueueError):
        queue.update(job("a", status="done"))
    assert queue.jobs[0].status == "failed"
    assert path.read_bytes() == before

    with pytest.raises(QueueError):
        queue.claim(job("a"))
    assert queue.jobs[0].status == "failed"

    with pytest.raises(QueueError):
        queue.retry("a")
    assert queue.jobs[0].status == "failed" and queue.jobs[0].output == "old.mp4"

    assert leftovers(path) == []
    assert JobQueue(path).jobs[0].status == "failed"


def test_fsync_failure_preserves_bytes_and_cleans_temp(tmp_path, monkeypatch):
    path = tmp_path / "queue.json"
    queue = JobQueue(path)
    queue.add(job("a"))
    before = path.read_bytes()

    def boom(fd):
        raise OSError("fsync failed")
    monkeypatch.setattr("subshort.queue.os.fsync", boom)
    with pytest.raises(QueueError):
        queue.add(job("b"))
    assert path.read_bytes() == before
    assert leftovers(path) == []
    assert [j.id for j in queue.jobs] == ["a"]


def test_malformed_history_is_kept_and_writes_are_disabled(tmp_path):
    path = tmp_path / "queue.json"
    path.write_text("{not json", encoding="utf-8")
    before = path.read_bytes()

    queue = JobQueue(path)
    assert queue.load_error and "hỏng" in queue.load_error
    assert queue.jobs == []

    for call in (lambda: queue.add(job()), lambda: queue.update(job()),
                 lambda: queue.claim(job()), lambda: queue.retry("j1"), queue.save):
        with pytest.raises(QueueError):
            call()
    assert path.read_bytes() == before


@pytest.mark.parametrize("payload", [
    {"id": "a"},
    [{"id": "a", "source": "x", "target_language": "vi", "bogus": 1}],
    [{"id": "a", "source": "x", "target_language": "vi", "status": "paused"}],
    [{"id": "a", "source": "x", "target_language": "vi"}, {"id": "a", "source": "y", "target_language": "vi"}],
    [{"id": "a", "source": "x", "target_language": "vi", "metadata": []}],
    [{"id": "a", "source": "x", "target_language": "vi", "progress": "half"}],
    [{"id": "", "source": "x", "target_language": "vi"}],
    [{"id": "a", "source": "x", "target_language": 5}],
])
def test_invalid_history_is_preserved_and_read_only(tmp_path, payload):
    path = tmp_path / "queue.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    before = path.read_bytes()

    queue = JobQueue(path)
    assert queue.load_error
    assert queue.jobs == []
    with pytest.raises(QueueError):
        queue.add(job())
    assert path.read_bytes() == before


def test_recovery_marks_interrupted_and_clears_missing_output(tmp_path):
    path = tmp_path / "queue.json"
    output = tmp_path / "done.mp4"
    output.write_text("video", encoding="utf-8")
    path.write_text(json.dumps([
        {"id": "run", "source": "a.mp4", "target_language": "vi", "status": "running",
         "stage": "render", "progress": 0.6},
        {"id": "gone", "source": "b.mp4", "target_language": "vi", "status": "done",
         "output": str(tmp_path / "missing.mp4"), "stage": "done", "progress": 1.0},
        {"id": "kept", "source": "c.mp4", "target_language": "vi", "status": "done",
         "output": str(output), "stage": "done", "progress": 1.0},
        {"id": "dir", "source": "d.mp4", "target_language": "vi", "status": "done",
         "output": str(tmp_path), "stage": "done"},
    ]), encoding="utf-8")

    queue = JobQueue(path)
    assert queue.load_error == ""
    by_id = {j.id: j for j in queue.jobs}
    assert by_id["run"].status == "failed" and by_id["run"].stage == "interrupted"
    assert by_id["run"].error
    assert by_id["gone"].status == "failed" and by_id["gone"].output == ""
    assert by_id["dir"].status == "failed" and by_id["dir"].output == ""
    assert by_id["kept"].status == "done" and by_id["kept"].output == str(output)

    persisted = {j["id"]: j for j in read(path)}
    assert persisted["run"]["status"] == "failed" and persisted["run"]["stage"] == "interrupted"
    assert persisted["gone"]["status"] == "failed" and persisted["gone"]["output"] == ""
    assert persisted["kept"]["status"] == "done"
    assert JobQueue(path).jobs[0].status == "failed"


def test_clean_reload_does_not_rewrite(tmp_path):
    path = tmp_path / "queue.json"
    queue = JobQueue(path)
    queue.add(job("a"))
    before = path.stat().st_mtime_ns
    JobQueue(path)
    assert path.stat().st_mtime_ns == before


def test_claim_sets_running_only_and_is_single_winner(tmp_path):
    path = tmp_path / "queue.json"
    queue = JobQueue(path)
    queue.add(job("a", status="queued", output="stale.mp4", error="old", stage="asr", progress=0.5))
    detached = queue.pending()[0]

    assert queue.claim(detached) is True
    assert queue.jobs[0].status == "running"
    assert queue.jobs[0].output == "stale.mp4" and queue.jobs[0].stage == "asr"
    assert read(path)[0]["status"] == "running"
    assert queue.claim(detached) is False

    results = []
    queue.add(job("race", status="queued"))
    target = queue.pending()[0]
    barrier = threading.Barrier(3)

    def attempt():
        barrier.wait()
        results.append(queue.claim(target))

    threads = [threading.Thread(target=attempt) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(results) == 1
    assert len([j for j in queue.jobs if j.status == "running"]) == 2


def test_cancelled_claimable_only_when_claiming_and_retry_clears_stale(tmp_path):
    path = tmp_path / "queue.json"
    queue = JobQueue(path)
    queue.add(job("c", status="cancelled", output="x.mp4", error="Đã hủy", stage="tts", progress=0.4,
                    metadata={"stages": ["tts"]}))
    queue.add(job("f", status="failed", output="y.mp4", error="boom", stage="render", progress=0.9,
                    metadata={"stages": ["render"]}))
    queue.add(job("q", status="queued"))

    assert [j.id for j in queue.pending()] == ["f", "q"]
    assert queue.retry("q") is False
    assert queue.retry("missing") is False

    assert queue.retry("f") is True
    stored = next(j for j in queue.jobs if j.id == "f")
    assert (stored.status, stored.output, stored.error, stored.stage, stored.progress, stored.metadata) == \
        ("queued", "", "", "", 0.0, {})
    assert read(path)[1]["status"] == "queued"

    detached = [j for j in queue.pending() if j.id == "c"]
    assert detached == []  # cancelled is not pending; explicit retry required
    assert queue.claim(job("c")) is True
    assert next(j for j in queue.jobs if j.id == "c").status == "running"
    assert queue.retry("c") is False  # retry only re-queues failed/cancelled


def test_snapshot_and_pending_are_deep_copies(tmp_path):
    queue = JobQueue(tmp_path / "queue.json")
    queue.add(job("a", metadata={"nested": {"x": [1]}}, config={"final_speed": 1.0}))
    queue.add(job("b", status="done"))
    queue.add(job("c", status="cancelled"))

    snap = queue.snapshot()
    assert [j.id for j in snap] == ["a", "b", "c"]
    pending = queue.pending()
    assert [j.id for j in pending] == ["a"]
    snap[0].status = "done"
    snap[0].metadata["nested"]["x"].append(2)
    snap[0].config["final_speed"] = 9.9
    pending[0].metadata.clear()
    assert queue.jobs[0].status == "queued"
    assert queue.jobs[0].config == {"final_speed": 1.0}
    assert queue.snapshot()[0].metadata == {"nested": {"x": [1]}}
    assert snap[0] is not queue.jobs[0]


def test_add_and_update_store_copies_and_update_is_strict(tmp_path):
    path = tmp_path / "queue.json"
    queue = JobQueue(path)
    original = job("a", config={"final_speed": 1.0})
    queue.add(original)
    original.status, original.config["final_speed"] = "done", 9.9
    assert queue.jobs[0].status == "queued" and queue.jobs[0].config["final_speed"] == 1.0

    detached = queue.pending()[0]
    detached.stage, detached.config["final_speed"] = "asr", 1.25
    queue.update(detached)
    detached.stage = "render"
    assert queue.jobs[0].stage == "asr" and queue.jobs[0].config["final_speed"] == 1.25

    before = path.read_bytes()
    with pytest.raises(KeyError):
        queue.update(job("missing"))
    with pytest.raises(ValueError):
        queue.add(job("a"))
    assert path.read_bytes() == before
    assert [j.id for j in queue.jobs] == ["a"]


def test_concurrent_mutations_keep_file_consistent(tmp_path):
    path = tmp_path / "queue.json"
    queue = JobQueue(path)
    errors = []
    barrier = threading.Barrier(6)

    def produce(prefix):
        try:
            barrier.wait()
            for index in range(5):
                queue.add(job(f"{prefix}-{index}"))
                queue.pending()
        except Exception as exc:  # pragma: no cover - surfaced by assertion
            errors.append(exc)

    def consume():
        try:
            barrier.wait()
            for _ in range(50):
                queue.snapshot()
                queue.pending()
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=produce, args=(f"p{n}",)) for n in range(4)]
    threads += [threading.Thread(target=consume) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    ids = [j["id"] for j in read(path)]
    assert len(ids) == 20 and len(set(ids)) == 20
    assert set(ids) == {j.id for j in queue.jobs}
    assert leftovers(path) == []
    assert JobQueue(path).load_error == ""
