"""Stage: document-level structure consolidation (runs once ALL pages are composed and reviewer decisions applied).

Page stages see one page at a time; layout engines too. Structure that only shows across pages is checked and
repaired here, book-wide:

  page furniture            (pipeline.furniture) running heads/feet, page numbers, edge tabs -> excluded
  captions across pages     a figure/table that ends page p without a caption and a caption that opens page
                            p+1 (or a caption that ends page p and a table/figure that opens p+1) -> linked,
                            marked inferred and recommended for review
  heading hierarchy         (pipeline.sections) unnumbered headings take the level of numbered headings with
                            the same engine heading style (book-wide vote); a level jump deeper than one under an
                            unnumbered parent is closed; numbering repeats/backward steps are flagged, never fixed
  boxes across pages        an automatic box group that runs to the end of its page continues on the next page
                            when the next page opens with the same shading or repeats the box label
                            ("ΠΑΡΑΔΕΙΓΜΑ 4.3 (συνέχεια)") -> one group spanning both pages

Safety contract (why this may run automatically):
  * only roles, levels, relations and group annotations change; text, LaTeX, crops and segment identities never do
  * a reviewer decision on a segment always wins (that segment is skipped)
  * every change is recorded in canonical['document_structure']['fixes'] with a stable key, the rule, the
    evidence and before/after; dismissing the key (suggestion_dismiss, undoable) reverts it on the next build
  * anything that would replace entities (merging paragraphs across a page break) is NOT done here: flow keeps
    the continuation relation and merges stay human-triggered
"""
from __future__ import annotations

import re

from ..core.labels import NUMBERED_HEADING, label_prefix
from ..core.model import CAPTIONABLE_KINDS, chosen_text
from ..core.reasons import flag
from ..core.text import fold, strip_markup
from .groups import gaps
from .sections import jump_key, revoke_key
from .structure import TONE_TOLERANCE, paper_tone, tone_distance
from .structure import key as skey

_CONTINUED = re.compile(r'(?<!\w)(συνέχεια|συνεχ\.|continued|cont\.|cont(?!\w)|suite(?!\w)|fortsetzung)(?!\w)', re.I)
_HUMAN_CAPTION_ACTIONS = {'associate_caption', 'detach_caption'}


def _fix(kind, rule, blocks, explanation_el, before=None, after=None, before_el=None, after_el=None,
         subject=None, **evidence) -> dict:
    """One automatic correction as the reviewer reads it: WHAT (subject), the original state and the result
    (before_el -> after_el), why (explanation, rule) and the stable key that undoes it."""
    ids = [b['id'] for b in blocks]
    main = subject if subject is not None else blocks[-1]
    return {'key': skey('doc:' + kind, ids), 'kind': kind, 'rule': rule, 'blocks': ids,
            'pages': sorted({b['page'] for b in blocks}), 'before': before, 'after': after,
            'before_el': before_el, 'after_el': after_el,
            'subject': (chosen_text(main) or '')[:90] if isinstance(main, dict) else main,
            'explanation_el': explanation_el, 'evidence': evidence}


def real_changes(fixes: list[dict]) -> list[dict]:
    """Only corrections that CHANGED something are reported (and undoable): a record whose state before equals
    its state after is evidence that agreed with the engine, not a correction — listing it invites an undo that
    can do nothing."""
    return [f for f in fixes if not (f.get('before') is not None and f.get('before') == f.get('after'))
            and not (f.get('before_el') and f.get('before_el') == f.get('after_el'))]


def level_el(lv) -> str:
    return 'όχι τίτλος' if not lv else f'Επίπεδο {lv}'


def _human(b, actions) -> bool:
    return any(d.get('action') in actions for d in b.get('decisions', []))


# ---------------------------------------------------------------- captions across a page break
def _body(blocks, page):
    return [b for b in blocks if b['page'] == page and b.get('role') not in ('page_header', 'page_footer')]


