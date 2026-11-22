# Implementation plan — robust but bounded scientific-textbook OCR

## 0. Product contract

Deliver a local Python command that ingests scanned/photographed books in Greek and English, including multi-column pages, embedded scientific equations, structural chemistry, Lewis diagrams, nuclear notation, thermodynamics, kinetics, tables, figures, and graph images, and produces:

1. **Immutable original PDF** (or original path + SHA-256 digest; never modify).
2. **Raw Marker JSON and raw Docling JSON** per batch/page, with model versions and logs. No direct cross-parser JSON ingestion.
3. **Canonical JSON** with typed elements, complete evidence, source page geometry, semantic hierarchy, reading order, crop paths, disagreement status, human decisions.
4. **Markdown in logical reading order** preserving textual content with original figures, captions, equations, and **visibly embedded image excerpts for anything not faithfully parsed**.
5. **Review report** with source crops, competing engine interpretations, and a simple JSON decision mechanism.
6. **Run manifest and audit log** that allow page-by-page retries, cache reuse, and no silent losses.

The pipeline should be robust through traceability, readable code, and a few meaningful auditors—not a tower of agents or services. It cannot mathematically prove no omission on noisy scans. Optimize for *detect-and-preserve*, not *pretend perfect*.

## 1. Engine roles, configured and installed up front

| Engine | Primary role | Key limitation | When invoked during processing |
|---|---|---|---|
| Marker | First text + mathematics/physics LaTeX + image candidates; alternate page geometry | OCR/layout errors, caption merging, may miss bottom text | All selected pages |
| Docling (Greek Tesseract) | Semantic tree, reading order, figure/caption relationships, geometric provenance, table alternatives | Misordered columns/missed blocks, scientific formula errors | All selected pages |
| UniMERNet | Independent math-expression recognition from original formula crops | Not specialized for molecules/Lewis diagrams; CPU speed/compatibility | Ambiguous or high-risk mathematical/physical equation crops |
| MolScribe | Molecular drawing → graph, atoms/bonds, SMILES and MOL; optional validated depiction | Doesn't guarantee electron dots, mechanisms or original layout | Detected standalone molecular structures |
| RxnScribe | Detect reactants/products/conditions and regions in reaction schemes | Not an all-purpose chemical equation renderer or mechanism recognizer | Multi-molecule reaction diagrams |

All five installed and checkpoints prefetched during Phase A. **Do not** run all five against every page. Separate installation lifecycle from inference dispatch.

Additional dependencies kept small: PyMuPDF (source crops/page images), Pillow, Tesseract CLI with `ell+eng`, RapidFuzz (matching), BeautifulSoup (light HTML processing), Pydantic/JSON Schema (schema validation), and **RDKit in chemistry env** for structured molecule validation. No paid LLM, Ollama VLM or external parser APIs.

### 1.1 Scientific content routing

- `ordinary_prose`, `heading`, `table`, `caption`: compare Marker/Docling, favor readable/complete verified blocks and keep provenance.
- `math_equation`, `physics_equation`, `thermodynamics_equation`, `kinetics_equation`: Marker LaTeX first. Keep both candidates. UniMERNet independent crop transcription when high impact/uncertain. Use LaTeX output *only if valid and credible*; otherwise image crop.
- `chemical_formula`, `text_reaction`, `ionic_reaction`, `nuclear_reaction`: Marker text/LaTeX first; normalized output can use `mhchem` but only when faithful; verify critical subscript/superscript/charge/isotope differences. Uncertain -> crop.
- `molecular_structure`: MolScribe outputs molecule graph + SMILES/MOL, not necessarily LaTeX. Keep image crop ALWAYS. Generate `chemfig` only if an available offline converter gives faithful and reviewable rendering. Otherwise image-backed LaTeX `\includegraphics{...}` plus structured molecule metadata.
- `reaction_scheme`: RxnScribe supplies reactants/products/conditions and boxes; MolScribe handles per-molecule structures. Preserve original full scheme crop; do not flatten stereochemical or electron arrow annotations if not detected.
- `lewis_structure`, `resonance`, `electron_pushing`, `orbital_diagram`: **always keep original crop**. Only add editable representation when all dots, lone pairs, radical electrons, bonds, charges, resonance/curved arrows and stereo cues can be represented and checked. If not, use **image_only**.
- `chart`, `plot`, `microscopy`, `radiology`, `pathway`, unknown images: keep cropped source image; OCR text labels only when safely extracted, do not invent quantitative curves/values.

