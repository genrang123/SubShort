import json
import sys
from types import SimpleNamespace

from subshort import cli, separation


def test_tts_worker_dispatch_skips_application_setup(monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "subshort.tts_worker", SimpleNamespace(main=lambda: calls.append("tts")))
    monkeypatch.setattr(sys, "argv", ["SubShort.exe", "--tts-worker"])
    monkeypatch.setattr(cli, "state_root", lambda *_: (_ for _ in ()).throw(AssertionError("worker must not initialize state")))

    cli.main()

    assert calls == ["tts"]


def test_separator_worker_dispatch_skips_application_setup(monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "subshort.separation", SimpleNamespace(main=lambda: calls.append("separator")))
    monkeypatch.setattr(sys, "argv", ["SubShort.exe", "--separator-worker"])
    monkeypatch.setattr(cli, "state_root", lambda *_: (_ for _ in ()).throw(AssertionError("worker must not initialize state")))

    cli.main()

    assert calls == ["separator"]


def test_frozen_separation_uses_executable_worker_dispatch(monkeypatch, tmp_path):
    source = tmp_path / "source.wav"
    source.write_bytes(b"source")
    work = tmp_path / "work"
    model = tmp_path / "model"
    model.mkdir()
    executable = tmp_path / "SubShort.exe"
    calls = []

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))
    monkeypatch.setattr(separation, "_check_cache", lambda directory: "model-hash")
    monkeypatch.setattr(
        separation,
        "run_process",
        lambda command, **kwargs: calls.append((command, kwargs)) or SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"files": [str(work / "background.wav")]}),
            stderr="",
        ),
    )
    monkeypatch.setattr(separation, "_instrumental_result", lambda files, output_dir: output_dir / "background.wav")

    separation.separate_background(source, work, model, executable)

    assert calls[0][0] == [str(executable), "--separator-worker"]
