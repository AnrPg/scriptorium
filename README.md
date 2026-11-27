# Scriptorium

**Offline, modular pipeline that prepares scanned Greek/English textbooks for RAG.** Local AI models extract, tag
and annotate; anything uncertain stays as a cropped image excerpt of the original page and goes to a local review UI.

**Mission:** Convert scanned Greek/English course textbooks into a faithful, auditable, multimodal document (canonical JSON + Markdown + source-image excerpts) entirely locally, as input for a RAG/agent system. Prioritize coverage, scientific fidelity (math, physics, chemistry, biology), and low code complexity.

**Models to INSTALL UP FRONT:** Marker, Docling, UniMERNet, MolScribe, RxnScribe. Their dependencies and local weights are installed/downloaded during setup, not introduced as surprise later phases. Installing does **not** mean every model runs for every page. The specialized models are dispatched only for relevant regions.

**Absolute requirement:** Anything not confidently and faithfully parsed **remains as a cropped excerpt from the source PDF** at its approximate reading position, with a clear `image_only` / `review_required` label. Never silently drop it or hallucinate a textual transcription. If an unparsed area's extent is uncertain, show a larger safe crop (up to the whole page) rather than risk omitting information.

The repository started as an implementation specification for Claude Code (the `*.md` planning documents below) and now contains the working application (`scientific_ocr/`). It is not a validated, error-free converter: outputs carry review statuses and nothing is called verified without human/source validation.

## Contents

- `CLAUDE.md` — binding instructions to put in the Claude Code project root.
- `IMPLEMENTATION_PLAN.md` — architecture, sequence, algorithms, release criteria, and bounded scope.
- `SCHEMA_AND_FORMATS.md` — canonical JSON data contract and Markdown conventions.
- `QA_REVIEW_AND_TESTS.md` — practical auditors, reviewer workflow, scientific test cases.
- `INSTALL_AND_MODELS.md` — setup, compatibility risks, model weights, Mac issues.
- `START_HERE.md` — exact commands and kickoff prompt.
- `scripts/bootstrap_macos.sh` — best-effort, all-up-front install and model prefetch with logs and failures surfaced.
- `scripts/download_weights.py` — prefetch external checkpoints.
- `scripts/doctor.py` — basic installation and weight presence checks (not accuracy tests).
- `examples/canonical_example.json` — illustrative normalized JSON.
- `REFERENCES.md` — primary upstream docs.

## High-level pipeline

```
original scanned PDF
   |--- Marker -> original JSON + images + equations
   |--- Docling (Greek Tesseract) -> original JSON + images + semantic tree
   +--- PDF page rasters and geometry from PyMuPDF
         |
         v
   cross-reading: a box only ONE engine found is read by the other
   (Docling-only -> Marker; Marker-only -> Docling's OCR on the crop); ink no engine boxed is left alone
         |
         v
   typed region classification + spatial/text matching
         |--- formula: Marker first; UniMERNet on conflict/uncertainty
         |--- molecular drawing: MolScribe graph/SMILES/MOL
         |--- reaction scheme: RxnScribe + MolScribe
         |--- Lewis dots/mechanisms/unknown: preserve original crop
         v
   canonical.json (all evidence + audit findings + source bboxes)
         |
   deterministic audit + concise reviewer queue
         |
   chapter.md + assets/*.png + review_report.html
```

**Source of truth:** original PDF + untouched engine outputs + canonical JSON evidence. **Derived reading artifact:** Markdown with visible excerpts and links to the actual PNGs. A text-only LLM cannot see an image just because Markdown links to it; multimodal lesson agents must receive the image bytes.

## Begin

Open `START_HERE.md`. Give Claude Code `CLAUDE.md` and ask it to implement the phases in `IMPLEMENTATION_PLAN.md`. The install bootstrap attempts all five model stacks up front but must not claim success without smoke tests.

## How to use (2026-11-01)

```
.venvs/docling/bin/python -m scientific_ocr ui        # → http://127.0.0.1:8765  (local only)
```
Everything happens in the app: upload a PDF or a folder of scanned pages (drag & drop), choose pages to process,
watch progress / stop, review any page or segment, redraw boxes, write book rules, undo/redo, export.
CLI equivalents: `process --book DIR [--pages 3,5-7]`, `reproject --book DIR`, `render --book DIR`, `doctor PDF`.

Importing: **Ανέβασμα φακέλου / PDF** use the browser's picker and upload the files; **Από τον υπολογιστή…** opens
the macOS Finder panel from the local server (files and folders together) and copies the files straight from disk
(no upload; best for large folders; home folder only). For page images both open the same page-order dialog
(by name, by date, reversed, or drag to rearrange) before anything is imported. HEIC/HEIF is converted locally.

### What happens to a review decision
Nothing is patched in place. Raw OCR (`raw/`) is immutable; every human action is appended to the book's
`events.jsonl` (event sourcing). A light background job then **re-projects** the book: `canonical.json`,
`chapter.md` and all exports are rebuilt from raw OCR + the effective events (seconds; never re-runs page OCR).
So review can happen at any time — during or long after processing — and undo/redo are just more events.
Decisions name the machine output they judged (`prev_hash`); if a page is later re-processed and that output
changes, the decision becomes *stale*: it is not applied, the segment returns to the review queue, the rest of
the book is unaffected.

### Page-structure review
Each page shows the engine boxes over the scan. A wrong box can be resized or a new region drawn; the region is
OCR'd on its own with layout detection disabled (Marker `--force_layout_block`) plus Docling as a second reading,
and replaces the boxes it covers. Side-by-side boxes on the same lines with OCR trouble (e.g. an L-shaped
paragraph around a figure) are offered as one-click "merge & re-OCR" suggestions. "Diligent re-examination"
re-extracts a whole page with forced OCR at higher resolution. Any segment, flagged or not, can be reported
wrong with a note → it is re-OCR'd as a region and stays in review with the note.

### Architecture

**Patterns.** Hexagonal (ports & adapters) in layers, event sourcing for all human input with rebuilt projections
(CQRS-style reads), and pipes & filters for the projection stages.

```
scientific_ocr/
  core/         pure domain: geometry, text/symbol comparison, chemistry keys, book rules, reason registry,
                event vocabulary (core/decisions.py), vocabulary of entity types
  ports.py      the few interfaces the pipeline depends on (SpecializedRunner, Progress, NoteInterpreter, PageImaging, Log)
  adapters/     engines (extraction, specialized models, engine JSON), pdf/imaging, storage (Book/Library),
                event store, local LLM, http (thin route table + static ES-module UI, no build step)
  pipeline/     projection stages: context, align, page assembly, checks (strategy registry), coverage, captions,
                document structure, sections, entities, notation, overlay (command handlers), audit, markdown, exports
  application/  use cases (application/use_cases/*), job queue, processing, projection orchestration
  cli.py        composition root (the only place adapters are wired to use cases)
```

- **Dependency rule.** Dependencies point inward: `core` imports nothing outside core; `pipeline` sees only `core` and
  `ports.py` (all I/O is injected); adapters never import `application` (except the driving HTTP adapter, which
  calls use cases). Enforced by `tests/test_application.py::test_dependency_rule`.
