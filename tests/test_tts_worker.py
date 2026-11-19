"""SDK fakes exercise the real subprocess protocol and SoundFile WAV output."""
import json
import os
import sys
import threading

import numpy as np
import pytest
import soundfile as sf

from subshort.execution import Cancelled, Execution
from subshort.tts import TtsSession


@pytest.fixture
def sdk(tmp_path, monkeypatch):
    packages = tmp_path / "packages"
    packages.mkdir()
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "onnx_update").mkdir()
    (snapshot / "audio_tokenizer").mkdir()
    cache = tmp_path / "hub"
    cache.mkdir()
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("TTS_CALL_LOG", str(log))
    monkeypatch.setenv("PYTHONPATH", str(packages) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    (packages / "vieneu.py").write_text('''
import os, json, time
import numpy as np
assert os.environ['HF_HUB_OFFLINE'] == '1'
assert os.environ['TRANSFORMERS_OFFLINE'] == '1'
print('sdk import chatter')
class Device:
    type = 'cuda'
class Engine:
    device = Device()
class Vieneu:
    sample_rate = 48000
    def __init__(self, **kwargs):
        assert kwargs['mode'] == 'v3turbo'
        assert os.path.isdir(kwargs['backbone_repo'])
        if kwargs['backend'] == 'onnx':
            assert kwargs['device'] == 'cpu'
            assert os.path.isdir(kwargs['onnx_dir'])
            self.backend = 'onnx'
            self.engine = type('Engine', (), {'device': type('Device', (), {'type': 'cpu'})()})()
        else:
            assert kwargs['backend'] == 'pytorch' and kwargs['device'] == 'cuda'
            self.backend = 'pytorch'
            self.engine = Engine()
        with open(os.environ['TTS_CALL_LOG'], 'a') as f: f.write(json.dumps(kwargs)+'\\n')
        if os.environ.get('TTS_MISSING'): raise FileNotFoundError('asset missing')
    def list_preset_voices(self): return [('Dynamic voice', 'dynamic-id'), ('Vietnamese voice', 'Hải Đăng')]
    def infer(self, text, **kwargs):
        print('inference chatter')
        os.write(1, b'native chatter\\n')
        os.write(2, b'x' * 200000)
        if text == 'sleep': time.sleep(20)
        assert kwargs.get('voice') in (None, 'dynamic-id', 'Hải Đăng')
        if kwargs.get('voice') == 'Hải Đăng': assert text == 'Xin chào Việt Nam'
        assert self.engine.device.type in ('cpu', 'cuda')
        return np.full(480, .125, dtype=np.float32)
''', encoding="utf-8")
    (packages / "torch.py").write_text("float32 = 'float32'\nfloat16 = 'float16'\n", encoding="utf-8")
    (packages / "omnivoice.py").write_text('''
import os, json
import numpy as np
assert os.environ['HF_HUB_OFFLINE'] == '1'
class OmniVoice:
    sampling_rate = 24000
    @classmethod
    def from_pretrained(cls, path, **kwargs):
        assert os.path.isdir(path)
        assert kwargs['load_asr'] is False and kwargs['local_files_only'] is True
        with open(os.environ['TTS_CALL_LOG'], 'a') as f: f.write(json.dumps(kwargs)+'\\n')
        if kwargs['device_map'] == 'cuda:0': raise RuntimeError('CUDA unavailable')
        assert kwargs['dtype'] == 'float32'
        return cls()
    def generate(self, **kwargs):
        assert kwargs['ref_audio'] and kwargs['ref_text']
        return [np.full(240, .25, dtype=np.float32)]
''', encoding="utf-8")
    return dict(python_executable=sys.executable, model_path=snapshot, hf_cache=cache), log


@pytest.mark.parametrize("device", ["cpu", "cuda"])
def test_vieneu_reuses_process_lists_dynamic_voices_and_writes_wav(sdk, tmp_path, device):
    args, log = sdk
    diagnostics = []
    with TtsSession(**args, provider="vieneu", device=device, diagnostic=diagnostics.append) as session:
        pid = session.process.pid
        assert session.voices() == [{"label": "Dynamic voice", "id": "dynamic-id"}, {"label": "Vietnamese voice", "id": "Hải Đăng"}]
        for voice in (None, "dynamic-id"):
            output = session.synthesize("hello", tmp_path / f"{voice}.wav", voice=voice)
            audio, rate = sf.read(output)
            assert rate == 48000 and len(audio) == 480
            assert np.allclose(audio, .125, atol=1 / 32768)
            assert session.process.pid == pid
    assert len(log.read_text().splitlines()) == 1
    call = json.loads(log.read_text().splitlines()[0])
    assert call["backend"] == ("onnx" if device == "cpu" else "pytorch")
    assert session.process is None
    assert not diagnostics


def test_vietnamese_voice_and_text_with_legacy_worker_encoding(sdk, tmp_path, monkeypatch):
    args, _ = sdk
    monkeypatch.setenv("PYTHONUTF8", "0")
    monkeypatch.setenv("PYTHONIOENCODING", "cp1252")
    with TtsSession(**args, provider="vieneu") as session:
        output = session.synthesize("Xin chào Việt Nam", tmp_path / "vietnamese.wav", voice="Hải Đăng")
        audio, rate = sf.read(output)
        assert rate == 48000 and np.allclose(audio, .125, atol=1 / 32768)


def test_omnivoice_cpu_fallback_keeps_same_snapshot_and_sample_rate(sdk, tmp_path):
    args, log = sdk
    ref = tmp_path / "reference.wav"
    sf.write(ref, np.full(100, .1), 16000)
    with TtsSession(**args, provider="omnivoice", device="cuda", license_accepted=True) as session:
        assert session.voices() == []
        output = session.synthesize("hello", tmp_path / "out.wav", ref_audio=ref, ref_text="reference")
        assert sf.info(output).samplerate == 24000
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert [call["device_map"] for call in calls] == ["cuda:0", "cpu"]


@pytest.mark.parametrize("ref,text", [(None, "words"), ("missing.wav", "words"), ("missing.wav", "")])
def test_omnivoice_rejects_missing_reference(sdk, tmp_path, ref, text):
    args, _ = sdk
    session = TtsSession(**args, provider="omnivoice", license_accepted=True)
    with pytest.raises(RuntimeError, match="ref_audio|ref_text"):
        session.synthesize("hello", tmp_path / "out.wav", ref_audio=ref, ref_text=text)
    assert session.process is None
    assert not (tmp_path / "out.wav").exists()


def test_license_and_local_snapshot_checked_before_process(sdk, tmp_path):
    args, log = sdk
    with pytest.raises(ValueError, match="licenses"):
        TtsSession(**args, provider="omnivoice")
    with pytest.raises(ValueError, match="local directory"):
        TtsSession(**{**args, "model_path": tmp_path / "absent"}, provider="vieneu")
    assert not log.exists()


def test_missing_offline_assets_have_fixed_error(sdk, monkeypatch):
    args, _ = sdk
    monkeypatch.setenv("TTS_MISSING", "1")
    with pytest.raises(RuntimeError, match="Offline TTS assets unavailable"):
        TtsSession(**args, provider="vieneu")


@pytest.mark.parametrize("samples", [np.zeros(100), np.full(100, np.nan)])
def test_invalid_reference_audio_rejected(sdk, tmp_path, samples):
    args, _ = sdk
    reference = tmp_path / "invalid.wav"
    sf.write(reference, samples, 16000, subtype="FLOAT")
    with TtsSession(**args, provider="vieneu") as session:
        with pytest.raises(RuntimeError, match="finite, non-silent"):
            session.synthesize("hello", tmp_path / "out.wav", ref_audio=reference)


def test_timeout_terminates_owned_worker(sdk, tmp_path):
    args, _ = sdk
    session = TtsSession(**args, provider="vieneu")
    process = session.process
    session.timeout = .1
    with pytest.raises(TimeoutError):
        session.synthesize("sleep", tmp_path / "out.wav")
    assert process.poll() is not None
    assert session.process is None


def test_cancellation_terminates_owned_worker(sdk, tmp_path):
    args, _ = sdk
    execution = Execution()
    session = TtsSession(**args, provider="vieneu")
    process = session.process
    timer = threading.Timer(.2, execution.cancel)
    timer.start()
    try:
        with execution.activate(), pytest.raises(Cancelled):
            session.synthesize("sleep", tmp_path / "out.wav")
    finally:
        timer.join()
        session.close()
    assert process.poll() is not None