A label classifier is a best-effort heuristic, not an authority. Ambiguous categories must be tagged `unknown_visual` and cropped, not discarded.

## 2. Phase A — installation, assets, and proof of inference

### A1. Preflight
- Detect macOS version, Intel vs Apple Silicon, RAM, free space, `brew`, `uv`, GPU/CPU availability, disk write permission, Greek language data.
- Never mutate existing `~/Documents/MyApps/marker-env`; create self-contained project envs under `.venvs/`.
- Record preflight JSON. Warn of model download disk needs and that chemistry stacks have old pinned dependencies.

### A2. Isolated installs and prefetch ALL checkpoints up front
- `.venvs/marker` Python 3.11 (or confirmed supported).
- `.venvs/docling` Python 3.11; includes core merger dependencies.
- `.venvs/unimernet` Python 3.10; officially documented baseline; installed separately.
- `.venvs/chem` Python 3.10; MolScribe and RxnScribe together (RxnScribe uses MolScribe).
- Download Marker weights with local model initialization, Docling layout/table/picture/code models via `docling-tools`, UniMERNet checkpoint via HF, MolScribe and RxnScribe checkpoints via HF.
- Create a model registry with `READY`, `INSTALLED_NOT_TESTED`, `INSTALL_FAILED`, `INFERENCE_FAILED`, model ID and file hash. **Downloaded != usable.**
- Check licenses and terms, both software and weights.

### A3. Smoke test on one small real crop per model
- Marker, Docling: actual Greek page. Check nonempty JSON + image mapping.
- UniMERNet: known mathematical equation crop; output contains plausible LaTeX.
- MolScribe: small real molecule crop; returns graph / MOL / SMILES; RDKit parse and visually compare.
- RxnScribe: reaction scheme crop; returns reactant/product/condition boxes and test outputs.
- If a tool doesn't work on the target Mac, keep its intended adapter and report `UNAVAILABLE`. Don't silently swap in a paid service. Existing engines and crop fallback still work.

**Exit gate A:** complete environment report. All five attempted, checkpoint paths recorded, inference truthfully reported.

## 3. Phase B — source pages, dual extraction, immutable evidence

### B1. Input and page handling
- Accept PDF file and page range; independently count pages with PyMuPDF, reject zero/invalid indices; normalize `page_no` to 1-based for canonical IDs.
- Hash the input PDF; render source page rasters at a configurable resolution suitable for small Greek accents and sub/superscripts (e.g. start 200–300 DPI, adjust to source quality). Keep coordinates in original PDF points and document transformation matrix to raster pixels.
- Detect rotation/deskew metadata, crop bounds, page dimensions, two-column candidate geometry.
- Store source page image and source PDF reference; never rely on rerendered engine output as ground truth.

### B2. Parallel, independent extraction
- Run Marker and Docling separately on **the same source-page range**, not passing Marker JSON as Docling input.
- Marker `--output_format json`, preferably configurable `fast` vs `balanced`; do not use paid `--use_llm` or remote APIs. The CLI syntax must be validated with locally installed version.
- Docling `--to json`, `--ocr-engine tesseract --ocr-lang ell,eng --ocr-mode full_page`; export referenced images or render independent source crops from coordinates.
- Do not flatten JSON to Markdown until after reconciliation; raw parser outputs preserved exactly.
- Page-level process logs include exit code, OCR setup, model/checkpoint version, timing and output files. Catch per-page errors without losing completed pages.

### B3. Repeatable work units
- One job per page or a small batch; cache keys include PDF SHA, canonical page set, engine version/config, model hashes, crop resolution. Atomic writes and file locks as required.
- Re-running `render` or `review` must reuse extraction cache. Re-running one failed page must not invalidate other pages.

