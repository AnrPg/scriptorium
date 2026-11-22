#!/usr/bin/env bash
# Best-effort macOS installer for ALL FIVE stacks. It reports failures instead of claiming readiness.
# Inspect before running; modifies Homebrew, creates project venvs and downloads public model weights.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG="$ROOT/logs"
VENV="$ROOT/.venvs"
MODELS="${SCIENTIFIC_OCR_MODEL_HOME:-$HOME/.cache/scientific-textbook-ocr/models}"
mkdir -p "$LOG" "$VENV" "$MODELS"
export SCIENTIFIC_OCR_MODEL_HOME="$MODELS"

run_step() {
  local label="$1"; shift
  echo "== $label =="
  if "$@" > "$LOG/$label.log" 2>&1; then
    printf 'READY: %s\n' "$label" | tee "$LOG/$label.status"
    return 0
  else
    code=$?
    printf 'FAILED: %s (exit %s); see %s\n' "$label" "$code" "$LOG/$label.log" | tee "$LOG/$label.status"
    return 0  # Continue to attempt the remaining components; don't hide failures.
  fi
}

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This installer targets macOS; not running on $(uname -s)." >&2
  exit 2
fi
if ! command -v brew >/dev/null 2>&1; then
  echo 'Homebrew is required. Install Homebrew, then rerun this script.' >&2
  exit 2
fi

run_step brew-dependencies brew install uv git-lfs tesseract tesseract-lang poppler llama.cpp
if ! command -v uv >/dev/null 2>&1; then
  echo "uv unavailable; see $LOG/brew-dependencies.log" >&2
  exit 2
fi
run_step python-runtimes uv python install 3.10 3.11
run_step marker-venv uv venv --python 3.11 "$VENV/marker"
run_step docling-venv uv venv --python 3.11 "$VENV/docling"
run_step unimernet-venv uv venv --python 3.10 "$VENV/unimernet"
run_step chemistry-venv uv venv --python 3.10 "$VENV/chem"

if [[ -x "$VENV/marker/bin/python" ]]; then
  run_step marker-install uv pip install --python "$VENV/marker/bin/python" marker-pdf
  run_step marker-weights "$VENV/marker/bin/python" -c 'from marker.models import create_model_dict; create_model_dict(); print("Marker local model initialization completed")'
fi
if [[ -x "$VENV/docling/bin/python" ]]; then
  run_step docling-install uv pip install --python "$VENV/docling/bin/python" docling pymupdf pillow rapidfuzz beautifulsoup4 pydantic jsonschema huggingface_hub
  if [[ -x "$VENV/docling/bin/docling-tools" ]]; then
    run_step docling-weights "$VENV/docling/bin/docling-tools" models download layout tableformer code_formula picture_classifier --output-dir "$MODELS/docling"
  fi
fi
if [[ -x "$VENV/unimernet/bin/python" ]]; then
  run_step unimernet-install uv pip install --python "$VENV/unimernet/bin/python" 'unimernet[full]'
fi
if [[ -x "$VENV/chem/bin/python" ]]; then
  run_step molscribe-install uv pip install --python "$VENV/chem/bin/python" MolScribe
  # RxnScribe upstream pins older dependencies and directly references MolScribe.
  run_step rxnscribe-install uv pip install --python "$VENV/chem/bin/python" 'git+https://github.com/thomas0809/RxnScribe.git'
  # REPAIR (2026-10-31): RxnScribe pulls pytorch-lightning 2.x, which upgrades torch 1.13.1 -> 2.x but
  # leaves torchvision 0.14.1 -> ImportError (_cast_Long). Re-pair torchvision with the installed torch.
  # Verified by known-answer MolScribe/RxnScribe inference (python -m scientific_ocr doctor).
  run_step chem-torchvision-repair uv pip install --python "$VENV/chem/bin/python" 'torch==2.14.1' 'torchvision==0.29.*'
fi
if [[ -x "$VENV/docling/bin/python" ]]; then
  run_step specialized-weights "$VENV/docling/bin/python" "$ROOT/scripts/download_weights.py" --model-dir "$MODELS"
fi

if command -v tesseract >/dev/null 2>&1; then
  tesseract --list-langs > "$LOG/tesseract-languages.log" 2>&1 || true
fi

echo
echo "Installation attempts complete. This DOES NOT establish model inference readiness."
echo "Inspect $LOG/*.status and $LOG/*.log, then run: python3 scripts/doctor.py"
echo "Next: have Claude Code run a real image inference smoke test for EACH engine and record results."

# EasyOCR weights for RxnScribe condition-text OCR (one-time download; inference stays offline)
step_easyocr() {
  mkdir -p "$HOME/.cache/scientific-textbook-ocr/models/easyocr"
  "$ROOT/.venvs/chem/bin/python" -c "import easyocr,os; easyocr.Reader(['en'], gpu=False, download_enabled=True, model_storage_directory=os.path.expanduser('~/.cache/scientific-textbook-ocr/models/easyocr'), verbose=False)"
}
if [ ! -f "$HOME/.cache/scientific-textbook-ocr/models/easyocr/english_g2.pth" ]; then step_easyocr; fi
