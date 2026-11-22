"""Regression tests for defects found by the project audit (each test names the failure it guards against)."""
from __future__ import annotations

import pytest
from conftest import make_run
from test_application import FakeJobs

from scientific_ocr.adapters.event_store import EventStore, fold
from scientific_ocr.adapters.pdf import parse_pages
from scientific_ocr.adapters.storage import Library
from scientific_ocr.application.projection import build
from scientific_ocr.application.services import App, ServiceError
from scientific_ocr.core.symbols import strip_math_delimiters


@pytest.fixture
def app(tmp_path):
    out = make_run(tmp_path)
    lib = Library(tmp_path)
    lib.adopt(out)
    assert build(out) == 0
    return App(lib, FakeJobs()), out


def test_strip_math_delimiters_never_joins_two_formulas():
    assert strip_math_delimiters('$x^2$') == 'x^2'
    assert strip_math_delimiters('$$E=mc^2$$') == 'E=mc^2'
    assert strip_math_delimiters('$x$ and $y$') == '$x$ and $y$'          # was 'x$ and $y'
    assert strip_math_delimiters('$$a$$ + $$b$$') == '$$a$$ + $$b$$'
    assert strip_math_delimiters('\\(a\\) b \\(c\\)') == '\\(a\\) b \\(c\\)'


def test_parse_pages_rejects_huge_range_without_building_it():
    with pytest.raises(ValueError):
        parse_pages('1-9999999999', 10)                                  # used to hang the request thread


def test_reviewer_undo_skips_system_events(tmp_path):
    es = EventStore(tmp_path)
    es.append({'type': 'block_hide', 'block_id': 'b1'}, actor='reviewer')
    es.append({'type': 'page_profile', 'page': 1, 'profile': 'diligent', 'auto': True}, actor='system:auto-diligent')
    st = fold(es.load())
    assert st['undo_targets'] == [1]                                      # the reviewer's own action, not the system's
    es.undo()
    st = fold(es.load())
    assert st['hidden'] == [] and st['profiles'] == {1: 'diligent'}


def test_removing_unknown_ids_records_nothing(app):
    a, out = app
    n = len(EventStore(out).load())
    for call in (lambda: a.ungroup('run', 'nope'), lambda: a.unmerge('run', 'nope'),
                 lambda: a.remove_region('run', 'nope'), lambda: a.show_block('run', 'nope'),
                 lambda: a.remove_rule('run', 'nope')):
        with pytest.raises(ServiceError):
            call()
    assert len(EventStore(out).load()) == n                              # no no-op event stealing the next undo


def test_class_acceptance_bounds_threshold_and_requires_the_drawn_sample(app):
    a, _ = app
    sm = a.sample('run', 'MISSING_BOTTOM_TEXT')
    with pytest.raises(ServiceError):
        a.accept_class('run', 'MISSING_BOTTOM_TEXT', [{'block_id': i['id'], 'ok': True} for i in sm['items']], max_error=1)
    with pytest.raises(ServiceError):
        a.accept_class('run', 'MISSING_BOTTOM_TEXT', [{'block_id': 'made-up', 'ok': True}])


def test_image_only_folder_imports(tmp_path, monkeypatch):
    from PIL import Image
    home = tmp_path / 'home'
    scans = home / 'scans'
    scans.mkdir(parents=True)
    for i in (1, 2):
        Image.new('RGB', (200, 280), 'white').save(scans / f'p{i}.png')
    a = App(Library(tmp_path / 'lib'), FakeJobs())
    monkeypatch.setattr(App, 'FS_ROOT', home)
    res = a.import_paths([str(scans)])                                   # used to fail: 'no PDF or page images found'
    assert len(res) == 1


def test_caption_without_text_keeps_its_crop_in_markdown():
    from scientific_ocr.pipeline import markdown, reading
    cap = {'id': 'c1', 'page': 1, 'kind': 'caption', 'chosen': {'type': 'image_only'}, 'relations': {},
           'source_crop': {'path': 'crops/c1.png'}, 'reasons': []}
    n = reading._node(cap, {'c1': cap})
    assert n['caption_images'] == ['crops/c1.png']
    md = markdown._figure(n, True, 'tags', reading.LABELS['el'], lambda p: p)
    assert 'crops/c1.png' in md                                          # was an empty <figure></figure>


def test_remove_segments_is_one_undo(app):
    a, out = app
    blocks = a.lib.get('run').canonical()['blocks']
    ids = [b['id'] for b in blocks if b.get('source') not in ('region', 'merge')][:2]
    assert len(ids) == 2
    a.remove_segments('run', ids)
    assert set(EventStore(out).state()['hidden']) == set(ids)             # both, in one step
    a.undo('run')                                                         # one undo brings both back
    assert EventStore(out).state()['hidden'] == []
    with pytest.raises(ServiceError):
        a.remove_segments('run', ['nope'])


def test_move_segments_moves_a_selection_together_in_one_undo(app):
    a, out = app
    blocks = [b for b in a.lib.get('run').canonical()['blocks'] if not b.get('excluded')]
    page = blocks[0]['page']
    same = [b['id'] for b in blocks if b['page'] == page]
    assert len(same) >= 4
    sel, target = [same[0], same[2]], same[-1]
    a.move_segments('run', sel, after=target)
    evs = [e for e in EventStore(out).load() if e['type'] == 'decision']
    assert [e['block_id'] for e in evs[-2:]] == sel and len({e.get('group') for e in evs[-2:]}) == 1
    with pytest.raises(ServiceError):
        a.move_segments('run', sel, after=sel[0])                         # target inside the selection
    with pytest.raises(ServiceError):
        a.move_segments('run', sel)                                       # no destination
