"""Use case — projection: immutable raw OCR + effective human events -> canonical.json, chapter.md, review snapshot.

Pipes & filters, each stage idempotent:
  assemble pages (engine blocks, coverage, reviewer regions, rules, checks) -> captions -> enrich (specialised
  models, content-addressed cache) -> overlay decisions (commands) -> document structure (furniture, hierarchy,
  boxes) -> entity classification (types/families per segment, reviewer assignments, rule statistics) ->
  annotations (references, triage, rule suggestions, LaTeX check) -> audit -> render -> audit export -> write.
Never runs page OCR. Safe to run any number of times; outputs are replaced atomically.
"""
from __future__ import annotations

import json
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..adapters import latex_render, specialized
from ..adapters import pdf as pdfio
from ..adapters.event_store import EventError, EventStore
from ..adapters.storage import (
    PREVIEW_DIR,
    CorruptFile,
    atomic_write,
    book_lock,
    read_json,
    read_json_strict,
)
from ..core import joblog, lineage
from ..core.decisions import event_pages, page_of
from ..core.reasons import flag
from ..core.model import content_hash, page_summary, release_status
from ..pipeline import (
    audit,
    captions,
    document,
    enrich,
    entities,
    flow,
    furniture,
    groups,
    latex_check,
    markdown,
    merge,
    notation,
    overlay,
    references,
    report,
    rule_suggest,
    sections,
    structure,
    structure_review,
    triage,
)
from ..pipeline.context import PageContext
from ..pipeline.page import assemble
from ..ports import Log, Progress, SpecializedRunner

SCHEMA_VERSION = '2.0'


def build(out: Path, log: Log = print, allow_inference: bool = True,
          runner: SpecializedRunner = specialized.run, progress: Progress | None = None) -> int:
    """Exit codes: 0 ok, 3 source hash changed, 4 event log unreadable, 5 integrity failure, 6 required input
    missing or unreadable.
    Serialised per book (storage.book_lock): concurrent builds from the UI and the CLI wait for each other."""
    with book_lock(out):
        return _build(out, log, allow_inference, runner, progress)


@dataclass
class _Inputs:
    src: dict
    pdf: Path
    extraction: dict
    regions_index: dict
    human: dict


