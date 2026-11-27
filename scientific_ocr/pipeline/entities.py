"""Stage (document level): ENTITIES of a STEM coursebook — examples, exercises, definitions, figures… — with
their placement, so exports can include or exclude them by type and placement.

Taxonomy (TYPES) and label lexicon (LEXICON) cover Greek and English, upper/lower case, accents, abbreviations
and the usual numbering formats ("ΠΑΡΑΔΕΙΓΜΑ 4.3", "Παράδ. 4-3", "Example 2.1:", "Ex. 3", "Άσκηση 12",
"Π4.3", "Sample Problem 5", "Ερωτήσεις επανάληψης", "Το ήξερες;"…). Sources, strongest first:
  box       a labelled box (automatic or reviewer group): the whole box; its sub-headings (solution, strategy,
            answer, hint…) become 'part' entities inside it
  section   a heading whose title is an entity label: the heading and everything under it in the hierarchy
            (a collective section — "Ασκήσεις", "Review Problems" — is one entity of that type)
  block     engine-recognised kinds: figure / chart / chemical drawing (+ their captions), table, equation,
            chemical equation; engine footnotes
  para      a paragraph that opens with a numbered label ("**Παράδειγμα 2.1** Υπολογίστε…") — low confidence
Placement: inline · margin (outside the text frame, left/right) · end_of_chapter (a collective section that
closes its chapter, and everything inside it).

Sources of truth, strongest first:
  human     the reviewer's own classification (event entity_assign: ordered members + a type or a whole family);
            always verified; a detection with exactly the same members is superseded by it
  detected  the detectors above; VERIFIED only when the reviewer confirms (entity_confirm, may correct the type)
            or dismissed (suggestion_dismiss)
Exports may exclude verified entities only — an unverified one always stays in the output.

Learning (stats): every reviewer verdict on a DETECTED entity is an outcome for the rule that produced it —
right (confirmed with the detected type) or wrong (confirmed with another type, dismissed, or its BOUNDARIES
corrected by the reviewer: the dismissal carries `boundary: true` and the reviewer's own entity replaces it). Per rule, with
enough evidence and a 95% upper error bound <= MAX_ERROR, the rule is TRUSTED: its unverified entities are
offered for one-click bulk confirmation (still a human act — nothing is verified automatically). Outcomes of
calibration books (same series) count as prior evidence.

annotate() writes onto every block the entities it belongs to (outermost first) — the segment-level view used
by the page list, the inspector and the exports.
"""
from __future__ import annotations

import hashlib
import re

from ..core.labels import FIGURE_LABEL
from ..core.model import CHEMICAL_EQUATION_KINDS, FIGURE_KINDS, chosen_text, in_output
from ..core.stats import wilson_upper
from ..core.text import label_key
from ..core.vocabulary import (  # noqa: F401
    FAMILIES,
    GROUP_ENTITY,
    PLACEMENTS,
    TYPES,
    family_of,
    toc_entries,
    valid_entity_type,
)
from .fills import band_boxes
from .furniture import body_frames

VISUAL_TYPES = {'figure', 'chart', 'chemical_drawing'}


# Matched against the label normalised by norm(): no accents, lower case, single spaces. Order matters (first wins).
def _rx(p: str) -> re.Pattern:
    """Patterns are normalised like the labels (casefold maps final sigma ς to σ)."""
    return re.compile(p.replace('ς', 'σ'))