def captions_across_pages(blocks, dismissed) -> list[dict]:
    """Must run before captions.finalize (which flags captions still without a figure)."""
    pages = sorted({b['page'] for b in blocks})
    by_id = {b['id']: b for b in blocks}
    fixes = []
    for p, q in zip(pages, pages[1:]):
        if q != p + 1:
            continue                                   # not consecutive: the break is not observed
        end, start = _body(blocks, p), _body(blocks, q)
        if not end or not start:
            continue
        pairs = []
        # figure/table ends p (no caption), captions open q   — or captions end p, table/figure opens q
        if end[-1]['kind'] in CAPTIONABLE_KINDS and not end[-1]['relations'].get('captions'):
            pairs.append((end[-1], _leading_captions(start)))
        if start[0]['kind'] in CAPTIONABLE_KINDS and not start[0]['relations'].get('captions'):
            pairs.append((start[0], _leading_captions(list(reversed(end)))[::-1]))
        for fig, caps in pairs:
            caps = [c for c in caps if not _human(c, _HUMAN_CAPTION_ACTIONS)]
            if not caps or _human(fig, _HUMAN_CAPTION_ACTIONS):
                continue
            f = _fix('caption_across_pages', 'figure and caption on the two sides of a page break', [fig] + caps,
                     f'Το σχήμα/πίνακας της σελ. {fig["page"]} και η λεζάντα της σελ. {caps[0]["page"]} βρίσκονται '
                     'εκατέρωθεν της αλλαγής σελίδας· συνδέθηκαν (προτείνεται έλεγχος)',
                     before=None, after={'caption_for': fig['id']}, subject=caps[0],
                     before_el=f'λεζάντα χωρίς εικόνα (σελ. {caps[0]["page"]})',
                     after_el=f'λεζάντα του σχήματος της σελ. {fig["page"]}')
            if f['key'] in dismissed:
                continue
            for c in caps:
                c['relations'].update(caption_for=fig['id'], inferred=True, caption_link_source='across pages')
                fig['relations'].setdefault('captions', []).append(c['id'])
                flag(c, 'CAPTION_ACROSS_PAGES', figure=fig['id'], figure_page=fig['page'])
            fig['relations']['captions'].sort(key=lambda cid: (by_id[cid]['page'], by_id[cid].get('reading_order', 0)))
            fixes.append(f)
    return fixes


def _leading_captions(seq):
    out = []
    for b in seq:
        if b['kind'] != 'caption' or b['relations'].get('caption_for'):
            break
        out.append(b)
    return out


# ---------------------------------------------------------------- boxes across a page break
def boxes_across_pages(auto_groups, blocks, dismissed) -> tuple[list[dict], list[dict]]:
    """Extends automatic box groups over a page break; returns (groups, fixes)."""
    by_id = {b['id']: b for b in blocks}
    pages = {b['page'] for b in blocks}
    groups = list(auto_groups)
    fixes = []
    for g in list(groups):
        if g not in groups:
            continue                                   # already absorbed by an earlier group
        last = by_id[g['members'][-1]]
        body = _body(blocks, last['page'])
        if not body or body[-1]['id'] != last['id'] or last['page'] + 1 not in pages:
            continue                                   # the box ends before the page does
        nxt = _body(blocks, last['page'] + 1)
        label = _label(g['name'])
        added, rule, absorbed = [], None, None
        twin = next((h for h in groups if h is not g and by_id[h['members'][0]]['page'] == last['page'] + 1
                     and nxt and h['members'][0] == nxt[0]['id'] and label and _label(h['name']) == label), None)
        if twin:                                       # the next page repeats the label: same box, continued
            added, rule, absorbed = twin['members'], 'the next page repeats the box label', twin
        else:
            head_tone = paper_tone(by_id[g['members'][0]])
            if g.get('rule') == 'background colour' and head_tone is not None:
                for b in nxt:
                    t = paper_tone(b)
                    if t is None or tone_distance(t, head_tone) > TONE_TOLERANCE or b['kind'] == 'heading':
                        break
                    added.append(b['id'])
                rule = 'the next page opens with the same box shading'
            elif nxt and nxt[0]['kind'] == 'heading' and _CONTINUED.search(chosen_text(nxt[0])):
                added, rule = [nxt[0]['id']], 'the next page opens with a "continued" heading'
        added = [m for m in added if m not in g['members']]
        if not added:
            continue
        f = _fix('box_across_pages', rule, [by_id[m] for m in g['members'] + added],
                 f'Το πλαίσιο «{g["name"][:60]}» συνεχίζει στη σελ. {last["page"] + 1}: η ομάδα επεκτάθηκε',
                 before=g['members'], after=g['members'] + added, subject=g['name'][:90],
                 before_el=f'πλαίσιο με {len(g["members"])} τμήματα (σελ. {last["page"]})',
                 after_el=f'πλαίσιο με {len(g["members"]) + len(added)} τμήματα (σελ. {last["page"]}–{last["page"] + 1})')
        if f['key'] in dismissed:
            continue
        if absorbed:
            groups.remove(absorbed)
        g.update(members=g['members'] + added, rule=f'{g.get("rule")} + {rule}')
        fixes.append(f)
    return groups, fixes


def _label(name: str) -> str | None:
    lab = label_prefix(strip_markup(name))
    return fold(lab) if lab else None