def _build(out: Path, log: Log, allow_inference: bool, runner: SpecializedRunner, progress: Progress | None) -> int:
    """Phases: load inputs -> compose pages -> apply decisions -> document structure -> annotations -> publish."""
    progress = progress or (lambda *a, **k: None)
    clock, t0 = {}, time.perf_counter()

    def lap(name):
        nonlocal t0
        clock[name] = round(time.perf_counter() - t0, 2)
        t0 = time.perf_counter()
    inp = _load_inputs(out, log)
    if isinstance(inp, int):
        return inp
    prev = read_json(out / 'canonical.json') or {}
    lap('load')
    blocks, pages, contexts, versions = _compose_pages(out, inp, progress)
    lap('compose pages')
    progress('models', 0, 1)
    spec = enrich.enrich(out, blocks, runner, allow_inference, log)
    _seal_machine_result(blocks)
    lap('specialised models (cache)')
    progress('render', 0, 1)
    result, merges = _apply_decisions(blocks, pages, contexts, inp)
    notation_summary = notation.annotate(blocks)          # one notation for text content (derived; source kept)
    lap('reviewer decisions')
    dims = {int(p): d for p, d in inp.src['page_dims_pt'].items()}
    heading_prior = [tuple(o) for c in _calibration_books(out) for o in ((c.get('heading_review') or {}).get('outcomes') or [])]
    model = structure_review.model_levels(read_json(out / structure_review.FILE) or {},   # 2nd-pass votes = hints
                                          inp.human.get('dismissed') or ())
    struct = _document_structure(blocks, pages, inp.human, dims, heading_prior, model)
    lap('document structure')
    struct.update(_classify_entities(out, blocks, struct, inp.human, dims))
    lap('entities')
    annotations = _annotations(out, blocks, inp.human, result)
    lap('annotations')
    for info in pages:
        info.update(page_summary([b for b in blocks if b['page'] == info['page']]))
    canonical = {
        'schema_version': SCHEMA_VERSION, 'document_id': inp.src['sha256'][:16],
        'source': {k: inp.src[k] for k in ('input_path', 'retained_copy', 'sha256', 'page_count', 'pages_processed', 'pages_removed',
                                           'page_dims_pt', 'coord_system', 'rasters')},
        'run': {'built_utc': datetime.now(timezone.utc).isoformat(), 'engine_versions': versions,
                'specialized': spec, 'allow_inference': allow_inference, 'latex_check': annotations.pop('latex_check')},
        'human': {'events': 'events.jsonl', 'last_seq': max((e['seq'] for e in inp.human['history']), default=0),
                  'decisions_applied': result['applied'], 'stale': result['stale'],
                  'superseded': result['superseded'], 'merges': merges, 'rules': inp.human['rules'],
                  'regions': inp.human['regions'], 'hidden': inp.human['hidden'], 'profiles': inp.human['profiles']},
        'pages': pages, **struct, **annotations, 'notation': notation_summary, 'blocks': blocks,
    }
    code = _publish(out, canonical, inp, spec, len(result['stale']), log)
    lap('audit + render + write')
    for line in change_report(prev, canonical, inp.human['history']) + change_facts(prev, canonical, inp.human['history']):
        log(line)
    log('timings (s): ' + ', '.join(f'{k} {v}' for k, v in clock.items()))
    b = canonical['blocks']
    log(joblog.fact('built', blocks=len(b), required=sum(x['review'] == 'required' for x in b),
                    recommended=sum(x['review'] == 'recommended' for x in b),
                    critical=sum(f['severity'] == 'critical' for f in canonical['findings']),
                    release_status=canonical['release_status'], seconds=round(sum(clock.values()), 1), timings=clock))
    atomic_write(out / 'cache' / 'build.json', json.dumps({'seconds': round(sum(clock.values()), 1), 'timings': clock,
                                                         'built_utc': canonical['run']['built_utc']}))
    for f in (out / PREVIEW_DIR).glob('p*.json'):     # the full build supersedes every page preview
        f.unlink(missing_ok=True)
    return code


def change_facts(prev: dict, c: dict, history: list[dict]) -> list[str]:
    """The same information as change_report, as FACT lines (core.joblog) for the plain-language job summary."""
    since = (prev.get('human') or {}).get('last_seq')
    new = [e for e in history if since is not None and e['seq'] > since]
    keep = ('type', 'action', 'block_id', 'entity_type', 'pages', 'include', 'level')
    out = [joblog.fact('trigger', count=len(new), first=since is None,
                       events=[{k: e[k] for k in keep if k in e} for e in new[-50:]])]
    if not prev:
        return out
    count = lambda x, pred: sum(1 for b in x.get('blocks', []) if pred(b))
    review = {name: [count(prev, pred), count(c, pred)] for name, pred in (
        ('required', lambda b: b['review'] == 'required'), ('recommended', lambda b: b['review'] == 'recommended'),
        ('omitted', lambda b: b.get('omitted')), ('furniture', lambda b: b.get('excluded')))}
    old_toc = {s['id']: s['level'] for s in prev.get('sections', [])}
    levels = [{'title': s['title'][:60], 'before': old_toc.get(s['id']), 'after': s['level']}
              for s in c.get('sections', []) if old_toc.get(s['id']) != s['level']]
    pf, cf = Counter(f['type'] for f in prev.get('findings', [])), Counter(f['type'] for f in c.get('findings', []))
    ps = {p['page']: p.get('state') for p in prev.get('pages', [])}
    ent = lambda x: [len(x.get('entities') or []), sum(e['verified'] for e in x.get('entities') or [])]
    out.append(joblog.fact(
        'changes', review={k: v for k, v in review.items() if v[0] != v[1]},
        entities={'total': [ent(prev)[0], ent(c)[0]], 'verified': [ent(prev)[1], ent(c)[1]]},
        levels=levels[:20], levels_total=len(levels),
        findings={k: [pf.get(k, 0), cf.get(k, 0)] for k in sorted(set(pf) | set(cf)) if pf.get(k, 0) != cf.get(k, 0)},
        pages=[{'page': p['page'], 'before': ps.get(p['page']), 'after': p.get('state')}
               for p in c.get('pages', []) if ps.get(p['page']) != p.get('state')][:50],
        release=[prev.get('release_status'), c.get('release_status')],
        heading_proposals=len((c.get('heading_review') or {}).get('proposals') or [])))
    return out


