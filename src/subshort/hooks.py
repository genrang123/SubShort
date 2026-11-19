"""Deterministic opening overlays drawn only from translated cue evidence."""

import math
from numbers import Real
from pathlib import Path
import random

from .providers import Segment
from .subtitles import _TOKEN, _FONTS, _centiseconds, _escape, _timestamp, write_ass


THEMES = ("auto", "bold", "minimal")


def _duration(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value) or not 1 <= value <= 8:
        raise ValueError("Hook duration must be finite and between 1 and 8 seconds")
    return float(value)


def _options(theme: str, seed: int) -> None:
    if not isinstance(theme, str) or theme not in THEMES:
        raise ValueError("Invalid hook theme")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("Hook seed must be an integer")


def build_hook(segments: list[Segment], duration: float = 3, theme: str = "auto",
               seed: int = 0) -> dict | None:
    """Select an exact cue prefix, with a zero-based source-list index as cue_id.

    Each CJK character counts toward the same twelve-token budget as a word.
    Invalid timings, empty text and bracket-only stage directions are not evidence.
    """
    duration = _duration(duration)
    _options(theme, seed)
    if not isinstance(segments, list) or any(not isinstance(cue, Segment) for cue in segments):
        raise ValueError("Hooks require a list of Segment")
    candidates = []
    for index, cue in enumerate(segments):
        if any(isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value)
               for value in (cue.start, cue.end)) or not 0 <= cue.start < cue.end:
            continue
        if not isinstance(cue.text, str) or "\x00" in cue.text:
            continue
        text = cue.text.strip()
        if not text or (text.startswith(("[", "(")) and text.endswith(("]", ")"))):
            continue
        tokens = list(_TOKEN.finditer(text))
        if not tokens or not any(char.isalnum() for char in text):
            continue
        excerpt = text[:tokens[min(12, len(tokens)) - 1].end()]
        candidates.append((index, excerpt))
    if not candidates:
        return None
    cue_id, text = random.Random(seed).choice(candidates)
    return {"text": text, "cue_id": cue_id, "evidence_ids": [cue_id], "theme": theme, "seed": seed,
            "start": 0, "end": duration}


def _hook_lines(hook: dict, width: int, height: int, language: str) -> tuple[str, str]:
    if not isinstance(hook, dict):
        raise ValueError("Invalid hook metadata")
    _options(hook.get("theme"), hook.get("seed"))
    duration = _duration(hook.get("end"))
    if isinstance(hook.get("start"), bool) or hook.get("start") != 0:
        raise ValueError("Hook must start at zero")
    cue_id = hook.get("cue_id")
    if isinstance(cue_id, bool) or not isinstance(cue_id, int) or cue_id < 0:
        raise ValueError("Invalid hook evidence index")
    evidence_ids = hook.get("evidence_ids", [cue_id])
    if (not isinstance(evidence_ids, list) or not evidence_ids
            or any(isinstance(value, bool) or not isinstance(value, int) or value < 0
                   for value in evidence_ids)
            or cue_id not in evidence_ids):
        raise ValueError("Invalid hook evidence IDs")
    text = hook.get("text")
    if not isinstance(text, str) or not text.strip() or "\x00" in text:
        raise ValueError("Invalid hook text")
    if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in (width, height)):
        raise ValueError("Subtitle dimensions must be positive integers")
    if not isinstance(language, str):
        raise ValueError("Subtitle language must be a string")
    font = _FONTS.get(language.lower().replace("_", "-").split("-")[0], "Noto Sans")
    minimal = hook["theme"] == "minimal"
    size = max(1, round(min(width, height) * (0.06 if minimal else 0.075)))
    horizontal, vertical = max(1, round(width * 0.06)), max(1, round(height * 0.08))
    outline = max(1, round(size * 0.06))
    colour = "&H00FFFFFF" if minimal else "&H0000FFFF"
    style = (f"Style: Hook,{font},{size},{colour},&H00FFFFFF,&H00000000,&H80000000,"
             f"-1,0,0,0,100,100,0,0,1,{outline},0,8,{horizontal},{horizontal},{vertical},1")
    event = f"Dialogue: 10,0:00:00.00,{_timestamp(_centiseconds(duration))},Hook,,0,0,0,,{_escape(text)}"
    return style, event


def append_hook_ass(path: Path, hook: dict | None, width: int, height: int,
                    language: str = "vi") -> None:
    """Append a top-aligned layer-10 overlay, creating an ASS document if absent.

    Existing documents must use the format emitted by subtitles.write_ass.
    A missing hook is a no-op, so silent inputs never acquire invented text.
    """
    if hook is None:
        return
    style, event = _hook_lines(hook, width, height, language)
    path = Path(path)
    if not path.exists():
        write_ass([], path, width, height, language)
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    event_format = "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
    if ("[V4+ Styles]" not in lines or "[Events]" not in lines or event_format not in lines
            or f"PlayResX: {width}" not in lines or f"PlayResY: {height}" not in lines):
        raise ValueError("Hook requires a matching SubShort ASS document")
    styles_start, events_start = lines.index("[V4+ Styles]"), lines.index("[Events]")
    if styles_start >= events_start:
        raise ValueError("Invalid ASS section order")
    # Keep a single style declaration when appending more than one overlay.
    lines[styles_start:events_start] = [line for line in lines[styles_start:events_start]
                                       if not line.startswith("Style: Hook,")]
    lines.insert(lines.index("[Events]"), style)
    lines.append(event)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_hook_ass(hook: dict | None, path: Path, width: int, height: int,
                   language: str = "vi") -> None:
    """Write a standalone hook ASS file without enabling or adding captions."""
    if hook is not None:
        _hook_lines(hook, width, height, language)
    path = Path(path)
    write_ass([], path, width, height, language)
    append_hook_ass(path, hook, width, height, language)