LEXICON = [(t, _rx(p)) for t, p in [
    ('review', r'^(ερωτησεις|ασκησεις|προβληματα) (επαναληψης|κατανοησης|αυτοαξιολογησης|εμπεδωσης)|^επαναληπτικ'
               r'|^review (questions|problems|exercises)|^chapter (review|test)|^self[- ]?(test|assessment|check)'
               r'|^αυτοαξιολογηση|^τεστ\b|^quiz|^test yourself|^ελεγξε τις γνωσεις|^check your (knowledge|progress)'
               r'|^γενικες ασκησεις|^additional (problems|exercises)|^end[- ]of[- ]chapter'),
    ('application', r'καθημεριν|στη ζωη μας|στην πραξη|in (everyday|daily) life|real[- ]world|in action\b'
                    r'|^connections?\b|^εφαρμογ|^applications?\b|^η (χημεια|φυσικη|βιολογια) (γυρω μας|στη)'),
    ('example', r'^(λυμενο |λυμενα )?παραδειγμ|^παραδ\.|^(worked |solved |sample )?examples?\b|^ex\.\s*\d'
                r'|^sample problem|^λυμενες? (ασκησ|προβλημ)'),
    ('exercise', r'^ασκησ|^ασκ\.|^exercises?\b|^practice (exercise|problem)s?|^drill|^εξασκηση|^try this'),
    ('problem', r'^προβλημ|^problems?\b|^προβλ\.'),
    ('question', r'^ερωτησ|^ερωτ\.|^questions?\b|^concept check|^checkpoint|^ελεγχος κατανοησης'
                 r'|^check your understanding|^stop and think|^σκεψου|^think about it|^ερωτημ'),
    ('solution', r'^λυση\b|^λυσεις\b|^solutions?\b'),
    ('answer', r'^απαντησ|^answers?\b'),
    ('strategy', r'^(ακολουθητεα )?στρατηγικ|^strategy|^plan\b|^αναλυση\b|^analy[sz]e\b|^analysis|^σχεδιο λυσης'
                 r'|^δεδομενα\b|^ζητουμενα\b|^given\b'),
    ('hint', r'^υποδειξ|^hints?\b'),
    ('check', r'^ελεγχος\b|^check\b|^does the answer make sense|^επαληθευση'),
    ('definition', r'^ορισμ|^definitions?\b'),
    ('theorem', r'^θεωρημ|^theorem|^λημμα|^lemma|^πορισμ|^corollary|^proposition\b'),
    ('proof', r'^αποδειξ|^proof\b'),
    ('law', r'^νομος\b|^νομοι\b|^law\b|^laws\b|^κανονας|^κανονες|^rule\b|^rules\b|^αρχη (του|της|των)|^principle'
            r'|^αξιωμ|^axiom|^postulate'),
    ('formula', r'^(βασικοι |σημαντικοι )?τυποι\b|^key (equations|formulas)|^formula (box|sheet)|^τυπολογιο'),
    ('caution', r'^προσοχη|^caution|^warning|^ασφαλεια|^safety|^κινδυνος|^danger'),
    ('tip', r'^συμβουλ|^tips?\b|^study tip|^μνημονικ'),
    ('did_you_know', r'^(το )?ηξερες|^γνωριζεις οτι|^did you know|^fun fact|^περιεργ|^το γνωριζατε'),
    ('history', r'^ιστορικ|λιγη ιστορια|^historical|^history\b|^a (bit|little) of history|^ματια στο παρελθον'),
    ('biography', r'^βιογραφ|^biography|^portrait\b|^προσωπικοτητ'),
    ('experiment', r'^πειραμ|^experiment|^εργαστηρ|^lab\b|^laboratory|^investigation|^διερευνησ|^hands[- ]on'),
    ('activity', r'^δραστηριοτητ|^activity|^activities|^εργασια\b|^εργασιες\b|^project|^μικρη ερευνα|^try it'
                 r'|^δοκιμασε|^for discussion|^για συζητηση'),
    ('case_study', r'^μελετη περιπτωσ|^case stud'),
    ('objectives', r'^(μαθησιακοι |διδακτικοι )?στοχοι|^τι θα μαθεις|^learning (objectives|outcomes|goals)'
                   r'|^objectives|^in this chapter|^σε αυτο το κεφαλαιο|^chapter (goals|outline)'),
    ('summary', r'^περιληψη|^συνοψη|^ανακεφαλαιωσ|^summary|^chapter summary|^recap|^in summary|^συμπερασμα'
                r'|^conclusions?\b|^τι μαθαμε|^what (we|you) (have )?learned'),
    ('key_terms', r'^βασικοι οροι|^λεξεις[- ]κλειδια|^βασικες εννοιες|^key (terms|concepts|words|ideas)'
                  r'|^vocabulary|^ορολογια|^important terms'),
    ('glossary', r'^γλωσσαρι|^glossary'),
    ('references', r'^βιβλιογραφ|^για περαιτερω|^further reading|^references\b|^αναφορες\b|^πηγες\b|^suggested reading'),
    ('note', r'^σημειωσ|^note\b|^notes\b|^παρατηρησ|^remarks?\b|^σχολιο|^comment\b|^nb\b'),
    ('toc', r'^περιεχομενα\b|^πινακας περιεχομενων|^(table of )?contents\b'),
]]
COLLECTIVE = _rx(r'^(ασκησεις|προβληματα|ερωτησεις|exercises|problems|questions|review|γενικες ασκησεις'
                        r'|επαναληπτικ|additional problems|end[- ]of[- ]chapter|ερωτησεις και ασκησεις'
                        r'|questions and problems|ασκησεις και προβληματα)')
