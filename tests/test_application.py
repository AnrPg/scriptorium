"""Application services, HTTP adapter and architecture rules (no OCR engines run)."""
import ast
import json
import re
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from conftest import build, load, make_run

from scientific_ocr.adapters.storage import Library
from scientific_ocr.application.services import App, ServiceError


class FakeJobs:
    """Stands in for JobQueue: records submissions instead of spawning processes."""
    def __init__(self):
        self.submitted = []

    def submit(self, book_id, kind, args, label, lane='heavy'):
        self.submitted.append((kind, lane))
        return {'id': 'j1', 'book': book_id, 'kind': kind, 'state': 'queued'}

    def busy(self, book_id):
        return None

    def list(self, book_id=None):
        return []


@pytest.fixture
def app(tmp_path):
    out = make_run(tmp_path)                       # tmp_path/run  -> library root tmp_path, book id 'run'
    lib = Library(tmp_path)
    lib.adopt(out)
    assert build(out) == 0
    return App(lib, FakeJobs()), out


def test_book_and_page_queries(app):
    a, out = app
    assert [b['id'] for b in a.books()] == ['run']
    bk = a.book('run')
    assert bk['page_count'] == 1 and bk['pages'][0]['state'] == 'needs_review'
    pg = a.page('run', 1)
    assert pg['dims'] == [600, 800] and all('bbox' in b and 'crop' in b for b in pg['blocks'])
    assert {x['review'] for x in a.queue('run', 'required')} == {'required'}
    assert a.page_image('run', 1, 300).exists()


def test_decide_undo_redo_cycle(app):
    a, out = app
    blk = next(b for b in a.page('run', 1)['blocks'] if 'SINGLE_ENGINE_DOCLING' in b['reasons'])
    a.decide('run', blk['id'], 'replace_text', value='fixed text')
    assert a.jobs.submitted[-1] == ('reproject', 'light')          # pipeline never waits for review
    build(out)
    assert next(b for b in load(out)[0]['blocks'] if b['id'] == blk['id'])['chosen']['text'] == 'fixed text'
    assert a.book('run')['can_undo']
    a.undo('run')
    build(out)
    assert next(b for b in load(out)[0]['blocks'] if b['id'] == blk['id'])['chosen']['type'] == 'image_only'
    assert a.book('run')['can_redo']
    with pytest.raises(ServiceError):
        a.decide('run', blk['id'], 'rm -rf')
    with pytest.raises(ServiceError):
        a.decide('run', blk['id'], 'replace_text', value='  ')


def test_approve_any_block_even_if_not_flagged(app):
    a, out = app
    ok = next(b for b in load(out)[0]['blocks'] if b['review'] in ('not_needed', 'recommended') and not b.get('excluded'))
    a.decide('run', ok['id'], 'approve')
    build(out)
    assert next(b for b in load(out)[0]['blocks'] if b['id'] == ok['id'])['review'] == 'approved'


def test_report_wrong_reocr_as_region_and_keeps_note(app):
    a, out = app
    para = next(b for b in load(out)[0]['blocks'] if b['chosen'].get('text', '').startswith('Alpha'))
    res = a.report_wrong('run', para['id'], note='second line is missing')
    build(out)
    c, _ = load(out)
    assert para['id'] not in {b['id'] for b in c['blocks']}             # original hidden (listed in page info)
    new = next(b for b in c['blocks'] if b['id'] == res['new_block'])
    assert new['source'] == 'region' and 'HUMAN_REPORTED_WRONG' in new['reasons']
    assert new['human_notes'][0]['note'] == 'second line is missing' and new['review'] == 'required'
    a.undo('run')                                                       # one undo reverts the whole action
    build(out)
    assert para['id'] in {b['id'] for b in load(out)[0]['blocks']}


def test_rule_preview_add_remove(tmp_path):
    from conftest import docling_json, marker_json
    m, d = marker_json(), docling_json()
    m['children'][0]['children'][1]['html'] = '<p>Alpha paragraph line one about base Γ and DNA</p>'
    d['texts'][0]['text'], d['texts'][1]['text'] = 'Alpha paragraph line one about base 6 and DNA', ''
    out = make_run(tmp_path, marker=m, docling=d)
    lib = Library(tmp_path)
    lib.adopt(out)
    build(out)
    a = App(lib, FakeJobs())
    assert len(a.preview_rule('run', {'find': 'Γ', 'replace': 'G'})) == 1
    rule = a.add_rule('run', {'find': 'Γ', 'replace': 'G'})
    a.add_rule('run', {'find': 'DNA', 'replace': 'ΔΝΑ'})
    build(out)
    para = next(b for b in load(out)[0]['blocks'] if b.get('rule_corrections'))
    assert 'base G and DNA' in para['chosen']['text']          # Γ fixed; DNA kept: Docling confirms "DNA"
    a.remove_rule('run', rule['id'])
    build(out)
    assert 'base Γ' in next(b for b in load(out)[0]['blocks'] if b['id'] == para['id'])['chosen']['text']


def test_region_validation_and_suggestion_errors(app):
    a, out = app
    with pytest.raises(ServiceError):
        a.add_region('run', 1, [10, 10, 12, 12], {'engine': 'marker', 'name': 'Text'})
    with pytest.raises(ServiceError):
        a.add_region('run', 1, [10, 10, 200, 200], {'engine': 'marker', 'name': 'Poem'})
    r = a.add_region('run', 1, [-5, 70, 900, 190], {'engine': 'docling', 'name': 'section_header'})
    assert r['bbox'][0] == 0 and r['bbox'][2] == 600                   # clamped to the page
    with pytest.raises(ServiceError):
        a.accept_suggestion('run', 1, 9)


@pytest.mark.parametrize('fmt,magic', [('markdown_images', b'PK'), ('markdown_text', b''), ('pdf', b'%PDF'),
                                       ('normalized_json', b'{'), ('canonical_json', b'{')])
def test_exports(app, fmt, magic):
    a, out = app
    data, ctype, name = a.export('run', fmt)
    assert data.startswith(magic) and name
    if fmt == 'markdown_text':
        assert b'![' not in data and (b'[Figure]' in data or '[Εικόνα]'.encode() in data)
    if fmt in ('markdown_text', 'markdown_images'):                     # reader-facing: no traces of the pipeline
        import io
        import zipfile
        text = data.decode() if fmt == 'markdown_text' else \
            next(zipfile.ZipFile(io.BytesIO(data)).read(n).decode() for n in zipfile.ZipFile(io.BytesIO(data)).namelist() if n.endswith('.md'))
        assert not re.search(r'p\d+_[0-9a-f]{8}|OCR|BLOCK_|review|provenance|release_status', text)
    if fmt == 'markdown_images':
        import io
        import zipfile
        names = zipfile.ZipFile(io.BytesIO(data)).namelist()
        assert all(n.endswith('.md') or n.startswith('images/') for n in names)        # only the document + its images
    if fmt == 'normalized_json':
        segs = json.loads(data)['segments']
        assert segs and all({'page', 'order', 'content_type', 'review'} <= set(s) for s in segs)


def test_library_dedupe_trash_restore(tmp_path):
    from conftest import make_pdf
    lib = Library(tmp_path / 'lib')
    p1, p2 = tmp_path / 'a.pdf', tmp_path / 'b.pdf'
    make_pdf(p1)
    p2.write_bytes(p1.read_bytes())
    b1, created = lib.create_from_pdf(p1, 'Βιολογία Κεφ. 5.pdf')
    assert created and b1.settings()['title'] == 'Βιολογία Κεφ. 5'
    b2, created2 = lib.create_from_pdf(p2, 'copy.pdf')
    assert not created2 and b2.id == b1.id                              # same content -> same book
    name = lib.trash(b1.id)
    assert not lib.books()
    lib.restore(name)
    assert [b.id for b in lib.books()] == [b1.id]


# ---------------------------------------------------------------- HTTP adapter
@pytest.fixture
def server(app):
    from http.server import ThreadingHTTPServer

    from scientific_ocr.adapters.http.server import Handler
    Handler.app = app[0]
    srv = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{srv.server_address[1]}'
    srv.shutdown()


def _req(url, method='GET', body=None, headers=None):
    r = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                               headers=headers or {})
    try:
        with urllib.request.urlopen(r) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_http_security_and_routes(server):
    assert _req(server + '/api/books')[0] == 200
    assert _req(server + '/api/books/run/undo', 'POST', {})[0] == 403                   # CSRF header missing
    assert _req(server + '/api/books', headers={'Host': 'evil.example'})[0] == 403    # DNS rebinding
    assert _req(server + '/api/books/run/undo', 'POST', {}, {'X-OCR-Client': '1'})[0] == 200
    assert _req(server + '/api/books/run/assets/../book.json')[0] == 404
    assert _req(server + '/api/books/nope')[0] == 400
    st, data = _req(server + '/')
    assert st == 200 and b'<html' in data
    st, data = _req(server + '/api/books/run/settings', 'POST', {'ocr_lang': 'not a language!'}, {'X-OCR-Client': '1'})
    assert st == 400 and b'ocr_lang' in data                                            # validation error -> 400 JSON
    raw = urllib.request.Request(server + '/api/books/run/settings', method='POST', data=b'{not json',
                                 headers={'X-OCR-Client': '1', 'Content-Type': 'application/json'})
    try:
        urllib.request.urlopen(raw)
        assert False, 'bad JSON accepted'
    except urllib.error.HTTPError as e:
        assert e.code == 400 and b'not valid JSON' in e.read()


# ---------------------------------------------------------------- architecture (ports & adapters)
PKG = Path(__file__).resolve().parent.parent / 'scientific_ocr'


