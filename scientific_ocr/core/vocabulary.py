"""Annotation vocabulary: the ENTITY TAXONOMY of STEM coursebooks (types in families, placements), the kinds of
groups a reviewer (or the structure stage) can declare, and how groups map onto entity types.

An entity type value is either a TYPE key ('example') or a whole FAMILY ('family:practice') — the reviewer may
know only the coarse category ("some practice item") without committing to the fine one."""
import re


GROUP_TYPES = {'labelled_box': 'Αριθμημένο πλαίσιο', 'example': 'Παράδειγμα', 'exercise': 'Άσκηση', 'solution': 'Λύση', 'definition': 'Ορισμός',
               'theorem': 'Θεώρημα / νόμος', 'sidebar': 'Ένθετο / πλαίσιο', 'figure_group': 'Σχήμα με λεζάντες',
               'table_group': 'Πίνακας με σημειώσεις', 'equation_group': 'Εξίσωση με επεξήγηση', 'question': 'Ερώτηση', 'summary': 'Περίληψη',
               'note': 'Σημείωση', 'toc': 'Πίνακας περιεχομένων', 'other': 'Άλλο'}
# group type -> entity type, for groups that ARE an entity of the taxonomy
GROUP_ENTITY = {'example': 'example', 'exercise': 'exercise', 'definition': 'definition', 'question': 'question',
                'problem': 'problem', 'solution': 'solution', 'toc': 'toc'}


FAMILIES = {   # family -> (Greek, English); order = display order
    'worked': ('Λυμένα / παραδείγματα', 'Worked'), 'practice': ('Εξάσκηση', 'Practice'),
    'part': ('Μέρη πλαισίων', 'Box parts'), 'theory': ('Θεωρία', 'Theory'), 'aside': ('Παραθέματα / σημειώσεις', 'Asides'),
    'activity': ('Δραστηριότητες', 'Activities'), 'frame': ('Πλαίσιο κεφαλαίου', 'Chapter frame'),
    'insert': ('Παρεμβολές', 'Inserts')}
# type -> (family, Greek label, English label)
TYPES = {
    'example': ('worked', 'Παράδειγμα', 'Example'),
    'application': ('worked', 'Εφαρμογή / καθημερινή ζωή', 'Application / everyday life'),
    'exercise': ('practice', 'Άσκηση', 'Exercise'),
    'problem': ('practice', 'Πρόβλημα', 'Problem'),
    'question': ('practice', 'Ερώτηση', 'Question'),
    'review': ('practice', 'Ερωτήσεις/ασκήσεις επανάληψης', 'Review / self-test'),
    'solution': ('part', 'Λύση', 'Solution'),
    'answer': ('part', 'Απάντηση', 'Answer'),
    'strategy': ('part', 'Στρατηγική / ανάλυση', 'Strategy / analysis'),
    'hint': ('part', 'Υπόδειξη', 'Hint'),
    'check': ('part', 'Έλεγχος αποτελέσματος', 'Check'),
    'definition': ('theory', 'Ορισμός', 'Definition'),
    'theorem': ('theory', 'Θεώρημα / λήμμα / πόρισμα', 'Theorem / lemma / corollary'),
    'proof': ('theory', 'Απόδειξη', 'Proof'),
    'law': ('theory', 'Νόμος / κανόνας / αρχή', 'Law / rule / principle'),
    'formula': ('theory', 'Πλαίσιο τύπων', 'Key formulas'),
    'note': ('aside', 'Σημείωση / παρατήρηση', 'Note / remark'),
    'caution': ('aside', 'Προσοχή / ασφάλεια', 'Caution / safety'),
    'tip': ('aside', 'Συμβουλή', 'Tip'),
    'did_you_know': ('aside', 'Το ήξερες;', 'Did you know?'),
    'history': ('aside', 'Ιστορικό σημείωμα', 'Historical note'),
    'biography': ('aside', 'Βιογραφία', 'Biography'),
    'experiment': ('activity', 'Πείραμα / εργαστήριο', 'Experiment / lab'),
    'activity': ('activity', 'Δραστηριότητα / εργασία', 'Activity / project'),
    'case_study': ('activity', 'Μελέτη περίπτωσης', 'Case study'),
    'objectives': ('frame', 'Στόχοι κεφαλαίου', 'Learning objectives'),
    'summary': ('frame', 'Σύνοψη / ανακεφαλαίωση', 'Summary'),
    'key_terms': ('frame', 'Βασικοί όροι', 'Key terms'),
    'glossary': ('frame', 'Γλωσσάριο', 'Glossary'),
    'references': ('frame', 'Βιβλιογραφία / περαιτέρω μελέτη', 'References / further reading'),
    'figure': ('insert', 'Εικόνα / σχήμα', 'Figure'),
    'chart': ('insert', 'Διάγραμμα / γράφημα', 'Chart / graph'),
    'chemical_drawing': ('insert', 'Χημική δομή / σχήμα αντίδρασης', 'Chemical structure / scheme'),
    'table': ('insert', 'Πίνακας', 'Table'),
    'equation': ('insert', 'Εξίσωση', 'Equation'),
    'chemical_equation': ('insert', 'Χημική εξίσωση', 'Chemical equation'),
    'footnote': ('insert', 'Υποσημείωση', 'Footnote'),
    'sidenote': ('aside', 'Σημείωση περιθωρίου (χωρίς ετικέτα)', 'Margin note (unlabelled)'),
    'toc': ('frame', 'Πίνακας περιεχομένων', 'Table of contents'),
}
PLACEMENTS = {'inline': ('μέσα στο κείμενο', 'in the text'), 'margin': ('στο περιθώριο', 'in the margin'),
              'end_of_chapter': ('στο τέλος κεφαλαίου', 'at the end of the chapter')}
FAMILY_PREFIX = 'family:'


def family_of(etype: str | None) -> str | None:
    if not etype:
        return None
    if etype.startswith(FAMILY_PREFIX):
        return etype[len(FAMILY_PREFIX):] if etype[len(FAMILY_PREFIX):] in FAMILIES else None
    return TYPES[etype][0] if etype in TYPES else None


def valid_entity_type(etype: str | None) -> bool:
    return bool(etype) and (etype in TYPES or family_of(etype) is not None)


def entity_label(etype: str, lang: str = 'el') -> str:
    i = 0 if lang == 'el' else 1
    if etype in TYPES:
        return TYPES[etype][1 + i]
    f = family_of(etype)
    return FAMILIES[f][i] if f else etype


# A table-of-contents ENTRY: a title followed by a page number after dot leaders or a slash ("…… 26",
# "Κύτταρα: Οι Θεμελιώδεις Μονάδες / 25"). Used to detect contents pages (entities) and to keep their titles out
# of the book's hierarchy (sections). Pure text test.
TOC_ENTRY = re.compile(r'(?:\.{3,}|(?:…\s*){2,}|\s/)\s*\d{1,4}\s*$')
TOC_LEADERS = re.compile(r'(?:\.{3,}|(?:…\s*){2,})\s*\d{1,4}\b')


def toc_entries(text: str) -> int:
    """How many contents entries a text holds (lines ending in a page number after dot leaders or ' / ')."""
    lines = [ln for ln in re.split(r'\n|<tr>|</tr>|<br\s*/?>', text or '') if ln.strip()]
    plain = [re.sub(r'<[^>]+>', ' ', ln).strip() for ln in lines]
    by_line = sum(1 for ln in plain if TOC_ENTRY.search(ln))
    return max(by_line, len(TOC_LEADERS.findall(re.sub(r'<[^>]+>', ' ', text or ''))))
