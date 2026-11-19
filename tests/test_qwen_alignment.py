import types

import pytest

from subshort.asr import ASRSession, _validate_qwen_alignment
from subshort.providers import ProviderError, Segment


def test_qwen_alignment_rejects_zero_length_item_with_details():
    rows = [types.SimpleNamespace(start_time=0.0, end_time=0.0, text="嗯")]
    with pytest.raises(ProviderError, match=r"item 0.*start=0\.0.*end=0\.0.*duration=0\.412000"):
        _validate_qwen_alignment(rows, 0.412)


def test_qwen_alignment_preserves_valid_order_and_bounds():
    rows = [
        types.SimpleNamespace(start_time=0.0, end_time=0.08, text="我"),
        types.SimpleNamespace(start_time=0.08, end_time=0.24, text="们"),
    ]
    assert _validate_qwen_alignment(rows, 0.412) == [
        Segment(0.0, 0.08, "我"),
        Segment(0.08, 0.24, "们"),
    ]


def test_qwen_alignment_does_not_repair_invalid_rows():
    rows = [types.SimpleNamespace(start_time=0.0, end_time=0.08, text="我"),
            types.SimpleNamespace(start_time=0.08, end_time=0.08, text="们")]
    with pytest.raises(ProviderError, match=r"item 1"):
        _validate_qwen_alignment(rows, 0.412)