def change_report(prev: dict, c: dict, history: list[dict]) -> list[str]:
    """What this build was FOR and what it changed, for the job log: the reviewer actions since the previous
    build, then the differences in the projection (hierarchy, entities, review load, findings, pages)."""
    lines = []
    since = (prev.get('human') or {}).get('last_seq')
    new = [e for e in history if since is not None and e['seq'] > since]
    if new:
        what = [f"{e['type']}" + (f":{e['action']}" if e.get('action') else '') + (f" {e['block_id']}" if e.get('block_id') else '')
                for e in new[-8:]]
        lines.append(f'trigger: {len(new)} new reviewer event(s): ' + '; '.join(what))
    elif since is None:
        lines.append('trigger: first build with change tracking (no previous projection to compare)')
    else:
        lines.append('trigger: no new reviewer events (rebuild: code, models or settings)')
    if not prev:
        return lines
    old_toc = {s['id']: s['level'] for s in prev.get('sections', [])}
    new_toc = {s['id']: (s['level'], s['title'][:40]) for s in c.get('sections', [])}
    lv = [f'«{t}» {old_toc.get(i, "—")}→{l}' for i, (l, t) in new_toc.items() if old_toc.get(i) != l]
    lv += [f'{i} no longer a heading' for i in old_toc if i not in new_toc]
    if lv:
        lines.append('hierarchy: ' + '; '.join(lv[:10]) + (' …' if len(lv) > 10 else ''))
    count = lambda x, pred: sum(1 for b in x.get('blocks', []) if pred(b))
    for name, pred in (('required', lambda b: b['review'] == 'required'), ('recommended', lambda b: b['review'] == 'recommended'),
                       ('omitted', lambda b: b.get('omitted')), ('furniture', lambda b: b.get('excluded'))):
        a, b = count(prev, pred), count(c, pred)
        if a != b:
            lines.append(f'{name}: {a} → {b}')
    pe, ce = len(prev.get('entities') or []), len(c.get('entities') or [])
    pv, cv = sum(e['verified'] for e in prev.get('entities') or []), sum(e['verified'] for e in c.get('entities') or [])
    if (pe, pv) != (ce, cv):
        lines.append(f'entities: {pe} ({pv} verified) → {ce} ({cv} verified)')
    pf = Counter(f['type'] for f in prev.get('findings', []))
    cf = Counter(f['type'] for f in c.get('findings', []))
    diff = [f'{k} {pf.get(k, 0)}→{cf.get(k, 0)}' for k in sorted(set(pf) | set(cf)) if pf.get(k, 0) != cf.get(k, 0)]
    if diff:
        lines.append('findings: ' + ', '.join(diff))
    ps = {p['page']: p.get('state') for p in prev.get('pages', [])}
    st = [f"p{p['page']} {ps.get(p['page'])}→{p.get('state')}" for p in c.get('pages', []) if ps.get(p['page']) != p.get('state')]
    if st:
        lines.append('pages: ' + ', '.join(st))
    hp = len((c.get('heading_review') or {}).get('proposals') or [])
    if hp:
        lines.append(f'heading-level proposals open: {hp}')
    if prev.get('release_status') != c.get('release_status'):
        lines.append(f"release status: {prev.get('release_status')} → {c.get('release_status')}")
    if len(lines) == 1:
        lines.append('no visible change in the projection')
    return lines


