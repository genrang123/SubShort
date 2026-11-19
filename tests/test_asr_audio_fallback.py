import argparse
import math
import sys
import types
import wave

import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("torchaudio")

from subshort.asr import ASRSession, ProviderError, _read_wav_audio


def _write_pcm(path, width, channels=2, rate=44100):
    if width == 1:
        sample = bytes([160])
    elif width == 2:
        sample = (1000).to_bytes(2, "little", signed=True)
    elif width == 3:
        sample = (1000).to_bytes(3, "little", signed=True)
    else:
        sample = (1000).to_bytes(4, "little", signed=True)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(width)
        output.setframerate(rate)
        output.writeframes(sample * (channels * 100))


def test_wav_fallback_downmixes_and_bandlimited_resamples(tmp_path):
    path = tmp_path / "stereo.wav"
    _write_pcm(path, 2)
    waveform = _read_wav_audio(path, 16000)
    assert waveform.numel() == math.ceil(100 * 16000 / 44100)
    assert waveform.abs().max() > 0


@pytest.mark.parametrize("width", [1, 2, 3, 4])
def test_wav_fallback_supports_pcm_widths(tmp_path, width):
    path = tmp_path / f"pcm{width}.wav"
    _write_pcm(path, width, channels=1, rate=16000)
    waveform = _read_wav_audio(path, 16000)
    assert waveform.numel() == 100
    assert waveform.abs().max() > 0


def test_wav_fallback_rejects_unsupported_width(tmp_path):
    path = tmp_path / "bad.wav"
    _write_pcm(path, 2, channels=1, rate=16000)
    raw = path.read_bytes()
    path.write_bytes(raw[:34] + (40).to_bytes(2, "little") + raw[36:])
    with pytest.raises(ProviderError, match="Unsupported WAV"):
        _read_wav_audio(path, 16000)


def test_firered_load_scopes_namespace_allowlist(monkeypatch, tmp_path):
    import torch

    model_path = tmp_path / "model"
    model_path.mkdir()
    observed = []

    class FakeModel:
        model = types.SimpleNamespace(cpu=lambda: None, cuda=lambda: None)

        @classmethod
        def from_pretrained(cls, kind, path):
            return cls()

    fake = types.ModuleType("fireredasr.models.fireredasr")
    fake.FireRedAsr = FakeModel
    monkeypatch.setitem(sys.modules, "fireredasr", types.ModuleType("fireredasr"))
    monkeypatch.setitem(sys.modules, "fireredasr.models", types.ModuleType("fireredasr.models"))
    monkeypatch.setitem(sys.modules, "fireredasr.models.fireredasr", fake)

    class Scope:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(torch.serialization, "safe_globals", lambda values: (observed.extend(values), Scope())[1])
    (tmp_path / "vad.jit").touch()
    session = ASRSession("firered", model_path, device="cpu", vad_model_path=tmp_path / "vad.jit")
    session._load_on("cpu")
    assert observed == [argparse.Namespace]
