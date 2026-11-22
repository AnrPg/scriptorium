# Upstream references checked 2026-10-31

- Marker official repository and JSON/Python APIs: https://github.com/datalab-to/marker
- Docling official repository, CLI, structured documents: https://github.com/docling-project/docling and https://docling-project.github.io/docling/reference/cli/
- Docling model prefetch utility: https://docling-project.github.io/docling/usage/advanced_options/
- UniMERNet (math expression images → LaTeX), Python/weights: https://github.com/opendatalab/UniMERNet
- UniMERNet base HF checkpoint: https://huggingface.co/wanderkid/unimernet_base
- MolScribe (molecular images → molecular graph, SMILES/MOL): https://github.com/thomas0809/MolScribe
- RxnScribe (reaction scheme parsing): https://github.com/thomas0809/RxnScribe
- Chemistry equation typesetting (`mhchem`): https://ctan.org/pkg/mhchem
- Molecule drawing notation (`chemfig`): https://ctan.org/pkg/chemfig
- PyMuPDF cropping and PDF coordinates: https://pymupdf.readthedocs.io/
- Tesseract language resources: https://github.com/tesseract-ocr/tessdata

Important: UniMERNet is a mathematical expression recognizer, **not** a chemical drawing transcription model. MolScribe outputs molecular connectivity and may not preserve Lewis lone pairs/charges as drawn. RxnScribe parses reaction diagram composition, not all mechanistic arrows. Neither tool guarantees faithful text-only LaTeX reproduction of every Lewis structure, electron movement, charge, physical graph, or thermodynamic diagram. Original cropped pixels are the fidelity-preserving fallback.

Old dependencies, checkpoints, Python and PyTorch on Mac may be incompatible; pin exact validated versions **after** setup and real smoke tests. Check each code license **and checkpoint/model license** before commercial reuse.
