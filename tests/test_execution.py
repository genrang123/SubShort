import asyncio
import os
import subprocess
import sys
import threading
import time
import types

import pytest

from subshort import providers
from subshort.execution import Cancelled, Execution, check_cancelled, run_process
from subshort.media import MediaAdapter
from subshort.providers import EdgeTTS, FatalProviderError, OpenAITranslator, ProviderError

SLEEPER = "import time; time.sleep(30)"
CHILD_HEARTBEAT = """
import pathlib, sys, time
p = pathlib.Path(sys.argv[1])
while True:
    with p.open("a") as handle:
        handle.write(".")
    time.sleep(0.05)
"""
DESCENDANT_SPAWNER = """
import pathlib, subprocess, sys, time
pid_file = pathlib.Path(sys.argv[4])
child = subprocess.Popen([sys.executable, "-c", sys.argv[2], sys.argv[3]])
pid_file.write_text(str(child.pid))
heartbeat = pathlib.Path(sys.argv[1])
while True:
    with heartbeat.open("a") as handle:
        handle.write(".")
    time.sleep(0.05)
"""


def _wait_for(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def _pid_alive(pid):
    if os.name == "nt":
        output = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in output
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def test_check_cancelled_is_inert_without_active_execution():
    check_cancelled()


def test_activate_scopes_cancellation_and_restores_context():
    execution = Execution()
    with execution.activate():
        check_cancelled()
    execution.cancel()
    check_cancelled()  # never leaks outside its own scope
    with execution.activate(), pytest.raises(Cancelled):
        check_cancelled()


def test_run_process_behaves_like_subprocess_run_outside_execution():
    result = run_process([sys.executable, "-c", "print('hi')"], capture_output=True, text=True)
    assert result.stdout.strip() == "hi" and result.returncode == 0
    with pytest.raises(subprocess.CalledProcessError):
        run_process([sys.executable, "-c", "raise SystemExit(3)"], capture_output=True, text=True, check=True)


def test_run_process_pipes_input_and_reports_returncode():
    with Execution().activate():
        result = run_process([sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read())"],
                             input="ping", capture_output=True, text=True)
        assert result.stdout == "ping" and result.returncode == 0
        failed = run_process([sys.executable, "-c", "raise SystemExit(4)"], capture_output=True, text=True)
        assert failed.returncode == 4


def test_run_process_honours_timeout():
    with Execution().activate(), pytest.raises(subprocess.TimeoutExpired):
        run_process([sys.executable, "-c", SLEEPER], capture_output=True, text=True, timeout=0.2)


def test_cancellation_kills_child_and_descendants(tmp_path):
    execution = Execution()
    parent_beats, child_beats, pid_file = tmp_path / "parent", tmp_path / "child", tmp_path / "child.pid"
    outcome = {}

    def run():
        try:
            with execution.activate():
                run_process([sys.executable, "-c", DESCENDANT_SPAWNER, str(parent_beats),
                             CHILD_HEARTBEAT, str(child_beats), str(pid_file)], capture_output=True, text=True)
        except BaseException as exc:
            outcome["error"] = exc

    thread = threading.Thread(target=run)
    thread.start()
    assert _wait_for(lambda: parent_beats.exists() and child_beats.exists() and pid_file.exists())
    child_pid = int(pid_file.read_text())
    assert _pid_alive(child_pid)
    execution.cancel()
    thread.join(15)
    assert not thread.is_alive()
    assert isinstance(outcome.get("error"), Cancelled)
    assert _wait_for(lambda: not _pid_alive(child_pid), timeout=5)
    parent_size, child_size = parent_beats.stat().st_size, child_beats.stat().st_size
    time.sleep(0.5)
    # Neither the child nor its descendant keeps writing: nothing was orphaned.
    assert parent_beats.stat().st_size == parent_size
    assert child_beats.stat().st_size == child_size


def test_cancellation_does_not_kill_unrelated_processes():
    execution = Execution()
    bystander = subprocess.Popen([sys.executable, "-c", SLEEPER])
    outcome = {}

    def run():
        try:
            with execution.activate():
                run_process([sys.executable, "-c", SLEEPER])
        except BaseException as exc:
            outcome["error"] = exc

    thread = threading.Thread(target=run)
    thread.start()
    time.sleep(0.3)
    execution.cancel()
    thread.join(15)
    try:
        assert isinstance(outcome.get("error"), Cancelled)
        assert bystander.poll() is None
    finally:
        bystander.kill()
        bystander.wait(10)


def test_media_adapter_lets_cancellation_through():
    execution = Execution()
    outcome = {}

    def run():
        try:
            with execution.activate():
                MediaAdapter(sys.executable).run([sys.executable, "-c", SLEEPER])
        except BaseException as exc:
            outcome["error"] = exc

    thread = threading.Thread(target=run)
    thread.start()
    time.sleep(0.3)
    execution.cancel()
    thread.join(15)
    assert isinstance(outcome.get("error"), Cancelled)


def test_edge_tts_cancellation_stops_awaiting(monkeypatch, tmp_path):
    class Communicate:
        def __init__(self, text, voice):
            pass

        async def save(self, path):
            await asyncio.sleep(3600)

    monkeypatch.setitem(sys.modules, "edge_tts", types.SimpleNamespace(Communicate=Communicate))
    execution = Execution()
    timer = threading.Timer(0.3, execution.cancel)
    timer.start()

    async def main():
        await EdgeTTS().synthesize("xin chào", tmp_path / "out.mp3")

    try:
        with execution.activate(), pytest.raises(Cancelled):
            asyncio.run(main())
    finally:
        timer.cancel()


def test_edge_tts_missing_dependency_is_fatal(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "edge_tts", None)
    with pytest.raises(FatalProviderError):
        asyncio.run(EdgeTTS().synthesize("x", tmp_path / "out.mp3"))




def test_openai_missing_key_is_fatal():
    with pytest.raises(FatalProviderError):
        OpenAITranslator("").translate([providers.Segment(0, 1, "a")], "vi")


def test_openai_key_never_reaches_child_argv(monkeypatch):
    calls = {}

    def fake_run_process(args, **kwargs):
        calls["args"], calls["input"] = args, kwargs["input"]
        return subprocess.CompletedProcess(args, 0, '{"ok": true, "body": "{}"}', "")

    monkeypatch.setattr(providers, "run_process", fake_run_process)
    with pytest.raises(ProviderError):
        OpenAITranslator("sk-secret").translate([providers.Segment(0, 1, "a")], "vi")
    assert "sk-secret" not in " ".join(calls["args"])
    assert "sk-secret" in calls["input"]