def _imports(path: Path) -> set[str]:
    mods = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom) and node.level:
            parts = path.relative_to(PKG).parent.parts
            base = parts[:len(parts) - node.level + 1]
            mods.add('.'.join(base + tuple((node.module or '').split('.'))).strip('.'))
    return mods


@pytest.mark.parametrize('layer,forbidden', [
    ('core', ('adapters', 'pipeline', 'application', 'config')),
    ('pipeline', ('application', 'adapters')),
    ('adapters', ('application',)),
])
def test_dependency_rule(layer, forbidden):
    """Dependencies point inward: core is pure; pipeline stages depend only on core and ports (I/O is injected)."""
    for f in (PKG / layer).rglob('*.py'):
        if layer == 'adapters' and 'http' in f.parts:
            continue                                   # the driving HTTP adapter calls application services
        bad = [m for m in _imports(f) if m.split('.')[0] in forbidden]
        assert not bad, f'{f.relative_to(PKG)} imports {bad}'


def test_local_file_picker_is_confined_and_copies(tmp_path, monkeypatch):
    from conftest import make_pdf
    home = tmp_path / 'home'
    (home / 'Books' / 'scans').mkdir(parents=True)
    make_pdf(home / 'Books' / 'chem.pdf')
    lib = Library(tmp_path / 'lib')
    a = App(lib, FakeJobs())
    monkeypatch.setattr(App, 'FS_ROOT', home)
    listing = a.browse(str(home / 'Books'))
    assert [d['name'] for d in listing['dirs']] == ['scans'] and listing['files'][0]['name'] == 'chem.pdf'
    with pytest.raises(ServiceError):
        a.browse(str(tmp_path))                                           # outside home
    res = a.import_paths([str(home / 'Books' / 'chem.pdf')])
    assert res[0]['created'] and (home / 'Books' / 'chem.pdf').exists()   # original untouched


class FakeInterpreter:
    name = 'fake-vlm'

    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def complete_json(self, system, user, schema, image_png=None):
        self.calls.append((system, user, image_png))
        return {'json': self.answer, 'raw': json.dumps(self.answer), 'model': self.name, 'seconds': 0.1}


def test_note_to_proposal_to_applied_decision(app):
    a, out = app
    para = next(b for b in load(out)[0]['blocks'] if b['chosen'].get('text', '').startswith('Alpha'))
    answer = {'understanding': 'Ο ελεγκτής λέει ότι λείπει μια λέξη.', 'action': 'replace_text',
              'value': 'Alpha paragraph line one about DNA and RNA', 'rule': None, 'label': None, 'level': None,
              'confidence': 'medium', 'clarification': None}
    a._interpreter = FakeInterpreter(answer)
    res = a.interpret_note('run', para['id'], 'λείπει το "and RNA"')
    assert res['valid'] and 'and RNA' in res['description'] and res['model'] == 'fake-vlm'
    assert a._interpreter.calls[0][2]                                     # the segment image was sent
    assert (out / 'proposals.jsonl').exists()                             # audit trail
    ev = a.apply_proposal('run', para['id'], res['proposal'], 'λείπει το "and RNA"', res['model'])
    assert ev['via']['proposed_by'] == 'fake-vlm' and ev['action'] == 'replace_text'
    build(out)
    assert next(b for b in load(out)[0]['blocks'] if b['id'] == para['id'])['chosen']['text'].endswith('and RNA')
    bad = {**answer, 'action': 'relabel', 'label': {'engine': 'marker', 'name': 'Poem'}}
    assert not a.check_proposal('run', para['id'], bad)['valid']
    with pytest.raises(ServiceError):
        a.apply_proposal('run', para['id'], bad, 'x', 'fake-vlm')         # invalid proposals are never applied


def test_lineage_and_raw_fragment(app):
    a, out = app
    c, _ = load(out)
    para = next(b for b in c['blocks'] if b['chosen'].get('text', '').startswith('Alpha'))
    assert para['content_source'] == 'marker'
    stages = [s['stage'] for s in para['lineage']]
    assert stages[0] == 'detected' and 'read' in stages and 'chosen' in stages
    reads = [s for s in para['lineage'] if s['stage'] == 'read']
    assert {s['by'] for s in reads} == {'marker', 'docling'} and all(s['raw_file'] for s in reads)
    frag = a.raw_fragment('run', reads[0]['raw_file'], reads[0]['raw_pointer'])['fragment']
    assert 'Alpha' in json.dumps(frag)                                     # exactly the engine's own output
    with pytest.raises(KeyError):
        a.raw_fragment('run', '../book.json', None)
    only = next(b for b in c['blocks'] if 'SINGLE_ENGINE_DOCLING' in b['reasons'])
    assert only['content_source'] == 'source image' and only['lineage'][0]['by'] == 'docling'


def test_relabel_twice_leaves_one_segment_and_one_undo_reverts(app):
    """Re-tagging the same box repeatedly must never duplicate it (observed: 'ΣΧΗΜΑ 4.9' appeared twice)."""
    a, out = app
    cap = next(b for b in load(out)[0]['blocks'] if b['kind'] == 'caption')
    a.relabel('run', cap['id'], {'engine': 'marker', 'name': 'SectionHeader'})
    build(out)
    first = next(b for b in load(out)[0]['blocks'] if b['source'] == 'region')
    a.relabel('run', first['id'], {'engine': 'docling', 'name': 'caption'})
    build(out)
    regs = [b for b in load(out)[0]['blocks'] if b['source'] == 'region']
    assert len(regs) == 1 and regs[0]['engine_label'] == 'docling:caption'
    a.undo('run')
    build(out)
    regs = [b for b in load(out)[0]['blocks'] if b['source'] == 'region']
    assert len(regs) == 1 and regs[0]['engine_label'] == 'marker:SectionHeader'


def test_later_region_supersedes_earlier_overlapping_region(app):
    a, out = app
    from scientific_ocr.adapters.event_store import EventStore
    box = [50, 75, 410, 125]
    EventStore(out).append({'type': 'region_add', 'region': {'id': 'g1', 'page': 1, 'bbox': box, 'kind': 'text'}},
                           {'type': 'region_add', 'region': {'id': 'g2', 'page': 1, 'bbox': box, 'kind': 'heading'}})
    build(out)
    regs = [b for b in load(out)[0]['blocks'] if b['source'] == 'region']
    assert [r['region_id'] for r in regs] == ['g2']


def test_service_guards_versions_and_history(app):
    a, out = app
    blk = next(b for b in load(out)[0]['blocks'] if 'SINGLE_ENGINE_DOCLING' in b['reasons'])
    with pytest.raises(ServiceError):
        a.decide('run', blk['id'], 'revert')                          # nothing to revert yet
    a.decide('run', blk['id'], 'replace_text', value='fixed')
    build(out)
    with pytest.raises(ServiceError):
        a.decide('run', blk['id'], 'approve')                         # cannot approve over a human value
    a.decide('run', blk['id'], 'revert')
    build(out)
    assert next(b for b in load(out)[0]['blocks'] if b['id'] == blk['id'])['chosen']['type'] == 'image_only'
    hist = a.block_history('run', blk['id'])
    assert [e['action'] for e in hist['events']] == ['replace_text', 'revert'] and hist['machine']


def test_merge_parts_into_one_paragraph_and_split_back(tmp_path):
    """A paragraph split by the layout model into two pieces becomes ONE segment; parts stay in history."""
    from conftest import marker_json
    m = marker_json()
    kids = m['children'][0]['children']
    kids[1]['html'] = '<p>The first half of a sentence that the layout cut in the mid-</p>'
    kids.insert(2, {'id': '/page/0/Text/8', 'block_type': 'Text', 'bbox': [55, 121, 400, 135],
                    'html': '<p>dle and the second half here.</p>', 'children': None, 'images': {}})
    out = make_run(tmp_path, marker=m)
    lib = Library(tmp_path)
    lib.adopt(out)
    build(out)
    a = App(lib, FakeJobs())
    c, _ = load(out)
    p1 = next(b for b in c['blocks'] if b['chosen'].get('text', '').startswith('The first'))
    p2 = next(b for b in c['blocks'] if b['chosen'].get('text', '').startswith('dle and'))
    a.decide('run', p2['id'], 'replace_text', value='dle and the second half, reviewed.')    # members keep reviews
    build(out)
    mg = a.merge('run', [p1['id'], p2['id']], {'engine': 'marker', 'name': 'Text'})   # the order given is kept
    build(out)
    c, md = load(out)
    merged = next(b for b in c['blocks'] if b.get('merge_id') == mg['id'])
    assert merged['chosen']['text'] == 'The first half of a sentence that the layout cut in the middle and the second half, reviewed.'
    assert {p1['id'], p2['id']}.isdisjoint({b['id'] for b in c['blocks']})                # parts left the flow
    assert [x['id'] for x in merged['merged_from']] == [p1['id'], p2['id']]                # ...but are recorded
    assert any(s['stage'] == 'merged' for s in merged['lineage']) and 'cut in the middle and the second half, reviewed.' in md
    a.decide('run', merged['id'], 'replace_text', value='One reviewed paragraph.')        # decide on the merged entity
    build(out)
    assert next(b for b in load(out)[0]['blocks'] if b['id'] == merged['id'])['chosen']['text'] == 'One reviewed paragraph.'
    a.unmerge('run', mg['id'])
    build(out)
    ids = {b['id'] for b in load(out)[0]['blocks']}
    assert {p1['id'], p2['id']} <= ids and merged['id'] not in ids
    with pytest.raises(ServiceError):
        a.merge('run', [p1['id']], {'engine': 'marker', 'name': 'Text'})


