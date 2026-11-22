# Claude Code instructions — Scientific Textbook OCR

## Purpose and non-negotiables
Build a maintainable **offline-first**, local Mac pipeline for scanned **Greek/English** textbooks covering mathematics, chemistry (including Lewis electron-dot structures), complex molecular structures, reaction schemes, thermodynamics, kinetics, nuclear physics, medical physics, and biology.

**ALL FIVE engines must be installed/prepared up front**: Marker, Docling, UniMERNet, MolScribe, RxnScribe. Download the necessary checkpoints in the setup phase and verify actual inference where hardware supports it. Use separate environments for conflicting dependency families. Do not invoke specialized models on every page merely because they are preinstalled. No paid LLM APIs, no hidden remote OCR/inference. Initial downloads are permitted; subsequent processing must be local.

**Never silently lose source content.** If a paragraph, formula, reaction, diagram, table, caption or unknown region cannot be parsed with adequate fidelity, **crop that portion from the original PDF** and embed a visible excerpt exactly where it belongs in the approximate reading flow. Keep page number, bounding box, reason, and image path. If geometry is uncertain, expand crop; if no safe localized crop exists, include a full-page excerpt and flag review. Do not invent text or chemistry structures.

Use **Marker's LaTeX preferentially** for mathematics and physics. Docling contributes semantic hierarchy, figures/captions and cross-checks. UniMERNet handles uncertain math/physics equation crops (not general Lewis structures). MolScribe handles molecular graph extraction; RxnScribe handles multi-object reaction schemes. Lewis electron dots, radical dots, electron-pushing mechanisms, resonance/stereochemical annotations, and kinetic/thermodynamic diagrams must be preserved as image excerpts when symbolic outputs are incomplete. Convert to `mhchem`, `chemfig`, SMILES/MOL **only where faithful and validated**; LaTeX `\includegraphics` is an acceptable lossless representation for a chemical drawing, even when text-only LaTeX is unavailable.

## Implementation style and token economy
- First read `START_HERE.md`, `IMPLEMENTATION_PLAN.md`, `SCHEMA_AND_FORMATS.md`, and `QA_REVIEW_AND_TESTS.md` as needed. Do not repeatedly ingest all files in every step.
- Prefer **Python CLI + plain files**, small typed dataclasses/Pydantic only if needed. No database server, multi-agent orchestrator, paid APIs, vector database, elaborate web UI, or services in v1.
- Single entrypoint `ocr_book <pdf> --output <dir> --pages <range>`, distinct `install`, `doctor`, `extract`, `merge`, `audit`, `render`, `review`, `resume` subcommands if useful. CLI may be `python -m scientific_ocr`.
- Idempotent, page-batched, resumable; never recompute OCR when a renderer or review flag changes.
- Pin versions and record model/checkpoint hashes; use content-addressed cache keys. Avoid installing into existing user's Python 3.14 Marker venv without permission.
- Keep raw outputs immutable and preserve source provenance for all claims. Keep block IDs stable across rendering; don't turn LLM guesses into authoritative OCR.
- Write small test fixtures first, test 3 real textbook pages, then 24; do not build unproven features.
- Build a **single minimal reviewer report** (static HTML or Markdown + editable decisions JSON) with original crop, candidates, reason, and page. No polished dashboard until necessary.
- For any setup/inference failure, record `UNAVAILABLE` / `FAILED`, log exact error and command, and continue to the best available fidelity-preserving path; NEVER mark model tested merely because it imports.

## Resolution rules
1. Original scan takes precedence over all machine-generated interpretation.
2. Conserve all detected elements; if uncertain, preserve cropped excerpt visibly.
3. Semantic associations + inferred reading order form the LLM view; source-page bboxes remain in canonical JSON.
4. Do not collapse or concatenate captions with body text. Wrap every figure/caption in explicit boundaries.
5. Do not auto-accept scientific symbols, coefficients, charges, superscripts, isotope numbers, reactant/product directions, equilibrium arrows, rates, units, stereochemistry, or lone pairs solely from OCR consensus.
6. No blanket `confidence > threshold = correct`. Validate on manually labeled Greek scientific pages and keep critical conflicts reviewable.
7. Never say a 500-page document is fully verified unless it actually has the required human/source validation; use statuses `DRAFT`, `REVIEW_REQUIRED`, `LLM_READY_WITH_EXCERPTS`, `VERIFIED`.

## Implementation order
**Setup all five tools and model weights first; then develop incrementally.**
Phase A: installation + smoke tests. Phase B: extraction & page images. Phase C: schema + matching + image-excerpt fallback. Phase D: scientific routing and validation. Phase E: auditing & reviewer corrections. Phase F: Markdown export and acceptance tests. Treat `IMPLEMENTATION_PLAN.md` as specification, but keep code lean.

No claims of guaranteed zero errors. Report precise limitations and demonstrate tests on actual input.