**Exit gate B:** both engines emitted raw, traceable records for three-page trial; absent or failed output is marked rather than treated as blank page.

## 4. Phase C — normalization and block reconciliation

### C1. Normalize without throwing away information
Create an adapter for each raw parser that yields an internal `Block`:
- `id`, `engine`, `page`, `kind`, `raw_text`, `latex`, `geometry`, `parent/child refs`, `reading_order_hint`, `source_record_pointer`, `original_image_path`, `confidence if available`.
- `bbox` normalized to top-left PDF point coords, with source coordinate origin also preserved.
- Store text lines/paragraphs and labels as distinct typed blocks; keep every original element reference. Preserve raw output separately.
- When block classes differ, allow uncertain `kind_candidates`; do not force a figure into prose.

### C2. Match blocks within page
- Use page number + normalized bbox IoU, center distance, and Unicode-normalized content similarity. Greek and Latin lookalikes are **not** safely interchangeable in scientific notation; use conservative normalization.
- Candidate matching should handle 1:N paragraph splits, N:1 merges, cross-column figures, and captions. Use local neighborhood context and a maximum displacement threshold.
- Build an alignment graph; avoid greedy 1:1 matching when a text block is split into two. Keep `matched`, `single_engine`, `conflict`, `overlap_ambiguous` statuses.
- Docling's logical sequence is the initial baseline, not absolute truth. Prefer Marker math candidate when reliable.

### C3. Preserve coverage and image excerpts — MANDATORY
For every region containing potentially meaningful source content:
1. If faithfully parsed as text or equation, include a structured text element and source provenance.
2. If it is a figure/graph, include an extracted **original** image crop and attach an explicit caption element.
3. If detection is uncertain, outputs conflict critically, a structure is only partially decoded, or the image contains unsupported notation: emit an `excerpt` block with a crop taken directly from the PDF, and clear `reason` + `review_required`.
4. If a region is unmatched (seen by only one engine), retain its text as a candidate and keep its image evidence; do not discard because primary reader missed it.
5. If page-level coverage audit finds suspicious unaccounted printed areas, create additional `excerpt` blocks. For uncertain crop location, include a safely expanded region or full-page image. Deduplicate overlapping fallback excerpts where possible without suppressing meaning.
6. A blank page cannot be inferred from `no OCR text` alone; visual check or review is required.

**Important:** No automated blank-space/edge detector can guarantee every missed printed element is found. Document that uncertainty and allow the reviewer to mark/append regions manually.

### C4. Main text vs caption separation
- Caption is a typed element with explicit `caption_for` figure/table ID and own bbox.
- Never append caption text to adjacent prose based only on close coordinates. Distinct marker in Markdown (see format doc).
- Reading order permits moving the figure group to a boundary *after* the nearest complete paragraph, while preserving original bbox and page metadata. Ambiguous positions -> review.

**Exit gate C:** page content not omitted merely because OCR failed; cropped excerpt visible; final figure captions bounded.

## 5. Phase D — scientific notation recognition and safe representation

### D1. Mathematical and physical expressions
- Detect inline vs display equations and crop from **original source** with padding (include important sub/superscripts and fraction bars).
- Preserve raw Marker LaTeX, Docling candidate and original crop; compare by strict token-aware normalization. Only normalize superficial spaces/braces where semantically safe.
- For differences in operators, exponents, subscripts, Greek symbols, coefficients, matrices, integrals, rates, boundaries, units or important domain notation, call UniMERNet on original crop. Save all candidates.
- UniMERNet is a *third transcription*, not an image-grounded proof. If the best candidate is unresolved, preserve crop in Markdown at that position. Never guess a missing glyph from context.
- Prefer Marker for correctly verified LaTeX. Render preview to detect syntax/renderability only; mathematical equivalence isn't transcription fidelity.