def _load_inputs(out: Path, log: Log):
    """Required inputs, verified; an int is an exit code (previous outputs stay untouched)."""
    try:
        src = read_json_strict(out / 'source' / 'source.json')
        ex = read_json_strict(out / 'raw' / 'extraction_index.json', {})
        regions_index = read_json_strict(out / 'raw' / 'regions' / 'index.json', {})
    except CorruptFile as exc:
        log(f'REQUIRED INPUT UNREADABLE: {exc}. Previous outputs left untouched.')
        return 6
    if not src:
        log('no source.json: the book was never prepared. Previous outputs left untouched.')
        return 6
    pdf = out / src['retained_copy']
    if pdfio.sha256_file(pdf) != src['sha256']:
        log('CRITICAL: retained PDF hash changed; aborting')
        return 3
    try:
        human = EventStore(out).state()
    except EventError as exc:
        log(f'EVENT LOG INVALID: {exc}. Previous outputs left untouched.')
        return 4
    removed = set(human['removed_pages'])           # pages the reviewer left out: raw OCR kept, outputs skip them
    src = {**src, 'pages_processed': [p for p in src['pages_processed'] if p not in removed],
           'pages_removed': sorted(removed)}
    return _Inputs(src, pdf, ex, regions_index, human)


def _compose_pages(out: Path, inp: _Inputs, progress):
    ex, src = inp.extraction, inp.src
    versions = {e: next((r.get('version') for r in d.values()), None) for e, d in ex.items()}
    blocks, pages, contexts = [], [], {}
    for i, page in enumerate(src['pages_processed']):
        progress('compose', i, len(src['pages_processed']), page=page)
        ctx = PageContext.load(out, inp.pdf, src['sha256'], page, src['page_dims_pt'][str(page)], ex, versions,
                               inp.human['rules'], imaging=pdfio)
        contexts[page] = ctx
        seq, info = assemble(ctx, inp.human['regions'], inp.regions_index, set(inp.human['hidden']))
        captions.associate(seq, ctx.docling, ctx.unit)
        blocks += seq
        pages.append(info)
    return blocks, pages, contexts, versions


def _seal_machine_result(blocks) -> None:
    """Record the AUTOMATIC result — after every machine stage (engines, rules, specialised models), before any
    human decision. It is what the reviewer sees and approves: `approve` pins auto_hash, so it must be taken after
    the specialised models too (they may withdraw a conflicting formula and keep the page excerpt)."""
    for b in blocks:
        b['auto_hash'] = content_hash(b['chosen'])
        b['machine_reasons'] = list(b['reasons'])     # flags before any human input (triage statistics)
        b['machine_image_only'] = b['chosen']['type'] == 'image_only'


def _apply_decisions(blocks, pages, contexts, inp: _Inputs):
    """Decisions on merged segments apply after the merge; all others before it (members contribute their reviewed
    text). Decisions on boxes the reviewer later replaced or hid are superseded by that action, not stale."""
    human, sha = inp.human, inp.src['sha256']
    merged_ids = {merge.merged_id(sha, m) for m in human['merges']}
    result = overlay.apply(blocks, [d for d in human['decisions'] if d['block_id'] not in merged_ids])
    merges = merge.apply(blocks, human['merges'], sha, lambda p, bid, bb: contexts[p].crop(bid, bb, pad=4), pages)
    later = overlay.apply(blocks, [d for d in human['decisions'] if d['block_id'] in merged_ids])
    for k in ('applied', 'stale', 'ignored', 'resolved_by_overlap'):
        result[k] = result[k] + later[k]
    replaced = {x['block_id'] for p in pages for x in p.get('replaced_by_region', []) + p.get('hidden', [])}
    result['superseded'] = [x for x in result['stale'] if x['block_id'] in replaced] + result['ignored']
    result['stale'] = [x for x in result['stale'] if x['block_id'] not in replaced]
    order: dict[int, int] = {}
    for b in blocks:                                  # reading order after possible reordering decisions
        order[b['page']] = order.get(b['page'], 0) + 1
        b['reading_order'] = order[b['page']]
    return result, merges


