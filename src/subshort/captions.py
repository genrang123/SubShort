"""Align final-voice word timings without replacing the authored TTS text."""

import math
from numbers import Real
from pathlib import Path
import re
import unicodedata

from .execution import Cancelled, check_cancelled
from .providers import FatalProviderError, ProviderError, Segment, validate_segments


# CJK scripts have useful caption units even when the author supplied no spaces.
_CJK = re.compile(r"([\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af\U00020000-\U0002ffff])")
_WORD = re.compile(r"[^\W_]+(?:['’][^\W_]+)*", re.UNICODE)


def _tokens(text):
    result = []
    for word in _WORD.finditer(text):
        check_cancelled()
        offset = word.start()
        for part in _CJK.split(word.group()):
            if part:
                normalized = ''.join(c for c in unicodedata.normalize('NFKC', part).casefold() if c.isalnum())
                result.append((offset, offset + len(part), normalized))
                offset += len(part)
    return result


def recognize_words(voice: Path, model: str | Path = "small", device: str = "cpu") -> list[Segment]:
    """Lazily load faster-whisper and return its individual word timestamps."""
    check_cancelled()
    from faster_whisper import WhisperModel

    # shortcut: native model loading/decoding cannot be interrupted; check between yields.
    recognizer = WhisperModel(str(model), device=device, compute_type="auto", local_files_only=True)
    check_cancelled()
    segments, _ = recognizer.transcribe(str(voice), word_timestamps=True)
    result = []
    for segment in segments:
        check_cancelled()
        for word in segment.words or ():
            check_cancelled()
            result.append(Segment(word.start, word.end, word.word))
    check_cancelled()
    return result


def _groups(text, tokens):
    """Return text slices and token ranges, retaining every original character."""
    if not tokens:
        return [(text, 0, 0)]
    groups = []
    first = 0
    start = 0
    for index, (_, end, _) in enumerate(tokens):
        check_cancelled()
        stop = tokens[index + 1][0] if index + 1 < len(tokens) else len(text)
        punctuation = any(unicodedata.category(c).startswith('P') for c in text[end:stop])
        if index - first + 1 >= 6 or punctuation or index + 1 == len(tokens):
            groups.append((text[start:stop], first, index + 1))
            first, start = index + 1, stop
    return groups


def build_captions(voice: Path, references: list[Segment], duration: float, recognizer=None) -> tuple[list[Segment], dict]:
    """Use ASR only for fully matched references; otherwise proportion their text.

    ``recognizer`` is a callable taking a Path and returning word Segments. Use
    ``functools.partial(recognize_words, model=..., device=...)`` to configure it,
    or wrap ``ASRSession.transcribe_words(voice, language) -> list[Segment]`` once
    the ASR session returns the configured backend's word timings.
    Metadata contains only fixed reason codes, never provider messages or paths.
    """
    check_cancelled()
    if isinstance(duration, bool) or not isinstance(duration, Real) or not math.isfinite(duration) or duration < 0:
        raise ProviderError("Invalid caption duration")
    validate_segments(references, duration)
    reason = None
    try:
        words = (recognizer or recognize_words)(voice)
        check_cancelled()
        words = validate_segments(words, duration)
    except (Cancelled, FatalProviderError):
        raise
    except Exception:
        check_cancelled()
        words = []
        reason = "recognition_failed"

    timed_tokens = []
    for word in words:
        check_cancelled()
        for _, _, token in _tokens(word.text):
            timed_tokens.append((token, word.start, word.end))

    captions = []
    matched = 0
    for reference in references:
        check_cancelled()
        tokens = _tokens(reference.text)
        groups = _groups(reference.text, tokens)
        candidates = [entry for entry in timed_tokens
                      if entry[1] >= reference.start - .2 and entry[2] <= reference.end + .2]
        aligned = bool(tokens) and [entry[0] for entry in candidates] == [token[2] for token in tokens]
        if aligned:
            # Clamping tolerated edge drift must still leave every group positive.
            bounds = [(max(reference.start, candidates[first][1]),
                       min(reference.end, candidates[last - 1][2]))
                      for _, first, last in groups]
            aligned = all(start < end for start, end in bounds)
        if aligned:
            matched += 1
        else:
            # Character weights also preserve punctuation-only and unspaced text.
            size = len(reference.text)
            offset = 0
            bounds = []
            for text, _, _ in groups:
                check_cancelled()
                start = reference.start + (reference.end - reference.start) * offset / size
                offset += len(text)
                end = reference.start + (reference.end - reference.start) * offset / size
                bounds.append((start, end))
        for (text, _, _), (start, end) in zip(groups, bounds):
            check_cancelled()
            captions.append(Segment(start, end, text))
    # Validate without stripping the authored whitespace returned to the caller.
    validate_segments(captions, duration)
    fallback = len(references) - matched
    return captions, {
        "source": "mixed" if matched and fallback else "asr" if matched else "reference",
        "matched_references": matched,
        "fallback_references": fallback,
        "reason": reason or ("unmatched_words" if fallback else None),
    }
