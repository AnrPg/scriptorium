"""Synthetic fixtures: a small PDF with known geometry + hand-written raw engine JSON.

Page 1 (600x800 pt):
  para A   y 80-120   seen by Marker and Docling (Docling splits it into 2 lines: 1:N)
  para B   y 140-180  seen ONLY by Docling (Marker misses it)
  figure   y 200-300  filled rectangle, Marker 'Picture' (or 'ChemicalBlock')
  caption  y 310-330  Marker Caption
  header   y 20-40    Marker PageHeader with EMPTY text
  bottom   y 740-760  printed but seen by NO engine  -> uncovered ink / bottom omission
"""
import json
import shutil
from pathlib import Path

import pymupdf
import pytest

FONT = Path('/System/Library/Fonts/Supplemental/Arial.ttf')
W, H = 600, 800


def _text(page, y, s):
    kw = {'fontfile': str(FONT), 'fontname': 'arialx'} if FONT.exists() else {}
    page.insert_text((60, y), s, fontsize=14, **kw)


def make_pdf(path: Path):
    doc = pymupdf.open()
    pg = doc.new_page(width=W, height=H)
    _text(pg, 34, 'Header 223')
    _text(pg, 95, 'Alpha paragraph line one about DNA')
    _text(pg, 115, 'Alpha paragraph line two continues')
    _text(pg, 160, 'Beta paragraph only docling saw')
    pg.draw_rect(pymupdf.Rect(80, 200, 400, 300), color=(0, 0, 0), fill=(0.2, 0.2, 0.2))
    _text(pg, 325, 'Figure 1. A caption for the figure')
    _text(pg, 755, 'Bottom line that no engine extracted at all')
    doc.save(path)


def marker_json(fig_type='Picture', header_html=''):
    blocks = [
        ('PageHeader', [55, 20, 160, 40], header_html),
        ('Text', [55, 80, 400, 120], '<p>Alpha paragraph line one about DNA Alpha paragraph line two continues</p>'),
        (fig_type, [80, 200, 400, 300], ''),
        ('Caption', [55, 310, 330, 330], '<p>Figure 1. A caption for the figure</p>'),
    ]
    return {'block_type': 'Document', 'children': [{
        'id': '/page/0/Page/0', 'block_type': 'Page', 'bbox': [0, 0, W, H], 'html': '',
        'children': [{'id': f'/page/0/{t}/{i}', 'block_type': t, 'bbox': bb, 'html': h, 'children': None, 'images': {}}
                     for i, (t, bb, h) in enumerate(blocks)]}]}


def _dl(i, label, bb, text):
    l, t, r, b = bb
    return {'self_ref': f'#/texts/{i}', 'label': label, 'text': text, 'orig': text, 'children': [],
            'prov': [{'page_no': 1, 'bbox': {'l': l, 't': H - t, 'r': r, 'b': H - b, 'coord_origin': 'BOTTOMLEFT'}}]}


def docling_json():
    texts = [_dl(0, 'text', [55, 80, 400, 100], 'Alpha paragraph line one about DNA'),
             _dl(1, 'text', [55, 101, 400, 120], 'Alpha paragraph line two continues'),
             _dl(2, 'text', [55, 140, 400, 180], 'Beta paragraph only docling saw'),
             _dl(3, 'caption', [55, 310, 330, 330], 'Figure 1. A caption for the figure')]
    pic = {'self_ref': '#/pictures/0', 'label': 'picture', 'children': [], 'captions': [{'$ref': '#/texts/3'}],
           'prov': [{'page_no': 1, 'bbox': {'l': 80, 't': H - 200, 'r': 400, 'b': H - 300, 'coord_origin': 'BOTTOMLEFT'}}]}
    return {'body': {'children': [{'$ref': f'#/texts/{i}'} for i in range(3)] + [{'$ref': '#/pictures/0'},
                                                                                  {'$ref': '#/texts/3'}]},
            'texts': texts, 'pictures': [pic], 'tables': [], 'groups': []}