### D2. Chemistry and nuclear reactions
- Distinguish symbolic text reaction from drawing-based scheme.
- Symbolic text reactions -> preserve Marker raw LaTeX; optional `\ce{...}` representation if it faithfully preserves stoichiometric coefficients, subscripts, charges, arrows, states, catalysts, equilibrium and isotope numbers.
- Optional deterministic checks: valid element symbols, atom balance when fully specified, charge balance when appropriate, nuclear A and Z conservation. **Scientific equations can be intentionally unbalanced, partial or represent open systems**: failing a check flags review, never causes correction or deletion.
- Ensure kinetic/thermodynamic signs and state labels remain faithful. Keep non-standard symbols as crop.

### D3. Molecular structures
- Feed genuine molecule drawing crops to MolScribe; store SMILES, MOL block, atom/bond coordinates/confidence and original crop.
- Check parseability with RDKit and round-trip **depiction** against the crop as an advisory QA check. Structure validity is NOT evidence it matches the printed molecule.
- Keep wedge/hash stereochemistry, aromatic bonds, formal charges, isotopes, attachment labels and atom mapping when possible; uncertain values require human review.
- **Lewis electron pairs, radicals, resonance and arbitrary annotations often do not survive SMILES/MOL.** Even if MolScribe returns valid SMILES, treat the original crop as obligatory and potentially `image_only` for those features.
- A verified molecular structure can optionally be converted into `chemfig` if a trustworthy offline conversion is available. Do not write bogus `\chemfig` strings as a cosmetic success claim. Image-backed LaTeX `\includegraphics{...}` is the faithful fallback.

### D4. Reaction schemes
- Send full scheme crop to RxnScribe; use its geometry to identify reaction components, conditions, directions and molecule crops; MolScribe handles molecular components.
- Preserve original full reaction scheme crop even if parsed. Never treat a reaction mechanism involving curved electron arrows as a simple reactant/product scheme unless reviewer confirms completeness.
- Save both structured reaction graph and image. If conditions or arrows ambiguous, mark review and export source scheme as image-only excerpt.

### D5. Diagrams and graphs
- Original crop is authoritative. Text labels may be added as verified metadata, not silently fabricated. Preserve graph axes and numerical ticks as pixels if numeric data cannot be reliably reconstructed.

**Exit gate D:** chemistry and math examples round-trip visibly or remain explicitly as excerpts. Model availability failures don't erase information.

## 6. Phase E — audits, review, and resolution

Implement auditors as simple, deterministic functions returning structured findings; see `QA_REVIEW_AND_TESTS.md`. Minimum:
- Page count / page mapping / extraction errors.
- Unmatched text and suspicious unaccounted visible regions, **especially bottom page margin**.
- Left/right column reading order and duplicate detection.
- Figure/caption relationship and boundary separation.
- Equation/crop agreement and critical scientific-notation disagreements.
- Molecular connectivity, charge/stereo/lone-pair warning and reaction scheme coverage.
- Asset existence, safe paths, Markdown parseability, JSON schema validity, exporter parity.

A deterministic auditor must never claim to visually verify image contents by magic. Suspected omissions and low-fidelity blocks appear in `review_report.html` with original crop and both engine candidates. Reviewer can approve a candidate, replace text/LaTeX, mark `image_only`, change reading order, adjust crop, or mark unresolved. Persist changes in an append-only `decisions.jsonl` or equivalent, never overwrite raw extractions.

Release gate: `LLM_READY_WITH_EXCERPTS` is allowed when unresolved areas remain visibly cropped and labeled; `VERIFIED` only after applicable review and verification requirements. Always report counts and outstanding issues.

## 7. Phase F — deterministic rendering