NUMBER = re.compile(r'(?:^|\s)((?:[ivxlcdm]{1,6}|[α-ω]?\d+(?:[.\-–]\d+)*[α-ωa-z]?))(?=\s|[:.)\]]|$)', re.I)


def norm(t: str) -> str:
    return label_key(t)


def classify(label: str) -> str | None:
    n = norm(label)
    for t, rx in LEXICON:
        if rx.search(n):
            return t
    return None


def number_of(label: str) -> str | None:
    m = NUMBER.search(norm(label)[:60])
    return m.group(1) if m else None


def key(anchor: str, granularity: str) -> str:
    return 'N' + hashlib.sha1(f'entity|{granularity}|{anchor}'.encode()).hexdigest()[:10]


MIN_EVIDENCE = 15
MAX_ERROR = 0.10


ANSWER_COLLECTION = _rx(r'^(απαντησεις|λυσεις|answers|solutions)\b')
ITEM_NUMBER = re.compile(r'^\W{0,3}(\d+(?:[.\-–]\d+)*)[.)]?\s')


def link_answers(entities: list[dict], blocks: list[dict]) -> int:
    """Answers gathered in a collection ("Απαντήσεις ασκήσεων", "Answers to selected problems") are LINKED to the
    exercise/problem/question with the same number — not grouped, not moved. entity['answers'] = block ids;
    block relations 'answer_for' = entity key. Returns the number of links."""
    by_id = {b['id']: b for b in blocks}
    practice = {}
    for e in entities:
        if family_of(e['type']) == 'practice' and e.get('number') and not e.get('collective'):
            practice.setdefault(re.sub(r'[\-–]', '.', e['number']), []).append(e)
    n = 0
    for coll in (e for e in entities if e['type'] == 'answer' and e.get('collective')):
        for m in coll['members'][1:]:
            b = by_id.get(m)
            mt = ITEM_NUMBER.match(re.sub(r'[*_]', '', chosen_text(b) if b else ''))
            for e in practice.get(re.sub(r'[\-–]', '.', mt.group(1)), []) if mt else []:
                e.setdefault('answers', []).append(m)
                b['relations']['answer_for'] = e['key']
                n += 1
    return n


def detect(blocks: list[dict], toc: list[dict], groups: list[dict], dims: dict, confirmed: dict, dismissed: set,
           assigned: dict | None = None, prior: list | None = None) -> dict:
    """Run the detectors in order of strength; a block claimed by a stronger source is not re-claimed by a weaker
    one (except engine kinds, which describe what a block IS, not what it belongs to). The reviewer's own
    entities come first. Returns {'entities', 'outcomes', 'stats'}."""
    d = _Detector(blocks, dims, confirmed, dismissed)
    d.human(assigned or {})
    d.boxes(groups)
    d.banners(band_boxes(blocks))
    d.sections(toc)
    d.labelled_paragraphs()
    d.contents_pages()
    d.margin_notes()
    d.engine_kinds()
    d.placements({s['id']: s for s in toc})
    ents = sorted(d.out, key=lambda e: d.order.get(e['members'][0], 0))
    link_answers(ents, blocks)
    outs = outcomes(ents, d.rejected)
    st = stats(outs + list(prior or []))
    for e in ents:
        e['trusted'] = not e['verified'] and bool((st['rules'].get(e['rule_class']) or {}).get('trusted'))
    return {'entities': ents, 'outcomes': outs, 'stats': st}