def _document_structure(blocks, pages, human, dims, heading_prior=(), model=None) -> dict:
    """Book-wide structure: needs every page and every decision (see pipeline.document for the safety contract)."""
    dismissed = set(human['dismissed'])
    toc_ids = frozenset(m for g in human['groups'] if g.get('type') == 'toc' for m in g.get('members', [])) | \
        frozenset(m for e in human['assigned'].values() if e.get('entity_type') == 'toc' for m in e.get('members', []))
    fixes = furniture.classify(blocks, dims, dismissed)
    fixes += document.captions_across_pages(blocks, dismissed)
    fixes += captions.continuations(blocks, {p['page']: p.get('typography_ref') for p in pages},
                                    {p['page']: (p.get('coverage') or {}).get('unit', 1.0) for p in pages}, dismissed)
    captions.finalize(blocks)
    for b in blocks:
        b['lineage'], b['content_source'] = lineage.trace(b), lineage.content_source(b)
    merged_into = {mid: into['into'] for p in pages for into in p.get('merged', []) for mid in into['members']}
    inner = []
    while True:                                       # hierarchy, flow and boxes until box sub-headings settle
        heading_review: dict = {}
        toc = sections.annotate(blocks, dismissed, heading_prior, heading_review,
                                [(p['page'], x.get('text') or '') for p in pages for x in p.get('hidden', [])], model,
                                {p['page']: p.get('typography_ref') for p in pages}, toc_ids)
        flow.annotate(blocks)
        auto_groups = structure.suggest(blocks, toc, pages, dismissed)
        auto_groups, box_fixes = document.boxes_across_pages(auto_groups, blocks, dismissed)
        new = document.box_headings(auto_groups, blocks, dismissed)
        if not new:
            break
        inner += new
    fixes = document.real_changes(fixes + inner + document.heading_fixes(blocks) + box_fixes)
    issues = document.heading_issues(blocks, toc)
    group_list = groups.annotate(blocks, human['groups'], merged_into, auto_groups)
    fixes += document.group_reports(group_list, blocks)
    issues += document.group_issues(group_list, blocks)
    return {'sections': toc, 'groups': group_list, 'document_structure': {'fixes': fixes, 'issues': issues},
            'heading_review': heading_review}


def _classify_entities(out: Path, blocks, struct, human, dims) -> dict:
    """Entity classification (pipeline.entities): after the hierarchy and the boxes, before triage and export.
    Reviewer assignments and verdicts come from the events; rule outcomes of calibration books are priors."""
    prior = [tuple(o) for c in _calibration_books(out) for o in (c.get('entity_outcomes') or [])]
    res = entities.detect(blocks, struct['sections'], struct['groups'], dims, human['entities'], set(human['dismissed']),
                          human['assigned'], prior)
    entities.annotate(blocks, res['entities'])
    return {'entities': res['entities'], 'entity_outcomes': res['outcomes'], 'entity_stats': res['stats']}


def _annotations(out: Path, blocks, human, result) -> dict:
    overrides = {f'{d["block_id"]}#{d.get("n")}': d.get('target') for d in human['decisions']
                 if d['action'] == 'link_reference' and d['seq'] in result['applied']}
    prior = [(a['reason'], 'right' if x.get('ok') else 'wrong') for a in human['accepts'] for x in a.get('sample', [])]
    prior += _calibration_outcomes(out)
    return {'references': references.annotate(blocks, overrides),
            'triage': triage.apply(blocks, prior, human['accepts']),
            'rule_suggestions': rule_suggest.suggest(blocks, human['rules']),
            'latex_check': latex_check.annotate(blocks, latex_render.check)}   # after every content change


def _publish(out: Path, canonical: dict, inp: _Inputs, spec, stale: int, log: Log) -> int:
    """Audit, render and write every output atomically; exit code 5 on an integrity failure."""
    blocks = canonical['blocks']
    canonical['findings'] = audit.pre_render(canonical, out)
    md = markdown.markdown(canonical)                 # the reader-facing document (also checked by the audit)
    canonical['findings'] += audit.post_render(canonical, md, out)
    canonical['release_status'] = release_status(blocks, canonical['findings'])
    canonical['assets'] = sorted({b['source_crop']['path'] for b in blocks if b.get('source_crop')})
    audit.validate_schema(canonical)
    atomic_write(out / 'chapter.md', md)
    atomic_write(out / 'canonical.json', json.dumps(canonical, ensure_ascii=False, indent=2))
    atomic_write(out / 'review_report.html', report.report_html(canonical))
    counts = {'blocks': len(blocks), 'required': sum(b['review'] == 'required' for b in blocks),
              'recommended': sum(b['review'] == 'recommended' for b in blocks),
              'critical': sum(f['severity'] == 'critical' for f in canonical['findings']), 'stale_decisions': stale}
    atomic_write(out / 'run_manifest.json', json.dumps({
        'built_utc': canonical['run']['built_utc'], 'source_sha256': inp.src['sha256'],
        'pages': inp.src['pages_processed'], 'release_status': canonical['release_status'],
        'extraction': inp.extraction, 'specialized': spec, 'counts': counts}, ensure_ascii=False, indent=2, default=str))
    log(f'built: {counts} release_status={canonical["release_status"]}')
    integrity = any(f['severity'] == 'critical' and f['type'].startswith('INTEGRITY') for f in canonical['findings'])
    return 5 if integrity else 0


