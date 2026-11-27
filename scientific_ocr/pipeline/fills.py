"""Stage (document level, beside pipeline.furniture): BACKGROUND FILLS are not uncovered content.

The coverage check (pipeline.coverage) reports printed ink that no engine box covers. Solid fills — the dark or
coloured bar behind «ΕΡΩΤΗΣΗ 1-2», «ΣΗΜΑΝΤΙΚΕΣ ΕΝΝΟΙΕΣ», a shaded box header — are ink too, and were most of
what reviewers hid by hand (Alberts: nearly all 89 uncovered regions were such bars).

A coverage region is a background fill when BOTH hold (two independent signals — never one alone):
  1. it is mostly a solid band (pipeline.coverage._band: >= BAND_MIN of its rows dark across the width), and
  2. an engine READ text on that band: a text block with letters overlaps the band vertically and lies
     within REACH of the region horizontally (the white title printed on the bar was read; the bar itself is
     not content).
Then the region leaves the outputs and the review like page furniture: `excluded` with its signals and crop,
listed under «Εξαιρέθηκαν», restorable (suggestion_dismiss of its key). Its box stays in the model: the bar is
evidence for the start of a box (pipeline.extents) — `fill_for` names the text block on it.
A dark band nobody read text on stays an uncovered region (it may hide white text): never excluded here.
"""
from __future__ import annotations

import re

from ..core import geometry as geo
from ..core.model import chosen_text, in_output
from ..core.reasons import flag

BAND_MIN = 0.25           # share of the region's rows that are a solid band
REACH_UNITS = 24.0        # horizontal distance from the region to the text read on its band (page units)
MIN_LETTERS = 2


MIN_WORD = 3             # an OCR'd word: at least this many letters
MIN_TEXT_LETTERS = 4     # fewer letters than this in a region's OCR is not text («2-2», «—», «Ι»)


def is_text(ocr: str | None) -> bool:
    """Quick OCR of uncovered ink (pipeline.page) reads as TEXT: a word of >= MIN_WORD letters and >= MIN_TEXT_LETTERS
    letters in all. Digits, dashes, single letters (the tail «2-2» of a title on a bar, specks) are not text."""
    if not ocr:
        return False
    letters = re.findall(r'[^\W\d_]', ocr)
    return len(letters) >= MIN_TEXT_LETTERS and bool(re.search(r'[^\W\d_]{%d,}' % MIN_WORD, ocr))


KNOWN_SHARE = 0.5         # with a dictionary: share of the OCR's words (>= MIN_WORD letters) it must know


def reads_as_text(ocr: str | None, known=None) -> bool:
    """is_text, and — when a dictionary is available (ports.Lexicon.known) — at least half of its words are real
    words: OCR of a photo or a bar invents letter strings («Ἔὂμο», «BAS). σσ»), real missed text does not."""
    if not is_text(ocr):
        return False
    if known is None:
        return True
    words = re.findall(r'[^\W\d_]{%d,}' % MIN_WORD, ocr)
    hits = sum(1 for w in words if known(w) or known(w.lower()))
    return hits >= 1 and hits >= KNOWN_SHARE * len(words)


IMAGE_VERDICTS = {'approve', 'mark_image_only'}   # «the image is right» — says nothing about it being TEXT


def _decided(b: dict) -> bool:
    """A reviewer decision that these rules must respect (anything but confirming the machine's image)."""
    return any(d.get('action') not in IMAGE_VERDICTS for d in b.get('decisions') or [])


def _letters(b: dict) -> int:
    return len(re.findall(r'[^\W\d_]', chosen_text(b)))