def test_merged_caption_fragments_become_one_caption_of_the_figure(app):
    a, out = app
    c, _ = load(out)
    cap = next(b for b in c['blocks'] if b['kind'] == 'caption')
    fig = next(b for b in c['blocks'] if b['kind'] == 'figure')
    para = next(b for b in c['blocks'] if b['chosen'].get('text', '').startswith('Alpha'))
    mg = a.merge('run', [cap['id'], para['id']], {'engine': 'marker', 'name': 'Caption'})
    build(out)
    c, md = load(out)
    merged = next(b for b in c['blocks'] if b.get('merge_id') == mg['id'])
    assert merged['kind'] == 'caption'
    a.decide('run', merged['id'], 'associate_caption', figure_id=fig['id'])
    build(out)
    c, md = load(out)
    f2 = next(b for b in c['blocks'] if b['id'] == fig['id'])
    assert f2['relations']['captions'] == [merged['id']]


def test_group_annotates_members_without_changing_them(app):
    """A group is metadata: members keep ids/types/content; output declares the group and tags members."""
    a, out = app
    c, _ = load(out)
    fig = next(b for b in c['blocks'] if b['kind'] == 'figure')
    para = next(b for b in c['blocks'] if b['chosen'].get('text', '').startswith('Alpha'))
    before = {b['id']: (b['kind'], b['chosen']) for b in c['blocks']}
    g = a.group('run', [fig['id'], para['id']], 'example', 'Παράδειγμα 4.3')
    build(out)
    c, md = load(out)
    assert {b['id']: (b['kind'], b['chosen']) for b in c['blocks']} == before            # nothing changed
    assert c['groups'][0]['members'] == [fig['id'], para['id']]                           # the order given
    assert all(g['id'] in next(b for b in c['blocks'] if b['id'] == i)['groups'] for i in (fig['id'], para['id']))
    issues = [i for i in c['document_structure']['issues'] if i['type'] == 'group_not_contiguous']
    assert (md.count('<aside>') == md.count('</aside>') == 1) != bool(issues)         # boxed if contiguous, else reported
    a.update_group('run', g['id'], [para['id']])                                          # < 2 members -> dissolved
    build(out)
    assert load(out)[0]['groups'] == []
    with pytest.raises(ServiceError):
        a.group('run', [para['id']], 'example')


def _book_with(tmp_path, extra_blocks):
    from conftest import marker_json
    m = marker_json()
    m['children'][0]['children'] += extra_blocks
    out = make_run(tmp_path, marker=m)
    lib = Library(tmp_path)
    lib.adopt(out)
    build(out)
    return App(lib, FakeJobs()), out


def test_auto_group_for_numbered_box_is_dismissable_and_keepable(tmp_path):
    a, out = _book_with(tmp_path, [
        {'id': '/page/0/SectionHeader/20', 'block_type': 'SectionHeader', 'bbox': [55, 400, 300, 415],
         'html': '<h3>Example 4.3</h3>', 'children': None, 'images': {}},
        {'id': '/page/0/Text/21', 'block_type': 'Text', 'bbox': [55, 420, 500, 470],
         'html': '<p>Classify the following compounds as strong or weak acids.</p>', 'children': None, 'images': {}}])
    c, md = load(out)
    auto = [g for g in c['groups'] if g['source'] == 'auto']
    assert len(auto) == 1 and auto[0]['type'] == 'labelled_box' and auto[0]['name'] == 'Example 4.3'
    texts = [next(b for b in c['blocks'] if b['id'] == m)['chosen'].get('text', '') for m in auto[0]['members']]
    assert texts[0].endswith('Example 4.3') and texts[1].startswith('Classify') and '<aside>' in md   # box = its section
    a.keep_group('run', auto[0]['key'], 'example')
    build(out)
    gs = load(out)[0]['groups']
    assert [(g['source'], g['type']) for g in gs] == [('human', 'example')]  # the reviewer's version wins
    a.undo('run')
    build(out)
    a.dismiss_suggestion('run', auto[0]['key'])
    build(out)
    assert load(out)[0]['groups'] == []                                      # dismissal persists across rebuilds


def test_split_paragraph_is_suggested_never_merged_automatically(tmp_path):
    a, out = _book_with(tmp_path, [
        {'id': '/page/0/Text/30', 'block_type': 'Text', 'bbox': [55, 500, 500, 520],
         'html': '<p>A sentence that the layout cut in two parts and that</p>', 'children': None, 'images': {}},
        {'id': '/page/0/Text/31', 'block_type': 'Text', 'bbox': [55, 522, 500, 540],
         'html': '<p>continues in the next segment.</p>', 'children': None, 'images': {}}])
    c, _ = load(out)
    sugg = [s for p in c['pages'] for s in p['suggestions'] if s['kind'] == 'merge_join']
    assert len(sugg) == 1 and not any(b.get('merge_id') for b in c['blocks'])  # suggested, not applied
    a.accept_suggestion('run', 1, sugg[0]['key'])
    build(out)
    c, _ = load(out)
    merged = [b for b in c['blocks'] if b.get('merge_id')]
    assert len(merged) == 1 and merged[0]['chosen']['text'].endswith('that continues in the next segment.')


def test_model_structure_proposals_are_validated_and_not_applied(app):
    a, out = app
    c, _ = load(out)
    fig = next(b for b in c['blocks'] if b['kind'] == 'figure')
    para = next(b for b in c['blocks'] if b['chosen'].get('text', '').startswith('Alpha'))
    cap = next(b for b in c['blocks'] if b['kind'] == 'caption')
    a._interpreter = FakeInterpreter({'understanding': 'Δύο προτάσεις.', 'operations': [
        {'op': 'merge', 'members': [fig['id'], para['id']], 'label': {'engine': 'marker', 'name': 'Text'}, 'reason': 'x'},
        {'op': 'group', 'members': [fig['id'], cap['id']], 'type': 'figure_group', 'name': '', 'reason': 'y'}]})
    res = a.propose_structure('run', 1)
    assert [op['valid'] for op in res['operations']] == [False, True]          # figure+text merge rejected
    assert 'segments' not in a._interpreter.calls[0][1][:0] and a._interpreter.calls[0][2]   # page image sent
    assert not load(out)[0]['groups']                                         # nothing applied by the model
    a.apply_structure('run', res['operations'][1])
    build(out)
    assert [g['type'] for g in load(out)[0]['groups']] == ['figure_group']


def test_bulk_decide_skips_human_values_and_is_one_undo(app):
    a, out = app
    c, _ = load(out)
    ids = [b['id'] for b in c['blocks'] if b['review'] in ('required', 'recommended')]
    a.decide('run', ids[0], 'replace_text', value='mine')
    build(out)
    r = a.bulk_decide('run', ids, 'approve')
    assert r['skipped'] >= 1 and r['applied'] == len(ids) - 1
    build(out)
    assert next(b for b in load(out)[0]['blocks'] if b['id'] == ids[0])['chosen']['text'] == 'mine'
    a.undo('run')
    build(out)
    assert all(not b.get('decisions') for b in load(out)[0]['blocks'] if b['id'] in ids[1:])


def test_sampling_flow_and_guardrails(app):
    a, out = app
    cls = {c['reason']: c for c in a.classes('run')['classes']}
    assert 'MISSING_BOTTOM_TEXT' in cls and not cls['MISSING_BOTTOM_TEXT']['critical']
    sm = a.sample('run', 'MISSING_BOTTOM_TEXT')
    assert sm['items'] and sm['population'] >= len(sm['items'])
    res = a.accept_class('run', 'MISSING_BOTTOM_TEXT', [{'block_id': i['id'], 'ok': True} for i in sm['items']])
    assert not res['passed']                    # 1 sample cannot prove <=5% error: honest, no acceptance
    with pytest.raises(ServiceError):
        a.sample('run', 'SYMBOL_READING_CONFLICT')


def test_priority_queue_puts_critical_and_equations_first(app):
    a, out = app
    q = a.queue('run', 'required')
    flags = [x['critical'] for x in q]
    assert flags == sorted(flags, reverse=True)


def test_rule_suggestion_after_repeated_corrections(tmp_path):
    from conftest import marker_json
    m = marker_json()
    kids = m['children'][0]['children']
    kids[1]['html'] = '<p>Η βάση Γ ζευγαρώνει με την C.</p>'
    kids.append({'id': '/page/0/Text/40', 'block_type': 'Text', 'bbox': [55, 400, 500, 420],
                 'html': '<p>Ξανά η βάση Γ εδώ.</p>', 'children': None, 'images': {}})
    kids.append({'id': '/page/0/Text/41', 'block_type': 'Text', 'bbox': [55, 430, 500, 450],
                 'html': '<p>Και τρίτη φορά Γ.</p>', 'children': None, 'images': {}})
    out = make_run(tmp_path, marker=m)
    lib = Library(tmp_path)
    lib.adopt(out)
    build(out)
    a = App(lib, FakeJobs())
    c, _ = load(out)
    p1 = next(b for b in c['blocks'] if b['chosen'].get('text', '').startswith('Η βάση'))
    p2 = next(b for b in c['blocks'] if b['chosen'].get('text', '').startswith('Ξανά'))
    a.decide('run', p1['id'], 'replace_text', value='Η βάση G ζευγαρώνει με την C.')
    a.decide('run', p2['id'], 'replace_text', value='Ξανά η βάση G εδώ.')
    build(out)
    sug = load(out)[0]['rule_suggestions']
    assert sug and sug[0]['find'] == 'Γ' and sug[0]['replace'] == 'G' and sug[0]['would_change'] == 1