def make_run(root: Path, marker=None, docling=None, marker_status='OK') -> Path:
    from scientific_ocr.adapters import pdf as source
    out = root / 'run'
    (out / 'source').mkdir(parents=True)
    pdf = out / 'source' / 'original.pdf'
    make_pdf(pdf)
    sha = source.sha256_file(pdf)
    raster = source.render_page(pdf, 1, out / 'source' / 'pages' / 'p0001.png')
    raster['path'] = 'source/pages/p0001.png'
    (out / 'source' / 'source.json').write_text(json.dumps({
        'input_path': str(pdf), 'retained_copy': 'source/original.pdf', 'sha256': sha, 'page_count': 1,
        'pages_processed': [1], 'page_dims_pt': {'1': [W, H]}, 'text_layer': {'1': True},
        'rasters': {'1': raster}, 'coord_system': 'PDF points, TOPLEFT'}))
    idx = {}
    for eng, data, status in (('marker', marker if marker is not None else marker_json(), marker_status),
                              ('docling', docling if docling is not None else docling_json(), 'OK')):
        d = out / 'raw' / eng / 'p0001-test'
        d.mkdir(parents=True)
        if status == 'OK':
            (d / 'out.json').write_text(data if isinstance(data, str) else json.dumps(data))
        idx[eng] = {'1': {'engine': eng, 'version': 'test', 'page': 1, 'status': status, 'exit_code': 0 if status == 'OK' else 1,
                          'raw_json': f'raw/{eng}/p0001-test/out.json' if status == 'OK' else None,
                          'log': f'raw/{eng}/p0001-test/process.log', 'seconds': 0, 'cache_key': 'test'}}
    (out / 'raw' / 'extraction_index.json').write_text(json.dumps(idx))
    return out


@pytest.fixture
def run_dir(tmp_path):
    return make_run(tmp_path)


def no_models(engine, jobs, out, allow_inference, log):
    """Fake SpecializedRunner (port): every model unavailable, nothing cached."""
    return {j['id']: {'status': 'NOT_RUN', 'error': 'test'} for j in jobs}


def build(out, runner=no_models):
    from scientific_ocr.application import projection
    return projection.build(out, log=lambda m: None, allow_inference=False, runner=runner)


def load(out):
    return json.loads((out / 'canonical.json').read_text()), (out / 'chapter.md').read_text()


@pytest.fixture
def built(run_dir):
    assert build(run_dir) == 0
    return (run_dir, *load(run_dir))


REAL_RUN = Path(__file__).resolve().parent.parent / 'library' / 'ch5'
REAL_PDF = Path.home() / 'Documents' / 'CHAPTER_5.pdf'


@pytest.fixture(scope='session')
def real(tmp_path_factory):
    """library/ch5 rebuilt in a temporary copy with FROZEN human input: only the book's symbol rules (part of the
    ground truth: the source prints Latin DNA letters) — none of the reviewer's ongoing decisions, groups or
    regions, so the assertions test the machine pipeline and never depend on live review work. Never runs OCR or
    model inference (raw outputs and the content-addressed model cache are reused)."""
    if not (REAL_RUN / 'canonical.json').exists():
        pytest.skip('real run not present (library/ch5)')
    from scientific_ocr.adapters import specialized
    from scientific_ocr.adapters.event_store import EventStore
    out = tmp_path_factory.mktemp('real') / 'ch5'
    shutil.copytree(REAL_RUN, out, ignore=shutil.ignore_patterns('*_llm.json', 'jobs', 'progress.json'))
    rules = EventStore(out).state()['rules']
    (out / 'events.jsonl').unlink()
    store = EventStore(out)
    for r in rules:
        store.append({'type': 'rule_add', 'rule': r}, actor='fixture')

    def cached_only(engine, jobs, out_, allow, log):
        return specialized.run(engine, jobs, out_, False, log)
    assert build(out, runner=cached_only) == 0
    return out, json.loads((out / 'canonical.json').read_text()), (out / 'chapter.md').read_text()


def copy_run(src: Path, dst: Path) -> Path:
    shutil.copytree(src, dst)
    return dst
