import sys
import types
from pathlib import Path

import pytest

from subshort.asr import ASRSession
from subshort.execution import Cancelled, Execution
from subshort.providers import FatalProviderError, ProviderError, Segment


@pytest.fixture
def local_paths(tmp_path):
    model = tmp_path / "model"
    model.mkdir()
    aligner = tmp_path / "aligner"
    aligner.mkdir()
    vad = tmp_path / "vad.jit"
    vad.touch()
    audio = tmp_path / "audio.wav"
    audio.touch()
    return model, aligner, vad, audio


def test_requires_existing_local_models(local_paths):
    model, _, _, _ = local_paths
    with pytest.raises(FatalProviderError):
        ASRSession("whisper", "small")
    with pytest.raises(FatalProviderError):
        ASRSession("qwen", model)


def test_whisper_is_lazy_reuses_model_and_closes(local_paths, monkeypatch):
    model, _, _, audio = local_paths
    calls = []

    class Whisper:
        def __init__(self, path, **kwargs):
            calls.append((path, kwargs))

        def transcribe(self, path, **kwargs):
            calls.append(kwargs)
            return iter([types.SimpleNamespace(start=0.2, end=1.2, text=" hello ")]), None

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=Whisper))
    session = ASRSession("whisper", model)
    assert calls == []
    with session:
        assert session.transcribe(audio, "en") == [Segment(.2, 1.2, "hello")]
        session.transcribe(audio)
    assert session._model is None
    assert calls[0] == (str(model.resolve()), {"device": "cpu", "compute_type": "int8", "local_files_only": True})
    assert calls[1] == {"language": "en", "vad_filter": True, "word_timestamps": False}
    assert len(calls) == 3


def test_whisper_word_transcription_reuses_configured_session(local_paths, monkeypatch):
    model, _, _, audio = local_paths
    calls = []

    class Whisper:
        def __init__(self, path, **kwargs):
            calls.append((path, kwargs))

        def transcribe(self, path, **kwargs):
            calls.append((path, kwargs))
            return iter([types.SimpleNamespace(words=[
                types.SimpleNamespace(start=.1, end=.7, word=" hello"),
                types.SimpleNamespace(start=.8, end=1.2, word="world"),
            ])]), None

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=Whisper))
    session = ASRSession("whisper", model)
    assert session.transcribe_words(audio, "vi") == [
        Segment(.1, .7, "hello"), Segment(.8, 1.2, "world")
    ]
    assert calls == [
        (str(model.resolve()), {"device": "cpu", "compute_type": "int8", "local_files_only": True}),
        (str(audio), {"language": "vi", "vad_filter": True, "word_timestamps": True}),
    ]

@pytest.mark.parametrize("available,error,expected", [
    (False, None, ["cpu"]),
    (True, "CUDA out of memory", ["cuda", "cpu"]),
    (True, "corrupt model", ["cuda"]),
])
def test_cuda_fallback_only_for_unavailable_or_load_oom(local_paths, monkeypatch, available, error, expected):
    model, _, _, audio = local_paths
    calls, diagnostics = [], []
    monkeypatch.setitem(sys.modules, "ctranslate2", types.SimpleNamespace(get_cuda_device_count=lambda: int(available)))

    class Whisper:
        def __init__(self, path, device, **kwargs):
            calls.append(device)
            if device == "cuda" and error:
                raise RuntimeError(error)

        def transcribe(self, *args, **kwargs):
            return iter([]), None

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=Whisper))
    session = ASRSession("whisper", model, "cuda", diagnostic=diagnostics.append)
    if error == "corrupt model":
        with pytest.raises(RuntimeError, match=error):
            session.transcribe(audio)
        assert diagnostics == []
    else:
        assert session.transcribe(audio) == []
        assert len(diagnostics) == 1 and "same ASR model" in diagnostics[0]
    assert calls == expected


class Waveform:
    def __init__(self, length):
        self.length = length

    def __len__(self):
        return self.length

    def __getitem__(self, key):
        return Waveform(key.stop - key.start)

    def numpy(self):
        return self


@pytest.fixture
def vad_modules(monkeypatch):
    saved = []
    module = types.ModuleType("silero_vad")
    module.read_audio = lambda *a, **kw: Waveform(64000)
    module.get_speech_timestamps = lambda *a, **kw: [{"start": 16000, "end": 32000}, {"start": 48000, "end": 64000}]
    module.save_audio = lambda path, clip, **kw: saved.append((path, len(clip)))
    utils = types.ModuleType("silero_vad.utils_vad")
    utils.init_jit_model = lambda path: object()
    monkeypatch.setitem(sys.modules, "silero_vad", module)
    monkeypatch.setitem(sys.modules, "silero_vad.utils_vad", utils)
    return module, saved


