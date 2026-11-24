from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

from subshort.captions import build_captions, recognize_words
from subshort.execution import Cancelled, Execution
from subshort.providers import FatalProviderError, ProviderError, Segment


VOICE = Path('final.wav')


def test_matching_uses_timings_and_only_authored_text():
    reference = Segment(0, 4, 'Hello, WORLD!')
    result, metadata = build_captions(VOICE, [reference], 4,
        lambda _: [Segment(.1, .7, 'hello'), Segment(1, 2, 'world')])
    assert result == [Segment(.1, .7, 'Hello, '), Segment(1, 2, 'WORLD!')]
    assert metadata == {'source': 'asr', 'matched_references': 1,
                        'fallback_references': 0, 'reason': None}


@pytest.mark.parametrize('recognized', [
    [Segment(.2, .8, 'one')],
    [Segment(.2, .8, 'one'), Segment(1, 1.5, 'wrong')],
    [Segment(.2, .8, 'two'), Segment(1, 1.5, 'one')],
    [Segment(.2, .8, 'one'), Segment(1, 1.5, 'two'), Segment(1.6, 1.8, 'extra')],
    [Segment(.2, .8, 'one'), Segment(2.3, 2.5, 'two')],
])
def test_uncertain_missing_wrong_or_out_of_bounds_falls_back(recognized):
    reference = Segment(0, 2, 'One two!')
    result, metadata = build_captions(VOICE, [reference], 3, lambda _: recognized)
    assert result == [reference]
    assert metadata['reason'] == 'unmatched_words'


def test_each_reference_matches_independently_and_tolerates_edge_drift():
    references = [Segment(.2, 1, 'First'), Segment(2, 3, 'Second')]
    words = [Segment(.1, 1.1, 'FIRST'), Segment(2.1, 2.7, 'wrong')]
    result, metadata = build_captions(VOICE, references, 3, lambda _: words)
    assert result == references
    assert metadata['source'] == 'mixed'


def test_nested_speaker_captions_preserve_speaker_and_order():
    references = [Segment(0, 8, 'uno dos tres cuatro cinco seis siete', '1', 'a'),
                  Segment(1, 2, 'hola', '2', 'b')]
    result, _ = build_captions(VOICE, references, 8, lambda _: [])
    assert [(cue.source_id, cue.speaker_id) for cue in result] == [('1', 'a'), ('2', 'b'), ('1', 'a')]
    assert result[1] == references[1]
    assert result[-1].end == 8


def test_fallback_preserves_cjk_punctuation_spaces_and_splits_six_words():
    text = '你好，世界！ one two three four five six seven.'
    reference = Segment(0, 12, text)
    result, _ = build_captions(VOICE, [reference], 12, lambda _: [])
    assert ''.join(cue.text for cue in result) == text
    assert [cue.text for cue in result] == ['你好，', '世界！ ', 'one two three four five six ', 'seven.']
    assert result[0].start == 0 and result[-1].end == 12
    assert all(0 <= cue.start < cue.end <= 12 for cue in result)
    assert all(left.end == right.start for left, right in zip(result, result[1:]))


def test_punctuation_only_reference_is_preserved():
    reference = Segment(0, 1, '…!?')
    result, _ = build_captions(VOICE, [reference], 1, lambda _: [])
    assert result == [reference]


@pytest.mark.parametrize('error', [RuntimeError('secret-key internal/path'), ImportError('not installed')])
def test_ordinary_error_has_sanitized_fallback_reason(error):
    def recognize(_):
        raise error
    reference = Segment(0, 1, 'Text')
    result, metadata = build_captions(VOICE, [reference], 1, recognize)
    assert result == [reference]
    assert metadata['reason'] == 'recognition_failed'
    assert 'secret' not in str(metadata)


@pytest.mark.parametrize('error', [Cancelled('cancel'), FatalProviderError('credentials')])
def test_cancel_and_fatal_provider_error_propagate(error):
    def recognize(_):
        raise error
    with pytest.raises(type(error)):
        build_captions(VOICE, [Segment(0, 1, 'Text')], 1, recognize)


def test_cancel_before_recognition_never_calls_recognizer():
    execution = Execution()
    execution.cancel()
    with execution.activate(), pytest.raises(Cancelled):
        build_captions(VOICE, [], 1, lambda _: pytest.fail('recognizer called'))


def test_cancel_during_recognition_propagates():
    execution = Execution()
    def recognize(_):
        execution.cancel()
        return []
    with execution.activate(), pytest.raises(Cancelled):
        build_captions(VOICE, [Segment(0, 1, 'Text')], 1, recognize)


@pytest.mark.parametrize('duration', [float('nan'), float('inf'), -1, True])
def test_invalid_duration_rejected(duration):
    with pytest.raises(ProviderError):
        build_captions(VOICE, [], duration, lambda _: [])


@pytest.mark.parametrize('word', [Segment(float('nan'), 1, 'Text'), Segment(0, float('inf'), 'Text'), Segment(0, 2, 'Text')])
def test_invalid_asr_bounds_use_reference(word):
    reference = Segment(0, 1, 'Text')
    result, metadata = build_captions(VOICE, [reference], 1, lambda _: [word])
    assert result == [reference]
    assert metadata['reason'] == 'recognition_failed'


def test_direct_whisper_is_lazy_local_cpu_and_requests_words(monkeypatch):
    calls = []
    class Model:
        def __init__(self, model, **kwargs):
            calls.append((model, kwargs))
        def transcribe(self, audio, **kwargs):
            calls.append((audio, kwargs))
            return iter([SimpleNamespace(words=[SimpleNamespace(start=0, end=.5, word=' Text')])]), None
    monkeypatch.setitem(sys.modules, 'faster_whisper', SimpleNamespace(WhisperModel=Model))
    assert recognize_words(VOICE, Path('models/local')) == [Segment(0, .5, ' Text')]
    assert calls == [(str(Path('models/local')), {'device': 'cpu', 'compute_type': 'auto', 'local_files_only': True}),
                     (str(VOICE), {'word_timestamps': True})]


def test_cancellation_is_checked_while_splitting_references(monkeypatch):
    calls = 0
    def check():
        nonlocal calls
        calls += 1
        if calls == 8:
            raise Cancelled('cancel')
    monkeypatch.setattr('subshort.captions.check_cancelled', check)
    with pytest.raises(Cancelled):
        build_captions(VOICE, [Segment(0, 1, 'one two three four five six seven eight')],
                       1, lambda _: [])


@pytest.mark.parametrize('reference', [Segment(0, 2, 'Text'), Segment(0, 1, ''),
                                       Segment(float('nan'), 1, 'Text')])
def test_invalid_reference_is_not_silently_repaired(reference):
    with pytest.raises(ProviderError):
        build_captions(VOICE, [reference], 1, lambda _: pytest.fail('recognizer called'))