### Markdown
- Export from canonical JSON only; don't feed raw Marker Markdown directly to LLM.
- Preserve headings, paragraphs, tables (HTML for complex spans), inline/display LaTeX, citations/page references, explicit figure/caption delimiters.
- Place each unresolved `excerpt` **between the nearest safely ordered surrounding paragraphs**, not at the end of the chapter; record its page and bbox. Prefer conservative paragraph boundary to unnatural mid-sentence image placement.
- Every `excerpt` must have image asset, warning, source location and reason. End delimiters separate caption from main prose.
- When an equation is both transcribed and preserved as source evidence, default display to LaTeX; link crop for review; when not confidently parsed, display crop (don't also show dubious LaTeX as fact).
- Keep image paths relative and validate that they resolve. Check rendering in Obsidian / common Markdown preview.

### Canonical JSON
- Source of truth for all downstream transforms and reviewer actions. Store 1-based page, typed blocks, original bbox and coordinate origin, alternate candidates, crops, review status and clear source provenance. JSON schema and example in `SCHEMA_AND_FORMATS.md`.

### LLM consumption
- Chunk by chapter/section and semantic continuity, never merely split an equation or separate a figure from its caption; maintain IDs/page references.
- Agent must be multimodal for image-only excerpts; provide image bytes, not just path. For text-only agents, annotate `UNREADABLE_IMAGE_CONTENT` and do not claim content was parsed.
- Downstream teaching content must distinguish OCR text, reviewed transcriptions, and source-image-only excerpts. The agent must not infer an exact Lewis structure or numerical graph from a caption alone.

## 8. Quality benchmark before scaling

Use a 10–20 page annotated set drawn from the user's PDFs, covering two-column Greek prose, bottom-of-page paragraphs, interleaved captions, Lewis electron dots, curved-arrow mechanism, organic skeletal formula, reaction scheme, thermodynamics equation, multi-line kinetics derivation, nuclear isotope expression, and graphs.

Measure:
- Text recall and character error on manually transcribed Greek passages.
- Critical omitted paragraphs per page, bottom edge losses.
- Reading order/caption association accuracy.
- Exact-match notation for reviewed mathematical/chemical/nuclear expressions.
- Molecular graph atom/bond/stereo correctness when applicable.
- **Excerpt coverage:** of manually annotated regions that cannot be parsed, fraction visibly preserved in correct page/neighborhood.
- False-claim rate: unverified engine output labeled `VERIFIED` (must be zero).
- Human workload per 10 pages, runtime/resource consumption.

Tune models and heuristics only with this evidence. Do not make up accuracy percentages. Check first 3 pages, then whole 24-page chapter; only then scale to 500 pages.

## 9. Minimal proposed code organization

```
scientific_ocr/
  cli.py                  # one CLI
  configs.py              # flags and YAML/JSON config
  extract.py              # subprocess adapters (marker, docling)
  source.py               # render pages and crop original PDF
  normalize.py            # preserve engine block data
  match.py                # spatial/text block alignment
  science.py              # math/chem routing, model subprocess calls
  canonical.py            # schema, IDs, versioning, provenance
  audit.py                # small deterministic functions
  review.py               # review report + decisions replay
  render.py               # JSON -> Markdown + assets
  cache.py                # resumability + hashes
  engines/
    unimernet_adapter.py
    molscribe_adapter.py
    rxnscribe_adapter.py
 tests/
```

Keep `engines/` as subprocesses in their respective virtual environments; no need for HTTP services or GPUs. Avoid duplicated abstractions. A small number of modules is illustrative, not mandatory. Unit tests cover data contracts, integration tests cover one actual page per category.

## 10. Risks and boundaries to state plainly

- Neither parser can guarantee comprehensive text coverage; region coverage estimation is imperfect. Original page or larger excerpt fallback is necessary when uncertain.
- UniMERNet does not solve chemical Lewis structures. MolScribe SMILES may omit precisely the lone pairs/electron notation required by a textbook. RxnScribe does not recreate every electron-pushing mechanism.
- There is no general reliable *automatic LaTeX conversion* for arbitrary scientific images. Valid `\includegraphics` in LaTeX and image-linked Markdown are acceptable faithful outputs; editable `chemfig` should be optional and verified.
- Real Apple Silicon/Intel support depends on pinned dependency versions; old RxnScribe/MolScribe packages may fail on current Python/PyTorch. Report and use original crop, not false success.
- Total disk and computation may be substantial: prefetching all models up front does not eliminate local resource cost.
- Licensing matters especially if user later commercializes the teaching app.