# ---------------------------------------------------------------- headings inside boxes
def box_headings(groups, blocks, dismissed) -> list[dict]:
    """Unnumbered headings that are members of a labelled box ("ΛΥΣΗ", "STRATEGY") are box sub-headings, not
    book sections. Marks them for a second sections pass; returns fixes (empty = no second pass needed)."""
    by_id = {b['id']: b for b in blocks}
    fixes = []
    for g in groups:
        if g.get('type') != 'labelled_box':
            continue
        head = g['members'][0]
        for mid in g['members'][1:]:
            b = by_id[mid]
            if b['kind'] != 'heading' or b.get('heading_level_source') in ('human', 'labelled_element', 'numbering',
                                                                            'box_heading'):
                continue
            f = _fix('box_heading', 'heading inside a labelled box', [by_id[head], b],
                     f'Ο τίτλος «{chosen_text(b)[:50]}» βρίσκεται μέσα στο πλαίσιο «{g["name"][:50]}»: υπότιτλος του '
                     'πλαισίου, όχι ενότητα του βιβλίου', before=b.get('heading_level'), after='box + 1', subject=b,
                     before_el=f'{level_el(b.get("heading_level"))} (ενότητα βιβλίου)', after_el='υπότιτλος του πλαισίου')
            if f['key'] in dismissed:
                continue
            b['heading_level_source'], b['box_head'] = 'box_heading', head
            fixes.append(f)
    return fixes


# ---------------------------------------------------------------- heading hierarchy (applied in pipeline.sections)
EVIDENCE_EL = {'style_of_checked': 'ίδιο στυλ με ελεγμένο τίτλο', 'human_style': 'ίδιο στυλ με τίτλο που όρισες εσύ', 'style_of_numbered': 'ίδιο στυλ με αριθμημένες ενότητες',
               'typography': 'μέγεθος/στυλ γραμμάτων', 'running_head': 'κεφαλίδες σελίδων', 'chapter_label': 'ετικέτα κεφαλαίου',
               'engine_rank': 'ετικέτα Marker', 'outline': 'λίστα περιεχομένων', 'model': 'τοπικό μοντέλο (2ο πέρασμα)'}


def heading_fixes(blocks) -> list[dict]:
    """Records the hierarchy repairs pipeline.sections made (same keys it checks for dismissal)."""
    out = []
    for b in blocks:
        src = b.get('heading_level_source')
        if src in EVIDENCE_EL:
            ev = ' + '.join(EVIDENCE_EL[x] for x in b.get('heading_evidence') or [src] if x in EVIDENCE_EL)
            engine = (b.get('heading_hints') or {}).get('engine_rank')
            if engine == b['heading_level']:
                continue                                  # the evidence confirmed the engine: nothing was corrected
            f = _fix('heading_style', f'heading level from checked evidence: {", ".join(b.get("heading_evidence") or [src])}',
                     [b], f'Επίπεδο {b["heading_level"]} από: {ev}', before=engine, after=b['heading_level'],
                     before_el=f'{level_el(engine)} (ετικέτα Marker)' if engine else 'χωρίς επίπεδο από τη μηχανή',
                     after_el=level_el(b['heading_level']))
            f['key'] = revoke_key(b['id'], b['heading_level'])       # undo = this level, not every future one
            out.append(f)
        if src == 'shifted_with_parent':
            sh = b.get('heading_shift') or {}
            f = _fix('level_shift', 'the reviewer moved the parent section; its sub-headings keep their relative depth', [b],
                     f'Ο γονικός τίτλος μετακινήθηκε κατά {sh.get("by", 0):+d} επίπεδα: ο υπότιτλος ακολούθησε',
                     before=sh.get('from'), after=b['heading_level'], before_el=level_el(sh.get('from')),
                     after_el=f'{level_el(b["heading_level"])} (ακολούθησε τον γονέα)')
            f['key'] = revoke_key(b['id'], b['heading_level'])
            out.append(f)
        if src == 'level_jump_closed':
            f = _fix('level_jump', 'heading more than one level below its parent', [b],
                     f'Ο τίτλος ήταν {b.get("heading_level_before")} επίπεδα βαθιά χωρίς ενδιάμεσο γονέα· '
                     f'μπήκε στο επίπεδο {b["heading_level"]}', before=b.get('heading_level_before'),
                     after=b['heading_level'], before_el=level_el(b.get('heading_level_before')), after_el=level_el(b['heading_level']))
            f['key'] = jump_key(b['id'], b.get('heading_level_before'), b['heading_level'])
            out.append(f)
    return out


# ---------------------------------------------------------------- groups after reordering (flags only)
def group_issues(group_list, blocks) -> list[dict]:
    """A group whose members are no longer next to each other (e.g. after a reviewer moved segments) is not boxed
    in the output and is reported, instead of a box silently stretching over unrelated content."""
    by_id = {b['id']: b for b in blocks}
    out = []
    for g in group_list:
        between = gaps(g['members'], blocks)
        if between:
            first = by_id[g['members'][0]]
            out.append({'type': 'group_not_contiguous', 'block': between[0], 'page': by_id[between[0]]['page'],
                        'group': g['id'], 'between': between,
                        'explanation_el': f'Η ομάδα «{(g.get("name") or g["type"])[:50]}» (σελ. {first["page"]}) δεν είναι πια '
                                          f'συνεχόμενη: {len(between)} τμήμα(τα) ανάμεσα στα μέλη της. Δεν εμφανίζεται ως '
                                          'πλαίσιο μέχρι να μετακινηθούν ή να αλλάξουν τα μέλη.'})
    return out