def classify(blocks: list[dict], dismissed, units: dict | None = None) -> list[dict]:
    """Mutates coverage blocks that are background fills; returns document-structure fixes (dismissable)."""
    units = units or {}
    by_page: dict[int, list[dict]] = {}
    for b in blocks:
        by_page.setdefault(b['page'], []).append(b)
    fixes = []
    for b in blocks:
        band = b.get('ink_band') or {}
        if b.get('source') != 'coverage' or band.get('band', 0) < BAND_MIN or not band.get('band_y') or _decided(b):
            continue
        key = 'fill:' + b['id']
        if key in dismissed:
            continue
        y0, y1 = band['band_y']
        rb = b['geometry']['bbox']
        reach = REACH_UNITS * units.get(b['page'], 1.0)
        on_band = None
        for t in by_page[b['page']]:
            if t is b or not in_output(t) or t.get('source') == 'coverage' or _letters(t) < MIN_LETTERS:
                continue
            tb = t['geometry']['bbox']
            vert = max(0.0, min(tb[3], y1) - max(tb[1], y0)) / max(1e-6, min(tb[3] - tb[1], y1 - y0))
            gap = max(0.0, max(tb[0], rb[0]) - min(tb[2], rb[2]))
            if vert >= 0.5 and gap <= reach:
                on_band = t
                break
        if on_band is None:
            continue
        sig = [f'solid band {round(100 * band["band"])}% of the region', f'text read on the band: «{chosen_text(on_band)[:40]}»']
        b['excluded'] = {'as': 'background_fill', 'key': key, 'fill_for': on_band['id'], 'signals': sig}
        flag(b, 'BACKGROUND_FILL', fill_for=on_band['id'], band=band['band'])
        b['review'], b['display_crop'] = 'not_needed', False
        title = chosen_text(on_band)[:60]
        fixes.append({'key': key, 'kind': 'background_fill', 'rule': '; '.join(sig), 'blocks': [b['id']],
                      'pages': [b['page']], 'before': None, 'after': 'background_fill', 'subject': title,
                      'before_el': 'μελάνι χωρίς κείμενο (για έλεγχο)', 'after_el': 'φόντο τίτλου (εκτός εξόδου)',
                      'explanation_el': f'Συμπαγής ζώνη χρώματος πίσω από τον τίτλο «{title}», που διαβάστηκε κανονικά: '
                                        'είναι το φόντο του, όχι κείμενο που χάθηκε.',
                      'evidence': {'signals': sig, 'fill_for': on_band['id']}})
    return fixes


def band_boxes(blocks: list[dict]) -> dict[str, list]:
    """text block id -> bbox of the background fill it is printed on (the start of a box; pipeline.extents)."""
    out = {}
    for b in blocks:
        ex = b.get('excluded') or {}
        if ex.get('as') == 'background_fill' and ex.get('fill_for'):
            out[ex['fill_for']] = geo.union([b['geometry']['bbox'], out.get(ex['fill_for'], b['geometry']['bbox'])])
    return out


# ---------------------------------------------------------------- empty fragments and duplicate readings
PICTURE_KINDS = {'figure', 'chart', 'molecular_structure', 'reaction_scheme', 'lewis_structure'}
INSIDE = 0.9              # share of a box inside another
EDGE = 0.02               # distance from the page edge, as a share of the page's smaller side
TINY = 0.0015             # area, as a share of the page
EDGE_MAX_AREA = 0.03      # at the edge: only slivers (a whole picture region missed by one engine is not one)
ENGINE_SOURCES = {'marker', 'docling', 'coverage'}


def _readings(b: dict) -> list[str]:
    out = [b['chosen'].get('text') or '']
    out += [c['text'] for c in b.get('candidates') or [] if isinstance(c.get('text'), str)]
    return [re.sub(r'<[^>]+>', ' ', t) for t in out]


def _key_text(t: str) -> str:
    return re.sub(r'\W+', '', t).casefold()