def rule_class(gran: str, etype: str) -> str:
    """The unit of learning: which detector, for which type ("box:example", "section:summary")."""
    return f'{gran}:{etype}'


def outcomes(entities: list[dict], rejected: list[dict]) -> list[tuple[str, str]]:
    """(rule class, 'right'|'wrong') for every reviewer verdict on a detected entity."""
    out = [(e['rule_class'], 'right' if e['type'] == e['detected_type'] else 'wrong')
           for e in entities if e['verified'] and e['granularity'] != 'human']
    return out + [(r['rule_class'], 'wrong') for r in rejected]


def stats(outs: list[tuple[str, str]]) -> dict:
    agg: dict[str, dict] = {}
    for rc, o in outs:
        a = agg.setdefault(rc, {'n': 0, 'wrong': 0})
        a['n'] += 1
        a['wrong'] += o == 'wrong'
    for a in agg.values():
        a['error_upper'] = round(wilson_upper(a['wrong'], a['n']), 3)
        a['trusted'] = a['n'] >= MIN_EVIDENCE and a['error_upper'] <= MAX_ERROR
    return {'rules': agg, 'min_evidence': MIN_EVIDENCE, 'max_error': MAX_ERROR}


MAX_SPAN_PAGES = 40         # a boundary correction longer than this is almost certainly a mis-click


def span(blocks: list[dict], first: str, last: str) -> list[str]:
    """The segments from `first` to `last` (inclusive, either order) in the book's reading order, over the same flow
    the detectors use (content in the output; page furniture left out): the members of an entity whose boundaries
    the reviewer set. Raises ValueError when a boundary is not in that flow or the span is implausibly long."""
    flow = [b for b in blocks if in_output(b) and b.get('flow') != 'furniture']
    pos = {b['id']: i for i, b in enumerate(flow)}
    if first not in pos or last not in pos:
        raise ValueError('a boundary is not a segment of the text (page furniture or removed)')
    i, j = sorted((pos[first], pos[last]))
    out = flow[i:j + 1]
    if out[-1]['page'] - out[0]['page'] >= MAX_SPAN_PAGES:
        raise ValueError(f'the span covers more than {MAX_SPAN_PAGES} pages')
    return [b['id'] for b in out]


def annotate(blocks: list[dict], entities: list[dict]) -> None:
    """Segment-level classification: each block lists the entities it belongs to, outermost (largest) first."""
    by_block: dict[str, list] = {}
    for e in entities:
        for m in e['members']:
            by_block.setdefault(m, []).append(e)
    for b in blocks:
        es = sorted(by_block.get(b['id'], []), key=lambda e: -len(e['members']))
        b['entities'] = [{'key': e['key'], 'type': e['type'], 'family': family_of(e['type']), 'verified': e['verified'],
                          'source': 'human' if e['granularity'] == 'human' else 'detected', 'head': e['members'][0] == b['id']}
                         for e in es]


def human_key(members: list[str]) -> str:
    return 'H' + hashlib.sha1(('human|' + '|'.join(members)).encode()).hexdigest()[:10]


