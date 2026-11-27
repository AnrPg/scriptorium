"""Reason registry: the single place that defines what each flag MEANS.

Every flag raised on a block is a Reason code. The registry decides how far it escalates review, whether the
source crop must be visible, whether the audit emits a finding (and how severe), and the plain-language text
shown in Markdown and the UI (en/el). Checks only say *what* they observed; policy lives here.
"""
from __future__ import annotations

from dataclasses import dataclass

REVIEW_ORDER = ['not_needed', 'recommended', 'required']


@dataclass(frozen=True)
class Reason:
    review: str | None                 # minimum review level implied (None = informational)
    crop: bool                         # source crop must be shown next to / instead of the text
    en: str
    el: str
    finding: tuple[str, str] | None = None   # (finding type, severity) emitted by the audit
    stage: str = 'content'             # 'layout' | 'content' | 'notation' | 'chemistry' | 'human' | 'profile'


R = Reason
REASONS: dict[str, Reason] = {
    # ---- layout / coverage (page-structure stage)
    'EXCLUDED_AS_PAGE_FURNITURE': R(None, False, 'page furniture (running head/foot, page number): excluded from outputs',
                                    'Στοιχείο σελίδας (κεφαλίδα/υποσέλιδο/αριθμός): εξαιρείται από την έξοδο', stage='layout'),
    'BACKGROUND_FILL': R(None, False, 'solid background of a title that was read (a bar or shaded header): not content',
                         'Φόντο τίτλου που διαβάστηκε (μπάρα/σκίαση): δεν είναι περιεχόμενο', stage='layout'),
    'EMPTY_FRAGMENT': R(None, False, 'empty fragment (no letter or digit read) inside a picture, at the scan edge or tiny: not content',
                        'Κενό κομμάτι (χωρίς γράμμα/ψηφίο) μέσα σε εικόνα, στην άκρη ή πολύ μικρό: δεν είναι περιεχόμενο', stage='layout'),
    'NO_TEXT_INK': R(None, False, 'uncovered ink whose own OCR reads no words (digits, dashes, stray letters): not content',
                     'Μελάνι χωρίς κείμενο: το OCR της περιοχής δεν βρίσκει λέξεις (αριθμοί, παύλες, σκόρπια γράμματα)', stage='layout'),
    'DUPLICATE_READING': R(None, False, 'its text is already read by the larger box that covers it',
                           'Το κείμενό του υπάρχει ήδη στο μεγαλύτερο πλαίσιο που το καλύπτει', stage='layout'),
    'ENGINE_FURNITURE_IN_BODY': R(None, False, 'labelled header/footer by an engine but inside the text frame: kept as text',
                                  'Η μηχανή το είπε κεφαλίδα, αλλά είναι μέσα στο κείμενο: κρατήθηκε ως κείμενο', stage='layout'),
    'EMPTY_OCR_TEXT': R('required', True, 'region detected but no text produced',
                        'Η περιοχή εντοπίστηκε αλλά δεν βγήκε κείμενο', stage='layout'),
    'RUNNING_HEADER_EMPTY': R('recommended', True, 'running header/page number without text',
                              'Κεφαλίδα/αριθμός σελίδας χωρίς κείμενο', stage='layout'),
    'SINGLE_ENGINE_DOCLING': R('required', True, 'only Docling found this region; Marker missed it',
                               'Μόνο το Docling βρήκε την περιοχή· το Marker τη έχασε', stage='layout'),
    # Policy (reviewer decision, 2026-11-02): Marker is the primary engine and statistically the more reliable
    # one, so a region ONLY Marker found is accepted as Marker read it (informational: provenance + statistics);
    # every content check (symbols, equations, numbers, density, empty OCR) still applies to it. A region only
    # DOCLING found is the opposite case — Marker missed content — and stays 'required'.
    'SINGLE_ENGINE_MARKER': R(None, False, 'only Marker found this region (accepted: primary engine)',
                              'Μόνο το Marker βρήκε την περιοχή — δεκτό (κύρια μηχανή)', stage='layout'),
    # Cross-reading (pipeline.crossread): a region one engine found is read by the other(s) BEFORE review, so the
    # reviewer finds every reading ready. Informational: the readings' agreement is what the content checks judge.
    'CROSS_READ': R(None, False, 'found by one engine only; the other engine read the region automatically before review',
                    'Την περιοχή τη βρήκε μία μόνο μηχανή· η άλλη τη διάβασε αυτόματα πριν τον έλεγχο', stage='layout'),
    'UNCOVERED_INK_REGION': R('required', True, 'printed content not covered by any engine',
                              'Τυπωμένο περιεχόμενο που δεν κάλυψε καμία μηχανή',
                              ('COVERAGE_UNCOVERED_INK', 'warning'), stage='layout'),
    'MISSING_BOTTOM_TEXT': R('required', True, 'possible omission at the bottom of the page',
                             'Πιθανή παράλειψη στο κάτω μέρος της σελίδας', stage='layout'),
    'TEXT_POSSIBLY_INCOMPLETE': R('required', True, 'OCR text is short for the printed area (box may be wrong)',
                                  'Λίγο κείμενο για την τυπωμένη επιφάνεια (ίσως λάθος πλαίσιο)', stage='layout'),
    'LAYOUT_SPLIT_SUSPECTED': R('required', True, 'boxes appear to split one paragraph; merge and re-OCR',
                                'Τα πλαίσια φαίνεται να κόβουν μία παράγραφο· συγχώνευση και νέο OCR',
                                ('LAYOUT_SPLIT_SUSPECTED', 'warning'), stage='layout'),
    'CONSERVATIVE_FULL_PAGE_EXCERPT': R('required', True, 'extraction failed; whole page preserved',
                                        'Η εξαγωγή απέτυχε· κρατήθηκε όλη η σελίδα', stage='layout'),
    'OCR_FAILED': R('required', True, 'OCR failed', 'Αποτυχία OCR', stage='layout'),
    'HUMAN_REGION': R(None, False, 'region drawn by reviewer, OCR\'d without layout detection',
                      'Περιοχή που σχεδίασε ο ελεγκτής· OCR χωρίς ανάλυση διάταξης', stage='human'),
    'REGION_OCR_PENDING': R('required', True, 'reviewer region waiting for OCR',
                            'Η περιοχή του ελεγκτή περιμένει OCR', stage='human'),
    'REGION_OCR_FAILED': R('required', True, 'OCR of reviewer region failed',
                           'Απέτυχε το OCR της περιοχής του ελεγκτή', stage='human'),
    # ---- content agreement
    'ENGINE_TEXT_CONFLICT': R('recommended', False, 'Marker and Docling disagree',
                              'Marker και Docling διαφωνούν', stage='content'),
    'ENGINE_TEXT_CONFLICT_STRONG': R('required', True, 'Marker and Docling disagree strongly',
                                     'Marker και Docling διαφωνούν έντονα', stage='content'),
    'FIGURE_INTERNAL_TEXT': R('recommended', True, 'text printed inside a figure; engines may invent text here',
                              'Κείμενο μέσα σε σχήμα — εδώ οι μηχανές μπορεί να «εφεύρουν» κείμενο',
                              stage='content'),
    # The figure image (always in the output) already carries this text: nothing is lost -> not mandatory.
    'FIGURE_INTERNAL_TEXT_WITHHELD': R('recommended', True, 'text inside a figure withheld (engines disagree); the image carries it',
                                       'Κείμενο μέσα σε σχήμα αποσύρθηκε (οι μηχανές διαφωνούν)· υπάρχει στην εικόνα', stage='content'),
    'TABLE_STRUCTURE_UNVERIFIED': R('recommended', True, 'table structure not verified',
                                    'Δομή πίνακα μη επαληθευμένη', stage='content'),
    'CAPTION_CONTINUATION': R(None, False, 'paragraph joined to the caption above it (continuation)',
                              'Συνέχεια λεζάντας: ενώθηκε με τη λεζάντα από πάνω', stage='layout'),
    'FIGURE_TITLE_NOT_CAPTION': R(None, False, "a picture's title or printed label (capitals / a few words, no number): not its caption",
                                  'Τίτλος ή ετικέτα της εικόνας (κεφαλαία / λίγες λέξεις χωρίς αριθμό): όχι λεζάντα', stage='layout'),
    'CAPTION_ASSOCIATION_INFERRED': R('recommended', False, 'caption-figure link inferred from geometry',
                                      'Η σύνδεση λεζάντας–σχήματος συμπεράνθηκε από τη θέση', stage='content'),
    'CAPTION_ENGINES_DISAGREE': R('required', False, 'Marker and Docling attach this caption to different figures',
                                  'Marker και Docling συνδέουν τη λεζάντα με διαφορετικό σχήμα',
                                  ('CAPTION_ENGINES_DISAGREE', 'warning'), stage='content'),
    'REFERENCE_AMBIGUOUS': R('recommended', False, 'a cross-reference matches several numbered elements',
                             'Μια παραπομπή ταιριάζει σε περισσότερα από ένα αριθμημένα στοιχεία', stage='content'),
    'CAPTION_ACROSS_PAGES': R('recommended', False, 'caption linked to a figure across a page break (inferred)',
                              'Η λεζάντα συνδέθηκε με σχήμα πέρα από αλλαγή σελίδας (συμπέρασμα)', stage='content'),
    'HEADING_NUMBER_DUPLICATE': R('recommended', True, 'section number already used: probably a misread number',
                                  'Ο αριθμός ενότητας έχει ήδη χρησιμοποιηθεί: πιθανή λάθος ανάγνωση', stage='content'),
    'HEADING_NUMBER_OUT_OF_ORDER': R('recommended', True, 'section number lower than the previous sibling: probably misread',
                                     'Αριθμός ενότητας μικρότερος από τον προηγούμενο: πιθανή λάθος ανάγνωση', stage='content'),
    'CAPTION_WITHOUT_FIGURE': R('required', False, 'caption without figure', 'Λεζάντα χωρίς σχήμα',
                                ('CAPTION_WITHOUT_FIGURE', 'warning'), stage='content'),
    # ---- notation
    'LATEX_NOT_RENDERABLE': R('recommended', True, 'the formula does not render with the app renderer (KaTeX + mhchem)',
                              'Ο τύπος δεν αποδίδεται από τον renderer (KaTeX + mhchem): φαίνεται και η εικόνα πηγής',
                              ('LATEX_NOT_RENDERABLE', 'warning'), stage='notation'),
    'SYMBOL_READING_CONFLICT': R('required', True, 'engines read scientific symbols differently',
                                 'Οι μηχανές διάβασαν διαφορετικά επιστημονικά σύμβολα',
                                 ('NOTATION_SYMBOL_CONFLICT', 'critical'), stage='notation'),
    'SYMBOL_READING_MINOR': R(None, False, 'minor reading differences (partial or unrelated second reading); evidence only',
                              'Μικρές διαφορές ανάγνωσης (μερική ή άσχετη δεύτερη ανάγνωση)· μόνο ως τεκμήριο',
                              stage='notation'),
    'SCRIPT_MIX_UNVERIFIED': R('required', True, 'isolated symbols mix scripts and no second reading exists',
                               'Μονογράμματα σύμβολα ανακατεύουν αλφάβητα χωρίς δεύτερη ανάγνωση',
                               ('NOTATION_SCRIPT_MIX', 'critical'), stage='notation'),
    'INLINE_NOTATION_UNVERIFIED': R('recommended', False, 'inline scientific notation not verified',
                                    'Επιστημονική σημειογραφία μέσα στο κείμενο — μη επαληθευμένη', stage='notation'),
    'DIGIT_ZERO_WHERE_OXYGEN_LIKELY': R('required', True, 'possible O/0 confusion', 'Πιθανή σύγχυση O/0',
                                        ('NOTATION_DIGIT_ZERO', 'critical'), stage='notation'),
    'MIXED_SCRIPT_IN_NOTATION': R('recommended', True, 'notation mixes alphabets', 'Η σημειογραφία ανακατεύει αλφάβητα',
                                  stage='notation'),
    'UNBALANCED_BRACES': R('required', True, 'unbalanced braces in LaTeX', 'Μη ισορροπημένες αγκύλες στο LaTeX',
                           ('NOTATION_UNBALANCED_BRACES', 'critical'), stage='notation'),
    'EQUATION_UNVERIFIED': R('required', True, 'equation awaiting independent check',
                             'Εξίσωση χωρίς ανεξάρτητο έλεγχο ακόμη', stage='notation'),
    'MARKER_UNIMERNET_AGREE': R(None, False, 'Marker and UniMERNet agree (agreement is not proof)',
                                'Marker και UniMERNet συμφωνούν (η συμφωνία δεν είναι απόδειξη)', stage='notation'),
    'EQUATION_CONFLICT': R('required', True, 'Marker and UniMERNet disagree; LaTeX withheld',
                           'Marker και UniMERNet διαφωνούν· το LaTeX αποσύρθηκε',
                           ('MATH_EQUATION_CONFLICT', 'critical'), stage='notation'),
    'UNIMERNET_UNAVAILABLE': R('required', True, 'independent equation check unavailable',
                               'Ο ανεξάρτητος έλεγχος εξίσωσης δεν ήταν διαθέσιμος',
                               ('MODEL_UNAVAILABLE', 'warning'), stage='notation'),
    # ---- chemistry
    'CHEM_EQUATION_NOTATION_UNVERIFIED': R('recommended', True,
                                           'chemical equation: charges, states, coefficients, arrows need checking',
                                           'Χημική εξίσωση: έλεγξε φορτία, καταστάσεις, συντελεστές, βέλη',
                                           stage='chemistry'),
    'CHEM_STRUCTURE_IMAGE_AUTHORITATIVE': R('recommended', True, 'chemical drawing preserved as image',
                                            'Χημικό σχέδιο: η εικόνα είναι το περιεχόμενο',
                                            ('CHEM_STRUCTURE_REVIEW', 'warning'), stage='chemistry'),
    'LEWIS_DOTS_IMAGE_AUTHORITATIVE': R('recommended', True, 'Lewis structure preserved as image (lone pairs, charges)',
                                        'Δομή Lewis: κρατιέται ως εικόνα (μονήρη ζεύγη, φορτία)',
                                        ('CHEM_STRUCTURE_REVIEW', 'warning'), stage='chemistry'),
    'REACTION_SCHEME_IMAGE_AUTHORITATIVE': R('recommended', True, 'reaction scheme preserved as image',
                                             'Σχήμα αντίδρασης: η εικόνα είναι το περιεχόμενο',
                                             ('CHEM_STRUCTURE_REVIEW', 'warning'), stage='chemistry'),
    'CHEM_CANDIDATE_IMPLAUSIBLE': R(None, False, 'structure candidate implausible (fragments / no heavy atoms)',
                                    'Η πρόταση δομής είναι απίθανη (θραύσματα / χωρίς βαριά άτομα)',
                                    ('CHEM_CANDIDATE_IMPLAUSIBLE', 'warning'), stage='chemistry'),
    'MOLSCRIBE_UNAVAILABLE': R(None, False, 'MolScribe unavailable', 'MolScribe μη διαθέσιμο',
                               ('MODEL_UNAVAILABLE', 'warning'), stage='chemistry'),
    'RXNSCRIBE_UNAVAILABLE': R(None, False, 'RxnScribe unavailable', 'RxnScribe μη διαθέσιμο',
                               ('MODEL_UNAVAILABLE', 'warning'), stage='chemistry'),
    # ---- human input
    'BOOK_RULE_APPLIED': R(None, False, 'book rule applied (see corrections)', 'Εφαρμόστηκε κανόνας βιβλίου',
                           stage='human'),
    'HUMAN_REPORTED_WRONG': R('required', True, 'reviewer reported this segment as wrong (see note); re-OCR\'d',
                              'Ο ελεγκτής το δήλωσε λάθος (δες σημείωση)· έγινε νέο OCR',
                              ('HUMAN_REPORTED_WRONG', 'warning'), stage='human'),
    'RESOLVED_BY_HUMAN_DECISION': R(None, False, 'resolved by the reviewer\'s decision on the segment covering it',
                                    'Επιλύθηκε από την απόφαση του ελεγκτή στο τμήμα που το καλύπτει', stage='human'),
    'MERGED_SEGMENT': R(None, False, 'one segment merged by the reviewer from several parts',
                        'Ένα τμήμα που ένωσε ο ελεγκτής από πολλά κομμάτια', stage='human'),
    'MERGE_PART_WITHOUT_TEXT': R('required', True, 'a merged part has no text; type the merged text or keep the image',
                                 'Ένα κομμάτι της ένωσης δεν έχει κείμενο· γράψε το ενωμένο κείμενο ή κράτα την εικόνα',
                                 stage='human'),
    'TRIAGE_DOWNGRADED': R(None, False, 'review lowered: this flag proved reliable-negative in reviewed samples',
                           'Χαμηλότερη προτεραιότητα: η επισήμανση αποδείχθηκε αβλαβής στα ελεγμένα δείγματα', stage='human'),
    'ACCEPTED_BY_SAMPLING': R(None, False, 'accepted statistically: its class passed a sampling audit',
                              'Έγινε δεκτό στατιστικά: η κατηγορία του πέρασε δειγματοληπτικό έλεγχο', stage='human'),
    # ---- output profile (pipeline.profile)
    'PROFILE_NOT_RELEVANT': R(None, False, 'not asked: the output profile drops pictures, so their fidelity does not reach the output',
                              'Δεν ζητείται έλεγχος: το προφίλ εξόδου δεν κρατά εικόνες', stage='profile'),
    'PROFILE_BOUNDARY_CHECK': R('recommended', False, 'body text continues across the edge of content the profile excludes: kept in the output; check the boundary',
                                'Το κυρίως κείμενο συνεχίζει πέρα από το όριο αυτού που εξαιρεί το προφίλ: κρατήθηκε· έλεγξε το όριο',
                                stage='profile'),
    'ENGINE_TEXT_LOSS_SUSPECTED': R('required', True, 'Docling read clearly more words here than the chosen reading: text may be missing',
                                    'Το Docling διάβασε αρκετές λέξεις που λείπουν από το κείμενο: μπορεί να χάθηκε κείμενο',
                                    ('ENGINE_TEXT_LOSS_SUSPECTED', 'warning'), stage='profile'),
    'TEXT_ONLY_LOSS': R('required', True, 'only an image of this content exists; a text-only output would lose it: type its text or leave it out',
                        'Υπάρχει μόνο εικόνα αυτού του περιεχομένου· η έξοδος μόνο-κειμένου θα το έχανε: γράψε το κείμενο ή βγάλ\' το εκτός',
                        ('TEXT_ONLY_LOSS', 'warning'), stage='profile'),
    'DECISION_STALE': R('required', True, 'a review decision no longer matches this block; review again',
                        'Η απόφασή σου δεν εφαρμόστηκε: άλλαξε το αποτέλεσμα που είχες κρίνει· αποφάσισε ξανά',
                        ('DECISION_STALE', 'warning'), stage='human'),
}


def flag(blk: dict, code: str, review: str | None = None, crop: bool | None = None, **evidence):
    """Record a reason on a block and apply the registry's escalation (never de-escalates)."""
    r = REASONS[code]
    if code not in blk['reasons']:
        blk['reasons'].append(code)
    escalate(blk, review or r.review)
    if r.crop if crop is None else crop:
        blk['display_crop'] = True
    if evidence:
        blk.setdefault('evidence', {})[code] = evidence


def escalate(blk: dict, level: str | None):
    if level and blk['review'] in REVIEW_ORDER and REVIEW_ORDER.index(level) > REVIEW_ORDER.index(blk['review']):
        blk['review'] = level


def text(code: str, lang: str = 'en') -> str:
    r = REASONS.get(code)
    return (r.el if lang == 'el' else r.en) if r else code


def as_table() -> dict:
    return {k: {'review': v.review, 'crop': v.crop, 'en': v.en, 'el': v.el, 'stage': v.stage,
                'finding': list(v.finding) if v.finding else None} for k, v in REASONS.items()}
