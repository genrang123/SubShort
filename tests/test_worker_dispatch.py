import ast
import json
import os
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace
import wave

import pytest

from subshort import cli, diarization, separation, tts
from subshort.asr_worker import ExternalASRSession
from subshort.providers import FatalProviderError


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


def _spec_worker_modules():
    spec_path = Path(__file__).resolve().parents[1] / "packaging" / "subshort.spec"
    tree = ast.parse(spec_path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "WORKER_MODULES" for t in node.targets):
            return tuple(ast.literal_eval(node.value))
    raise AssertionError("WORKER_MODULES not found in packaging/subshort.spec")


def _write_wav(path: Path) -> Path:
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b"\x00" * 1600)
    return path




def test_frozen_external_workers_import_bundled_worker_runtime(tmp_path, monkeypatch):
    src_pkg = Path(__file__).resolve().parents[1] / "src" / "subshort"
    bundle = tmp_path / "bundle"
    runtime_pkg = bundle / "worker_runtime" / "subshort"
    runtime_pkg.mkdir(parents=True)
    for name in _spec_worker_modules():
        shutil.copy2(src_pkg / f"{name}.py", runtime_pkg / f"{name}.py")

    stale_pkg = tmp_path / "stale" / "subshort"
    stale_pkg.mkdir(parents=True)
    (stale_pkg / "__init__.py").write_text("", encoding="utf-8")
    for name in ("asr_worker", "diarization_worker", "tts_worker", "separation"):
        (stale_pkg / f"{name}.py").write_text('raise RuntimeError("stale_worker_imported")\n', encoding="utf-8")

    sdk = tmp_path / "sdk"
    (sdk / "pyannote" / "audio").mkdir(parents=True)
    (sdk / "audio_separator").mkdir(parents=True)
    (sdk / "requests").mkdir(parents=True)
    (sdk / "onnxruntime-0.0.0.dist-info").mkdir(parents=True)
    (sdk / "onnxruntime-0.0.0.dist-info" / "METADATA").write_text("Metadata-Version: 2.1\nName: onnxruntime\nVersion: 0.0.0\n", encoding="utf-8")
    (sdk / "requests" / "__init__.py").write_text(
        "class _Session:\n"
        "    def request(self, *a, **kw): return None\n"
        "class sessions:\n"
        "    Session = _Session\n",
        encoding="utf-8",
    )
    (sdk / "torch.py").write_text(
        "class _T:\n"
        "    def to(self, *a, **kw): return self\n"
        "    def __truediv__(self, other): return self\n"
        "    def unsqueeze(self, dim): return self\n"
        "int16 = 'int16'\n"
        "float32 = 'float32'\n"
        "def frombuffer(buf, dtype=None): return _T()\n",
        encoding="utf-8",
    )
    (sdk / "pyannote" / "__init__.py").write_text("", encoding="utf-8")
    (sdk / "pyannote" / "audio" / "__init__.py").write_text(
        "class _Turn:\n"
        "    def __init__(self, s, e): self.start, self.end = s, e\n"
        "class _Dia:\n"
        "    def itertracks(self, yield_label=True): return [(_Turn(0.0, 0.5), None, 'S0')]\n"
        "class Pipeline:\n"
        "    @classmethod\n"
        "    def from_pretrained(cls, path): return cls()\n"
        "    def __call__(self, inp): return _Dia()\n",
        encoding="utf-8",
    )
    (sdk / "audio_separator" / "__init__.py").write_text("", encoding="utf-8")
    (sdk / "audio_separator" / "separator.py").write_text(
        "from pathlib import Path\n"
        "class Separator:\n"
        "    def __init__(self, output_dir, **kw):\n"
        "        self.output_dir = Path(output_dir)\n"
        "        self.onnx_execution_provider = ['CPUExecutionProvider']\n"
        "        self.torch_device = 'cpu'\n"
        "    def load_model(self, model_filename): pass\n"
        "    def separate(self, source, custom_output_names=None):\n"
        "        out = self.output_dir / 'background.wav'\n"
        "        out.write_bytes(b'inst')\n"
        "        return [str(out)]\n",
        encoding="utf-8",
    )
    (sdk / "vieneu.py").write_text(
        "class Vieneu:\n"
        "    def __init__(self, **kw): self.sample_rate = 24000\n"
        "    def list_preset_voices(self): return [('Voice', 'v0')]\n"
        "    def infer(self, text, voice=None): return [0.0, 0.0]\n",
        encoding="utf-8",
    )

    wav = _write_wav(tmp_path / "input.wav")
    diar_model = tmp_path / "diar_model"
    diar_model.mkdir()
    sep_model = tmp_path / "sep_model"
    sep_model.mkdir()
    (sep_model / separation.MODEL_FILENAME).write_bytes(b"onnx")
    for name in separation.CACHE_FILES[1:]:
        (sep_model / name).write_text("{}", encoding="utf-8")
    tts_model = tmp_path / "tts_model"
    (tts_model / "onnx_update").mkdir(parents=True)
    hf_cache = tmp_path / "hf_cache"
    hf_cache.mkdir()

    external_python = sys.executable
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setattr(sys, "executable", str(bundle / "SubShort.exe"))
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(stale_pkg.parent), str(sdk)]))

    turns = diarization.DiarizationSession(external_python, diar_model).diarize(wav)
    assert [(t.start, t.end, t.speaker_id) for t in turns] == [(0.0, 0.5, "S0")]

    bg = separation.separate_background(wav, tmp_path / "sep_work", sep_model, python_executable=external_python)
    assert bg.read_bytes() == b"inst"

    out_tts = tmp_path / "speech.wav"
    with tts.TtsSession(external_python, provider="vieneu", model_path=tts_model, hf_cache=hf_cache) as session:
        assert session.voices() == [{"label": "Voice", "id": "v0"}]
        assert session.synthesize("Xin chào", out_tts) == out_tts
    with wave.open(str(out_tts), "rb") as stream:
        assert (stream.getframerate(), stream.getnframes()) == (24000, 2)

    with pytest.raises(FatalProviderError, match="ASR local model path not found"):
        ExternalASRSession(
            external_python, "qwen", str(tmp_path / "missing_asr_model"), "cpu",
            forced_aligner_path=None, vad_model_path=None, firered_type="aed", diagnostic=None,
        ).transcribe(wav)