def fragments(blocks: list[dict], dismissed, dims: dict, known=None) -> list[dict]:
    """Boxes that cannot lose anything, excluded like furniture (evidence kept, restorable):
      no_text_ink        uncovered ink whose own quick OCR reads no text (reads_as_text: no word, or — with the
                         book's dictionaries — mostly invented letter strings);
      empty_fragment     no engine read a single letter or digit in it, it is not a caption, and it lies inside a
                         picture, at the very edge of the scan, or is tiny (specks, scan borders, photo slivers);
      duplicate_reading  >= INSIDE of it lies inside a LARGER text box whose text already contains all of its text
                         (two engines or a region reading the same lines twice).
    Segments with a reviewer decision are never touched."""
    by_page: dict[int, list[dict]] = {}
    for b in blocks:
        if in_output(b):
            by_page.setdefault(b['page'], []).append(b)
    fixes = []
    for b in [x for x in blocks if in_output(x)]:
        if _decided(b) or b['kind'] in PICTURE_KINDS or b['kind'] == 'caption' \
                or b.get('source') not in ENGINE_SOURCES:
            continue                                     # (never a full-page fallback or a reviewer's region)
        bb = b['geometry']['bbox']
        W, H = dims.get(b['page']) or (600, 800)
        mates = [x for x in by_page[b['page']] if x is not b and in_output(x)]
        why = None
        if b.get('source') == 'coverage' and b.get('ink_text') is not None and not reads_as_text(b['ink_text'], known):
            why = ('no_text_ink', [f'OCR of the region reads no text: «{(b["ink_text"] or "").strip()[:30]}»'])
        elif not any(re.search(r'[^\W_]', t) for t in _readings(b)):
            inside_pic = any(geo.covered_by(bb, x['geometry']['bbox']) >= INSIDE for x in mates if x['kind'] in PICTURE_KINDS)
            small = geo.area(bb) / (W * H)
            edge = min(bb[0], bb[1], W - bb[2], H - bb[3]) / min(W, H) < EDGE and small < EDGE_MAX_AREA
            tiny = small < TINY
            if inside_pic or edge or tiny:
                why = ('empty_fragment', ['no letter or digit in any reading',
                                          'inside a picture' if inside_pic else 'at the edge of the scan' if edge else 'tiny'])
        else:
            mine = _key_text(chosen_text(b))
            for x in mates:
                xb = x['geometry']['bbox']
                bigger = geo.area(xb) > geo.area(bb) or (geo.area(xb) == geo.area(bb) and x['id'] < b['id'])
                if x['kind'] in PICTURE_KINDS or not bigger or geo.covered_by(bb, xb) < INSIDE:
                    continue
                if mine and mine in _key_text(chosen_text(x)):
                    why = ('duplicate_reading', [f'inside a larger box that already reads all of it ({x["id"]})'])
                    break
        if why is None:
            continue
        key = f'{why[0]}:{b["id"]}'
        if key in dismissed:
            continue
        b['excluded'] = {'as': why[0], 'key': key, 'signals': why[1]}
        flag(b, {'empty_fragment': 'EMPTY_FRAGMENT', 'no_text_ink': 'NO_TEXT_INK'}.get(why[0], 'DUPLICATE_READING'), signals=why[1])
        b['review'], b['display_crop'] = 'not_needed', False
        fixes.append({'key': key, 'kind': why[0], 'rule': '; '.join(why[1]), 'blocks': [b['id']], 'pages': [b['page']],
                      'before': None, 'after': why[0], 'subject': chosen_text(b)[:60] or '[χωρίς κείμενο]',
                      'before_el': 'πλαίσιο για έλεγχο',
                      'after_el': {'empty_fragment': 'κενό κομμάτι (εκτός εξόδου)', 'no_text_ink': 'μελάνι χωρίς κείμενο (εκτός εξόδου)'}
                      .get(why[0], 'διπλή ανάγνωση (εκτός εξόδου)'),
                      'explanation_el': {'empty_fragment': 'Καμία μηχανή δεν διάβασε γράμμα ή ψηφίο εδώ και βρίσκεται μέσα σε εικόνα, '
                                                           'στην άκρη της σάρωσης ή είναι πολύ μικρό.',
                                         'no_text_ink': 'Μελάνι που δεν κάλυψε καμία μηχανή· το OCR της ίδιας της περιοχής δεν βρίσκει '
                                                        'λέξεις (μόνο αριθμούς, παύλες ή σκόρπια γράμματα, π.χ. το τέλος ενός τίτλου).'}
                      .get(why[0], 'Όλο το κείμενό του υπάρχει ήδη στο μεγαλύτερο πλαίσιο που το καλύπτει.'),
                      'evidence': {'signals': why[1]}})
    return fixes
