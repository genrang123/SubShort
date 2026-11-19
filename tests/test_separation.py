import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from subshort import separation
from subshort.execution import Cancelled


def cache(tmp_path):
    folder = tmp_path / "cache"
    folder.mkdir()
    (folder / separation.MODEL_FILENAME).write_bytes(b"local model")
    for name in separation.CACHE_FILES[1:]:
        (folder / name).write_text("{}", encoding="utf-8")
    return folder


def source(tmp_path):
    path = tmp_path / "source.wav"
    path.write_bytes(b"source")
    return path


@pytest.mark.parametrize("missing", separation.CACHE_FILES)
def test_missing_assets_prevent_worker_start(tmp_path, monkeypatch, missing):
    folder = cache(tmp_path)
    (folder / missing).unlink()
    monkeypatch.setattr(separation, "run_process", lambda *a, **k: pytest.fail("must not launch loader"))
    with pytest.raises(RuntimeError, match="downloads are disabled"):
        separation.separate_background(source(tmp_path), tmp_path / "work", folder)


def test_invalid_metadata_prevents_worker_start(tmp_path, monkeypatch):
    folder = cache(tmp_path)
    (folder / "mdx_model_data.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(separation, "run_process", lambda *a, **k: pytest.fail("must not launch loader"))
    with pytest.raises(RuntimeError, match="invalid mdx_model_data"):
        separation.separate_background(source(tmp_path), tmp_path / "work", folder)


@pytest.mark.parametrize("result", [[], ["vocals.wav"], ["background.wav", "vocals.wav"], ["../background.wav"], "background.wav"])
def test_only_requested_instrumental_is_accepted(tmp_path, result):
    output = tmp_path / "owned"
    output.mkdir()
    (output / "background.wav").write_bytes(b"instrumental")
    (tmp_path / "background.wav").write_bytes(b"outside")
    with pytest.raises(RuntimeError):
        separation._instrumental_result(result, output)


def test_empty_instrumental_is_rejected(tmp_path):
    (tmp_path / "background.wav").touch()
    with pytest.raises(RuntimeError, match="nonempty"):
        separation._instrumental_result(["background.wav"], tmp_path)


def test_parent_uses_protocol_and_selected_python(tmp_path, monkeypatch):
    folder = cache(tmp_path)
    audio = source(tmp_path)

    def worker(args, **kwargs):
        assert args == ["separator-python", "-m", "subshort.separation", "--worker"]
        assert kwargs["env"]["AUDIO_SEPARATOR_MODEL_DIR"] == str(folder.resolve())
        assert kwargs["env"]["CUDA_VISIBLE_DEVICES"] == "-1"
        request = json.loads(kwargs["input"])
        assert request["source"] == str(audio.resolve())
        result = Path(request["output_dir"]) / "background.wav"
        result.write_bytes(b"instrumental")
        return subprocess.CompletedProcess(args, 0, json.dumps({"files": [str(result)]}), "SDK diagnostics")

    monkeypatch.setattr(separation, "run_process", worker)
    result = separation.separate_background(audio, tmp_path / "work", folder, "separator-python")
    assert result.read_bytes() == b"instrumental"
    assert audio.read_bytes() == b"source"


@pytest.mark.parametrize("failure", [Cancelled("cancelled"), RuntimeError("worker failed")])
def test_failure_removes_owned_output(tmp_path, monkeypatch, failure):
    def worker(*args, **kwargs):
        raise failure

    monkeypatch.setattr(separation, "run_process", worker)
    folder = cache(tmp_path)
    with pytest.raises(type(failure)):
        separation.separate_background(source(tmp_path), tmp_path / "work", folder)
    assert list((tmp_path / "work").iterdir()) == []


def test_worker_failure_diagnostics_are_preserved(tmp_path, monkeypatch):
    monkeypatch.setattr(separation, "run_process", lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "offline: missing model"))
    with pytest.raises(RuntimeError, match="offline: missing model"):
        separation.separate_background(source(tmp_path), tmp_path / "work", cache(tmp_path))


def fake_environment(tmp_path, monkeypatch, body):
    modules = tmp_path / "modules"
    package = modules / "audio_separator"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "separator.py").write_text(body, encoding="utf-8")
    (modules / "requests.py").write_text(
        "class Session:\n    def request(self, *a, **kw):\n        raise AssertionError('unblocked network')\n"
        "class sessions:\n    Session = Session\n", encoding="utf-8",
    )
    dist = modules / "onnxruntime-1.17.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_text("Metadata-Version: 2.1\nName: onnxruntime\nVersion: 1.17\n", encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(modules))


def test_real_worker_protocol_with_fake_sdk(tmp_path, monkeypatch):
    fake_environment(tmp_path, monkeypatch, '''
from pathlib import Path
import requests
class Separator:
    def __init__(self, **kwargs):
        print("SDK stdout must not corrupt protocol")
        assert kwargs["output_single_stem"] == "Instrumental"
        assert kwargs["output_format"] == "WAV"
        self.output = Path(kwargs["output_dir"])
        self.onnx_execution_provider = ["CPUExecutionProvider"]
        self.torch_device = "cpu"
        try:
            requests.sessions.Session().request("GET", "https://must-not-run.invalid")
        except RuntimeError as exc:
            assert "network requests are disabled" in str(exc)
        else:
            raise AssertionError("network was not blocked")
    def load_model(self, model_filename):
        assert model_filename == "UVR-MDX-NET-Inst_HQ_3.onnx"
    def separate(self, audio_file_path, custom_output_names):
        assert custom_output_names == {"Instrumental": "background"}
        (self.output / "background.wav").write_bytes(b"instrumental")
        return ["background.wav"]
''')
    folder = cache(tmp_path)
    assert separation._check_cache(folder) == hashlib.sha256(b"local model").hexdigest()
    result = separation.separate_background(source(tmp_path), tmp_path / "work", folder, sys.executable)
    assert result.read_bytes() == b"instrumental"


def test_worker_rejects_acceleration_before_model_load(tmp_path, monkeypatch):
    fake_environment(tmp_path, monkeypatch, '''
class Separator:
    def __init__(self, **kwargs):
        self.onnx_execution_provider = ["CUDAExecutionProvider"]
        self.torch_device = "cuda"
    def load_model(self, **kwargs):
        raise AssertionError("must reject acceleration before loading")
''')
    with pytest.raises(RuntimeError, match="CPU-only separator environment"):
        separation.separate_background(source(tmp_path), tmp_path / "work", cache(tmp_path))
