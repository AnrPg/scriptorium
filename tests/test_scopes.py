"""Decisions belong to the output profile they were taken under (core.scopes): kept apart, never undone by a switch,
carried over only when the reviewer chooses, at the switch or later; exports follow the profile in force."""
import pytest

from scientific_ocr.adapters.event_store import EventStore, fold
from scientific_ocr.core import scopes
from scientific_ocr.core.decisions import event_errors


def _dec(seq, block, action='mark_image_only', **kw):
    return {'seq': seq, 'type': 'decision', 'block_id': block, 'action': action, 'actor': 'reviewer', **kw}


def _prof(seq, name, **kw):
    return {'seq': seq, 'type': 'output_profile', 'profile': name, 'actor': 'reviewer', **kw}


def _ids(state):
    return [d['block_id'] for d in state['decisions']]


def test_history_from_before_scopes_projects_exactly_as_before():
    """No `scope`, no `carry`: events belong to the profile then in force and a switch carried everything."""
    evs = [_dec(1, 'a'), _prof(2, 'rag'), _dec(3, 'b')]
    st = fold(evs)
    assert st['scope'] == 'rag' and _ids(st) == ['a', 'b']
    assert [d['scope'] for d in st['decisions']] == ['reading', 'rag']


def test_separate_profiles_keep_their_own_decisions_and_nothing_is_undone():
    evs = [_dec(1, 'a', scope='reading'), _prof(2, 'rag', carry='separate'), _dec(3, 'b', scope='rag')]
    st = fold(evs)
    assert _ids(st) == ['b'] and st['active'] == {1, 2, 3} and st['applies'] == {2, 3}
    back = fold(evs + [_prof(4, 'reading', carry='separate')])
    assert _ids(back) == ['a']                                   # reading's decisions are all there again
    again = fold(evs + [_prof(4, 'reading', carry='separate'), _prof(5, 'rag', carry='separate')])
    assert _ids(again) == ['b']
    assert scopes.summary(evs) == {'reading': 1, 'rag': 1}


def test_copy_at_the_switch_and_later_is_bounded_and_transitive():
    evs = [_dec(1, 'a', scope='reading'), _prof(2, 'rag', carry='copy'), _dec(3, 'b', scope='rag'),
           _prof(4, 'reading', carry='separate'), _dec(5, 'c', scope='reading'), _prof(6, 'rag', carry='separate')]
    assert _ids(fold(evs)) == ['a', 'b']                         # carried up to the switch only: not c
    copied = evs + [{'seq': 7, 'type': 'profile_copy', 'from': 'reading', 'to': 'rag', 'upto': 6, 'actor': 'reviewer'}]
    assert _ids(fold(copied)) == ['a', 'b', 'c']                 # later copy: reading's decisions up to now
    undone = copied + [{'seq': 8, 'type': 'undo', 'targets': [7], 'actor': 'reviewer'}]
    assert _ids(fold(undone)) == ['a', 'b']                      # the copy itself is undoable
    assert scopes.sources('x', [('a', 'x', 10), ('b', 'a', 5)]) == {'x': float('inf'), 'a': 10, 'b': 5}
    assert scopes.sources('x', [('a', 'x', 3), ('b', 'a', 5)]) == {'x': float('inf'), 'a': 3, 'b': 3}


def test_undo_steps_only_through_what_applies_in_the_current_profile():
    evs = [_dec(1, 'a', scope='reading'), _prof(2, 'rag', carry='separate'), _dec(3, 'b', scope='rag'),
           _prof(4, 'reading', carry='separate')]
    assert fold(evs)['undo_targets'] == [4]                      # the switch, not rag's last decision
    assert fold(evs[:3])['undo_targets'] == [3]


def test_store_stamps_every_reviewer_event_with_the_profile_in_force(tmp_path):
    es = EventStore(tmp_path)
    es.append({'type': 'block_hide', 'block_id': 'p1_x'})
    es.append({'type': 'output_profile', 'profile': 'rag', 'carry': 'separate'}, {'type': 'block_hide', 'block_id': 'p1_y'})
    evs = es.load()
    assert [e.get('scope') for e in evs] == ['reading', None, 'rag']
    assert es.state()['hidden'] == ['p1_y']


def test_carry_and_copy_events_are_validated():
    assert event_errors({'type': 'output_profile', 'profile': 'rag', 'carry': 'maybe'})
    assert event_errors({'type': 'profile_copy', 'from': 'rag', 'to': 'rag'})
    assert event_errors({'type': 'profile_copy', 'from': 'nope', 'to': 'rag'})
    assert event_errors({'type': 'profile_copy', 'from': 'reading', 'to': 'rag', 'upto': 3}) == []


def test_use_cases_switch_copy_and_export_follow_the_profile(tmp_path):
    from conftest import build, make_run
    from test_application import FakeJobs

    from scientific_ocr.adapters.storage import Library
    from scientific_ocr.application.services import App, ServiceError
    out = make_run(tmp_path)
    lib = Library(tmp_path)
    lib.adopt(out)
    assert build(out) == 0
    a = App(lib, FakeJobs())
    blk = next(b for b in a.page('run', 1)['blocks'] if b['chosen'].get('type') == 'text')
    a.decide('run', blk['id'], 'mark_image_only')                                 # under 'reading'
    a.set_output_profile('run', 'rag', carry='separate')
    assert a.output_profile('run')['decisions'] == {'reading': 1, 'rag': 0}
    assert not EventStore(out).state()['decisions']
    a.set_output_profile('run', 'rag', overrides={'heading_depth': 2})             # same profile: no choice needed
    with pytest.raises(ServiceError):
        a.copy_decisions('run', 'rag')                                            # not from itself
    a.copy_decisions('run', 'reading')
    assert [d['block_id'] for d in EventStore(out).state()['decisions']] == [blk['id']]
    a.set_output_profile('run', 'reading', carry='separate')
    assert build(out) == 0
    a.export('run', 'markdown_text')                                              # built for the profile in force
