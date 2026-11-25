"""Synthesize and play a fixed Vietnamese sample for a voice preview.
render_preview() reuses EdgeTTS/MediaAdapter.normalize_clip and TtsSession
exactly as the pipeline does, but writes only to its own temporary directory
and returns WAV bytes, so the caller owns caching and lifetime. An already
loaded session can be passed in to skip process startup. playback is Windows
winsound only; the WAV path therefore lives in a module-owned temp file until
stop_preview() or the next play. One preview plays at a time.
"""
import asyncio
import os
from pathlib import Path
import tempfile

from .providers import EdgeTTS
from .tts import TtsSession

SAMPLE_TEXT = "Xin chào, rất vui được gặp bạn."

_playback_path = None


def _synthesize(session, provider, voice_id, reference, raw):
    # OmniVoice also requires the stored reference transcript; VieNeu rejects
    # ref_text, so only forward it for OmniVoice.
    if reference:
        session.synthesize(SAMPLE_TEXT, raw, ref_audio=reference["path"],
                           ref_text=reference.get("ref_text") if provider == "omnivoice" else None)
    else:
        session.synthesize(SAMPLE_TEXT, raw, voice=voice_id)


def render_preview(provider, voice_id, reference, python, options, media, *, recording=False,
                   session=None, speed: float = 1.0):
    """Return WAV bytes for one preview.

    provider is edge/vieneu/omnivoice; reference is a resolved VoiceRegistry
    record (or None) and is required for OmniVoice and custom local voices.
    recording=True returns the stored recording unchanged. session is an
    already-loaded offline TtsSession owned by the caller; it is used as-is and
    never closed here. Raises ValueError / provider or media errors on invalid
    input; cancellation flows through the caller's active Execution.
    """
    if recording:
        if not reference or not reference.get("path"):
            raise ValueError("Bản thu không có tệp WAV để nghe")
        return Path(reference["path"]).read_bytes()
    if provider not in ("edge", "vieneu", "omnivoice"):
        raise ValueError("Bộ tạo giọng đang tắt hoặc không hỗ trợ nghe thử")
    if reference is not None and reference.get("provider") != provider:
        raise ValueError("Giọng tham chiếu không thuộc bộ tạo giọng đã chọn")
    if reference is None and (provider == "omnivoice" or not voice_id):
        raise ValueError("Cần chọn một giọng để nghe thử")
    with tempfile.TemporaryDirectory(prefix="subshort-preview-") as directory:
        work = Path(directory)
        raw = work / ("edge.mp3" if provider == "edge" else "raw.wav")
        wav = work / "preview.wav"
        if provider == "edge":
            asyncio.run(EdgeTTS().synthesize(SAMPLE_TEXT, raw, voice_id))
        elif session is not None:
            _synthesize(session, provider, voice_id, reference, raw)
        else:
            with TtsSession(python, **{**(options or {}), "provider": provider}) as created:
                _synthesize(created, provider, voice_id, reference, raw)
        media.normalize_clip(raw, wav, speed=speed)
        return wav.read_bytes()


def play_preview(wav):
    """Start asynchronous playback of WAV bytes; replaces any current preview."""
    if not isinstance(wav, (bytes, bytearray)) or not wav:
        raise ValueError("Dữ liệu WAV nghe thử không hợp lệ")
    if os.name != "nt":
        raise RuntimeError("Nghe thử giọng chỉ được hỗ trợ trên Windows")
    import winsound

    global _playback_path
    stop_preview()
    handle, name = tempfile.mkstemp(prefix="subshort-preview-", suffix=".wav")
    os.close(handle)
    path = Path(name)
    try:
        path.write_bytes(bytes(wav))
        winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    _playback_path = path


def stop_preview():
    """Stop playback and release the owned temp WAV, if any."""
    global _playback_path
    if os.name == "nt":
        import winsound

        try:
            winsound.PlaySound(None, 0)
        except RuntimeError:
            pass
    path, _playback_path = _playback_path, None
    if path is not None:
        # shortcut: a rare Windows lock lag may leave this temp file behind,
        # cleaned by the OS temp cleaner; retry if it ever becomes visible.
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