def test_auto_diligent_selects_pages_with_layout_trouble(tmp_path):
    from scientific_ocr.application.processing import auto_diligent_pages
    out = make_run(tmp_path, marker_status='FAILED')               # full-page fallback: layout trouble
    Library(tmp_path).adopt(out)
    build(out)
    pages = auto_diligent_pages(out, [1])
    c, _ = load(out)
    n = sum(1 for b in c['blocks'] if b['review'] == 'required')
    assert pages == ([1] if n >= 3 else [])
    import json as _j
    (out / 'book.json').write_text(_j.dumps({**_j.loads((out / 'book.json').read_text()), 'auto_diligent': False}))
    assert auto_diligent_pages(out, [1]) == []


def test_page_output_preview_and_markdown(app):
    a, out = app
    o = a.page_output('run', 1)
    c, md = load(out)
    for b in c['blocks']:
        if not b.get('excluded') and b.get('flow') != 'furniture' and b['kind'] != 'caption' and not b['relations'].get('continues_from'):
            assert f'data-seg="{b["id"]}"' in o['html']                               # every segment linkable (reviewer view)
    assert 'id=' not in o['markdown'] and 'data-seg' not in o['markdown']             # the export slice is clean
    assert 'Figure 1. A caption' in o['html'] and '<figcaption' in o['html']           # caption inside its figure
    assert '<script' not in o['html']
    with pytest.raises(ServiceError):
        a.page_output('run', 9)


def test_model_structure_pass_only_proposes_and_reviewer_decides(app):
    """Second pass with a (fake) local model: constrained answers -> voted proposals; nothing applied until
    accepted; accept = an ordinary undoable decision; reject = remembered dismissal."""
    from scientific_ocr.application import structure_llm
    from scientific_ocr.pipeline import structure_review as sr
    a, out = app
    para = next(b for b in load(out)[0]['blocks'] if b['kind'] == 'paragraph' and b['chosen'].get('text'))
    a.decide('run', para['id'], 'replace_text', value='**ΠΑΡΑΔΕΙΓΜΑ 1**')
    build(out)
    c, _ = load(out)
    assert any(t['id'] == para['id'] for t in sr.targets(c))          # bold short line = heading-like target

    class Fake:
        name = 'fake'
        def complete_json(self, system, user, schema, **kw):
            assert 'TARGET:' in user and 'PREVIOUS' in user and 'FOLLOWING' in user and 'ALLOWED PARENTS' in user
            assert schema['properties']['parent']['enum'][0] == 'ROOT'
            return {'json': {'role': 'box_label', 'parent': 'ROOT', 'confidence': 'high', 'reason': 'πλαίσιο'}}
    assert structure_llm.run(out, samples=3, interpreter=Fake()) == 0
    r = a.structure_proposals('run')
    p = next(x for x in r['proposals'] if x['block_id'] == para['id'])
    assert p['votes'] == 3 and p['proposed'] == {**p['proposed'], 'role': 'box_label', 'level': 1}
    assert next(b for b in load(out)[0]['blocks'] if b['id'] == para['id'])['kind'] == 'paragraph'   # not applied
    a.accept_structure_proposal('run', p['key'])
    build(out)
    assert next(b for b in load(out)[0]['blocks'] if b['id'] == para['id'])['kind'] == 'heading'
    assert not any(x['key'] == p['key'] for x in a.structure_proposals('run')['proposals'])   # settled
    a.undo('run'); build(out)
    assert any(x['key'] == p['key'] for x in a.structure_proposals('run')['proposals'])       # undo reopens it
    a.dismiss_suggestion('run', p['key'])
    assert not any(x['key'] == p['key'] for x in a.structure_proposals('run')['proposals'])   # rejection remembered


def test_reader_reorders_reading_order_before_or_after_and_undo(app):
    a, out = app
    c, _ = load(out)
    ids = [b['id'] for b in c['blocks'] if b['page'] == 1 and not b.get('excluded')]
    last, first = ids[-1], ids[0]
    a.decide('run', last, 'move_after_block', before=first)                 # to the top of the page
    build(out)
    now = [b['id'] for b in load(out)[0]['blocks'] if b['page'] == 1 and not b.get('excluded')]
    assert now[0] == last and now[1:] == ids[:-1]                            # everything else keeps its order
    a.decide('run', last, 'move_after_block', after=ids[1])
    build(out)
    now = [b['id'] for b in load(out)[0]['blocks'] if b['page'] == 1 and not b.get('excluded')]
    assert now.index(last) == now.index(ids[1]) + 1
    a.undo('run'); a.undo('run'); build(out)
    assert [b['id'] for b in load(out)[0]['blocks'] if b['page'] == 1 and not b.get('excluded')] == ids
    with pytest.raises(ServiceError):
        a.decide('run', last, 'move_after_block', after=last)


def test_reviewer_classifies_segments_by_type_or_family_and_exports_follow(app):
    """entity_assign: the reviewer's own category (fine type or whole family) on ordered segments — verified,
    on every member block, usable by export filters by type and by family; withdrawable; undoable."""
    a, out = app
    c, _ = load(out)
    para = next(b for b in c['blocks'] if b['chosen'].get('text', '').startswith('Alpha'))
    with pytest.raises(ServiceError):
        a.assign_entity('run', [para['id']], 'not_a_type')
    with pytest.raises(ServiceError):
        a.assign_entity('run', ['nope'], 'note')
    ev = a.assign_entity('run', [para['id']], 'family:practice')
    build(out)
    c, _ = load(out)
    e = next(x for x in c['entities'] if x['key'] == ev['key'])
    assert e['verified'] and e['granularity'] == 'human' and e['type'] == 'family:practice'
    blk = next(b for b in c['blocks'] if b['id'] == para['id'])
    assert blk['entities'][-1] == {**blk['entities'][-1], 'family': 'practice', 'source': 'human', 'verified': True}
    keep = a.export('run', 'markdown_text', options={'exclude': ['exercise']})[0].decode()
    gone = a.export('run', 'markdown_text', options={'exclude': ['family:practice']})[0].decode()
    assert 'Alpha' in keep and 'Alpha' not in gone
    listed = a.entities('run')
    assert next(x for x in listed['entities'] if x['key'] == ev['key'])['source'] == 'human'
    a.assign_entity('run', [para['id']], 'exercise')                      # same members: the latest type wins
    build(out)
    assert [x['type'] for x in load(out)[0]['entities'] if x['key'] == ev['key']] == ['exercise']
    a.remove_entity('run', ev['key'])
    build(out)
    assert not any(x['key'] == ev['key'] for x in load(out)[0]['entities'])
    a.undo('run'); build(out)
    assert any(x['key'] == ev['key'] for x in load(out)[0]['entities'])


def test_entity_verdicts_feed_rule_statistics(app):
    """Confirming a detection with another type, or dismissing it, is a WRONG outcome for its rule class."""
    a, out = app
    c, _ = load(out)
    fig = next(e for e in c['entities'] if e['detected_type'] == 'figure')
    a.confirm_entities('run', [fig['key']], 'chart')
    build(out)
    st = load(out)[0]['entity_stats']['rules'][fig['rule_class']]
    assert st['n'] == 1 and st['wrong'] == 1 and not st['trusted']
    with pytest.raises(ServiceError):
        a.confirm_trusted('run')                                           # nothing trusted yet


def test_model_entity_pass_only_proposes_and_reviewer_decides(app):
    """Second pass on classification: untyped boxes and weak detections are asked (constrained to the taxonomy),
    voted, proposed; accept = entity_assign / entity_confirm (undoable); reject = remembered dismissal."""
    from scientific_ocr.application import entity_llm
    from scientific_ocr.pipeline import entity_review as er
    a, out = app
    c, _ = load(out)
    fig = next(b for b in c['blocks'] if b['kind'] == 'figure')
    para = next(b for b in c['blocks'] if b['chosen'].get('text', '').startswith('Alpha'))
    a.group('run', [para['id'], fig['id']], 'other', '')                 # a box no detector can type
    build(out)
    c, _ = load(out)
    t = next(t for t in er.targets(c) if t['mode'] == 'classify')
    assert t['members'] == [para['id'], fig['id']]

    class Fake:
        name = 'fake'
        def complete_json(self, system, user, schema, **kw):
            assert 'TAXONOMY' in system and 'TARGET' in user and 'placement' in user
            assert 'none' in schema['properties']['type']['enum'] and 'family:practice' in schema['properties']['type']['enum']
            return {'json': {'type': 'note', 'confidence': 'high', 'reason': 'σημείωση'}}
    assert entity_llm.run(out, samples=3, interpreter=Fake()) == 0
    r = a.entity_proposals('run')
    p = next(x for x in r['proposals'] if x['mode'] == 'classify')
    assert p['votes'] == 3 and p['proposed'] == 'note' and p['decision']['members'] == [para['id'], fig['id']]
    assert not any(e['granularity'] == 'human' for e in load(out)[0]['entities'])          # nothing applied
    a.accept_entity_proposal('run', p['key'])
    build(out)
    assert any(e['granularity'] == 'human' and e['type'] == 'note' for e in load(out)[0]['entities'])
    assert not any(x['key'] == p['key'] for x in a.entity_proposals('run')['proposals'])  # settled
    a.undo('run'); build(out)
    assert any(x['key'] == p['key'] for x in a.entity_proposals('run')['proposals'])      # undo reopens it
    a.dismiss_suggestion('run', p['key'])
    assert not any(x['key'] == p['key'] for x in a.entity_proposals('run')['proposals'])  # rejection remembered


