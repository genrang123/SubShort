import re

import pytest

from subshort.providers import Segment
from subshort.subtitles import write_ass


def _dialogue(path):
    return [line.split(",", 9) for line in path.read_text(encoding="utf-8").splitlines()
            if line.startswith("Dialogue:")]


def _visible(text):
    """Decode the libass text escapes used by the writer, retaining literal braces."""
    result = []
    position = 0
    while position < len(text):
        if text[position] == "{":
            end = text.index("}", position)
            assert re.fullmatch(r"\{\\kf\d+\}", text[position:end + 1])
            position = end + 1
        elif text[position:position + 2] in (r"\{", r"\}", r"\N"):
            result.append("\n" if text[position + 1] == "N" else text[position + 1])
            position += 2
        else:
            result.append(text[position])
            position += 1
    return "".join(result).replace("\u2060", "")


def test_authored_text_cannot_inject_overrides_or_events(tmp_path):
    path = tmp_path / "captions.ass"
    text = '  中文 日本語 한국어 Việt {\\pos(0,0)\\p1} \\N \\n \\h \\\\ {x}\r\nDialogue: 0,evil  '
    write_ass([Segment(0, 2, text)], path, 1080, 1920)
    rows = _dialogue(path)
    assert len(rows) == 1
    assert _visible(rows[0][-1]) == text.replace("\r\n", "\n")
    assert "\\\u2060pos" in rows[0][-1]
    assert "\ufeff" not in path.read_text(encoding="utf-8")


def test_centiseconds_round_half_up_and_carry(tmp_path):
    path = tmp_path / "captions.ass"
    write_ass([Segment(59.995, 60.015, "one two"), Segment(3600, 3600.001, "tiny")], path, 640, 360)
    rows = _dialogue(path)
    assert rows[0][1:3] == ["0:01:00.00", "0:01:00.02"]
    assert rows[1][1:3] == ["1:00:00.00", "1:00:00.01"]
    assert sum(map(int, re.findall(r"\{\\kf(\d+)\}", rows[0][-1]))) == 2


def test_karaoke_weights_tokens_and_retains_text(tmp_path):
    path = tmp_path / "captions.ass"
    write_ass([Segment(0, 1.01, "a longer 中文!")], path, 1080, 1920)
    text = _dialogue(path)[0][-1]
    durations = list(map(int, re.findall(r"\{\\kf(\d+)\}", text)))
    assert len(durations) == 5
    assert sum(durations) == 101
    assert durations[1] > durations[0]
    assert _visible(text) == "a longer 中文!"


@pytest.mark.parametrize("text", [
    "مرحبا بالعالم! اختبار الترجمة العربية.",
    "שלום עולם 123 ABC",
    "مرحبا ABC 123 بالعالم\nSecond line שלום 456",
    "English 123 مرحبا بالعالم",
])
def test_unicode_bidi_karaoke_preserves_logical_text_and_timing(tmp_path, text):
    path = tmp_path / "captions.ass"
    write_ass([Segment(0, 2, text)], path, 640, 360)
    style = next(line for line in path.read_text(encoding="utf-8").splitlines()
                 if line.startswith("Style: Default,"))
    # libass Encoding=-1 keeps bidi layout across karaoke override runs.
    assert style.split(",")[-1] == "-1"
    event = _dialogue(path)[0][-1]
    assert _visible(event) == text
    assert sum(map(int, re.findall(r"\{\\kf(\d+)\}", event))) == 200


def test_layout_scales_with_resolution(tmp_path):
    styles = []
    for width, height in [(1080, 1920), (2160, 3840)]:
        path = tmp_path / f"{width}.ass"
        write_ass([], path, width, height)
        content = path.read_text(encoding="utf-8")
        assert f"PlayResX: {width}\nPlayResY: {height}" in content
        styles.append(next(line for line in content.splitlines() if line.startswith("Style:")).split(","))
    for index in [2, 19, 20, 21]:
        assert abs(int(styles[1][index]) - 2 * int(styles[0][index])) <= 1
    assert styles[0][18] == "2"  # bottom-centre alignment


@pytest.mark.parametrize("language,font", [
    ("vi", "Noto Sans"), ("zh-CN", "Noto Sans CJK SC"), ("ja", "Noto Sans CJK JP"),
    ("ko", "Noto Sans CJK KR"), ("ar", "Noto Sans Arabic"), ("he", "Noto Sans Hebrew"),
    ("th", "Noto Sans Thai"), ("hi", "Noto Sans Devanagari"), ("km", "Noto Sans Khmer"),
])
def test_language_font_selection(tmp_path, language, font):
    path = tmp_path / "captions.ass"
    write_ass([], path, 1920, 1080, language)
    assert f"Style: Default,{font}," in path.read_text(encoding="utf-8")


def test_explicit_font_and_validation_precede_file_write(tmp_path):
    path = tmp_path / "captions.ass"
    write_ass([], path, 640, 360, font="Custom Font")
    original = path.read_bytes()
    assert b"Custom Font" in original
    for kwargs in [{"font": "bad\n[Events]"}, {"font": "bad,font"}, {"width": 0}, {"height": True}]:
        options = {"width": 640, "height": 360, **kwargs}
        with pytest.raises(ValueError):
            write_ass([], path, **options)
        assert path.read_bytes() == original
    for cue in [Segment(-1, 1, "bad"), Segment(0, float("inf"), "bad"), Segment(0, 1, "\x00")]:
        with pytest.raises(ValueError):
            write_ass([cue], path, 640, 360)
        assert path.read_bytes() == original
