"""JSON worker for an explicitly installed, local pyannote community-1 pipeline."""
import json
import os
import sys
import wave
from pathlib import Path


def _waveform(path):
    try:
        import torch
    except ImportError as exc:
        raise RuntimeError(f"Thiếu torch trong worker: {exc.name}") from None
    try:
        with wave.open(str(path), "rb") as source:
            channels, width, rate, count = source.getnchannels(), source.getsampwidth(), source.getframerate(), source.getnframes()
            if channels != 1 or width != 2 or rate != 16000:
                raise RuntimeError("Audio diarization phải là WAV mono PCM 16 kHz 16-bit")
            raw = source.readframes(count)
    except (wave.Error, EOFError, OSError) as exc:
        raise RuntimeError(f"Không đọc được audio diarization: {exc}") from None
    if len(raw) != count * 2:
        raise RuntimeError("Audio diarization bị thiếu dữ liệu PCM")
    values = torch.frombuffer(bytearray(raw), dtype=torch.int16).to(torch.float32) / 32768.0
    return {"waveform": values.unsqueeze(0), "sample_rate": rate}


def main():
    try:
        request = json.load(sys.stdin)
        if not isinstance(request, dict):
            raise ValueError("Yêu cầu worker không hợp lệ")
        audio = Path(request["audio"])
        model_path = Path(request["model_path"])
        if not audio.is_file() or not model_path.is_dir():
            raise RuntimeError("Audio/model diarization local không tồn tại")
        try:
            os.environ["PYANNOTE_METRICS_ENABLED"] = "0"
            from pyannote.audio import Pipeline
        except ImportError as exc:
            raise RuntimeError(f"Thiếu pyannote.audio trong worker: {exc.name}") from None
        # community-1 is expected to be provisioned locally; offline env prevents hidden downloads.
        pipeline = Pipeline.from_pretrained(str(model_path))
        device = request.get("device", "cpu")
        if device == "cuda":
            import torch
            if not torch.cuda.is_available():
                raise RuntimeError("CUDA diarization không khả dụng")
            pipeline.to(torch.device("cuda"))
        output = pipeline(_waveform(audio))
        diarization = getattr(output, "speaker_diarization", None)
        if diarization is None:
            diarization = getattr(output, "exclusive_speaker_diarization", None)
        if diarization is None:
            diarization = output
        turns = []
        for turn, _, speaker in diarization.itertracks(yield_label=True):
            turns.append({"start": float(turn.start), "end": float(turn.end),
                          "speaker_id": str(speaker), "status": "resolved"})
        print(json.dumps({"ok": True, "turns": turns}, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)[:500]}, ensure_ascii=False))
        return 1
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
