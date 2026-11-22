# Auditors, reviewers, and testing (small, rigorous)

## Auditors as deterministic Python functions

A finding has `id`, `page`, `block_ids`, `type`, `severity` (`info`, `warning`, `critical`), `evidence`, `reason`, `recommended_action`, `review_status`.

### Required checks

1. **Page integrity:** source page count matches processed set; page-index conversion correct; no missing batch silently accepted.
2. **Engine integrity:** raw JSON parseable; per-page failures and empty text recorded; cache versions match.
3. **Coverage & bottom-of-page:** compare combined detection regions to original-page raster; inspect bottom ~20–25%, look for uncovered printed content and Docling/Marker unmatched paragraphs. Detect suspicious uncovered regions, *not falsely claim 100% detection*. Escalate to source crop or reviewer.
4. **Reading order:** overlapping two-column blocks, large unexpected jumps, reversed headings/paragraphs; record source order vs logical order.
5. **Deduplication:** prevent adding two copies of the same paragraph or crop; never deduplicate distinct scientific formulas with superficially similar text.
6. **Caption boundaries:** each caption has a parent figure/table or is flagged; no continuous caption text merged into body paragraphs; explicit END in Markdown.
7. **Figure and excerpt survival:** every non-text visual is either represented faithfully or included as a visible original crop, as appropriate. `excerpt` without PNG is critical.
8. **Math notation:** malformed LaTeX/important differences (sign, fraction, exponent, index, Greek/Latin collision, derivative/integral bounds). Syntax validation alone does not mean visual fidelity.
9. **Chemistry/nuclear notation:** charge/isotope arrow differences; optional conservation warnings if the reaction is complete; do not auto-correct deliberately unbalanced equations.
10. **Molecular structure:** RDKit parses returned molecule; check stereo, charges, atom counts vs source labels when available; `Lewis` flag if lone pairs/radical dots not representable in SMILES.
11. **Reaction scheme:** reactant/product arrows and conditions accounted for; if curved arrows or conditions missed, keep original scheme crop.
12. **Asset and path integrity:** all relative image paths exist, PNG readable, bbox within page, no path traversal, crop content nonempty, source hash present.
13. **Export integrity:** all canonical `required` or `image_only` elements appear as text or visible image excerpt in final MD. HTML review preview renders properly; MD text is Unicode Greek.

### General policies

- Agreement of Marker + Docling is *not proof*; allow sampled human audits of agreements.
- Unsupported equation type should become `IMAGE_ONLY` + reviewer notice, not invented LaTeX.
- A confidence score can help prioritize human review, but cannot auto-certify without empirical calibration and a trusted test set.
- No errors in tool logs may be silently suppressed. `NO_TEXT_FOUND` is not the same as a blank page.

## Human review interface (minimal)

Use **one static HTML review file** (generated locally, no backend) or Markdown report paired with a `decisions.jsonl` file read by the CLI. For each critical item display:
- PDF page and source crop with zoom / link to original whole page.
- Marker candidate, Docling candidate, and specialized model candidate if applicable.
- Side-by-side rendered LaTeX preview where supported, plus original text strings.
- Suggested block kind, reading-order neighbors, source bbox, and deterministic reason for review.

Reviewer actions, recorded append-only:
- `approve_candidate` with engine ID; `replace_text`; `replace_latex`; `mark_image_only`; `adjust_bbox`; `move_after_block`; `associate_caption`; `request_rerun`; `mark_unreadable`.
- Store timestamp, actor (local reviewer ID), previous value hash, current value, reason; no destructive rewrite of raw input.
- `resume` / `render` should replay decisions without rerunning inference; invalid decisions must fail validation safely.

## Test fixtures (build first)

**Unit fixtures:**
- Marker 1:N vs Docling N:1 paragraph splits; IoU/coordinate transforms; Greek/Latin visually similar letters; captions ending before following paragraph; duplicate cross-column text; symbol disagreement; no figure pixels; missing crop file; bad/empty JSON.

**Integration: verified source-page crops:**
- 1: Greek two-column textbook with footnote and bottom-page text.
- 2: Graph with Greek axis labels, legend, and caption under figure.
- 3: Organic skeletal formula including stereochemistry and charged atoms.
- 4: Lewis structure with electron pairs/radicals, resonance, formal charges.
- 5: Organic reaction with reaction conditions + curved electron arrows.
- 6: Thermodynamics complex multiline equation (e.g. partial derivatives, summations).
- 7: Kinetics rate law with nested powers/subscripts/Greek variables.
- 8: Nuclear notation with isotopes, decay products, exponents.
- 9: Image-only biology illustration without OCRable text.
- 10: Original page entirely unreadable / distorted; full-page fallback visible.

**End-to-end assertions:**
- Every manual annotated region that both parsers miss appears either via reviewer-added excerpt or page-level unresolved fallback. The program must disclose inability to detect unknown regions automatically.
- No `excerpt` missing from Markdown. No caption bleeding into body text. Mathematically significant conflicts are not silently resolved.
- Valid and reviewable canonical JSON; every rendered image path resolves; original PDF hash unchanged.
- Model-unavailable test: simulated UniMERNet / MolScribe / RxnScribe error cannot drop source crop or make job status successful.
- A 3-page incremental edit to caption wording does not rerun Marker/Docling.
- A 24-page rerun resumes and does not duplicate content.

## Readiness levels and truthful claims

- `DRAFT`: extraction incomplete, tests not passed, or critical unknown omitted areas.
- `REVIEW_REQUIRED`: unresolved scientific conflicts/regions; excerpts preserved but document not yet approved for unsupervised content generation.
- `LLM_READY_WITH_EXCERPTS`: all known content represented as either verified text or visible, labeled original images; multimodal consumption is necessary.
- `VERIFIED`: human-reviewed according to a defined coverage protocol; not a blanket zero-error guarantee.

For the first release require: no disappearing content in manually annotated benchmark, caption boundaries correct on benchmark, all known unparsed portions visible as PNG excerpts, and a reproducible 24-page run. Publish remaining warnings clearly instead of a false 100% accuracy statement.
