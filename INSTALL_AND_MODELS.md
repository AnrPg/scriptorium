# Install all tools up front — local macOS strategy

**All five stacks must be installed/prefetched before implementation proceeds:** Marker, Docling, UniMERNet, MolScribe and RxnScribe. **Installation attempts are not equivalent to successful inference**. The supplied `scripts/bootstrap_macos.sh` is a best-effort first pass that writes a log per component and continues after failures. Claude Code must investigate failures, fix compatible cases, and leave clear `UNAVAILABLE` statuses if a framework truly cannot run on the user's hardware.

## Environments

Use `uv` to supply known Python versions rather than reuse the existing Marker Python 3.14 venv:

| Stack | Path | Baseline interpreter | Notes |
|---|---|---|---|
| Marker | `.venvs/marker` | Python 3.11 | PDF OCR, layouts, LaTeX |
| Docling + merger | `.venvs/docling` | Python 3.11 | Greek Tesseract OCR, PyMuPDF, JSON / Markdown |
| UniMERNet | `.venvs/unimernet` | Python 3.10 | Official examples use Python 3.10, CPU inference fallback |
| MolScribe + RxnScribe | `.venvs/chem` | Python 3.10 | Old dependency pins; may require compatibility remediation |

**Why one chemistry environment?** RxnScribe uses MolScribe; creating two would increase maintenance. **Why separate UniMERNet?** Its model stack may conflict with older chemistry OpenNMT/Albumentations packages.

## Commands

From root:

```bash
bash scripts/bootstrap_macos.sh
python3 scripts/doctor.py
```

This invokes Homebrew for dependencies, creates environments, attempts package installation, prefetches model artifacts, writes logs. Inspect actual output and `logs/` before declaring success.

### Model sources and prefetch

- Marker: initialize local `marker.models.create_model_dict()` to fetch its required weights. If inference backend/service is needed, configure local runtime; never enable the paid Gemini `--use_llm` path.
- Docling: use `docling-tools models download layout tableformer code_formula picture_classifier --output-dir ...`. Run local Greek Tesseract (`ell+eng`).
- UniMERNet: download `wanderkid/unimernet_base` via Hugging Face snapshot; official instructions describe base/small/tiny model variants, but this bundle targets base initially, if resources permit.
- MolScribe: `yujieq/MolScribe` checkpoint `swin_base_char_aux_1m.pth` as shown in official examples.
- RxnScribe: `yujieq/RxnScribe` checkpoint `pix2seq_reaction_full.ckpt` as shown in official examples.
- Model manifest records paths, file sizes, digests, HF revisions where available, installation/runtime versions, model licenses, tested examples.

NOTE: These model downloads can consume gigabytes and may require a Hugging Face token/rate-limit compliance. No paid OCR vendor is necessary. Do not use the HF Inference API; only download checkpoint bytes and perform inference locally.

## Hard compatibility caveats

- UniMERNet's documented demo uses CUDA if present, else CPU. Do **not** assume Apple Metal/MPS is supported by that exact code path. CPU is acceptable, but may be slow.
- MolScribe's upstream dependencies include older `OpenNMT-py`, older `timm`, `albumentations`, and `numpy<2`; RxnScribe references MolScribe and pins Pillow 9.5.0. A current Mac may require tested constraints or source compatibility patches. Keep modifications in an isolated fork/patch with regression tests and clear provenance.
- Apple Silicon vs Intel influences available prebuilt wheels. Avoid forcing Linux CUDA. Check if GPU/MPS works with actual inference before selecting.
- Do not silently replace model by similarly named packages, invent flags, or mark local inference working from mere imports. Check the exact versions installed with `--help` / code introspection.
- User's existing `~/Documents/MyApps/marker-env` should remain untouched; migrate only deliberately after benchmark.
- Check package/code/model-license terms before commercial distribution.

## Minimal smoke tests Claude must implement after installation

```text
marker_smoke: one 1-page scanned Greek sample -> non-empty JSON + image candidate
Docling_smoke: same source -> valid JSON + provenance + Greek OCR text
UniMERNet_smoke: math crop -> non-empty LaTeX that renders
MolScribe_smoke: molecular drawing crop -> valid candidate graph + SMILES/MOL
RxnScribe_smoke: reaction scheme crop -> reactants/products/conditions candidate
```

Validate against source images, record false outputs, and save `model_readiness.json`. A model that imports but lacks checkpoint or fails inference is `INSTALLED_NOT_READY`.

If any fail, the initial system can still operate using the successful engines and **original image excerpts**. But the upfront install phase must document exactly what failed and why; don't defer installation secretly to a later phase.

## Spelling dictionaries (spelling proposals)
`uv pip install --python .venvs/docling/bin/python spylls==0.1.7` then `.venvs/docling/bin/python -m scientific_ocr
install-dictionaries` — downloads LibreOffice `el_GR` and `en_US` Hunspell files once (sha256 pinned in
`config.DICTIONARIES`) into `~/.cache/scientific-textbook-ocr/dictionaries`. Optional: without them the spelling
stage uses only the book's own vocabulary and the second engine's reading.