def test_category_can_be_changed_again_after_verification(app):
    """A verified entity (detected or the reviewer's own) can be re-categorised any number of times."""
    a, out = app
    c, _ = load(out)
    fig = next(e for e in c['entities'] if e['detected_type'] == 'figure')
    para = next(b for b in c['blocks'] if b['chosen'].get('text', '').startswith('Alpha'))
    a.confirm_entities('run', [fig['key']], 'chart'); build(out)
    a.confirm_entities('run', [fig['key']], 'family:insert'); build(out)
    assert next(e for e in load(out)[0]['entities'] if e['key'] == fig['key'])['type'] == 'family:insert'
    ev = a.assign_entity('run', [para['id']], 'note'); build(out)
    a.confirm_entities('run', [ev['key']], 'tip'); build(out)
    e = next(e for e in load(out)[0]['entities'] if e['key'] == ev['key'])
    assert e['type'] == 'tip' and e['granularity'] == 'human'


def test_region_replacing_a_region_is_one_atomic_undoable_step(app):
    """Redrawing a reviewer region: region_remove + region_add in ONE group; one undo restores the old one."""
    a, out = app
    old = a.add_region('run', 1, [50, 75, 410, 125], {'engine': 'marker', 'name': 'Text'})
    new = a.add_region('run', 1, [50, 70, 420, 130], {'engine': 'marker', 'name': 'Text'}, replaces_region=old['id'])
    evs = [json.loads(x) for x in (out / 'events.jsonl').read_text().splitlines()]
    last = [e for e in evs if e.get('group') and e['group'] == evs[-1].get('group')]
    assert {e['type'] for e in last} == {'region_remove', 'region_add'}
    st = a._book('run').events.state()
    assert [r['id'] for r in st['regions']] == [new['id']]
    a.undo('run')
    assert [r['id'] for r in a._book('run').events.state()['regions']] == [old['id']]


def test_merge_preview_returns_rendered_html_and_changes_nothing(app):
    a, out = app
    c, _ = load(out)
    paras = [b['id'] for b in c['blocks'] if b['kind'] == 'paragraph' and b['chosen'].get('text')][:2]
    before = (out / 'events.jsonl').read_text() if (out / 'events.jsonl').exists() else ''
    r = a.merge_preview('run', paras)
    assert r['text'].startswith('Alpha') and 'Alpha' in r['html']             # same inline renderer as the output view
    assert ((out / 'events.jsonl').read_text() if (out / 'events.jsonl').exists() else '') == before


def test_cli_render_and_job_queue_run_a_real_subprocess(tmp_path):
    """The CLI entry point and the job queue end to end: a light job runs `python -m scientific_ocr reproject` in a
    subprocess, writes its log, finishes with code 0; a second submission while running is coalesced."""
    import subprocess
    import sys
    import time

    from scientific_ocr.application.jobs import JobQueue
    out = make_run(tmp_path)
    lib = Library(tmp_path)
    lib.adopt(out)
    r = subprocess.run([sys.executable, '-m', 'scientific_ocr', 'render', '--book', str(out)], capture_output=True, text=True)
    assert r.returncode == 0 and (out / 'canonical.json').exists()
    q = JobQueue(tmp_path)
    j = q.submit('run', 'reproject', ['reproject', '--book', str(out)], 'test', lane='light')
    again = q.submit('run', 'reproject', ['reproject', '--book', str(out)], 'test', lane='light')
    assert again['id'] == j['id']                                          # coalesced into the running job
    t0 = time.time()
    while time.time() - t0 < 120 and any(x['state'] in ('queued', 'running') for x in q.list('run')):
        time.sleep(0.3)
    done = [x for x in q.list('run') if x['state'] == 'done']
    assert done and all(x['code'] == 0 for x in done) and q.log_path(done[0]).exists()


def test_out_of_output_keeps_content_in_the_model_and_is_undoable(app):
    """set_output: real content left out of the READER exports only. Unlike hiding a wrong box, it stays in the
    canonical model, the hierarchy and the JSON (flagged); groups go out as a whole; one undo restores."""
    a, out = app
    c, _ = load(out)
    para = next(b for b in c['blocks'] if b['chosen'].get('text', '').startswith('Alpha'))
    fig = next(b for b in c['blocks'] if b['kind'] == 'figure')
    a.set_output('run', [para['id']], False)
    build(out)
    c, md = load(out)
    blk = next(b for b in c['blocks'] if b['id'] == para['id'])
    assert blk.get('omitted') and 'Alpha paragraph' not in md                       # out of the text
    assert not [f for f in c['findings'] if f['type'] == 'INTEGRITY_CONTENT_NOT_EXPORTED']   # deliberate, not a loss
    seg = next(s for s in json.loads(a.export('run', 'normalized_json')[0])['segments'] if s['id'] == para['id'])
    assert seg['in_reader_output'] is False and seg['text']                            # kept, flagged
    assert any(n['id'] == para['id'] and not n['in_output'] for n in a.outline('run')['nodes'])
    assert para['id'] not in [q['id'] for q in a.queue('run', 'recommended') + a.queue('run', 'required')]
    a.undo('run'); build(out)
    assert 'Alpha paragraph' in load(out)[1]
    g = a.group('run', [para['id'], fig['id']], 'other', '')
    build(out)
    assert a.set_output('run', [], False, group=g['id'])['applied'] == 2
    build(out)
    c, md = load(out)
    assert all(next(b for b in c['blocks'] if b['id'] == i).get('omitted') for i in (para['id'], fig['id']))
    assert 'Figure 1. A caption' not in md                                           # the figure's caption goes with it


def test_render_inline_is_the_output_renderer_and_escapes_everything_else(app):
    a, _ = app
    html = a.render_inline('H<sub>3</sub>PO<sub>4</sub> **ισχυρό** $x^2$ <script>x</script>')['html']
    assert '<sub>3</sub>' in html and ('<strong>' in html or '<b>' in html)
    assert '$x^2$' in html and '<script>' not in html
    with pytest.raises(ServiceError):
        a.render_inline(None)


def test_decisions_on_a_group_member_apply_to_the_whole_group_and_its_end_can_be_set(app):
    """Moving or leaving out one segment of a group (automatic or the reviewer's) moves / leaves out the whole
    group, in its order, in one undo; the extent of a group can be corrected by choosing its last segment."""
    a, out = app
    c, _ = load(out)
    para = next(b for b in c['blocks'] if b['chosen'].get('text', '').startswith('Alpha'))
    beta = next(b for b in c['blocks'] if 'SINGLE_ENGINE_DOCLING' in b['reasons'])
    fig = next(b for b in c['blocks'] if b['kind'] == 'figure')
    g = a.group('run', [para['id'], beta['id']], 'example', 'Παράδειγμα')
    build(out)
    a.decide('run', beta['id'], 'move_after_block', after=fig['id'])
    build(out)
    order = [b['id'] for b in load(out)[0]['blocks'] if b['page'] == 1]
    assert order.index(fig['id']) < order.index(para['id']) < order.index(beta['id'])   # both moved, in order
    a.undo('run'); build(out)
    assert a.set_output('run', [para['id']], False)['applied'] == 2                         # the whole group goes out
    a.undo('run'); build(out)
    flow = [b['id'] for b in load(out)[0]['blocks'] if b.get('flow') != 'furniture' and not b.get('excluded')]
    a.set_group_end('run', g['id'], flow[flow.index(para['id']) + 3])
    build(out)
    grp = next(x for x in load(out)[0]['groups'] if x['members'][0] == para['id'])
    assert len(grp['members']) == 4
    with pytest.raises(ServiceError):
        a.set_group_end('run', grp['id'], flow[0] if flow[0] != para['id'] else flow[-1] if False else para['id'])


def _example_book(tmp_path):
    """A white page with an UNSHADED worked example whose solution is followed by a big gap and more of it, a
    numbered exercise, and an answers collection — run through the whole pipeline."""
    from conftest import marker_json
    m = marker_json()
    kids = m['children'][0]['children']
    def add(t, bb, html):
        kids.append({'id': f'/page/0/{t}/{len(kids)}', 'block_type': t, 'bbox': bb, 'html': html, 'children': None, 'images': {}})
    add('SectionHeader', [60, 380, 300, 396], '<h3>ΠΑΡΑΔΕΙΓΜΑ 4.1</h3>')
    add('Text', [60, 400, 540, 430], '<p>Υπολογίστε το pH διαλύματος HCl 0,01 M.</p>')
    add('Text', [60, 434, 540, 450], '<p><b>Λύση:</b> Το HCl είναι ισχυρό οξύ.</p>')
    add('Text', [60, 520, 540, 536], '<p>Άρα pH = 2.</p>')
    add('SectionHeader', [60, 560, 300, 576], '<h3>Άσκηση 4.2</h3>')
    add('Text', [60, 580, 540, 596], '<p>Βρείτε το pH διαλύματος NaOH 0,1 M.</p>')
    add('SectionHeader', [60, 640, 300, 656], '<h2>Απαντήσεις</h2>')
    add('Text', [60, 660, 540, 676], '<p>4.2 pH = 13.</p>')
    out = make_run(tmp_path, marker=m)
    lib = Library(tmp_path)
    lib.adopt(out)
    assert build(out) == 0
    return App(lib, FakeJobs()), out