class _Detector:
    def __init__(self, blocks, dims, confirmed, dismissed):
        self.flow = [b for b in blocks if in_output(b) and b.get('flow') != 'furniture']
        self.by_id = {b['id']: b for b in self.flow}
        self.order = {b['id']: i for i, b in enumerate(self.flow)}
        self.frames = body_frames(blocks, dims) if blocks else {}
        self.dims, self.confirmed, self.dismissed = dims, confirmed, dismissed
        self.out, self.claimed, self.rejected, self.human_sets = [], {}, [], set()

    def human(self, assigned: dict):
        """The reviewer's own entities (entity_assign): verified, any type or a whole family."""
        for k, ev in assigned.items():
            mem = [m for m in ev['members'] if m in self.by_id]
            if not mem or not valid_entity_type(ev['entity_type']):
                continue
            first = chosen_text(self.by_id[mem[0]]) or ''
            e = self.add(mem[0], 'human', ev['entity_type'], mem, first[:60], 'human', 'reviewer', k=k)
            if e is not None:
                self.human_sets.add(frozenset(mem))

    def add(self, anchor, gran, etype, members, label, confidence, rule, k=None):
        members = [m for m in dict.fromkeys(members) if m in self.by_id]
        if not members or not valid_entity_type(etype) or (gran != 'human' and etype not in TYPES):
            return None
        k = k or key(anchor, gran)
        rc = rule_class(gran, etype)
        if gran != 'human' and frozenset(members) in self.human_sets:
            return None                                   # the reviewer classified exactly this
        if k in self.dismissed:
            self.rejected.append({'key': k, 'rule_class': rc, 'type': etype})
            return None
        conf = self.confirmed.get(k)
        e = {'key': k, 'type': (conf or {}).get('entity_type') or etype, 'detected_type': etype, 'granularity': gran,
             'anchor': anchor, 'label': (label or '')[:120], 'number': number_of(label or ''), 'members': members,
             'pages': sorted({self.by_id[m]['page'] for m in members}), 'confidence': confidence, 'rule': rule,
             'rule_class': rc, 'verified': gran == 'human' or bool(conf), 'placement': 'inline'}
        self.out.append(e)
        for m in members:
            self.claimed.setdefault(m, []).append(k)
        return e

    def outside_frame(self, b) -> bool:
        f = self.frames.get(b['page'])
        W = (self.dims.get(b['page']) or (600, 800))[0]
        x0, x1 = b['geometry']['bbox'][0], b['geometry']['bbox'][2]
        return bool(f) and (x1 <= f[0] + 0.01 * W or x0 >= f[2] - 0.01 * W)

    def boxes(self, groups):
        """Labelled boxes (automatic or reviewer groups) and their sub-headed parts (solution, strategy…)."""
        for g in groups:
            mem = [m for m in g['members'] if m in self.by_id]
            if len(mem) < 2:
                continue
            label = g.get('name') or chosen_text(self.by_id[mem[0]])
            etype = classify(label) or GROUP_ENTITY.get(g.get('type'))
            if not etype:
                continue
            self.add(mem[0], 'box', etype, mem, label, 'high' if number_of(label) else 'medium', f'box «{label[:40]}»')
            heads = [i for i, m in enumerate(mem) if i > 0 and self.by_id[m]['kind'] == 'heading'
                     and (t := classify(chosen_text(self.by_id[m]))) and TYPES[t][0] == 'part']
            for j, i in enumerate(heads):
                stop = heads[j + 1] if j + 1 < len(heads) else len(mem)
                t = chosen_text(self.by_id[mem[i]])
                self.add(mem[i], 'part', classify(t), mem[i:stop], t, 'high', f'sub-heading «{t[:30]}» in box')

    BANNER_SLACK = 8.0          # page units a member may stick out of the banner's column
    BANNER_GAP = 30.0           # largest vertical gap between consecutive members (page units)

    def banners(self, bars: dict):
        """A labelled title printed on a BACKGROUND BAR (pipeline.fills: «ΕΡΩΤΗΣΗ 1-2» on a dark band): the bar is
        the top of a box and gives its COLUMN (label + bar, left to right). Members: the label and the blocks that
        follow it in reading order on the same page inside that column and vertically contiguous, then the text
        that continues them (pipeline.flow continuation links, e.g. onto the next page). Independent of the shading
        detector, which misses light or split boxes."""
        for lid, bar in bars.items():
            lab = self.by_id.get(lid)
            if lab is None or lid in self.claimed:
                continue
            label = chosen_text(lab)
            etype = classify(label)
            if not etype or TYPES[etype][0] in ('insert', 'part') or COLLECTIVE.search(norm(label)):
                continue
            lb = lab['geometry']['bbox']
            x0, x1 = min(lb[0], bar[0]) - self.BANNER_SLACK, max(lb[2], bar[2]) + self.BANNER_SLACK
            members, bottom = [lid], max(lb[3], bar[3])
            for b in self.flow[self.order[lid] + 1:]:
                bb = b['geometry']['bbox']
                if b['page'] != lab['page'] or bb[0] < x0 or bb[2] > x1 or bb[1] < lb[1] or bb[1] - bottom > self.BANNER_GAP \
                        or b['id'] in self.claimed or (b['kind'] == 'heading' and classify(chosen_text(b))):
                    break
                members.append(b['id'])
                bottom = max(bottom, bb[3])
            while (nxt := self.by_id.get(self.by_id[members[-1]]['relations'].get('continues_in'))) is not None \
                    and nxt['id'] not in members and nxt['id'] not in self.claimed:
                members.append(nxt['id'])            # the box's text continues (next column/page)
            if len(members) >= 2:
                self.add(lid, 'box', etype, members, label, 'high', f'label on a background bar «{label[:30]}»')

    def sections(self, toc):
        """A heading whose title is an entity label: the heading and its whole subtree."""
        for s in toc:
            if s['id'] not in self.by_id or s['id'] in self.claimed:
                continue
            etype = classify(s['title'])
            if etype in ('answer', 'solution') and s['level'] <= 2 and ANSWER_COLLECTION.search(norm(s['title'])):
                sub = [x['id'] for x in self.flow if s['id'] in (x.get('section_path') or [])]
                e = self.add(s['id'], 'section', 'answer', sub or [s['id']], s['title'], 'high', 'answers collection')
                if e is not None:
                    e['collective'] = True
                continue                                  # answers gathered elsewhere: linked, not grouped
            if not etype or TYPES[etype][0] == 'part':
                continue
            sub = [x['id'] for x in self.flow if s['id'] in (x.get('section_path') or [])]
            coll = bool(COLLECTIVE.search(norm(s['title'])))
            e = self.add(s['id'], 'section', etype, sub or [s['id']], s['title'],
                         'high' if number_of(s['title']) or coll else 'medium', 'collective section' if coll else 'section title')
            if e is not None:
                e['collective'] = coll

    def labelled_paragraphs(self):
        """A paragraph opening with a bold numbered label ("**Παράδειγμα 2.1** …"): low confidence."""
        for b in self.flow:
            if b['kind'] != 'paragraph' or b['id'] in self.claimed:
                continue
            lead = re.match(r'^\s*\*\*([^*]{2,40})\*\*', chosen_text(b))
            if lead and (et := classify(lead.group(1))) and number_of(lead.group(1)) \
                    and TYPES[et][0] in ('worked', 'practice', 'theory'):
                self.add(b['id'], 'para', et, [b['id']], lead.group(1), 'low', 'numbered label opening a paragraph')

    TOC_MIN_ENTRIES = 3

    def contents_pages(self):
        """Table-of-contents blocks: >= TOC_MIN_ENTRIES lines ending in a page number after dot leaders (or a
        slash), with the entry title right before them on the page (e.g. 'ΚΕΦΑΛΑΙΟ 1 Κύτταρα … / 25')."""
        prev = None
        for b in self.flow:
            if b['id'] in self.claimed or b.get('excluded') or b['chosen'].get('type') not in ('text', 'latex'):
                prev = b
                continue
            if toc_entries(chosen_text(b)) >= self.TOC_MIN_ENTRIES:
                members = [b['id']]
                if prev is not None and prev['page'] == b['page'] and prev['id'] not in self.claimed \
                        and toc_entries(chosen_text(prev)) >= 1 and prev['kind'] in ('heading', 'paragraph'):
                    members.insert(0, prev['id'])
                self.add(members[0], 'block', 'toc', members, 'Πίνακας περιεχομένων', 'medium',
                         'contents entries: titles with page numbers after dot leaders')
            prev = b

    def margin_notes(self):
        """Unlabelled text outside the text frame (after everything labelled has claimed its blocks)."""
        for b in self.flow:
            if b['id'] in self.claimed or b['kind'] in FIGURE_KINDS or b['kind'] == 'caption' or not chosen_text(b):
                continue
            if self.outside_frame(b):
                self.add(b['id'], 'block', 'sidenote', [b['id']], chosen_text(b)[:40], 'medium', 'text outside the text frame')

    def engine_kinds(self):
        """What the layout engines recognised: pictures (+ their captions), tables, equations, footnotes."""
        for b in self.flow:
            caps = [c for c in b['relations'].get('captions', []) if c in self.by_id]
            et = _ENGINE_TYPE.get(b['kind']) or ('footnote' if b.get('role') == 'footnote' else None)
            if not et:
                continue
            label = next((self.by_id[c].get('caption_label') for c in caps if self.by_id[c].get('caption_label')), None) or ''
            self.add(b['id'], 'block', et, [b['id']] + caps, label, 'high', f'engine kind {b["kind"]}')

    def placements(self, tocd):
        """end_of_chapter: inside a collective section that no ordinary section follows; margin: every member
        outside the text frame; otherwise inline."""
        end_sections = set()
        for s in tocd.values():
            if not COLLECTIVE.search(norm(s['title'])):
                continue
            later = [x for x in tocd.values() if x.get('parent') == s.get('parent') and x['id'] in self.by_id
                     and self.order.get(x['id'], -1) > self.order.get(s['id'], -1)]
            if all(COLLECTIVE.search(norm(x['title'])) for x in later):
                end_sections.add(s['id'])
        for e in self.out:
            mem = [self.by_id[m] for m in e['members']]
            if e['anchor'] in end_sections or any(set(b.get('section_path') or []) & end_sections for b in mem):
                e['placement'] = 'end_of_chapter'
            elif mem and all(self.outside_frame(b) for b in mem):
                e['placement'] = 'margin'