- **Event sourcing.** Raw engine output and the source PDF are immutable. Every human action (decisions, book rules,
  regions, groups, merges, entity tags, page removal, undo/redo) is appended to the book's `events.jsonl`; event
  schemas are defined once in `core/decisions.py` and validated on append. `canonical.json`, `chapter.md` and every
  export are a projection rebuilt from raw + effective events, so review never blocks processing, nothing is lost,
  and undo restores exactly. Decisions name the machine output they judged (`prev_hash`) and go *stale* if it changes.
- **Command pattern.** Every review decision is a command with its own validator and handler
  (`pipeline/overlay.py`, `COMMANDS`); services only produce validated events.
- **Strategy registry for checks.** Content checks only observe and raise *reasons* (`pipeline/checks.py`); what a
  reason means (review level, crop visibility, audit finding, Greek/English text) is defined once in `core/reasons.py`.
- **Unit of work.** A bulk action is one event group, i.e. one undo step (`ServiceBase._batch`).
- **Job queue (producer/consumer).** A *heavy* lane runs one extraction at a time; a *light* lane per book coalesces
  many clicks into one re-projection. Jobs run in subprocesses (cancellable process groups) and are resumable.
- **Content-addressed caches.** Every expensive step is keyed by the hash of its inputs (PDF, page, engine version,
  arguments), so re-runs resume where they stopped and settings changes recompute only what they affect.

**Principles that go with it.**
- Book-specific knowledge is *data* (book rules and events), never code; fixes are generic and root-cause.
- Book conventions are calibrated, checked *hints*, not hard rules.
- Local models (LLM/VLM) only *propose*; proposals are reviewable and never become authoritative OCR.
- The original scan wins over every interpretation; OCR and excerpts never render a page below the scan's own pixel
  density.

### Key root-cause fixes (book-independent)
* Symbol errors are detected by **cross-engine evidence**: tokens Marker and Docling read differently, classified
  (script swap G↔Γ, digit/letter O↔0 in formulas, different numbers); omissions, truncations, diacritics and
  same-script noise are ignored. Script-mix heuristics apply only when no second reading exists.
* **Cross-reading before review** (`pipeline/crossread.py`, `application/processing.cross_read`, step
  «Διασταυρούμενη ανάγνωση» of every processing run, after extraction): every region one detector missed is read by
  the engine that missed it — the same batched crop OCR as reviewer regions, content-addressed, idempotent
  (`raw/crossread/index.json`). Composition then judges it like any other segment: two agreeing readings make a
  Docling-only box ordinary content; a disagreement stays a visible excerpt with both readings; a Marker-only box
  gets Tesseract's reading as cross-engine evidence (symbol/conflict checks). Ink no engine boxed is NOT read again
  (mostly noise; see «Review load»). Docling is not run on crops: on a crop of one paragraph it detects no layout.
* Book rules (data, previewed, undoable) never "correct" a glyph that the independent reading confirms.
* Typeset chemical equations keep Marker LaTeX, are checked by UniMERNet with an arrow/charge-preserving key,
  and always keep their crop; Lewis structures are image-authoritative; MolScribe only sees molecular drawings.
* Stale decisions never block a book; cancelled jobs resume from cache; orphaned engine servers are reaped.

### Output formats
Markdown + images (ZIP), Markdown without images (explicit placeholders), PDF, normalized JSON (ordered
segments), canonical JSON (everything). The Markdown boundary grammar is written at the top of every file:
PAGE / BLOCK / FIGURE (captions inside) / EXCERPT pairs with stable ids, `flow=main|insert|furniture`,
`within=` for inserts that interrupt a paragraph, and `TEXT_CONTINUES` / `TEXT_RESUMES` at the exact cut and
resume points of a paragraph split by a figure or a page break. All of it is generated from canonical JSON.

### Output profiles — «RAG» (core/profiles.py, pipeline/profile.py, pipeline/rag.py)
A book is prepared FOR something, chosen once (Ρυθμίσεις → Προφίλ εξόδου; event `output_profile`, undoable).
**Pre-processing per profile** (pipeline/profile.text_policy, application/build_history: profiles/<name>/). After
OCR, and after every book update, each profile has its own pre-processed version, so what you REVIEW is what that
profile outputs: RAG drops the page's styling, repairs line-break hyphens, normalises Unicode, writes scripts as
LaTeX and drops caption credit notes (the text as read stays in `chosen.profile_source`; bold terms are kept as the
chunks' key terms); `reading` keeps the text as read. «Αποτέλεσμα» shows the profile's own rendering (RAG: titles to
H3, picture captions at the END OF THEIR SECTION right before the next title, tables as rows). Only the shown
profile is rebuilt while you wait; the others are rebuilt in the background only when their inputs changed (key =
inputs fingerprint + the profile's events, core.scopes.signature); a switch shows the ready version at once.
Settings → Προφίλ εξόδου → «Ξανά για όλα» re-runs it.
**Decisions per profile** (core/scopes.py). Every reviewer decision belongs to the profile it was taken under
and the outputs follow the decisions of the profile in force (an export refuses a book built under another
profile). Changing the profile asks: *carry* the current decisions over, or keep the profiles *separate*; nothing is
undone either way — switching back brings that profile's decisions back. «Αντιγραφή αποφάσεων από …» carries
another profile's decisions later (event `profile_copy`, undoable; on the same segment the later decision wins).
Book-level events (pages, page OCR profile, the profile choice, undo/redo) are global. History from before scopes
projects unchanged (its switches carried everything). A content decision carried from another profile is asked
again where the new output would lose it (TEXT_ONLY_LOSS); «Εγκρίνω ως έχει» confirms it.
`reading` is the book as it is. `rag` prepares text for a retrieval pipeline used by a **text-only** LLM agent:
* **Content.** Questions/exercises/problems and their solutions (hard distractors: they look like user queries but
  hold no answer), contents, index, front matter, objectives, history/biography and «Το ήξερες;» leave the output;
  a question with a linked answer stays with it. Pictures do not travel; their captions become paragraphs placed
  after the first paragraph that cites the figure; trailing credit notes are dropped.
* **Safety.** Only boundaries with independent evidence are excluded automatically (a box, an engine element, a
  collective section closing its chapter); a heading's subtree alone is only *suggested* (a margin label set as a
  heading owns the body text after it). A member that body text continues into/out of is kept
  (PROFILE_BOUNDARY_CHECK). Each automatic exclusion waits for one boundary confirmation («Έλεγχος» tab; status
  REVIEW_REQUIRED until then). The reviewer's «Στην έξοδο» always wins. «Έλεγχος ορίων ▶» loops over them on
  their pages, members drawn hatched with ΑΡΧΗ/ΤΕΛΟΣ (↵ σωστό · X όχι αυτό · J/K · T αρχή/τέλος · Esc).
  Right kind, wrong limits: «Όρια ✎» (D) — select a segment (any page) and press [ / ] for the new start / end;
  ↵ saves. One undoable step: the detection is dismissed as wrongly bounded (`boundary: true`, a wrong outcome for
  its rule) and the span (pipeline/entities.span, reading order, furniture left out) becomes your own verified entity.
* **Review.** Excluded content is not drawn on the page, not listed, not queued, not in bulk review; it is listed
  under «Κρυμμένα από την έξοδο — προφίλ». Picture fidelity flags are not asked (PROFILE_NOT_RELEVANT). Content
  that exists only as an image but is not a picture (table, paragraph, uncovered region) is asked again
  (TEXT_ONLY_LOSS) when it was decided before the profile was chosen.
* **Export** (`rag_markdown`, `rag_chunks`, `rag_zip`): minimal Markdown (headings to depth 3, deeper titles and
  box labels lead their paragraph; lists, Markdown tables or `Header: value` rows; `$…$`; no HTML, images or
  emphasis; NFC, hyphen repair) and structure-aligned chunks (~220 words, max 380, sentence-safe splits, tiny tails
  merged) prefixed with their heading path, with pages, content types, figure labels and key terms (the book's bold
  terms). `manifest.json` states what the text does not contain. The export refuses while required review remains.
  Optional: `python -m scientific_ocr rag-context --book …` adds one situating sentence per chunk from the local
  model, in a separate `context` field (contextual retrieval), cached by chunk id.
Evidence: hard distractors (Cuconasu et al. EMNLP 2025; arXiv 2605.10828), structure-aligned chunks with real heading
paths (arXiv 2610.10170), fixed ~200-word size competitive vs semantic chunking (Qu et al. 2025), markup cost
(HtmlRAG WWW 2025 vs PixelRAG 2026; He et al. NAACL 2025), Markdown tables (arXiv 2402.12869), OCR semantic noise
dominates (OHRBench ICCV 2025), contextual retrieval (Anthropic 2024; Merola & Singh 2025).

### Review load (pipeline/fills.py, pipeline/profile.review_policy, entities.banners)
Measured on the reviewer's own decisions (Alberts: 2,484 actions, ~10 h) and turned into generic rules:
* **Background fills.** Uncovered-ink regions that are a solid band (title bars, shaded headers) with text read on
  the band are background, not lost content (`background_fill`); a band nobody read text on stays for review.
* **Ink that reads as nothing.** Every uncovered-ink region is OCR'd on its own (Tesseract, exact box, cached
  with the coverage): no word of >= 3 letters / < 4 letters («2-2», dashes), or — with the book's dictionaries —
  mostly invented strings → `no_text_ink`; real words stay for review with the OCR text as a one-click candidate.
  An earlier «approve the image» never keeps a fill/fragment as content (it confirms the image, not that it is text).