def test_whole_pipeline_unshaded_example_extent_answers_and_model_end_check(tmp_path):
    a, out = _example_book(tmp_path)
    c, _ = load(out)
    txt = {b['id']: (b['chosen'].get('text') or '') for b in c['blocks']}
    ex = next(g for g in c['groups'] if g['name'].startswith('ΠΑΡΑΔΕΙΓΜΑ 4.1'))
    assert [txt[m][:7] for m in ex['members']] == ['ΠΑΡΑΔΕΙ', 'Υπολογί', '**Λύση:'] and ex['ambiguous']   # big gap
    exe = next(e for e in c['entities'] if e['type'] == 'exercise')
    assert exe.get('answers') and txt[exe['answers'][0]].startswith('4.2')                      # linked, not grouped
    assert any(f['kind'] == 'auto_group' and f.get('ambiguous') for f in c['document_structure']['fixes'])
    # the model's second pass: the gap was inside the solution -> continue to "Άρα pH = 2."
    from scientific_ocr.application import extent_llm

    class Fake:
        name = 'fake'
        def complete_json(self, system, user, schema, **kw):
            assert 'LAST' in system and 'WHAT FOLLOWS IT' in user
            want = next(ch for ch in schema['properties']['last']['enum'] if f'[{ch}]' in user and 'Άρα pH' in user.split(f'[{ch}]')[1][:60])
            return {'json': {'last': want, 'confidence': 'high', 'reason': 'συνέχεια της λύσης'}}
    assert extent_llm.run(out, samples=3, interpreter=Fake()) == 0
    p = a.extent_proposals('run')['proposals'][0]
    assert p['votes'] == 3 and any('Άρα pH' in x for x in p['added'])
    a.accept_extent_proposal('run', p['key'])
    build(out)
    g = next(g for g in load(out)[0]['groups'] if g['members'][0] == ex['members'][0])
    assert g['source'] == 'human' and txt[g['members'][-1]].startswith('Άρα pH')
    assert not a.extent_proposals('run')['proposals']                                            # settled


def test_changing_a_heading_carries_the_reviewers_own_sub_heading_levels_along(tmp_path):
    """Sub-headings the reviewer had levelled (relative to the parent as it was) move with a later change of the
    parent by the same amount — in one undoable step."""
    a, out = _example_book(tmp_path)
    c, _ = load(out)
    top = next(b for b in c['blocks'] if (b['chosen'].get('text') or '').startswith('Υπολογίστε'))
    sub = next(b for b in c['blocks'] if (b['chosen'].get('text') or '').startswith('Άρα pH'))
    a.decide('run', top['id'], 'set_heading_level', level=2); build(out)
    a.decide('run', sub['id'], 'set_heading_level', level=4); build(out)
    lv = {b['id']: b.get('heading_level') for b in load(out)[0]['blocks']}
    assert (lv[top['id']], lv[sub['id']]) == (2, 4)
    a.decide('run', top['id'], 'set_heading_level', level=3); build(out)     # +1 for the section
    lv = {b['id']: b.get('heading_level') for b in load(out)[0]['blocks']}
    assert (lv[top['id']], lv[sub['id']]) == (3, 5)
    a.undo('run'); build(out)                                               # one undo: both back
    lv = {b['id']: b.get('heading_level') for b in load(out)[0]['blocks']}
    assert (lv[top['id']], lv[sub['id']]) == (2, 4)


def test_page_order_is_natural_and_independent_of_staging_names():
    """Folder uploads: pages follow the natural order of the ORIGINAL names (p2 before p10, folder by folder),
    never the random staging prefixes nor the upload order."""
    from scientific_ocr.adapters.storage import natural_key, page_order
    names = ['scan10.png', 'Scan2.png', 'scan1.png', 'b/1.png', 'a/2.png', 'a/10.png', '1page.png', 'page1.png']
    assert [names[i] for i in page_order(names)] == \
        ['1page.png', 'a/2.png', 'a/10.png', 'b/1.png', 'page1.png', 'scan1.png', 'Scan2.png', 'scan10.png']
    assert natural_key('Σελίδα 2') < natural_key('σελίδα 10')


def test_book_from_images_keeps_name_order_and_records_files(tmp_path):
    from PIL import Image
    a = App(Library(tmp_path / 'lib'), FakeJobs())
    files, names = [], ['p10.png', 'p1.png', 'p2.png']
    for i, n in enumerate(names):                     # staging-like random prefixes that sort the other way
        f = a.staging_file(f'{"zyx"[i]}-{n}')
        Image.new('RGB', (20 + 10 * int(n[1:-4]), 30), 'white').save(f)
        files.append(f)
    r = a.create_from_images(files, 'Σάρωση', names)
    b = a.lib.get(r['book']['id'])
    assert b.settings()['source']['page_files'] == ['p1.png', 'p2.png', 'p10.png']
    from scientific_ocr.adapters import pdf as pdfio
    w = [p['width'] for p in pdfio.page_info(b.source_pdf)]          # widths encode the page (30, 40, 120 px)
    assert w[0] < w[1] < w[2] and abs(w[2] / w[0] - 4) < 0.01


def test_remove_and_restore_pages_is_reversible_and_skipped_everywhere(app, tmp_path):
    a, out = app
    with pytest.raises(ServiceError):                 # a book keeps at least one page
        a.remove_pages('run', [1])
    with pytest.raises(ServiceError):
        a.remove_pages('run', [2])
    b = a.lib.get('run')
    b.events.append({'type': 'page_remove', 'pages': [1]})         # (the 1-page fixture: bypass the guard)
    assert b.pages()[0]['state'] == 'removed' and a.book('run')['page_count'] == 0
    with pytest.raises(ServiceError):                 # removed pages are never processed
        a.process('run', '1')
    assert build(out) == 0
    c, _ = load(out)
    assert not c['blocks'] and c['source']['pages_removed'] == [1] and c['source']['pages_processed'] == []
    a.undo('run')
    assert b.pages()[0]['state'] != 'removed'
    assert build(out) == 0 and load(out)[0]['blocks']                # back without re-processing


def test_finished_jobs_can_be_removed_from_the_list(tmp_path):
    from scientific_ocr.application.jobs import JobQueue
    q = JobQueue(tmp_path)
    q.jobs = {'a': {'id': 'a', 'book': 'x', 'state': 'done', 'created': 1, 'lane': 'heavy'},
              'b': {'id': 'b', 'book': 'x', 'state': 'running', 'created': 2, 'lane': 'heavy'},
              'c': {'id': 'c', 'book': 'y', 'state': 'failed', 'created': 3, 'lane': 'heavy'}}
    log = q.log_path(q.jobs['a']); log.parent.mkdir(parents=True); log.write_text('x')
    with pytest.raises(ValueError):
        q.remove('b')                                 # stop it first
    q.remove('a')
    assert 'a' not in q.jobs and not log.exists()
    assert q.clear('x') == 0 and q.clear() == 1 and list(q.jobs) == ['b']
    assert [j['id'] for j in JobQueue(tmp_path).list()] == ['b']   # persisted (running -> interrupted on reload)


def test_book_from_images_keeps_a_reviewer_chosen_order(tmp_path):
    """order != 'name': the files arrive in the page order the reviewer previewed (date, reversed, by hand)."""
    from PIL import Image
    from scientific_ocr.adapters import pdf as pdfio
    a = App(Library(tmp_path / 'lib'), FakeJobs())
    names, files = ['a1.png', 'a3.png', 'a2.png'], []
    for n in names:
        f = a.staging_file(f'x-{n}')
        Image.new('RGB', (10 * int(n[1]), 30), 'white').save(f)
        files.append(f)
    b = a.lib.get(a.create_from_images(files, 'Χειροκίνητη', names, order='manual')['book']['id'])
    src = b.settings()['source']
    assert src['page_files'] == names and src['page_order'] == 'manual'
    w = [p['width'] for p in pdfio.page_info(b.source_pdf)]
    assert w[0] < w[2] < w[1]                          # 10, 30, 20 px: kept as given, not re-sorted by name
    with pytest.raises(ServiceError):
        a.create_from_images([a.staging_file('y.png')], 't', ['y.png'], order='bad order; rm')


def test_import_images_from_disk_in_chosen_order_with_previews(tmp_path, monkeypatch):
    """In-app file browser: page images on disk -> one book in the order the reviewer chose; previews are served
    only for image files inside the home folder."""
    from PIL import Image
    from scientific_ocr.adapters import pdf as pdfio
    from scientific_ocr.application.use_cases.library import LibraryService
    home = tmp_path / 'home'
    (home / 'scan').mkdir(parents=True)
    monkeypatch.setattr(LibraryService, 'FS_ROOT', home)
    paths = []
    for n, w in (('b.png', 30), ('a.png', 10), ('c.png', 20)):
        Image.new('RGBA', (w, 40), (255, 255, 255, 0)).save(home / 'scan' / n)
        paths.append(str(home / 'scan' / n))
    a = App(Library(tmp_path / 'lib'), FakeJobs())
    assert a.fs_thumb(paths[0], 100).stat().st_size > 0                      # RGBA -> JPEG preview
    with pytest.raises(ServiceError):
        a.fs_thumb(str(tmp_path / 'lib'), 100)                                # outside home / not an image
    r = a.import_images(paths, 'Από δίσκο', 'date:reverse', str(home / 'scan'))
    b = a.lib.get(r['book']['id'])
    assert b.settings()['source']['page_files'] == ['b.png', 'a.png', 'c.png']
    assert b.settings()['source']['page_order'] == 'date:reverse'
    w = [p['width'] for p in pdfio.page_info(b.source_pdf)]
    assert w[1] < w[2] < w[0]
    assert all(Path(p).exists() for p in paths)                               # originals copied, never moved