@pytest.mark.parametrize("language,canonical", [("en", "English"), ("zh", "Chinese"), ("English", "English"), (None, None)])
def test_qwen_alignment_is_offset_by_real_vad_boundaries(local_paths, monkeypatch, vad_modules, language, canonical):
    model, aligner, vad, audio = local_paths
    calls = []

    class Qwen:
        @classmethod
        def from_pretrained(cls, path, **kwargs):
            calls.append((path, kwargs))
            return cls()

        def transcribe(self, **kwargs):
            assert kwargs["language"] == canonical
            calls.append(kwargs)
            return [types.SimpleNamespace(text="hi", time_stamps=[types.SimpleNamespace(start_time=.1, end_time=.8, text="hi")])]

    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(float32="fp32", float16="fp16"))
    monkeypatch.setitem(sys.modules, "qwen_asr", types.SimpleNamespace(Qwen3ASRModel=Qwen))
    session = ASRSession("qwen", model, forced_aligner_path=aligner, vad_model_path=vad)
    assert session.transcribe(audio, language) == [Segment(1.1, 1.8, "hi"), Segment(3.1, 3.8, "hi")]
    options = calls[0][1]
    assert options["local_files_only"] is True
    assert options["forced_aligner"] == str(aligner.resolve())
    assert options["forced_aligner_kwargs"]["local_files_only"] is True
    assert calls[1]["return_time_stamps"] is True and calls[1]["language"] == canonical
    assert len(calls[1]["audio"][0]) == 16000


def test_firered_transcribes_whole_vad_intervals_not_fixed_chunks(local_paths, monkeypatch, vad_modules):
    model, _, vad, audio = local_paths
    class Scope:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
    torch_stub = types.SimpleNamespace(serialization=types.SimpleNamespace(safe_globals=lambda values: Scope()))
    monkeypatch.setitem(sys.modules, "torch", torch_stub)
    def save_wav(path, clip, sampling_rate):
        vad_modules[1].append((path, len(clip)))
    monkeypatch.setattr("subshort.asr._save_wav_audio", save_wav)
    calls = []

    class FireRed:
        model = types.SimpleNamespace(cpu=lambda: None)

        @classmethod
        def from_pretrained(cls, kind, path):
            calls.append((kind, path))
            return cls()

        def transcribe(self, ids, paths, options):
            calls.append((ids, options))
            assert Path(paths[0]).name == "speech.wav"
            return [{"uttid": "speech", "text": " hello "}]

    monkeypatch.setitem(sys.modules, "fireredasr.models.fireredasr", types.SimpleNamespace(FireRedAsr=FireRed))
    session = ASRSession("firered", model, vad_model_path=vad)
    assert session.transcribe(audio) == [Segment(1, 2, "hello"), Segment(3, 4, "hello")]
    assert calls[0] == ("aed", str(model.resolve()))
    assert calls[1:] == [(["speech"], {"use_gpu": False})] * 2
    assert [length for _, length in vad_modules[1]] == [16000, 16000]


def test_cancelled_before_load_does_not_import_backend(local_paths, monkeypatch):
    model, _, _, audio = local_paths
    monkeypatch.delitem(sys.modules, "faster_whisper", raising=False)
    session = ASRSession("whisper", model)
    execution = Execution()
    execution.cancel()
    with execution.activate(), pytest.raises(Cancelled):
        session.transcribe(audio)
    assert session._model is None


def test_cancelled_during_native_load_drops_model(local_paths, monkeypatch):
    model, _, _, audio = local_paths
    execution = Execution()

    class Whisper:
        def __init__(self, *args, **kwargs):
            execution.cancel()

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=Whisper))
    session = ASRSession("whisper", model)
    with execution.activate(), pytest.raises(Cancelled):
        session.transcribe(audio)
    assert session._model is None


def test_whisper_rejects_malformed_transcript(local_paths, monkeypatch):
    model, _, _, audio = local_paths
    backend = types.SimpleNamespace(transcribe=lambda *a, **kw: (iter([types.SimpleNamespace(start=0, end=1, text=None)]), None))
    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=lambda *a, **kw: backend))
    with pytest.raises(ProviderError):
        ASRSession("whisper", model).transcribe(audio)


@pytest.mark.parametrize("interval", [{"start": -1, "end": 2}, {"start": 0, "end": 64001}, {"start": 2, "end": 1}])
def test_invalid_vad_boundaries_never_reach_decoder(local_paths, monkeypatch, vad_modules, interval):
    model, _, vad, audio = local_paths
    vad_modules[0].get_speech_timestamps = lambda *a, **kw: [interval]
    session = ASRSession("firered", model, vad_model_path=vad)
    session._model, session._vad = object(), object()
    with pytest.raises(ProviderError, match="VAD"):
        session.transcribe(audio)


def test_cancelled_during_decode_is_observed(local_paths, monkeypatch):
    model, _, _, audio = local_paths
    execution = Execution()

    class Whisper:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, *args, **kwargs):
            execution.cancel()
            return iter([]), None

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=Whisper))
    with execution.activate(), pytest.raises(Cancelled):
        ASRSession("whisper", model).transcribe(audio)