def _calibration_outcomes(out: Path) -> list[tuple[str, str]]:
    """Reviewer outcomes of the calibration books listed in book.json ('calibrate_with': sibling book ids).

    This is a read-model query ACROSS aggregates: the sibling's projection is read for statistics (which flags
    proved right or wrong under review) and contributes no decision to this book. Decisions of THIS book still
    come only from its own event log."""
    return [o for other in _calibration_books(out) for o in triage.collect(other.get('blocks', []))]


def _calibration_books(out: Path) -> list[dict]:
    """Projections of the calibration books listed in book.json ('calibrate_with': sibling book ids)."""
    ids = (read_json(out / 'book.json', {}) or {}).get('calibrate_with') or []
    return [c for bid in ids if bid != out.name and (c := read_json(out.parent / bid / 'canonical.json'))]


# ---------------------------------------------------------------- page previews (rebuild one page at once)
# After a PAGE-LOCAL action (core.decisions.event_pages) only that page is rebuilt: the page-local stages run on it
# (composition, specialised models from cache, decisions, notation, LaTeX check) and the result is written as a
# PAGE PREVIEW (cache/page_preview/pNNNN.json), never into canonical.json — the book's full projection stays
# consistent for exports and book-wide views until the next full rebuild, which deletes the previews.
# Book-wide fields of a segment (hierarchy, furniture, groups, entities, references, cross-page captions, triage)
# cannot be recomputed from one page: an UNCHANGED segment is taken verbatim from the full projection; a changed
# one keeps those fields from it (BOOK_WIDE_FIELDS / BOOK_WIDE_REASONS). A preview is valid only for the full
# build it was made from (base = run.built_utc).
BOOK_WIDE_FIELDS = ('role', 'excluded', 'heading_level', 'heading_level_source', 'heading_style', 'heading_hints',
                    'heading_evidence', 'heading_shift', 'section_path', 'running_head', 'engine_rank',
                    'style_of_numbered', 'human_style', 'model', 'typography_rel', 'flow', 'entities', 'references',
                    'box_head', 'groups', 'review_triage')
BOOK_WIDE_REASONS = ('EXCLUDED_AS_PAGE_FURNITURE', 'ENGINE_FURNITURE_IN_BODY', 'CAPTION_CONTINUATION',
                     'CAPTION_ACROSS_PAGES', 'HEADING_NUMBER_DUPLICATE', 'HEADING_NUMBER_OUT_OF_ORDER',
                     'REFERENCE_AMBIGUOUS', 'TRIAGE_DOWNGRADED', 'ACCEPTED_BY_SAMPLING')


def preview_path(out: Path, page: int) -> Path:
    return out / PREVIEW_DIR / f'p{page:04d}.json'


def read_preview(out: Path, page: int, canonical: dict | None) -> dict | None:
    """The page's preview if it was made from the current full build, else None."""
    pv = read_json(preview_path(out, page))
    base = ((canonical or {}).get('run') or {}).get('built_utc')
    return pv if pv and base and pv.get('base') == base else None


def dirty_pages(out: Path) -> list[int]:
    """Pages with page-local actions recorded after the last full build that no valid preview includes yet."""
    c = read_json(out / 'canonical.json') or {}
    last = (c.get('human') or {}).get('last_seq', 0)
    evs = EventStore(out).load()
    by_seq = {e['seq']: e for e in evs}
    regions = {e['region']['id']: int(e['region']['page']) for e in evs if e['type'] == 'region_add'}
    merges_ = {e['merge']['id']: int(e['merge']['page']) for e in evs if e['type'] == 'merge_add'}
    covered: dict[int, int] = {}
    dirty: set[int] = set()
    for e in evs:
        if e['seq'] <= last:
            continue
        for p in event_pages(e, by_seq, regions, merges_) or ():
            if p not in covered:
                pv = read_preview(out, p, c)
                covered[p] = pv['last_seq'] if pv else last
            if e['seq'] > covered[p]:
                dirty.add(p)
    return sorted(dirty)