def test_heic_pages_even_under_a_png_name_are_converted_locally(tmp_path):
    """iPhone/macOS HEIC (often saved as .png by screenshots) is decoded via macOS `sips`, losslessly, offline."""
    import shutil
    import subprocess
    from PIL import Image
    from scientific_ocr.adapters import pdf as pdfio
    if not shutil.which('sips'):
        pytest.skip('macOS sips not available')
    Image.new('RGB', (64, 90), 'white').save(tmp_path / 'p.png')
    heic = tmp_path / 'fake.png'
    r = subprocess.run(['sips', '-s', 'format', 'heic', str(tmp_path / 'p.png'), '--out', str(heic)], capture_output=True)
    if r.returncode or not heic.exists():
        pytest.skip('sips cannot write HEIC here')
    assert pdfio.image_thumbnail(heic, 100, tmp_path / 't.jpg').exists()
    pdfio.images_to_pdf([heic], tmp_path / 'b.pdf')
    assert pdfio.page_info(tmp_path / 'b.pdf')[0]['width'] > 0
    (tmp_path / 'junk.png').write_bytes(b'not an image')
    with pytest.raises(pdfio.UnsupportedImage):
        pdfio.images_to_pdf([tmp_path / 'junk.png'], tmp_path / 'c.pdf')


def test_page_images_get_paper_sized_pages_with_untouched_pixels(tmp_path):
    """A phone photo declaring 72 dpi must not become a 31x43-inch page (huge rasters, slow engines): it is placed on
    an A4-sized page; its JPEG bytes are embedded as they are; EXIF rotation is applied; real scan DPI is trusted."""
    import pymupdf
    from PIL import Image
    from scientific_ocr.adapters import pdf as pdfio
    Image.new('RGB', (2200, 3100), 'white').save(tmp_path / 'phone.jpg', dpi=(72, 72))
    Image.new('RGB', (2480, 3508), 'white').save(tmp_path / 'scan300.png', dpi=(300, 300))
    im = Image.new('RGB', (300, 200), 'white'); ex = im.getexif(); ex[0x0112] = 6   # stored sideways
    im.save(tmp_path / 'rot.jpg', exif=ex)
    info = pdfio.images_to_pdf([tmp_path / 'phone.jpg', tmp_path / 'scan300.png', tmp_path / 'rot.jpg'], tmp_path / 'b.pdf')
    pages = pdfio.page_info(tmp_path / 'b.pdf')
    assert abs(pages[0]['height'] - 11.69 * 72) < 1 and info[0]['dpi'] > 260          # A4 long side
    assert abs(pages[1]['width'] - 8.27 * 72) < 1 and info[1]['dpi'] == 300             # declared scan DPI kept
    assert pages[2]['height'] > pages[2]['width'] and info[2]['px'] == [200, 300]       # upright
    doc = pymupdf.open(tmp_path / 'b.pdf')
    x = doc[0].get_images()[0][0]
    assert doc.extract_image(x)['image'] == (tmp_path / 'phone.jpg').read_bytes()       # pixels untouched


def test_ocr_and_excerpts_never_render_below_the_scan_density(tmp_path):
    """Resolution rule: a 300-dpi scan is OCR'd / cropped at >= 300 dpi (engine defaults are 192/216); a coarse
    72-dpi scan keeps the engine defaults (unchanged args, so existing caches stay valid)."""
    from PIL import Image
    from scientific_ocr import config
    from scientific_ocr.adapters import extraction as ex, pdf as pdfio
    Image.new('RGB', (2480, 3508), 'white').save(tmp_path / 's300.png', dpi=(300, 300))
    Image.new('RGB', (600, 800), 'white').save(tmp_path / 's72.png', dpi=(72, 72))
    pdfio.images_to_pdf([tmp_path / 's300.png'], tmp_path / 'hi.pdf')
    pdfio.images_to_pdf([tmp_path / 's72.png'], tmp_path / 'lo.pdf')
    hi, lo = pdfio.native_dpi(tmp_path / 'hi.pdf', 1), pdfio.native_dpi(tmp_path / 'lo.pdf', 1)
    assert abs(hi - 300) < 1 and abs(lo - 72) < 1
    assert ex._resolution_args('marker', [], hi) == ['--highres_image_dpi', '300']
    assert ex._resolution_args('docling', [], hi) == ['--ocr-option', 'scale=4.167']
    assert ex._resolution_args('marker', [], lo) == [] and ex._resolution_args('docling', [], lo) == []
    assert ex._resolution_args('marker', ['--force_ocr', '--highres_image_dpi', '288'], lo) == \
        ['--force_ocr', '--highres_image_dpi', '288']                         # a profile's higher dpi is kept
    assert ex.ocr_dpi(2000, 192) == config.OCR_MAX_DPI
    rec = pdfio.crop(tmp_path / 'hi.pdf', 1, [100, 100, 200, 200], tmp_path / 'c.png', pad=0)
    assert abs(rec['width_px'] - 100 * 300 / 72) <= 2                        # every scan pixel kept
    region = ex.region_pdf(tmp_path / 'hi.pdf', 1, [100, 100, 200, 200], tmp_path / 'r.pdf')
    assert abs(pdfio.native_dpi(region, 1) - 300) < 2                         # the region carries the density on


def test_native_picker_items_are_described_and_confined_to_home(tmp_path, monkeypatch):
    """«Από τον υπολογιστή»: the OS picker returns paths; the service classifies them (folder / PDF / page image)
    and refuses anything outside the home folder. No real panel is opened in tests."""
    from scientific_ocr.adapters import os_picker
    from scientific_ocr.application.use_cases.library import LibraryService
    home = tmp_path / 'home'
    (home / 'scans').mkdir(parents=True)
    (home / 'a.pdf').write_bytes(b'%PDF-1.4')
    (home / 'scans' / 'p1.jpg').write_bytes(b'x')
    monkeypatch.setattr(LibraryService, 'FS_ROOT', home)
    monkeypatch.setattr(os_picker, 'available', lambda: True)
    monkeypatch.setattr(os_picker, 'pick', lambda *a, **k: [str(home / 'scans'), str(home / 'a.pdf'),
                                                            str(home / 'scans' / 'p1.jpg'), str(tmp_path / 'x.pdf')])
    r = App(Library(tmp_path / 'lib'), FakeJobs()).pick_native()
    assert [(i['type'], i['name']) for i in r['items']] == [('dir', 'scans'), ('pdf', 'a.pdf'), ('image', 'p1.jpg')]
    assert r['items'][2]['dir'] == str((home / 'scans').resolve()) and r['outside_home'] == [str(tmp_path / 'x.pdf')]
    monkeypatch.setattr(os_picker, 'available', lambda: False)
    assert App(Library(tmp_path / 'lib2'), FakeJobs()).pick_native() == {'available': False, 'items': []}


def test_progress_names_the_careful_phase_and_its_pages(tmp_path):
    """The automatic careful re-examination reports its own phase and pages (the UI names it and marks them);
    the final 'done' report carries no phase."""
    from scientific_ocr.adapters.storage import read_json
    from scientific_ocr.application.processing import progress_for
    careful = progress_for(tmp_path, 'process', phase='diligent', phase_pages=[3, 7])
    careful('extract', 1, 2, engine='marker', page=7)
    p = read_json(tmp_path / 'progress.json')
    assert p['phase'] == 'diligent' and p['phase_pages'] == [3, 7] and p['page'] == 7 and p['step'] == 'extract'
    progress_for(tmp_path, 'process')('done', 1, 1, code=0)
    assert 'phase' not in read_json(tmp_path / 'progress.json')


def test_integrity_audit_reads_html_escaped_export_text():
    """A caption with 'ΔG > 0' is exported inside <figcaption> as '&gt;': that is present content, not a loss."""
    from scientific_ocr.pipeline import audit
    t = 'Η ενεργειακά μη ευνοϊκή ($\\Delta G > 0$) αντίδραση $X \\rightarrow Y$ δε μπορεί'
    md = '<figure><figcaption>' + t.replace('>', '&gt;') + '</figcaption></figure>'
    assert audit._snippet(t) in audit._norm(md)


def test_integrity_exit_is_a_finished_job_with_a_warning(tmp_path, monkeypatch):
    """Exit 5 (outputs written, integrity finding) is 'done' + warning, never shown as a failed job."""
    import subprocess as sp
    from scientific_ocr.application import jobs as jq

    class P:
        pid = 0
        def wait(self):
            return 5
    monkeypatch.setattr(jq.subprocess, 'Popen', lambda *a, **k: P())
    q = jq.JobQueue(tmp_path)
    job = {'id': 'j', 'book': 'b', 'kind': 'reproject', 'label': 'x', 'lane': 'light', 'args': [], 'state': 'queued',
           'created': 1, 'started': None, 'finished': None, 'code': None}
    q.jobs['j'] = job
    q._execute(job)
    assert job['state'] == 'done' and job['warning'] == 'integrity' and job['code'] == 5


def test_actions_are_banked_in_slow_books_until_rebuild(app):
    """A book whose rebuild is slow does not rebuild after every action: the action is recorded and BANKED
    (pending, with OCR-needing ones counted); `rebuild` applies all of them in one job."""
    import json
    a, out = app
    (out / 'cache').mkdir(exist_ok=True)
    (out / 'cache' / 'build.json').write_text(json.dumps({'seconds': 400}))      # like a 465-page book
    blk = next(b for b in a.page('run', 1)['blocks'] if 'SINGLE_ENGINE_DOCLING' in b['reasons'])
    before = len(a.jobs.submitted)
    a.decide('run', blk['id'], 'replace_text', value='fixed text')
    a.add_region('run', 1, [10, 10, 200, 60], {'engine': 'marker', 'name': 'Text'})
    assert set(a.jobs.submitted[before:]) == {('pages', 'light')}             # only the touched pages, no full rebuild
    p = a.book('run')['pending']
    assert p['count'] >= 2 and p['ocr'] == 1 and p['estimate_s'] == 400
    rev = a.revision('run')
    assert rev['pending'] == p['count'] and rev['pending_ocr'] == 1
    a.rebuild('run')
    assert a.jobs.submitted[-1] == ('reproject', 'light')
    assert build(out) == 0 and a.book('run')['pending']['count'] == 0         # applied: nothing pending


