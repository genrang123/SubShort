"""UTF-8 ASS captions for libass; font availability is the caller's responsibility."""

from decimal import Decimal, ROUND_HALF_UP
import math
from numbers import Real
from pathlib import Path
import re

from .providers import Segment


_CJK = "\\u3040-\\u30ff\\u3400-\\u9fff\\uac00-\\ud7af\\U00020000-\\U0002ffff"
_TOKEN = re.compile(f"[{_CJK}]|[^\\s{_CJK}]+")
_FONTS = {
    "zh": "Noto Sans CJK SC", "ja": "Noto Sans CJK JP", "ko": "Noto Sans CJK KR",
    "ar": "Noto Sans Arabic", "fa": "Noto Sans Arabic", "ur": "Noto Sans Arabic",
    "he": "Noto Sans Hebrew", "iw": "Noto Sans Hebrew", "th": "Noto Sans Thai",
    "hi": "Noto Sans Devanagari", "mr": "Noto Sans Devanagari", "ne": "Noto Sans Devanagari",
    "km": "Noto Sans Khmer",
}


def _centiseconds(seconds: float) -> int:
    return int((Decimal(str(seconds)) * 100).to_integral_value(rounding=ROUND_HALF_UP))


def _timestamp(value: int) -> str:
    hours, value = divmod(value, 360000)
    minutes, value = divmod(value, 6000)
    seconds, fraction = divmod(value, 100)
    return f"{hours}:{minutes:02}:{seconds:02}.{fraction:02}"


def _escape(text: str) -> str:
    # libass has no literal-backslash escape: WORD JOINER prevents authored \\N/\\h escapes.
    # Normalize every ASS line separator, not only the two ASCII newline forms.
    return (text.replace("\\", "\\\u2060").replace("{", "\\{").replace("}", "\\}")
            .replace("\r\n", "\n").replace("\r", "\n")
            .replace("\u2028", "\n").replace("\u2029", "\n")
            .replace("\n", "\\N"))


def _karaoke(text: str, duration: int) -> str:
    matches = list(_TOKEN.finditer(text))
    total = sum(len(match.group()) for match in matches)
    pieces = []
    previous_end = previous_time = weight = 0
    for index, match in enumerate(matches):
        weight += len(match.group())
        end_time = duration * weight // total
        end = len(text) if index == len(matches) - 1 else match.end()
        pieces.append(f"{{\\kf{end_time - previous_time}}}" + _escape(text[previous_end:end]))
        previous_end, previous_time = end, end_time
    return "".join(pieces)


def write_ass(captions: list[Segment], path: Path, width: int, height: int,
              language: str = "vi", font: str | None = None) -> None:
    """Write bottom-centred captions with proportional per-token karaoke timing.

    Cue times are seconds, rounded half-up to centiseconds. A sub-centisecond cue
    occupies at least one centisecond. Authored Unicode and spacing are retained;
    backslashes gain an invisible WORD JOINER to disable ASS escape interpretation.
    """
    if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0
           for value in (width, height)):
        raise ValueError("Subtitle dimensions must be positive integers")
    if not isinstance(language, str):
        raise ValueError("Subtitle language must be a string")
    selected_font = font if font is not None else _FONTS.get(language.lower().replace("_", "-").split("-")[0], "Noto Sans")
    if not isinstance(selected_font, str) or not selected_font.strip() or any(
            char in selected_font for char in ",{}\\\r\n\x00"):
        raise ValueError("Invalid ASS font name")
    if not isinstance(captions, list):
        raise ValueError("Captions must be a list of Segment")
    size = max(1, round(min(width, height) * 0.055))
    horizontal = max(1, round(width * 0.05))
    vertical = max(1, round(height * 0.08))
    outline = max(1, round(size * 0.06))
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}", f"PlayResY: {height}",
        "WrapStyle: 0", "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        # libass Unicode bidi layout must span karaoke runs (legacy Encoding=1 reorders RTL words).
        f"Style: Default,{selected_font},{size},&H0000FFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,{outline},0,2,{horizontal},{horizontal},{vertical},-1",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    speaker_slots = {}
    for cue in captions:
        if isinstance(cue, Segment) and cue.speaker_id:
            speaker_slots.setdefault(cue.speaker_id, len(speaker_slots))
    previous_start = previous_end = 0.0
    for cue in captions:
        if not isinstance(cue, Segment) or any(
                isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value)
                for value in (cue.start, cue.end)):
            raise ValueError("Invalid subtitle cue")
        if not 0 <= cue.start < cue.end or cue.start < previous_start or (cue.end < previous_end and not cue.speaker_id):
            raise ValueError("Invalid subtitle cue timing")
        if not isinstance(cue.text, str) or not cue.text.strip() or "\x00" in cue.text:
            raise ValueError("Invalid subtitle cue text")
        start = _centiseconds(cue.start)
        end = max(start + 1, _centiseconds(cue.end))
        prefix = ""
        if cue.speaker_id:
            slot = speaker_slots[cue.speaker_id]
            y = height - vertical - slot * (size + outline * 4)
            prefix = f"{{\\pos({width // 2},{max(size, y)})}}"
        lines.append(f"Dialogue: 0,{_timestamp(start)},{_timestamp(end)},Default,,0,0,0,,{prefix}{_karaoke(cue.text, end - start)}")
        previous_start, previous_end = cue.start, cue.end
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
