import pytest

from subshort.hooks import THEMES, append_hook_ass, build_hook, write_hook_ass
from subshort.providers import Segment
from subshort.subtitles import write_ass


def test_hook_is_exact_evidence_excerpt_and_deterministic():
    cues = [Segment(0, 2, ""), Segment(2, 8, "  One two three four five six seven eight nine ten eleven twelve thirteen.  "),
            Segment(8, 12, "Another reliable translated cue.")]
    hook = build_hook(cues, seed=7)
    assert hook == build_hook(cues, seed=7)
    assert hook["text"] in cues[hook["cue_id"]].text
    assert len(hook["text"].split()) <= 12
    assert hook["start"] == 0 and hook["end"] == 3
    assert hook["theme"] == "auto" and hook["seed"] == 7


def test_hook_preserves_exact_word_and_cjk_prefix():
    text = "Một  hai ba bốn năm sáu bảy tám chín mười mười-một mười-hai cuối"
    assert build_hook([Segment(0, 5, text)])["text"] == text.rsplit(" ", 1)[0]
    cjk = "这是一个没有空格的中文字幕示例内容"
    assert build_hook([Segment(0, 5, cjk)])["text"] == cjk[:12]


@pytest.mark.parametrize("cues", [[], [Segment(0, 1, "  ")], [Segment(0, 1, "[Music]")],
                                      [Segment(0, 1, "♪♪")], [Segment(1, 0, "Bad timing")],
                                      [Segment(0, float("nan"), "Bad timing")]])
def test_no_reliable_speech_has_no_hook(cues):
    assert build_hook(cues) is None


@pytest.mark.parametrize("duration", [True, "3", None, 0, 0.99, 8.01, 10, float("nan"), float("inf")])
def test_invalid_duration_rejected_even_without_speech(duration):
    with pytest.raises(ValueError, match="duration"):
        build_hook([], duration=duration)


@pytest.mark.parametrize("duration", [1, 3.25, 8])
def test_duration_boundaries(duration):
    assert build_hook([Segment(0, 2, "Evidence")], duration=duration)["end"] == duration


@pytest.mark.parametrize("theme", ["unknown", "AUTO", "", None])
def test_invalid_theme_rejected(theme):
    with pytest.raises(ValueError, match="theme"):
        build_hook([], theme=theme)


@pytest.mark.parametrize("theme", THEMES)
def test_supported_themes(theme):
    assert build_hook([Segment(0, 1, "Evidence")], theme=theme)["theme"] == theme


@pytest.mark.parametrize("seed", [True, 1.2, "1", None])
def test_invalid_seed_rejected(seed):
    with pytest.raises(ValueError, match="seed"):
        build_hook([], seed=seed)


def test_seed_can_select_different_evidence():
    cues = [Segment(i, i + 1, f"Evidence number {i}") for i in range(10)]
    assert len({build_hook(cues, seed=seed)["cue_id"] for seed in range(20)}) > 1


def test_standalone_ass_is_independent_and_escaped(tmp_path):
    path = tmp_path / "hook.ass"
    hook = build_hook([Segment(0, 4, "Exact {claim} \\N\nEvidence")], duration=3.125)
    write_hook_ass(hook, path, 1080, 1920, "ja")
    contents = path.read_text(encoding="utf-8")
    assert "[Script Info]" in contents and "[Events]" in contents
    assert "Style: Hook,Noto Sans CJK JP," in contents
    assert ",0,8," in contents
    assert "Dialogue: 10,0:00:00.00,0:00:03.13,Hook," in contents
    assert "\\{claim\\}" in contents and "\\\u2060N\\NEvidence" in contents
    assert "Dialogue: 0," not in contents


def test_append_preserves_caption_dialogue(tmp_path):
    path = tmp_path / "combined.ass"
    write_ass([Segment(0, 2, "Caption remains")], path, 720, 1280)
    original_dialogue = next(line for line in path.read_text(encoding="utf-8").splitlines()
                             if line.startswith("Dialogue:"))
    hook = build_hook([Segment(0, 2, "Opening evidence")])
    append_hook_ass(path, hook, 720, 1280)
    contents = path.read_text(encoding="utf-8")
    assert original_dialogue in contents
    assert contents.count("[Events]") == 1
    assert contents.count("Style: Hook,") == 1
    assert "Dialogue: 10," in contents


def test_append_creates_document_and_none_is_noop(tmp_path):
    path = tmp_path / "hook.ass"
    append_hook_ass(path, None, 720, 1280)
    assert not path.exists()
    append_hook_ass(path, build_hook([Segment(0, 1, "Evidence")]), 720, 1280)
    assert "[Script Info]" in path.read_text(encoding="utf-8")
    before = path.read_bytes()
    append_hook_ass(path, None, 720, 1280)
    assert path.read_bytes() == before


def test_append_rejects_mismatch_without_modifying_file(tmp_path):
    path = tmp_path / "captions.ass"
    write_ass([], path, 720, 1280)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="matching"):
        append_hook_ass(path, build_hook([Segment(0, 1, "Evidence")]), 1080, 1920)
    assert path.read_bytes() == before


def test_invalid_metadata_does_not_overwrite_existing_document(tmp_path):
    path = tmp_path / "captions.ass"
    path.write_text("existing", encoding="utf-8")
    hook = build_hook([Segment(0, 1, "Evidence")])
    hook["end"] = float("nan")
    with pytest.raises(ValueError):
        write_hook_ass(hook, path, 720, 1280)
    assert path.read_text(encoding="utf-8") == "existing"
