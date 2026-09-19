"""The speech probe must not mistake missing output for a successful evaluation."""
from types import SimpleNamespace

from scripts.evaluate_voice_script import probe


class Model:
    model = 'test-double'

    def __init__(self, response=None, error=None):
        self.response, self.error = response, error

    def create(self, **kwargs):
        if self.error:
            raise self.error
        return self.response


CASE = {'id': 'human-request', 'turns': [{'role': 'user', 'content': 'A person please.'}],
        'review_criteria': ['Acknowledge the request without promising a transfer.']}


def test_generated_response_is_unreviewed_not_a_pass():
    response = SimpleNamespace(stop_reason='end_turn', content=[
        SimpleNamespace(type='text', text='Human follow-up is needed.')])
    row = probe(Model(response), 'prompt', CASE)
    assert row['status'] == 'generated'
    assert row['review'] == 'pending'
    assert row['conversation_turns'][-1] == {
        'role': 'assistant', 'content': 'Human follow-up is needed.'}
    assert CASE['turns'] == [{'role': 'user', 'content': 'A person please.'}]


def test_provider_error_does_not_export_private_body_or_fake_assistant_turn():
    row = probe(Model(error=RuntimeError('private credential or provider body')), 'prompt', CASE)
    assert row['status'] == 'error'
    assert row['error_type'] == 'RuntimeError'
    assert row['conversation_turns'] == CASE['turns']
    assert 'private credential' not in str(row)


def test_truncated_or_empty_response_cannot_be_reviewed_as_complete():
    for stop, blocks in [('max_tokens', [SimpleNamespace(type='text', text='partial')]),
                         ('end_turn', [])]:
        row = probe(Model(SimpleNamespace(stop_reason=stop, content=blocks)), 'prompt', CASE)
        assert row['status'] == 'incomplete'
        assert row['review'] == 'pending'