_ENGINE_TYPE = {**{k: 'chemical_drawing' for k in FIGURE_KINDS}, 'figure': 'figure', 'chart': 'chart', 'table': 'table',
                'math': 'equation', **{k: 'chemical_equation' for k in CHEMICAL_EQUATION_KINDS}}


def selectors(e: dict) -> list[str]:
    """Export selectors an entity answers to: its type and its family, each alone and @placement."""
    names = [e['type']] + ([f'family:{f}'] if (f := family_of(e['type'])) and not e['type'].startswith('family:') else [])
    return [x for n in names for x in (n, f'{n}@{e["placement"]}')]


def excluded_blocks(entities: list[dict], exclude: set) -> set:
    """Block ids removed by an export filter: members of VERIFIED entities whose type or family (optionally
    @placement) is excluded. Unverified entities never remove anything."""
    gone = set()
    for e in entities:
        if e.get('verified') and exclude.intersection(selectors(e)):
            gone.update(e['members'])
    return gone


def is_captioned_visual(block: dict, entity_type: str | None, caption_blocks: list[dict]) -> bool:
    """Eligible to leave the text flow as a picture: it HAS a caption and it is a visual element — by its entity
    type (engine kind or the reviewer's correction) or by its caption label naming a picture. Equations and text
    that stayed an image because OCR failed are never eligible (no caption, or a caption that is not a picture
    label, or a reviewer type that is not visual)."""
    if not caption_blocks:
        return False
    if entity_type in VISUAL_TYPES:
        return True
    if entity_type in ('equation', 'chemical_equation', 'table') and block['chosen'].get('type') != 'image_only':
        return False
    return any(FIGURE_LABEL.search(norm(c.get('caption_label') or '')) for c in caption_blocks)
