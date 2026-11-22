# Start here — exact Mac / Claude Code workflow

1. Extract the ZIP into a **new** project folder (do not overwrite your older `textbook_pipeline` unless you choose to merge):

```bash
mkdir -p "$HOME/Documents/MyApps/scientific_textbook_ocr"
unzip "$HOME/Downloads/Claude_Scientific_Textbook_OCR_Claude_Code_Kit.zip" -d "$HOME/Documents/MyApps/scientific_textbook_ocr"
cd "$HOME/Documents/MyApps/scientific_textbook_ocr"
```

2. Open this directory in Claude Code (or your editor). `CLAUDE.md` is already at the project root.

3. **Inspect the bootstrap before running it**; it installs Homebrew packages, creates virtual environments, and downloads models/checkpoints. Then:

```bash
bash scripts/bootstrap_macos.sh
python3 scripts/doctor.py
```

If any model is unavailable, keep the error logs. Claude should repair incompatibilities where possible and implement an image-only fallback where not. No need to pretend that all engines support your Mac natively.

4. Your initial PDF fixture is `~/Documents/CHAPTER_5.pdf` (confirmed earlier as 24 pages). Test one troublesome two-column page, one page with a bottom omission, and one with Greek chemistry/math. In Marker the page range is **zero-based**; in Docling it is **one-based**. Store a canonical 1-based PDF page ID in the merger.

5. Give Claude Code this exact instruction:

> Build this scientific textbook OCR pipeline from scratch. Read `CLAUDE.md` as authoritative, then `IMPLEMENTATION_PLAN.md`, `SCHEMA_AND_FORMATS.md`, and `QA_REVIEW_AND_TESTS.md`. Install and verify all five stacks and local model weights **up front** using/repairing the bootstrap, without altering my existing venv. Start with 3 real pages from `~/Documents/CHAPTER_5.pdf`; retain original PDF and all raw outputs. Any unparsed or uncertain part MUST be visibly retained as a crop from the source page, close to its reading position, with source references. Use Marker-first LaTeX, Docling semantic structure, UniMERNet for complex math conflicts, MolScribe for molecules and RxnScribe for reaction schemes. Do not falsely convert Lewis dots or mechanisms into incomplete SMILES or LaTeX; use original image excerpts when necessary. Implement the smallest working end-to-end solution, tests, and review report before adding refinements. After each phase report what actually works, what fails, the exact commands run, and any unresolved critical audit findings. Do not add paid LLMs or a complex agent framework.

6. Definition of a successful *first delivery*: `canonical.json`, `chapter.md`, `assets/`, `review_report.html` or `.md`, `run_manifest.json`, raw extraction files, and real regression tests. A visible image-only fallback is a successful preservation outcome, not a failure of the whole chapter.

## Notes

- Keep the source PDF physically separate and unchanged. Do not edit the user's actual textbook.
- The included setup script is a **best-effort installer**, not proof of all frameworks running. Legacy chemistry packages may be difficult on current Macs. Run model inference tests explicitly and record failures.
- The installer does not require a cloud LLM subscription. Initial Hugging Face/GitHub model downloads do need internet and sufficient disk space.
