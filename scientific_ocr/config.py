"""Paths, interpreters and pinned settings. Plain constants; override model home via env."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENVS = ROOT / '.venvs'
MODEL_HOME = Path(os.getenv('SCIENTIFIC_OCR_MODEL_HOME', str(Path.home() / '.cache/scientific-textbook-ocr/models')))
ENGINES_DIR = Path(__file__).resolve().parent / 'engines'

PY = {name: VENVS / name / 'bin' / 'python' for name in ('marker', 'docling', 'unimernet', 'chem')}
MARKER_BIN = VENVS / 'marker' / 'bin' / 'marker_single'
DOCLING_BIN = VENVS / 'docling' / 'bin' / 'docling'

CKPT = {
    'docling': MODEL_HOME / 'docling',
    'unimernet': MODEL_HOME / 'unimernet_base',
    'molscribe': MODEL_HOME / 'molscribe' / 'swin_base_char_aux_1m.pth',
    'rxnscribe': MODEL_HOME / 'rxnscribe' / 'pix2seq_reaction_full.ckpt',
    'easyocr': MODEL_HOME / 'easyocr',
}

# Spelling dictionaries (Hunspell .aff/.dic, LibreOffice dictionaries project; downloaded once by
# `python -m scientific_ocr install-dictionaries`, pinned by sha256). Tesseract language code -> dictionary.
DICT_HOME = MODEL_HOME.parent / 'dictionaries'
DICTIONARIES = {
    'ell': {'name': 'el_GR', 'url': 'https://raw.githubusercontent.com/LibreOffice/dictionaries/master/el_GR/el_GR',
            'sha256': {'aff': '868dfe13ad7d34251915a745e44d38542831463d56e60732ba3a5bc1dcca582a',
                       'dic': 'e5b9b9c2cf05bbc59e03fe302b462dae85968f822f4fc219a8ed2879d6943720'}},
    'eng': {'name': 'en_US', 'url': 'https://raw.githubusercontent.com/LibreOffice/dictionaries/master/en/en_US',
            'sha256': {'aff': 'e746c882dd6f303c2c46e7452804b9201115a6942cfeb15f18f8edf774d2e24e',
                       'dic': 'f0b1a234bd178bdd01875b2a392a9647f888b8fe879f79c52aae62c2759b3647'}},
}

# Processing must be local: forbid hub lookups once weights are prefetched.
OFFLINE_ENV = {'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'HF_DATASETS_OFFLINE': '1',
               'TOKENIZERS_PARALLELISM': 'false', 'PYTHONWARNINGS': 'ignore'}

# Engine configuration is part of the cache key: change it and extraction re-runs.
MARKER_ARGS = ['--output_format', 'json', '--disable_tqdm']          # mode: device default (fast on MPS)
DEFAULT_OCR_LANG = 'ell,eng'          # per-book setting (tesseract codes, e.g. 'eng', 'deu,eng', 'ell,eng')


def docling_args(lang: str | None = None) -> list[str]:
    return ['--to', 'json', '--ocr-engine', 'tesseract', '--ocr-lang', lang or DEFAULT_OCR_LANG,
            '--ocr-mode', 'full_page', '--image-export-mode', 'placeholder']


DOCLING_ARGS = docling_args()
# Extraction profiles. 'diligent' = forced full OCR at higher resolution, for pages whose layout/OCR failed.
PROFILES = {'standard': [], 'diligent': ['--force_ocr', '--highres_image_dpi', '288', '--lowres_image_dpi', '144']}
REGION_ZOOM = 3.0

# Page rasters/crops: these scans are ~1 PDF point per scan pixel, so zoom 2 (144 dpi nominal)
# over-samples slightly; crops are taken from the original embedded scan pixels via PyMuPDF.
RENDER_ZOOM = 2.0
# Resolution rule: OCR and excerpts never render a page BELOW the pixel density of its own scan (a 300-dpi scan read
# at Marker's default 192 dpi would lose a third of its detail), and never above OCR_MAX_DPI. Defaults of the
# engines, used when the scan is coarser: Marker OCR images 192 dpi, Docling/Tesseract scale 3 = 216 dpi.
OCR_MAX_DPI = 600
# Rebuilding a book (re-projection) after a reviewer action: done at once only when the book's last rebuild took
# at most this long; otherwise actions are BANKED (recorded, not yet applied to the outputs) until the reviewer
# rebuilds — explicitly, or when a view that needs book-wide results asks for it.
AUTO_REBUILD_MAX_S = 15
PAGE_PREVIEW_MAX_PAGES = 8               # more changed pages than this: a full rebuild (~17 s) beats page previews
BUILD_SECONDS_PER_PAGE_GUESS = 1.0      # until a book's first measured rebuild (cache/build.json)
MARKER_DEFAULT_OCR_DPI = 192
DOCLING_DEFAULT_OCR_DPI = 216
CROP_PAD_PT = 8.0


def subprocess_env(extra=None):
    env = dict(os.environ)
    env.update(OFFLINE_ENV)
    if extra:
        env.update(extra)
    return env

# Local vision-language model that interprets reviewer notes into structured proposals (see adapters.local_llm).
LLAMA_SERVER = Path(os.getenv('SCIENTIFIC_OCR_LLAMA_SERVER', '/opt/homebrew/bin/llama-server'))
NOTE_MODELS = {
    'gemma-4-12b-it': {'name': 'gemma-4-12b-it Q4_K_M',
                       'model': MODEL_HOME / 'llm' / 'gemma-4-12b-it' / 'gemma-4-12b-it-Q4_K_M.gguf',
                       'mmproj': MODEL_HOME / 'llm' / 'gemma-4-12b-it' / 'mmproj-F16.gguf'},
    'qwen3.5-9b': {'name': 'Qwen3.5-9B Q4_K_M', 'model': MODEL_HOME / 'llm' / 'qwen3.5-9b' / 'Qwen3.5-9B-Q4_K_M.gguf',
                   'mmproj': MODEL_HOME / 'llm' / 'qwen3.5-9b' / 'mmproj-F16.gguf'},
}
NOTE_MODEL = NOTE_MODELS[os.getenv('SCIENTIFIC_OCR_NOTE_MODEL', 'qwen3.5-9b')]   # measured 7/8 on real notes (2026-11-01)

# Group types offered in the UI (data, not logic: any string is accepted; extend per book or institution).


# ---- local renderers (offline): node runs the vendored KaTeX for checks; Chrome prints the export HTML to PDF
def node() -> Path | None:
    import shutil
    for c in (os.getenv('SCIENTIFIC_OCR_NODE'), shutil.which('node'), '/opt/homebrew/bin/node', '/usr/local/bin/node'):
        if c and Path(c).exists():
            return Path(c)
    return None


def chrome() -> Path | None:
    import shutil
    for c in (os.getenv('SCIENTIFIC_OCR_CHROME'),
              '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
              '/Applications/Chromium.app/Contents/MacOS/Chromium',
              '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
              '/Applications/Brave Browser.app/Contents/MacOS/Brave Browser',
              shutil.which('chromium'), shutil.which('google-chrome'), shutil.which('chromium-browser')):
        if c and Path(c).exists():
            return Path(c)
    return None
