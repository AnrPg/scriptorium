# Data contract and rendering rules

## Canonical data model (v1)

The sample in `examples/canonical_example.json` is illustrative, not Marker's or Docling's native JSON schema. Convert original JSON to this contract; NEVER rewrite raw engine files.

### Root fields

`schema_version`, `document_id`, `source` (input SHA-256, path, page count, page dimensions), `run` (versions/settings/model hashes), `pages` (page records), `blocks` (reading-order list), `findings` (audits), `review_events` or link to immutable decisions log, `assets` (image references), `release_status`.

### Block fields

- `id`: stable, deterministic ID from PDF hash + page + source block mapping / region geometry.
- `page`: PDF page number **1-based**; distinguish from printed book page number, which can differ.
- `kind`: `heading`, `paragraph`, `caption`, `table`, `figure`, `chart`, `math`, `chemical_reaction`, `nuclear_reaction`, `molecular_structure`, `reaction_scheme`, `lewis_structure`, `excerpt`, `unknown`.
- `reading_order`: positive integer within page/section, possible manual override.
- `geometry`: original PDF bbox `[x0,y0,x1,y1]`, units points, origin TOPLEFT, optional source transform info; preserved raw source bbox in evidence.
- `chosen`: selected content: `text`, `latex`, `image_only`, `structure` or `unresolved` and selected provenance ID. A `structure` may have simultaneous image representation.
- `candidates`: engine-specific raw transcriptions, parent/child IDs, typed confidence (if available, never fabricated), origin and version.
- `source_crop`: relative PNG path, source page, bbox, DPI, SHA-256; **mandatory for all `excerpt` blocks** and substantive scientific diagrams.
- `relations`: `caption_for`, `figure_group`, `derived_from`, `related_blocks` when supported. If inferred, mark relationship as inferred and reviewable.
- `review`: `not_needed`, `recommended`, `required`, `approved`, `image_only`, `unresolved`; separate from technical OCR status.
- `reasons`: explicit reasons, e.g. `CHEM_LEWIS_DOTS_NOT_REPRESENTABLE`, `MISSING_BOTTOM_TEXT`, `EQUATION_CONFLICT`, `UNKNOWN_VISUAL`, `UNSUPPORTED_SCHEME`, `OCR_FAILED`.

### Evidence

For every merged block retain raw Marker and Docling pointers, original text/LaTeX alternatives and geometry, model-based candidates (UniMERNet, MolScribe, RxnScribe), inference errors, and the source image. Preserve immutability of original records.

## Image crop policy

1. Crop from **original PDF pixels** via PyMuPDF (not reconstructed engine images), using bbox transformed to page space and padded to include nearby extrema, electron dots, arrows, small labels and Greek marks.
2. Retain scale, checksum, page number, source bbox and cropping parameters. Use PNG (lossless) by default.
3. If uncertain boundaries may cut off meaningful text/dots, enlarge region. If still uncertain, use the full page (or a large enclosing region), explicitly marked `CONSERVATIVE_FULL_PAGE_EXCERPT`.
4. Do not create a tiny crop of only a recognized molecule when an adjacent reaction arrow or conditions would be lost. Reaction scheme crop should include complete scheme.
5. Preserve per-region crops for reviewer and downstream multimodal agent, not just paths pointing to original PDF.

## Markdown: recommended agent-facing form

Use ordinary Markdown for Greek text. For a correctly recognized equation:

```markdown
<!-- BLOCK_START id=p5_math2 page=5 type=math provenance=marker -->
$$
\Delta G = \Delta H - T\Delta S
$$
<!-- BLOCK_END id=p5_math2 -->
```

For an ordinary figure with caption, use bounded Markdown that displays in Obsidian:

```markdown
<!-- FIGURE_START id=p5_fig3 page=5 -->
![Original textbook figure 5.3](assets/p5_fig3.png)

**Figure caption (source, p. 5):** ...
<!-- FIGURE_END id=p5_fig3 -->
```

For **anything not sufficiently parsed**, the original excerpt must be **visible in the main document** at the appropriate reading location:

```markdown
Πρώτη παράγραφος...

<!-- EXCERPT_START id=p5_u9 page=5 type=lewis_structure status=IMAGE_ONLY reason=LEWIS_ELECTRON_DOTS_UNVERIFIED -->
![Original unparsed Lewis structure, page 5](assets/p5_u9.png)

**Source excerpt — p. 5.** Preserved as image; molecular electrons / charges could not be faithfully transcribed. Multimodal inspection required.
<!-- EXCERPT_END id=p5_u9 -->

Επόμενη παράγραφος...
```

**Explicitly retain both BEGIN and END boundaries** so captions and descriptions cannot bleed into main prose. In canonical JSON these are typed fields, not syntax guesses. Header/footer omission should appear in audit findings and, when relevant, a bottom-page excerpt.

## LaTeX representation decision table

| Source | Primary structured representation | Always-safe fallback |
|---|---|---|
| Mathematical expression / physics formula | Marker LaTeX, UniMERNet/Docling candidates when needed | `\includegraphics{crop.png}` / image-linked MD |
| Greek/chemical equation | faithful LaTeX or `\ce{...}` (`mhchem`) | source equation PNG |
| Isotope/nuclear reaction | faithful LaTeX/`mhchem` with proper Z/A superscripts | source reaction PNG |
| Skeletal molecule drawing | validated SMILES + MOL, optionally verified `\chemfig{...}` | source molecular PNG |
| Lewis electron-dot structure | carefully validated hand/auto `chemfig` only if all dots/charges shown | **original PNG** |
| Arrow-pushing mechanism | reaction graph metadata + original crop | **original full scheme PNG** |
| Chart/graph/radiation image | pixel-preserved figure + optional verified metadata | **original PNG** |

Do not present a SMILES string as a faithful visual reconstruction of Lewis electrons. Do not imply `mhchem` is an OCR engine. Keeping a faithful source crop is a legitimate, preferred result when extraction cannot be verified.

## Downstream LLM agent contract

- The LLM must consume `chapter.md` **and** referenced actual image attachments for `image_only` regions; a relative path alone does not provide pixels.
- If a region is unverified, the teaching agent may discuss qualitative context but may not invent exact equations, charges, lone pairs, molecule stereochemistry, graph data or nuclear indices.
- Pass page/block IDs into generated lessons and exercises for auditability. Allow unresolved excerpts to be routed to a person before high-stakes content production.