# ---------------------------------------------------------------- heading audit (flags only)
_NUM = NUMBERED_HEADING


def heading_issues(blocks, toc) -> list[dict]:
    """Numbering that repeats or steps backwards is most likely a misread number: flagged for review, not fixed.
    Numbers missing between consecutive processed pages are reported (a heading may have been read as text)."""
    by_id = {b['id']: b for b in blocks}
    processed = {b['page'] for b in blocks}
    issues, seen, last = [], {}, {}                   # last: parent number -> (previous sibling, its page)
    for s in toc:
        if s['source'] != 'numbering':
            continue
        m = _NUM.match(s['title'])
        if not m:
            continue
        num = tuple(int(x) for x in re.split(r'[.\-]', m.group(1)))
        b, prev = by_id[s['id']], last.get(num[:-1])
        if num in seen:
            flag(b, 'HEADING_NUMBER_DUPLICATE', first=seen[num])
            issues.append({'type': 'duplicate', 'block': s['id'], 'page': s['page'], 'number': m.group(1),
                           'explanation_el': f'Ο αριθμός ενότητας {m.group(1)} εμφανίζεται δεύτερη φορά'})
        elif prev and num[-1] < prev[0][-1]:
            flag(b, 'HEADING_NUMBER_OUT_OF_ORDER', previous='.'.join(map(str, prev[0])))
            issues.append({'type': 'out_of_order', 'block': s['id'], 'page': s['page'], 'number': m.group(1),
                           'explanation_el': f'Η ενότητα {m.group(1)} έρχεται μετά την '
                                             f'{".".join(map(str, prev[0]))}: πιθανή λάθος ανάγνωση αριθμού'})
        elif prev and num[-1] > prev[0][-1] + 1 and all(p in processed for p in range(prev[1], s['page'] + 1)):
            issues.append({'type': 'gap', 'block': s['id'], 'page': s['page'], 'number': m.group(1),
                           'explanation_el': f'Λείπει ενότητα ανάμεσα στην {".".join(map(str, prev[0]))} και την '
                                             f'{m.group(1)} (σελ. {prev[1]}–{s["page"]}): ίσως ο τίτλος '
                                             'διαβάστηκε ως κείμενο'})
        seen.setdefault(num, s['id'])
        last[num[:-1]] = (num, s['page'])
    return issues


# ---------------------------------------------------------------- automatic groups (entity extents) as reports
EXTENT_OPTIONS = 8              # segments after the current end offered as a "correct end"


def group_reports(group_list, blocks) -> list[dict]:
    """Every AUTOMATIC group (an entity with its whole extent, pipeline.extents) reported like an automatic
    correction: what it is, before -> after, the rule, and the segments around its end so the reviewer can set the
    correct end. Its key is the group's own (undo = dismiss the group)."""
    flow = [b for b in blocks if b.get('flow') != 'furniture' and not b.get('excluded')]
    idx = {b['id']: i for i, b in enumerate(flow)}
    out = []
    for g in group_list:
        if g.get('source') != 'auto' or not g.get('key'):
            continue
        mem = [m for m in g['members'] if m in idx]
        if not mem:
            continue
        a, z = idx[mem[0]], idx[mem[-1]]
        options = [{'id': b['id'], 'text': (chosen_text(b) or f'[{b["kind"]}]')[:70], 'member': b['id'] in g['members']}
                   for b in flow[a + 1:z + 1 + EXTENT_OPTIONS]]
        out.append({'key': g['key'], 'kind': 'auto_group', 'blocks': mem, 'ambiguous': bool(g.get('ambiguous')),
                    'rule': f"{g.get('rule') or ''}; ended by: {g.get('stop') or '—'}",
                    'pages': g['pages'], 'before': None, 'after': len(mem), 'subject': (g.get('name') or '')[:90],
                    'before_el': 'μόνο η ετικέτα (χωρίς ομάδα)', 'after_el': f'ομάδα {len(mem)} τμημάτων (σελ. {"–".join(map(str, sorted({g["pages"][0], g["pages"][-1]})))})',
                    'explanation_el': 'Αυτόματη ομάδα: η οντότητα με όλη της την έκταση (τίτλος, εκφώνηση, υπότιτλοι, παράγραφοι, '
                                      'εξισώσεις, εικόνες, λύση). Μετακίνηση, σειρά και έξοδος εφαρμόζονται σε όλη την ομάδα.',
                    'group': g['id'], 'extent_options': options, 'evidence': {}})
    return out