* **Empty fragments / duplicate readings.** No letter or digit in any reading and inside a picture, a sliver at the
  scan edge or tiny (`empty_fragment`); text fully contained in a larger box covering it (`duplicate_reading`).
  Both are excluded like furniture: evidence + crop kept, listed, restorable. Never a fallback page or a region.
* **Boxes from title bars.** A labelled title on a background bar («ΕΡΩΤΗΣΗ 1-2») opens a box whose column is the
  bar's width; members are the contiguous blocks below inside it plus their continuations.
* **Collective sections** («ΕΡΩΤΗΣΕΙΣ») are corroborated by >= 2 numbered items inside them and end with the last.
* **Engine policy** `marker_primary` (RAG default, per-book switch): Marker/Docling disagreements about how text
  reads are recorded but not asked (`waived`); a much longer Docling reading (possible lost text) is asked
  (ENGINE_TEXT_LOSS_SUSPECTED); equations and tables always are.
* Caption continuations never take a body sentence (lowercase start after a closed caption); box labels do not
  break the course text's continuation; acronyms/numbers/brackets continue an open sentence.

### Spelling proposals (pipeline/spelling.py, adapters/lexicon.py)
OCR word errors are proposed, never applied: a suspect word (unknown to the Hunspell dictionaries of the book's
languages and rare in the book, or mixing lookalike letters of two scripts) gets candidates with their evidence —
lexicon, the other engine's reading at the same place (difflib word alignment), the book's own vocabulary, an
accent-only or script-only variant. Two independent kinds = «ισχυρή». Capitals (no accents) and word endings
(inflection) are not errors. «Μαζικός έλεγχος → Ορθογραφία» applies the chosen ones as replace_text decisions (one
undo). Dictionaries: `python -m scientific_ocr install-dictionaries` (LibreOffice el_GR/en_US, pinned sha256, into
~/.cache/scientific-textbook-ocr/dictionaries; package `spylls==0.1.7` in the docling venv). Without them only the
book and engine evidence remain.

### Reviewer notes → proposals (local vision-language model)
A note in plain language ("the arrow is an equilibrium ⇌", "this is the page header", "in the whole book Brønsted
is spelled Brönsted") is sent with the segment image to a local model served by llama.cpp (`adapters/local_llm.py`,
127.0.0.1 only, started on demand, stopped when idle). Decoding is constrained by a discriminated-union JSON
schema built from the engine label catalogs (`core/proposals.schema`), so the model cannot emit an inconsistent
action. The UI shows three things: the model's paraphrase ("Κατάλαβα…"), the deterministic description of what
the code will do (derived from the JSON only), and the editable JSON with live schema/domain validation. Nothing
is applied without the reviewer; applied decisions carry `via: {note, proposed_by}`; every proposal is logged
in `<book>/proposals.jsonl`. Model choice: `config.NOTE_MODELS` (env `SCIENTIFIC_OCR_NOTE_MODEL`).

### Symbol conflicts: classes
Every disagreement between Marker and Docling on a symbol-like token is classified (`core/symbols.py`).
Critical (escalate): `script_swap` (G↔Γ), `digit_letter` (H3O↔H30), `number` (2↔3), `partial_reading` (one engine
read less, e.g. a dropped subscript: text alone cannot tell which engine is right). Minor (recorded as evidence,
shown in review, no escalation): `lookalike` (Α/A, accents), `unrelated` (no plausible relation, a misread by the
weaker engine).

### Structure, labels, separators
* Section hierarchy (`pipeline/sections.py`): numbering depth > Marker heading tags rank-normalised over the book;
  labelled boxes ("ΠΑΡΑΔΕΙΓΜΑ 4.3") sit one level below their section; reviewer can set levels ("Δομή" tab).
  Output: SECTION boundaries, `section=` on every block, `section_path` in normalized JSON.
* Re-tagging uses the engines' own label catalogs (Marker / Docling); the label drives the canonical kind and
  Marker's `--force_layout_block` for the region re-OCR.
* Boundary style per export: HTML comments or XML-like tags; the grammar is written at the top of every file and
  the export dialog offers a copyable instruction text for downstream LLM agents.

### Segment review model (versions and modes)
A segment has one **current version**: the machine result, or a reviewer value (edit, chosen candidate, image only,
unreadable). `approve` confirms the machine version and can never replace a reviewer value (enforced in the
overlay and by the service); `revert` returns to the machine version. The UI shows the version banner and has
three modes, each with one primary action: view (approve / edit), draft (save my correction / discard), model
proposal (apply / edit before applying / reject). Decided segments offer edit or revert, never "approve".
Keyboard shortcuts follow the mode. Every version (machine readings, proposals, decisions incl. undone) is listed
in the segment's history with side-by-side comparison.

