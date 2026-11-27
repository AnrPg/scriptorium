"""Reviewer regions are read TOGETHER: one engine run (one model load) for every pending region, one page each."""
import copy
from pathlib import Path

import pymupdf
from conftest import marker_json

from scientific_ocr.adapters import extraction
from scientific_ocr.pipeline import engine_json


def test_marker_output_with_several_pages_is_read_by_page_id():
    raw = marker_json()
    second = copy.deepcopy(raw['children'][0])
    second['id'] = '/page/1/Page/0'
    for c in second['children']:
        c['id'] = c['id'].replace('/page/0/', '/page/1/')
        c['html'] = c['html'].replace('Alpha', 'Beta')
    raw['children'].append(second)
    one, two = engine_json.marker_blocks(raw, 1), engine_json.marker_blocks(raw, 2)
    assert 'Alpha' in one[1]['text'] and 'Beta' in two[1]['text']
    assert two[1]['raw_pointer'].startswith('/children/1/')         # provenance points into the right page


def test_pending_regions_share_one_run_per_engine(tmp_path, monkeypatch):
    src = tmp_path / 'src.pdf'
    doc = pymupdf.open()
    pg = doc.new_page(width=400, height=400)
    for i in range(4):
        pg.insert_text((20, 40 + 80 * i), f'region text {i}', fontsize=14)
    doc.save(src)
    regions = [({'id': f'r{i}', 'page': 1, 'bbox': [10, 20 + 80 * i, 300, 60 + 80 * i]}, 'Text') for i in range(3)]
    regions.append(({'id': 'r3', 'page': 1, 'bbox': [10, 260, 300, 300]}, 'Caption'))     # another forced type
    runs = []

    def fake_extract(engine, pdf, sha, page, raw_root, version, log=print, lang=None, profile='standard',
                     marker_extra=None, subdir=None, last=None):
        runs.append((engine, Path(pdf).name, page, last, tuple(marker_extra or ())))
        return {'engine': engine, 'status': 'OK', 'raw_json': f'raw/{subdir}/x.json', 'version': version}

    monkeypatch.setattr(extraction, 'engine_version', lambda e: 'v1')
    monkeypatch.setattr(extraction, 'extract_page', fake_extract)
    r0_crop = []                                                      # r0 was read alone before: reused, not rerun
    real_crop = extraction._region_crop

    def crop(out, src_pdf, region):
        p, sha = real_crop(out, src_pdf, region)
        if region['id'] == 'r0':
            r0_crop.append(p.name)
        return p, sha
    monkeypatch.setattr(extraction, '_region_crop', crop)
    monkeypatch.setattr(extraction, 'cached_page', lambda e, pdf, *a, **k:
                        {'engine': e, 'status': 'OK', 'raw_json': 'raw/old.json'} if Path(pdf).name in r0_crop else None)
    recs = extraction.regions_ocr(tmp_path, src, regions)
    marker = [r for r in runs if r[0] == 'marker']
    docling = [r for r in runs if r[0] == 'docling']
    assert sorted((r[3] or 1, r[4]) for r in marker) == [(1, ('--force_layout_block', 'Caption')), (2, ('--force_layout_block', 'Text'))]
    assert [(r[2], r[3]) for r in docling] == [(1, 3)]              # docling: r1, r2, r3 in one run
    assert recs['r0']['engines']['marker']['raw_json'] == 'raw/old.json'
    assert [recs[i]['engines']['marker']['page'] for i in ('r1', 'r2', 'r3')] == [1, 2, 1]
    assert [recs[i]['engines']['docling']['page'] for i in ('r1', 'r2', 'r3')] == [1, 2, 3]
