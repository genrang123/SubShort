"""Offline SDK worker: one JSON object per line on stdin and stdout.

load: provider, model_path, hf_cache, device (cpu/cuda), license_accepted,
      optional onnx_dir. synthesize: text, output_path, voice/ref_audio/ref_text.
voices and close take no arguments. Replies are {ok: true, result: ...} or
{ok: false, error: ...}. Only load imports the separately installed SDKs.
"""
import contextlib
import json
import os
from pathlib import Path
import sys


ASSET_ERROR = "Offline TTS assets unavailable; provision the complete local snapshot and Hugging Face cache."


def directory(value, name):
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        raise ValueError(f"{name} must be an existing local directory")
    return path


class Worker:
    def __init__(self):
        self.model = None
        self.provider = None
        self.device = None
        self.diagnostic = None

    def load(self, request):
        if self.model is not None:
            raise ValueError("A model is already loaded")
        if not isinstance(request, dict):
            raise ValueError("load request must be an object")
        provider = request.get("provider")
        if provider not in ("vieneu", "omnivoice"):
            raise ValueError("Unknown TTS provider")
        snapshot = directory(request["model_path"], "model_path")
        cache = directory(request["hf_cache"], "hf_cache")
        device = request.get("device", "cpu")
        if device not in ("cpu", "cuda"):
            raise ValueError("device must be cpu or cuda")
        if provider == "omnivoice" and request.get("license_accepted") is not True:
            raise ValueError("OmniVoice model and tokenizer licenses must be accepted")
        os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                          HF_HUB_CACHE=str(cache), HF_DATASETS_OFFLINE="1")
        fallback = False
        try:
            if provider == "vieneu":
                from vieneu import Vieneu
                if device == "cuda":
                    self.model = Vieneu(mode="v3turbo", backend="pytorch", device="cuda",
                                        dtype="auto", backbone_repo=str(snapshot))
                    engine = getattr(self.model, "engine", None)
                    actual_device = getattr(engine, "device", None)
                    if getattr(self.model, "backend", None) != "pytorch" or getattr(actual_device, "type", None) != "cuda":
                        raise RuntimeError("VieNeu CUDA backend did not load a CUDA PyTorch engine")
                else:
                    onnx = directory(request.get("onnx_dir") or snapshot / "onnx_update", "onnx_dir")
                    self.model = Vieneu(mode="v3turbo", backend="onnx", device="cpu",
                                        precision="fp32", onnx_dir=str(onnx), backbone_repo=str(snapshot))
            else:
                import torch
                from omnivoice import OmniVoice
                directory(snapshot / "audio_tokenizer", "audio_tokenizer")
                def create(target):
                    return OmniVoice.from_pretrained(str(snapshot), device_map=target,
                                                     dtype=torch.float32 if target == "cpu" else torch.float16,
                                                     load_asr=False, local_files_only=True)
                fallback = False
                if device == "cuda":
                    try:
                        self.model = create("cuda:0")
                    except (RuntimeError, AssertionError):
                        self.model = create("cpu")
                        fallback = True
                else:
                    self.model = create("cpu")
        except Exception as exc:
            raise RuntimeError(ASSET_ERROR) from exc
        self.provider = provider
        self.device = "cpu" if provider == "omnivoice" and (device == "cpu" or fallback) else device
        self.diagnostic = "CUDA unavailable; loaded the same TTS model on CPU" if provider == "omnivoice" and device == "cuda" and self.device == "cpu" else None
        result = {"sample_rate": self.sample_rate, "device": self.device}
        if self.diagnostic:
            result["diagnostic"] = self.diagnostic
        return result

    @property
    def sample_rate(self):
        return int(self.model.sample_rate if self.provider == "vieneu" else self.model.sampling_rate)

    def voices(self):
        if self.provider != "vieneu":
            return []
        result = []
        for item in self.model.list_preset_voices():
            if not isinstance(item, (tuple, list)) or len(item) != 2:
                raise ValueError("Invalid VieNeu preset voice")
            label, voice_id = item
            if not isinstance(label, str) or not label.strip() or not isinstance(voice_id, str) or not voice_id.strip():
                raise ValueError("Invalid VieNeu preset voice")
            result.append({"label": label, "id": voice_id})
        return result

    def synthesize(self, request):
        import numpy as np
        import soundfile as sf
        if not isinstance(request, dict):
            raise ValueError("synthesize request must be an object")
        text = request.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("text must not be empty")
        voice = request.get("voice")
        if voice is not None and (not isinstance(voice, str) or not voice.strip()):
            raise ValueError("voice must be a non-empty string")
        ref = request.get("ref_audio")
        transcript = request.get("ref_text")
        if ref is not None and (not isinstance(ref, str) or not ref.strip()):
            raise ValueError("ref_audio must be a non-empty path")
        if transcript is not None and not isinstance(transcript, str):
            raise ValueError("ref_text must be a string")
        if ref and voice:
            raise ValueError("voice and ref_audio are mutually exclusive")
        if self.provider == "omnivoice" and (not ref or not isinstance(transcript, str) or not transcript.strip()):
            raise ValueError("OmniVoice requires ref_audio and ref_text; automatic transcription is disabled")
        if self.provider == "vieneu" and transcript is not None:
            raise ValueError("ref_text is only supported by OmniVoice")
        if ref:
            path = Path(ref).expanduser().resolve()
            if not path.is_file():
                raise ValueError("ref_audio must be an existing file")
            samples, rate = sf.read(str(path), dtype="float32")
            if rate <= 0 or samples.size == 0 or not np.isfinite(samples).all() or not np.any(samples):
                raise ValueError("ref_audio must contain finite, non-silent audio")
            ref = str(path)
        if self.provider == "vieneu":
            kwargs = {}
            if ref:
                kwargs["ref_audio"] = ref
            elif request.get("voice"):
                voice = request["voice"]
                if voice not in {item["id"] for item in self.voices()}:
                    raise ValueError("Unknown VieNeu preset voice")
                kwargs["voice"] = voice
            audio = self.model.infer(text, **kwargs)
        else:
            audio = self.model.generate(text=text, ref_audio=ref, ref_text=transcript)[0]
        audio = np.asarray(audio)
        if audio.ndim != 1 or audio.size == 0 or not np.isfinite(audio).all():
            raise ValueError("TTS returned invalid audio")
        output_value = request.get("output_path")
        if not isinstance(output_value, str) or not output_value.strip():
            raise ValueError("output_path must be a non-empty path")
        output = Path(output_value).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(output), audio, self.sample_rate, format="WAV")
        return {"output_path": str(output), "sample_rate": self.sample_rate}

    def dispatch(self, request):
        if not isinstance(request, dict) or not isinstance(request.get("command"), str):
            raise ValueError("worker request must contain a command")
        command = request["command"]
        if command == "load":
            return self.load(request)
        if command == "close":
            self.model = None
            self.provider = self.device = self.diagnostic = None
            return None
        if self.model is None:
            raise ValueError("Load a model first")
        if command == "voices":
            return self.voices()
        if command == "synthesize":
            return self.synthesize(request)
        raise ValueError("Unknown worker command")


def main():
    # Keep even native SDK stdout writes away from the protocol descriptor.
    protocol = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8", buffering=1)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    worker = Worker()
    for line in sys.stdin:
        request = None
        try:
            request = json.loads(line)
            with contextlib.redirect_stdout(sys.stderr):
                result = worker.dispatch(request)
            reply = {"ok": True, "result": result}
        except Exception as exc:
            reply = {"ok": False, "error": str(exc)}
        protocol.write(json.dumps(reply, ensure_ascii=False) + "\n")
        protocol.flush()
        if isinstance(request, dict) and request.get("command") == "close":
            break
    protocol.close()


if __name__ == "__main__":
    main()