### Caption groups
A figure (or any insert, including equations) owns an ordered group of caption parts with roles `title`
(numbered label) / `text`. Split fragments are chained automatically when adjacent; reviewers link, detach and
set roles. Label and role are derived from the final (corrected) text. Output: `FIGURE … label=` with ordered
`CAPTION … role part parts label` inside it; normalized JSON carries caption_for / caption_role / caption_label.

### Groups vs merges, structure suggestions
* **Group** (annotation): members keep id, type and content; a group has a type (config.GROUP_TYPES) and optional
  name, may span pages; output: `GROUP id type name members pages` + `groups=` on members; JSON `groups`.
* **Merge**: members leave the output, one new segment contains them (`merged_from`), split/undo restores them.
* Selection: Shift+click or selection mode (S) on the page or in the page list; G = group…, M = merge… (dropdowns
  with the group types / engine labels), Esc clears.
* Automatic by risk: numbered boxes become **auto groups** (annotation; members = following segments on the same
  page sharing the box's background colour, else only the next segment); merges are **suggestions only**
  (layout-split paragraph on one page; side-by-side boxes with OCR trouble). Dismissals persist. On demand the
  local model proposes groups/merges for a page (schema restricted to that page's segment ids, validated, never
  applied without the reviewer).
* Geometry thresholds are in page units (diagonal/1000) so behaviour is scan-resolution independent.

### Reducing reviewer workload (without lowering quality)
* **Measured flag reliability / triage** (`pipeline/triage.py`): reviewer outcomes per flag (approve = machine right,
  edit = machine wrong); with >=20 outcomes and a 95% Wilson upper error bound <=5%, undecided segments whose only
  mandatory flag is that one drop from required to recommended. Critical flags are never lowered.
* **Bulk review** per flag class (grid of crop vs result, one undo) and **sampling acceptance** (sample sized so zero
  errors proves <=5% error at 95%; passing makes the rest `accepted`, never VERIFIED; critical classes excluded).
* **Rule suggestions** from repeated identical corrections; **auto-diligent** re-extraction of pages with >=3
  mandatory layout problems (once, recorded as a system event); **calibration** from reviewed books of the same
  series (`book.json calibrate_with`); **priority queue** (critical > equations/tables/captions/headings > prose >
  image-only); image-is-content flags are not mandatory. Partial readings (one engine reads fewer characters) are
  critical by reviewer policy.

## Per-page output preview
Each processed page has three views (keys 1/2/3): **Σάρωση** (scan + boxes), **Αποτέλεσμα** (an HTML
approximation of the final output, rendered from the same canonical JSON as the exports, with KaTeX), and
**Markdown** (exactly the page's slice of the exported Markdown, copyable). Hovering or clicking a segment links it to
the inspector. `#/b/<book>/p/<n>?view=output|markdown|scan` opens a view directly. Canonical LaTeX never carries
math delimiters (`core.symbols.strip_math_delimiters` at ingestion and for reviewer values); renderers add their own.

## Document-level structure stage (`pipeline/document.py`)
Runs after all pages are composed and every reviewer decision has been applied, because some structure only shows
across pages:
- captions across a page break: a figure/table that ends page p uncaptioned plus a caption that opens p+1 (or the reverse) are linked, marked inferred, and review is recommended
- heading hierarchy (in `pipeline/sections.py`): unnumbered headings take the level of numbered headings with the same engine style (a book-wide vote); otherwise the styles are ranked book-wide, with ALL-CAPS above mixed case within one engine tag. A jump of more than one level under an unnumbered parent is closed. Headings inside a labelled box become box sub-headings, in a second pass.
- boxes across a page break: an automatic box group that runs to the end of its page continues when the next page repeats the label, keeps the same shading, or opens with a "continued" heading
- numbering audit: a repeated section number or a backward step is flagged for review (never "fixed"), and a number missing between consecutive processed pages is reported

Safety contract:
- only roles, levels, relations and groups change, never text, LaTeX, crops or ids
- a reviewer decision on a segment always wins
- every change is listed in `canonical.document_structure.fixes` with a key, rule, evidence and before/after, and "Αναίρεση διόρθωσης" (an undoable `suggestion_dismiss`) reverts it
- merges across pages stay human-triggered

## Page furniture (running heads/feet, page numbers, edge tabs) — `pipeline/furniture.py`
Excluded from every output: Markdown, normalized JSON, PDF and the page preview. It is kept in canonical.json
(`excluded: {as, side, signals, key}`, with its crop). No fixed band or style is assumed. A segment counts as
furniture only if all three hold:
1. it is outside the page's text frame. The frame comes from long text, tables and equations; figure boxes are used only on pages without those, because picture boxes often swallow the margin. A page with no such content uses the book median per odd/even page.
2. it is short.
3. it has an independent supporting signal:
   - repeated at the same place on several pages (numbers folded, fuzzy, case- and script-insensitive)
   - a page-number sequence (offset to the PDF index shared by ≥2 pages), or a lone number at the top/bottom
   - a running title quoting a heading
   - an engine PageHeader/PageFooter label

Guards:
- Left/right sides, and footnote-like text at the bottom, need a strong signal (repetition or the page sequence).
- Position alone removes only segments without readable text: at the same place, directly or mirrored, as furniture confirmed on another page.
- An engine "header" lying well inside the frame is given back to the text.
- The audit fails the build (critical) if an exclusion has no evidence or crop, and warns if an excluded segment still appears in the output.
- "Επαναφορά" (an undoable `suggestion_dismiss` of the key) puts the segment back.

## Concurrency
`project.build` holds an exclusive per-book file lock (`storage.book_lock`, flock), so builds started by the UI
server and by the CLI wait for each other and never interleave writes.

## Navigation
← / → in the top bar (or Alt+← / Alt+→) go back and forward along the path the user actually took. Each
browser history entry is stamped with its position (`history.state.nav`), so a link to an older screen
creates a new entry instead of being mistaken for "back". The labels say where each step leads. The breadcrumbs
still go *up* the sitemap.

## Second pass on the hierarchy with the local model (`pipeline/structure_review.py`, `application/structure_llm.py`)
Proposals only. Started from the Δομή tab, it runs as a background job (`propose-structure --book`). The runner (sampling, voting, caching, proposal file) is shared with the entity pass: `application/llm_review.py`, which takes a review *strategy* module of pure functions (`targets`, `prompt`, `schema`, `resolve`, `vote`, `proposal`, optional `decorate`/`agreement`).
- **What is asked:** only what stays ambiguous after the rules: unnumbered headings levelled by typography, box labels and box sub-headings, and heading-like paragraphs.
- **Input:** structured text, not raw text. It contains the open sections, the previous 12 elements with their type and inferred level/role, the target with its typography, and up to 100 lines of following elements. Local aliases (e1…) keep it short.
- **Output:** grammar-constrained JSON: `role` ∈ {section_heading, box_label, box_subheading, not_heading} and `parent` ∈ {ROOT, open headings}. The level is the parent's level + 1, so it cannot jump.
- **Validation:** answers are validated (e.g. no section inside a box), asked 3 times (T=0, then sampled), voted and cached.
- **Review:** each proposal shows its votes, the model's reason (with aliases resolved to text) and exactly what the model saw. Accept = an ordinary undoable `set_heading_level` decision; reject = a remembered dismissal.

## Exports: one renderer for preview and PDF
- **PDF:** the export HTML (`html_preview.document_html`) printed by local headless Chrome/Chromium. It uses the same page renderer, `static/preview.css` and `static/render-math.js` (KaTeX + mhchem) as the in-app preview. Without a browser, the basic PyMuPDF PDF is produced and says that formulas are shown as source.
- **`latex_check`:** renders every formula (equations and inline `$…$`) with that same KaTeX build (node, offline). Formulas that do not typeset get LATEX_NOT_RENDERABLE, and their source image is shown in every export.
- **Text formatting:** Marker emphasis and sub/sup are rendered as formatting in HTML/PDF; everything else is escaped. The normalized JSON declares `text_format`.
- **Approved text:** approving a text/LaTeX segment makes the text the content (no verification crop). Image-only drawings keep their image.

## Frontend structure (no build step)
`static/js/` holds vanilla ES modules, one per responsibility: core (dom, api, strings, state, nav, chrome, keyboard), ui, library, book (book, quality, structure, entities), export (wizard), workspace and inspector. `main.js` is the entry point. All UI strings live in `core/strings.js`. The key shortcuts are one declarative table (`SHORTCUTS` in `core/keyboard.js`) that also generates the help. Dialogs stack (`openDialog`). The rendered page output is cached per (book, page, revision, style) with a race token. Colours are tokens in `static/tokens.css`, shared by `app.css` and `preview.css` (and so by the PDF export). Component classes are namespaced (`ws-box`, `view-page`, `seg-ctl`, `math-view`).

## Workspace toolbar and responsive layout
- **One table drives the toolbar, the shortcuts and the help:** `VIEW_TOOLS` in js/workspace/workspace.js. Row 1 holds page navigation, status and the view selector (1/2/3). Row 2 holds the actions for the current view, spread across the full width. Scan shows zoom, B, R, S and diligent re-examination. Output and Markdown show zoom (text scale) and diligent re-examination.
- **Scan-only modes:** a tool that is hidden is also unreachable by its key. Leaving the scan view ends drawing and selection modes.
- **Breakpoints:**
  - 1200 px: narrower inspector.
  - 900 px: the inspector moves below the page, and tabs and tables scroll horizontally.
  - 600 px: no brand text, the view selector spans the width, actions in two columns, and stacked lineage cards.
  - The page itself never scrolls horizontally (checked at 1440, 820 and 390 px).

## Ordered selection (group / merge)
The multi-selection is ordered: a numbered list in the inspector, matching numbered badges on the page. You
reorder it by dragging ⠿, with ↑/↓, or with Alt+↑/↓, and "Σειρά ανάγνωσης" resets it to reading order. That
order is recorded in the event (`ordered: true`) and respected everywhere:
- a merge joins the texts in that order, and the new entity takes the reading position of the earliest member;
- a group keeps its members in that order;
- events recorded before ordering existed (no `ordered`) keep reading order, so old merges never change text silently.


## Reader-facing exports (Markdown, PDF): the final document
`pipeline/reading.py` linearises the canonical document into a reading model. `pipeline/markdown.py` serialises
it to Markdown, and `pipeline/html_preview.py` serialises it to HTML for the PDF and the preview.

What these exports contain:
- **Only the book:** headings, paragraphs, formulas, tables, and figures with their captions.
- **No pipeline traces:** no ids, review states, source pages, provenance or banners, and nothing that says how the document was produced.
- **Lightweight separators** for what is inserted into the text flow. The `tags` style (default) uses `<figure>…<figcaption>…</figure>` and `<aside>…</aside>` for boxed elements. The `comments` style uses `<!-- figure -->…<!-- /figure -->`.
- **Joined paragraphs:** a paragraph interrupted by an insert or a page break is one paragraph, and the insert follows it.
- **Content kept as images:** it appears as a plain image (or `[Εικόνα]` in the text-only export), so nothing is dropped.
- **The ZIP** holds `<title>.md` and `images/001.png …` only.

Provenance and review state live in the normalized and canonical JSON. The audit checks the reader document:
every text, formula and image is present, nothing internal leaks, captions stay inside their figure, and the
separators balance. The previous traceable grammar (BLOCK_START …) was removed. Sections are now Markdown headings, and levels deeper than h6 are rendered as bold lines.

## Order preservation and reader reordering
- **Automatic stages never reorder content.** Anything that replaces segments takes the reading position of the first segment it replaces: a merge, or a reviewer region from drawing, re-tagging, "είναι λάθος" or merge-with-re-OCR. Geometry places only genuinely new content (Docling-only blocks, uncovered ink, a region over empty ground). Automatic groups list their members in reading order. In the reader-facing export, an insert that interrupted a paragraph is placed right after the joined paragraph, and the paragraph keeps its position.
- **The reviewer can reorder.** Drag a row in the page list onto the upper or lower half of another row, or press Alt+↑/↓ on the selected segment. This records `move_after_block` with `after` or `before`, which is an explicit and undoable human decision.

## Previews of structure suggestions
Every merge, re-OCR or group suggestion (rule, automatic or model) and every page group shows:
- what will happen, in words;
- the members in order, with page thumbnails;
- a preview of the result: the joined text, computed by the merge stage's own joining rules (`POST /merge/preview`), the region that will be re-read, or the boxed group as it will appear.

The actions are Αποδοχή, Απόρριψη, Επεξεργασία μελών/σειράς (loads the members into the ordered selection) and Εμφάνιση στη σελίδα.

## Derived indicators always follow the edits
Section levels (H1, H2…, Πλαίσιο/Υπότ.), `section_path`, paragraph continuations (↳ συνέχεια / joined
paragraphs), caption part order, inserts, groups/boxes, document issues and the reader-facing separators are
**never stored as decisions**. They are recomputed from the current order and content on every projection.
- A group whose members a reorder made non-contiguous is not boxed. It is reported (`group_not_contiguous`) instead of stretching over unrelated content.
- Dragging a caption only reorders the parts of its figure's caption; anywhere else, the UI explains that captions follow their figure.
- **UI freshness:** the server exposes a book revision (`GET /api/books/<id>/rev`, from the canonical and event-log files). After any edit, the UI polls fast and reloads everything on screen as soon as the revision changes, even when the re-projection finished before its job was observed. It never reloads while you are typing.

## Entities and reader-export content options (`pipeline/entities.py`)
- **Taxonomy:** about 37 types in 8 families:
  - worked: example, application / everyday life
  - practice: exercise, problem, question, review
  - box parts: solution, answer, strategy, hint, check
  - theory: definition, theorem, proof, law, formulas
  - asides: note, caution, tip, did-you-know, history, biography
  - activities: experiment, activity, case study
  - chapter frame: objectives, summary, key terms, glossary, references
  - inserts: figure, chart, chemical drawing, table, equation, chemical equation, footnote
- **Labels:** a Greek and English lexicon, case- and accent-insensitive, covering abbreviations and numbering formats. Labels and patterns are normalised the same way (`casefold` maps ς to σ).
- **Sources:** labelled boxes and their sub-headings, entity-titled sections with their whole subtree (collective sections such as "Ασκήσεις" or "Review Problems"), paragraphs that open with a numbered label, and engine kinds.
- **Placement:** inline, margin (outside the text frame) or end_of_chapter (a collective section that closes its chapter).
- **Where it runs:** its own projection step, `_classify_entities` in `application/projection.py`, after the document structure (hierarchy, boxes) and before triage/export. The taxonomy lives in `core/vocabulary.py` (`TYPES`, `FAMILIES`, `PLACEMENTS`) and is served to the UI through `meta.entity_types`, so there is one source.
- **Category values:** a type (`example`) or a whole family (`family:practice`) when only the coarse kind is known. The engine label (Marker/Docling kind) is never changed; the category is a separate annotation.
- **Per segment:** `entities.annotate` writes `block.entities` (outermost first: key, type, family, verified, source, head). The page list shows a chip per segment (one colour per family; filled = verified), and the segment inspector has a «Κατηγορία» card.
- **Reviewer input (events):**
  - `entity_confirm {key, entity_type}` verifies a detection or changes its type or family — any number of times, verified or not; the latest wins.
  - `entity_assign {key, members, entity_type}` is the reviewer's own classification of one segment or an ordered selection (multi-select → «Κατηγορία…», key C). It is always verified, and a detection with exactly the same members is superseded by it. Re-assigning the same members changes the type.
  - `entity_remove {key}` withdraws an assignment; «Δεν είναι αυτό» on a detection is a remembered dismissal.
  - All of them are undoable.
- **Exports:** only verified entities can be excluded or moved as a whole type, by type, by family (`family:practice`), or either @placement. The wizard has a «όλη η οικογένεια» row per family.
- **Learning (statistics):** every verdict on a detected entity is an outcome for its rule class (`granularity:type`, e.g. `box:example`): right if confirmed with the detected type; wrong if retyped or dismissed. With at least 15 outcomes and a 95% Wilson upper error ≤ 10%, the rule is *trusted*, and its unverified entities are offered for one-click bulk confirmation («Επιβεβαίωση αξιόπιστων») — still a human act. Outcomes of calibration books count as priors. The Δομή tab shows the table per rule and the model's agreement with the reviewer per family.
- **Second pass with the local model** (`pipeline/entity_review.py`, `application/entity_llm.py`, job `propose-entities --book`): it is asked only about unverified, untrusted, non-engine detections and about boxes no rule could type. The input is the typed elements around the target, the target's full text, its placement, its enclosing box and the detector's evidence. The answer is grammar-constrained to the taxonomy (a type, `family:<f>` or `none`), with a short Greek reason. Answers are validated (a box part needs an enclosing example/exercise), asked 3 times, voted and cached. Accept = `entity_confirm` / `entity_assign` / dismissal; «Άλλη κατηγορία…» lets the reviewer pick another; reject = a remembered dismissal.
- **Figures out of the text flow:** the export option `figures=end|end_captions` (plus `unit_level`) collects every insert rendered as a picture and puts it, with its captions, at the end of each chapter or unit under «Εικόνες». In captions-only mode an uncaptioned, unverified picture keeps its image. Transcribed equations and tables, and untranscribed text, stay in place.
- **Where the options apply:** the Εξαγωγή dialog (saved per book) and the URL parameters `exclude`, `figures` and `unit_level`, for Markdown (both variants) and PDF.


## Moving content to the end of each unit (export option `move`)
- **`figures`:** only captioned visual elements leave the text flow, with all their caption parts. An element counts as visual if its entity type is figure, chart or chemical drawing (engine kind or reviewer correction), or if its caption label names a picture: Σχήμα, Εικόνα, Εικ., Διάγραμμα, Γράφημα, Fig., Figure, Photo, Graph, Plot… Equations, tables and text kept as images because OCR failed never move. The option `fig_images=keep|drop` keeps the pictures or only the captions.
- **Margin content** (`*@margin`, `<type>@margin`, `sidenote`): detected entities qualify, with no caption and no verification needed, because the criterion is their position, which is geometric. They move whole, with their box. `sidenote` covers unlabelled text found in the margin.
- **A whole type anywhere** (`<type>`, e.g. every example): verified entities only, because the classification needs the reviewer.
- **Output:** moved content is grouped by kind under a short heading at the end of each unit (`unit_level`).
- **Export wizard:** 1 format · 2 content (exclusions, verified only) · 3 move to the end (multi-select list; per type: no / margin only / everywhere; figures with or without images) · 4 separators, summary, preview and download. JSON formats skip steps 2–3. Choices are remembered per book.


## Excursus (παρέκβαση) and reading strands

- An **excursus** is a stretch intercalated into the course text (historical note, applications, an appendix of another science), on part of a page or over many pages. It is stored by its two ends (first and last segment) as `excursus_set` / `excursus_remove` events, so everything between them in reading order belongs to it even after merges, new regions or re-OCR (`pipeline/excursus.py`).
- **Marking:** use «Αρχή παρέκβασης εδώ» and then «Τέλος παρέκβασης εδώ» on any later page; a multi-selection (first to last selected segment); or contiguous pages in the «Σελίδες» tab. **Suggestions:** a course paragraph that stops mid-sentence and resumes only after pages that do not continue it.
- **Reading strands** (`pipeline/flow.py`): continuation is decided within a context. The course text continues across inserts, boxes, excursus and page breaks. Text inside a box, meaning any categorised segment (question, key terms, example, groups with a type), continues inside the same box. Text inside an excursus continues inside the same excursus. `relations.continues_strand` names the context. Every categorised segment is an insert by default, and the reviewer's flow choice overrides this.
- **Placement in outputs:** in place (a box; the interrupted paragraph is joined around it), at the end of the chapter, at the end of the book, or left out. This is set per excursus or for all excursus of a type.

## Multi-selection flow
Selecting segments (S, Shift+click, or clicks in the page list) keeps you in the list. The selected segments show
their order number on the page and in the list, and a bar shows "N επιλεγμένα". The group/merge screen opens only on
request: "Ομαδοποίηση/συγχώνευση →", Enter, G or M. In that screen, "← Πίσω στη λίστα" (or Esc) returns without a
decision and keeps the selection. Esc in the list clears the selection.

## Book hierarchy from checked hints (`pipeline/sections.py`)
Layout engines tag headings per page, so their levels are not comparable across a book. Levels come from HINTS, and a hint applies on its own only after a check:
- **Hint sources (all optional; books differ):**
  - measured print size and case per heading (`typography`, from `adapters/pdf.text_metrics`, robust to skewed photos), grouped into book-wide styles with a batch separation check;
  - the style of headings the reviewer levelled (`human_style`), and numbered headings of a style (`style_of_numbered`);
  - running heads grouped by their place on the page: a changing series = sections, an unchanging one = the outer level (`running_head`);
  - a chapter label (`chapter_label`);
  - an outline that names several headings (`outline`, including segments the reviewer hid);
  - the engine tag (`engine_rank`);
  - the local model's majority vote from the second pass (`model`).
- **Typography is relative:** print size, stroke weight and ink colour are measured RELATIVE to the body text of the same page (or of the book), so photo distance, scan resolution, page size and small type cancel out. Measurements with too few pixels per line are ignored. Weight and colour are reported, but do not split styles: in photographed pages they vary with lighting.
- **What applies on its own:**
  - Style evidence applies as an automatic, undoable correction, without the engine's agreement: the style of a heading settled elsewhere (reviewer, checked or numbered), or the measured typography of a statistically distinct style.
  - If this book's decisions show a style source errs (≥ 10 outcomes, > 20 % wrong), it stops applying alone and becomes a proposal.
  - Other single hints (running heads, outline, chapter label, model) apply when a second independent kind agrees, or when they are calibrated (≥ 10 outcomes, 95 % upper error ≤ 10 %; calibration books count as priors). Otherwise they become proposals, grouped by style and level (Δομή → «Επίπεδα τίτλων · ενδείξεις»). Accepting is one undoable group of decisions; rejecting counts against the sources.
- **Same style = same level, carried from checked levels (`style_of_checked`):** once a heading passes the check, e.g. because its typography and the engine agree, every other heading of the same measured style gets that level as an independent hint.
- **Undo:** undoing an automatic level restores exactly the original (engine) level (`revoked`), and no other automatic repair rewrites it. The undo is per level (`sections.revoke_key`), so undoing level 2 does not block a later, different level 3. If neither the original nor the correction is right, the corrections list lets the reviewer set the level directly. It counts as a wrong outcome for the evidence that caused it. The corrections list shows only real changes, each as «before → after».
- **Order and human input:** all of this runs in the projection AFTER the reviewer's decisions. Furniture stays excluded from the output but is used as evidence. Hidden segments never reach the output but still count as evidence.
- **Second pass with the model:** `pipeline/structure_review.py` shows the model these hints. Every proposal is extended to the heading's whole style group, simulated to list its consequences, and asked again over up to 3 rounds (`application/llm_review.py`). The pass re-runs automatically after an accepted proposal.

## Full hierarchy, page overlay, out-of-output
- **Δομή → «Πλήρης ιεραρχία»:** every segment under its section, with levels editable and segments that will not reach the output marked (`/api/books/<id>/outline`, `application/use_cases/hierarchy.py`).
- **«Άνοιγμα στη σελίδα»** and clicks on proposals open the real page screen in an overlay (`ui/page_overlay.js`, `?embed=1`); «Πλήρης οθόνη» goes to the page itself.
- **«Εκτός εξόδου» (O, toolbar, inspector, whole groups):**
  - Decision `set_output`: real content left out of Markdown/PDF only. It stays in the model, the hierarchy and the review history, and in JSON with `in_reader_output: false`.
  - Different from «Αφαίρεση λάθος πλαισίου» (`block_hide`), which is for a wrong box.
- **Rendering speed:** page crops and coverage are content-addressed caches, so a re-projection after a click rebuilds in under 1 s.
- **Confirmation messages:** an action's confirmation appears only once the rebuild that reflects it has finished.
- **Job logs:** each rebuild logs what triggered it, what changed and the timings per phase.
- **Undo without a rebuild:**
  - Pending changes are counted **net of undo** (`event_store.net_pending`): an action undone before any rebuild saw it cancels out, so nothing is pending and no rebuild is asked for.
  - The previous builds are kept as hard links in `cache/builds/` (`config.BUILD_HISTORY_KEEP`, `application/build_history.py`), keyed by the active events plus a fingerprint of every other build input (raw OCR, source, settings, model proposals, calibration books, dictionaries, code). When undo/redo returns to exactly the state of a kept build, that build is put back at once. Redo comes back the same way. If anything else changed, the normal rebuild runs.
- **Engine conflicts:** marked in the text and in the editor (`ui/highlight.js`), with 12 words of context on each side.
- **Coverage:** uses the darkest colour channel, so coloured print counts as ink. An unread header is not excluded on an engine label alone.
- **Captions and figures:** a labelled «ΕΙΚΟΝΑ N» paragraph becomes a caption, and the pieces of a split picture are offered as one region (`figure_assemble`).

## One notation for text content (`pipeline/notation.py`)
- **What engines emit:** Marker emits `<math>` (converted to `$…$` / `$$…$$`), `<b>/<i>` (converted to Markdown), lists, and headings as text. Two things stay HTML: `<sub>/<sup>` scripts, sometimes with U+2212 minus signs, and tables. Other readings may use Unicode scripts (`H₂O`, `Na⁺`).
- **Projection step (after the reviewer's decisions):** derives `text_norm`, in one convention: Markdown prose; LaTeX for formulas and for sub/superscripts (`$\mathrm{H_{3}PO_{4}}$`, also inside table cells); tables stay HTML because Markdown cannot express merged cells. The conversion is mechanical and lossless, and applies only to tokens of letters, digits, brackets and charges. Anything else is left exactly as written.
- **Source text and decisions:** `chosen.text`, what the engine or the reviewer wrote and what decisions refer to, never changes. `core.model.chosen_text` returns the normalized form, so exports, previews, matching, checks and the model read one notation. The normalized JSON has `text` (normalized), `text_as_written` and `text_format`.
- **Chemistry:** not converted to mhchem `\ce{}` automatically, because it needs a chemical judgement and validation. Existing `\ce{}` is kept and typeset by the same KaTeX + mhchem build used for preview, PDF and `latex_check`.
- **LaTeX fields:** they never carry math delimiters (stripped on save on the server and in drafts/previews in the UI).

## Entity extents = automatic groups (`pipeline/extents.py`)
- **What an extent is:** an entity (example, exercise, definition, application, box…) is its label plus its WHOLE body: title, statement, sub-headings, paragraphs, equations, figures, solution.
- **Anchors:** a labelled heading, an entity label in a heading or a bold lead, or the first line of any text segment (boxes typed as figures).
- **Members:** they follow the anchor while the shading chains neighbour to neighbour (tolerant to the lighting gradient of photos). The page's paper is its brightest text background. Without shading, the layout rule applies (column, indentation, gap), and equations, figures, tables and the entity's own parts (solution, strategy, answer…) do not end it.
- **Ends:** the next anchor, a section heading, the end of the shading, a large gap or an indentation change. At the end of a page the entity continues on the next one.
- **Groups and reports:** every extent is an automatic group, reported in Δομή → «Αυτόματες ομάδες» with «Σωστό τέλος…» (`set_group_end`) and undo. A reviewer group that overlaps it always wins.
- **Lists:** groups are shown as a bracket and a name in the page list and in the full hierarchy.
- **Whole-group decisions:** moving one member in the reading order moves the whole group in its order (one undo); «Εκτός εξόδου» takes the whole group; export moves are atomic per group (never half an example); a figure inside an example stays with it.
- **More extent rules:**
  - Segments of another column, or a page figure that the reading order interleaves, are looked past (up to 3), not taken as the end.
  - A theorem's proof is a part of it.
  - A heading printed more prominently than the entity's label (e.g. «Απαντήσεις») ends it, even if its word names a part.
  - An equation and its explanation («όπου …», «where …») form an automatic group (`equation_group`).
- **Why an extent ended:** each group records `stop`. Ends on a weak signal (gap, indentation, an unconfirmed next page) are `ambiguous`, marked «αμφίβολο τέλος», and asked to the local model (`pipeline/extent_review.py`, job `propose-extents`). The model chooses the last element that belongs, from the group's own elements and the 8 that follow. Answers are voted over 3 samples. Accepting = «Σωστό τέλος», and the pass re-runs automatically.
- **Notes under a table or figure without a caption** («* …», «Πηγή:», «Σημ.») join it as caption parts.
- **Caption continuations:** list items, legends, «Πηγή:» lines, or caption-size text directly under a caption join it (`captions.continuations`, reported and undoable).
- **Answers gathered elsewhere** («Απαντήσεις ασκήσεων»): linked to the exercise with the same number (`entities.link_answers`), never grouped or moved.

## Heading levels follow the reviewer's change of a section (`sections.shift_subtrees`)
- **Subtree shift:** when the reviewer moves a heading by N levels, every inferred heading of its subtree moves by the same N (H4 → H2 makes its H6 an H4). The subtree is taken as it was before the change.
- **Base levels:** each subtree heading's level WITHOUT the reviewer's decisions is computed with a second inference pass, so nothing is shifted twice (e.g. a child that its style already moved stays put).
- **Exceptions:** nested reviewer decisions take over their own subtree. Numbered headings keep their numbering level. Labelled elements and box sub-headings follow by construction.
- **Reports:** each shift is reported («Υπότιτλοι που ακολούθησαν τον γονέα») with undo and «Σωστό επίπεδο…».

## Segment panel (inspector) — information architecture
In order:
1. Header (kind, state, queue ↑↓).
2. ONE status sentence: «Χρειάζεται έλεγχο: <main reason>», «Δεν χρειάζεται έλεγχο» or «Η απόφασή σου …», with the content's source.
3. The source crop.
4. «Γιατί επισημάνθηκε», only when something is asked; machine notes collapsed.
5. Content, rendered first, then the code (open when engines disagree or for LaTeX).
6. Actions for the state: approve, edit, image-only only when review is needed; just «Επεξεργασία» when nothing is to be resolved, with an explicit check under «Άλλα»; edit or revert for the reviewer's own value.
7. «Ιδιότητες στο βιβλίο»: output, category, group, level in the structure, engine label.
8. Tools and details.
- **Sub-headings the reviewer set:** they move with a later change of their parent too (`pipeline.overlay.heading_level_events`, used by every path that sets a level: inspector, hierarchy, corrections, hint and model proposals). The parent and its carried sub-headings form one undoable step.
- **Scroll position:** re-rendering never moves the reviewer. `core/dom.keepScroll` keeps the scroll of the screen and of every `[data-keep-scroll]` pane across re-renders of the same screen (book tab, workspace, inspector, hierarchy tree), and keeps restoring it while images and cards load. It stops when the reviewer scrolls or clicks. Automatic scrolling to the selected segment happens only when the selection changes.
- **Resizable side panel:** drag the handle between the page and the panel (or use ←/→ when the handle has focus) to resize it. Double-click resets it. The width is remembered per browser and kept between 300px and 70% of the window. Panel content adapts to the panel width through container queries (`container: side`), so cut text reappears as the panel grows. Below 360px, property rows stack.
- **Phones / narrow screens:** at ≤900px the panel becomes a bottom sheet with a grip. Drag to resize; a tap or Enter cycles through small / half / large. At ≤600px the toolbar becomes **one row**: ‹ n/N ›, the page state as a dot (a number when review is needed), a short view switch (Σάρωση / Έξοδος / MD), and a "⋯" button that opens the view's tools as a vertical menu (zoom, toggles with ✓). The "⋯" is highlighted while a mode is active. The breadcrumbs keep only the book and the page.
- **Cut text:** any text cut with an ellipsis shows in full on hover (`installOverflowTitles`). The tooltip is recomputed on every hover, so it disappears once the text fits.
- **Segment highlights from suggestions** belong to the card that created them. They clear as soon as the card disappears (accept/reject/redraw), on Escape, or on a click outside the card, so a stale highlight never looks like a group.

## Audit fixes (robustness rules)
* **Excerpts travel with unsettled text in every output.** Machine text or LaTeX that still has `display_crop`
  (symbol or engine conflicts, unverified tables, chemical equations) carries its source crop: it is a visible image
  in the Markdown/HTML/PDF and collapsed in the reviewer view (`reading.excerpt`). A caption without usable text
  (image only, unreadable, withheld) keeps its crop inside its figure (`caption_images`).
* **Undo is the reviewer's own.** Events written by the system (`actor: system:*`, e.g. the automatic careful
  re-examination) are never Ctrl-Z targets and do not clear the redo stack. Undo/redo targets are computed under
  the event-log lock. Undoing a page-profile change re-processes that page (a cache hit), so its OCR really reverts.
* **"Accept all" is one undoable step** with one rebuild (a unit of work collects the events). Suggestions that
  overlap one accepted just before are skipped as stale.
* **Removals must name something that exists** (group, merge, region, rule, hidden segment): an unknown id records
  nothing, instead of a no-op event that would take the next undo.
* **Class acceptance by sampling:** the error bound is capped by the server (≤ 5%), and the results must cover
  exactly the sample the server draws.
* **Concurrency:** every atomic write uses a unique temporary name. Reviewer-region OCR is serialised per book (a
  light rebuild and a heavy job may both reach it). Page rasters are written atomically and reused only when their
  zoom and hash match. The region OCR cache key includes the OCR language.
* **Errors:** only an explicit `NotFound` becomes HTTP 404. Any other exception is logged with its traceback.
* **Local model runs:** a run where every question failed is `FAILED` (exit 1), not `OK`. The answer cache key
  includes `MAX_TOKENS`, temperature and seed. A model the reviewer rejected no longer counts as a heading hint.
* **Heuristics as checked hints:** mirrored odd/even margins are used only when measured in the book. (Two other
  attempts were reverted on real pages because they turned scan shadows into review work: keeping small Docling-only
  boxes with "text", and keeping position-only furniture exclusions visible. Specks under 150 unit² are ignored and
  position-twin furniture is excluded, as before.)

## Page list: multi-selection
* Click = one segment (opens it); **⌘/Ctrl+click** = add/remove; **Shift+click** = the contiguous range from the last
  clicked row (⌘/Ctrl+Shift adds the range). The selection bar then offers only actions that are safe on many
  segments, each one undo: **Ομαδοποίηση/συγχώνευση/κατηγορία →** (↵, G, M, C; merge only within one page),
  **Εκτός εξόδου** (O), **Αφαίρεση** as wrong boxes (⌫), and **dragging** any selected row, which moves the whole
  selection to that place in its current reading order (`POST /segments/move`, within one page; a member of a group
  brings its group).
* Deliberately not offered in bulk: **approve** (accepting content you did not look at, CLAUDE.md rule 5),
  re-labelling / new OCR (heavy, per segment), and heading levels (a structure decision per heading, with its
  subtree).