def build_page(out: Path, page: int, log: Log = print, runner: SpecializedRunner = specialized.run) -> int:
    """Rebuild ONE page as a preview (see above). 0 ok; 2 nothing to do (no full build yet / page not processed);
    other codes as build()."""
    with book_lock(out):
        prev = read_json(out / 'canonical.json')
        if not prev:
            return 2
        inp = _load_inputs(out, log)
        if isinstance(inp, int):
            return inp
        if page not in inp.src['pages_processed']:
            return 2
        t0 = time.perf_counter()
        one = _Inputs({**inp.src, 'pages_processed': [page]}, inp.pdf, inp.extraction, inp.regions_index, inp.human)
        blocks, pages, contexts, _ = _compose_pages(out, one, lambda *a, **k: None)
        enrich.enrich(out, blocks, runner, True, log)
        _seal_machine_result(blocks)
        sha = inp.src['sha256']
        page_merges = [m for m in inp.human['merges'] if int(m['page']) == page]
        ids = {b['id'] for b in blocks} | {merge.merged_id(sha, m) for m in page_merges}
        human = {**inp.human, 'merges': page_merges, 'decisions': [d for d in inp.human['decisions'] if d['block_id'] in ids]}
        result, _ = _apply_decisions(blocks, pages, contexts, _Inputs(one.src, one.pdf, one.extraction, one.regions_index, human))
        notation.annotate(blocks)
        captions.finalize(blocks)
        old = {b['id']: b for b in prev.get('blocks', []) if b['page'] == page}
        merged = [_preview_block(b, old.get(b['id'])) for b in blocks]
        latex_check.annotate(merged, latex_render.check)
        info = {**pages[0], **page_summary(merged)}
        last = max((e['seq'] for e in inp.human['history']), default=0)
        atomic_write(preview_path(out, page), json.dumps({
            'base': prev['run']['built_utc'], 'page': page, 'last_seq': last, 'built_utc': datetime.now(timezone.utc).isoformat(),
            'applied': result['applied'], 'stale': result['stale'], 'info': info, 'blocks': merged}, ensure_ascii=False))
        log(f'page {page} preview: {len(merged)} segments, {sum(b["review"] == "required" for b in merged)} required, '
            f'{round(time.perf_counter() - t0, 1)} s')
        return 0


def _same(a: dict, b: dict) -> bool:
    seqs = lambda x: [d['seq'] for d in x.get('decisions', [])]
    return (a.get('auto_hash') == b.get('auto_hash') and seqs(a) == seqs(b) and a['chosen'] == b['chosen']
            and a['geometry']['bbox'] == b['geometry']['bbox'] and a['kind'] == b['kind'])


def _preview_block(new: dict, old: dict | None) -> dict:
    """An unchanged segment: the full projection's version (all book-wide fields exact). A changed one: the page
    rebuild's version, keeping the book-wide fields and reasons of the full projection."""
    if old is not None and _same(new, old):
        return {**old, 'reading_order': new['reading_order']}
    if old is None:
        return {**new, 'preview_new': True}
    out = dict(new)
    for k in BOOK_WIDE_FIELDS:
        if k in old:
            out[k] = old[k]
    for r in BOOK_WIDE_REASONS:
        if r in old.get('reasons', []) and r not in out['reasons']:
            flag(out, r, **((old.get('evidence') or {}).get(r) or {}))
    rel_old, rel_new = old.get('relations') or {}, out.get('relations') or {}
    if rel_old.get('caption_for') and not rel_new.get('caption_for') and page_of(rel_old['caption_for']) != out['page']:
        out['relations'] = {**rel_new, 'caption_for': rel_old['caption_for']}    # a caption across pages
    out['preview_changed'] = True
    return out