def test_job_log_carries_facts_for_a_plain_language_summary(app):
    """A rebuild writes FACT lines (why it ran, what changed, the result) that the job summary parses; free-text
    lines and timestamps around them are ignored."""
    from scientific_ocr.core import joblog
    a, out = app
    lines = []
    blk = next(b for b in a.page('run', 1)['blocks'] if 'SINGLE_ENGINE_DOCLING' in b['reasons'])
    a.decide('run', blk['id'], 'replace_text', value='fixed text')
    from scientific_ocr.application import projection
    from conftest import no_models
    assert projection.build(out, log=lines.append, runner=no_models) == 0
    facts = joblog.parse('\n'.join('2026-11-23T10:00:00 ' + ln for ln in lines) + '\n@@fact {broken')
    kinds = [f['kind'] for f in facts]
    assert kinds == ['trigger', 'changes', 'built']
    trig, built = facts[0], facts[2]
    assert trig['count'] == 1 and trig['events'][0]['action'] == 'replace_text' and trig['events'][0]['block_id'] == blk['id']
    assert built['blocks'] > 0 and 'seconds' in built and built['release_status']


def test_review_loop_queue_is_book_ordered_and_includes_warnings(app):
    """The review loop walks errors AND warnings in book order (page, reading order); the priority queue is kept."""
    a, out = app
    q = a.queue('run', 'all', 'book')
    assert {x['review'] for x in q} <= {'required', 'recommended'}
    assert len(q) == len(a.queue('run', 'required')) + len(a.queue('run', 'recommended'))
    assert [(x['page'], x['reading_order']) for x in q] == sorted((x['page'], x['reading_order']) for x in q)
    with pytest.raises(ServiceError):
        a.queue('run', 'everything')


def test_approving_what_the_specialised_models_left_is_not_stale(tmp_path):
    """auto_hash is the result the reviewer SEES: taken after the specialised models. A block they changed (e.g. a
    formula withdrawn to its page excerpt) can be approved, and the approval applies on every rebuild."""
    from conftest import make_run, build, load
    from scientific_ocr.adapters.event_store import EventStore
    from scientific_ocr.pipeline.overlay import decision_event
    out = make_run(tmp_path)
    assert build(out) == 0
    c, _ = load(out)
    blk = next(b for b in c['blocks'] if b['chosen']['type'] in ('text', 'latex'))
    from scientific_ocr.core.model import content_hash
    assert blk['auto_hash'] == content_hash(blk['chosen'])      # sealed after every machine stage
    EventStore(out).append(decision_event('approve', blk, {}, lambda _id: None))
    assert build(out) == 0
    c, _ = load(out)
    b2 = next(b for b in c['blocks'] if b['id'] == blk['id'])
    assert 'DECISION_STALE' not in b2['reasons'] and not c['human']['stale']


def test_page_local_actions_rebuild_only_their_page_as_a_preview(app):
    """In a slow book, a page-local action (approve, correct, image only…) is applied to ITS PAGE at once — a page
    preview the page view and later decisions use — while canonical.json (exports, book-wide views) stays the full
    build until a full rebuild, which supersedes the previews. Book-wide actions do not dirty any page."""
    import json
    from scientific_ocr.application import projection
    from conftest import no_models
    a, out = app
    (out / 'cache').mkdir(exist_ok=True)
    (out / 'cache' / 'build.json').write_text(json.dumps({'seconds': 400}))
    canon_before = (out / 'canonical.json').read_bytes()
    blk = next(b for b in a.page('run', 1)['blocks'] if 'SINGLE_ENGINE_DOCLING' in b['reasons'])
    a.decide('run', blk['id'], 'replace_text', value='fixed text')
    assert projection.dirty_pages(out) == [1]
    assert projection.build_page(out, 1, log=lambda m: None, runner=no_models) == 0
    assert projection.dirty_pages(out) == []
    pg = a.page('run', 1)
    seg = next(b for b in pg['blocks'] if b['id'] == blk['id'])
    assert pg['preview'] and seg['chosen']['text'] == 'fixed text'                      # visible at once
    assert (out / 'canonical.json').read_bytes() == canon_before                         # the book is untouched
    assert set(pg['preview']['applied']) <= set(a.book('run')['pending']['page_applied'])
    other = next(b for b in pg['blocks'] if b['id'] != blk['id'] and b['review'] in ('required', 'recommended'))
    a.decide('run', other['id'], 'approve')                       # validated against the preview version
    assert projection.dirty_pages(out) == [1]
    a.assign_entity('run', [other['id']], 'example')              # book-wide: banked, no page made dirty by it
    projection.build_page(out, 1, log=lambda m: None, runner=no_models)
    assert projection.dirty_pages(out) == []
    assert build(out) == 0                                                               # full rebuild
    assert a.page('run', 1)['preview'] is None and not list((out / 'cache' / 'page_preview').glob('*.json'))
    c, _ = load(out)
    assert next(b for b in c['blocks'] if b['id'] == blk['id'])['chosen']['text'] == 'fixed text'


def test_dismissed_structure_suggestion_leaves_the_page_list_at_once(app):
    """A dismissal is book-wide (banked in a slow book until the full rebuild), yet the page's suggestion list
    drops it immediately; undoing the dismissal brings it back, still without a rebuild."""
    import json
    a, out = app
    (out / 'cache').mkdir(exist_ok=True)
    (out / 'cache' / 'build.json').write_text(json.dumps({'seconds': 400}))      # slow book: actions are banked
    c = json.loads((out / 'canonical.json').read_text())
    ids = [b['id'] for b in c['blocks'] if b['page'] == 1][:2]
    sugg = {'kind': 'merge_join', 'members': ids, 'label': {'engine': 'marker', 'name': 'Text'}, 'source': 'rule',
            'reason': 'test', 'key': 'k_test_sugg'}
    c['pages'][0]['suggestions'] = [sugg]
    (out / 'canonical.json').write_text(json.dumps(c))
    assert [s['key'] for s in a.page('run', 1)['info']['suggestions']] == ['k_test_sugg']
    a.dismiss_suggestion('run', 'k_test_sugg')
    assert a.book('run')['pending']['count'] >= 1                                  # banked, not rebuilt
    assert a.page('run', 1)['info']['suggestions'] == []
    a.undo('run')
    assert [s['key'] for s in a.page('run', 1)['info']['suggestions']] == ['k_test_sugg']


def test_caption_links_have_one_source_of_truth():
    """A caption's caption_for is the truth; figures' lists are derived: no caption listed under one figure while
    pointing to another (shown twice), no link to a vanished segment (shown as 'no figure')."""
    from scientific_ocr.pipeline import captions
    rel = lambda **k: {'captions': [], **k}
    blocks = [{'id': 'f1', 'kind': 'figure', 'relations': rel(captions=['c1', 'c2'])},
              {'id': 'f2', 'kind': 'figure', 'relations': rel()},
              {'id': 'c1', 'kind': 'caption', 'relations': rel(caption_for='f2')},      # moved to f2
              {'id': 'c2', 'kind': 'caption', 'relations': rel(caption_for='gone')}]    # owner merged away
    captions.reconcile(blocks)
    by = {b['id']: b for b in blocks}
    assert by['f1']['relations']['captions'] == [] and by['f2']['relations']['captions'] == ['c1']
    assert by['c2']['relations']['caption_for'] is None


def test_decisions_follow_merges_and_later_actions_supersede(app):
    """Generic rules for decisions and merges: a caption linked to a MERGED figure is applied (not stale);
    a decision on a segment the reviewer later hid is superseded, not sent back to review."""
    from scientific_ocr.pipeline import merge as mergemod
    a, out = app
    c, _ = load(out)
    fig = next(b for b in c['blocks'] if b['kind'] == 'figure')
    cap = next(b for b in c['blocks'] if b['kind'] == 'caption')
    other = next(b for b in c['blocks'] if b['page'] == fig['page'] and b['id'] not in (fig['id'], cap['id'])
                 and b['kind'] == 'paragraph')
    r = a.merge('run', [fig['id'], other['id']], {'engine': 'marker', 'name': 'Picture'}, mode='join')
    assert build(out) == 0
    c, _ = load(out)
    merged = next(b for b in c['blocks'] if b.get('merged_from') and fig['id'] in [m['id'] for m in b['merged_from']])
    a.decide('run', cap['id'], 'associate_caption', figure_id=merged['id'])
    para = next(b for b in c['blocks'] if b['kind'] == 'excerpt' and not b.get('merged_from'))
    a.decide('run', para['id'], 'mark_image_only')
    a.hide_block('run', para['id'])                                # a later action of yours removes it
    assert build(out) == 0
    c, _ = load(out)
    by = {b['id']: b for b in c['blocks']}
    assert by[cap['id']]['relations']['caption_for'] == merged['id'] and not by[cap['id']]['relations'].get('inferred')
    assert cap['id'] in by[merged['id']]['relations']['captions']
    assert c['human']['stale'] == [] and not any('DECISION_STALE' in b['reasons'] for b in c['blocks'])
