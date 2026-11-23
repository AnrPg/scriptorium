"""Ports: the few interfaces the pipeline depends on, so adapters can be swapped or faked in tests."""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Protocol

Log = Callable[[str], None]


class SpecializedRunner(Protocol):
    def __call__(self, engine: str, jobs: list[dict], out: Path, allow_inference: bool, log: Log) -> dict:
        """jobs: [{'id', 'image', 'sha256'}] -> {block_id: result}; must never raise for model failures."""


class Progress(Protocol):
    def __call__(self, step: str, index: int = 0, total: int = 1, **detail) -> None:
        """Report progress of a named step (see application.processing.STEPS)."""


class NoteInterpreter(Protocol):
    name: str

    def complete_json(self, system: str, user: str, schema: dict, image_png: bytes | None = None,
                      max_tokens: int = 1200, temperature: float = 0.0, seed: int | None = None) -> dict:
        """Return {'json': <object matching schema>, 'raw': str, 'model': str, 'seconds': float}."""


class PageImaging(Protocol):
    """Raster access to the retained source PDF (adapters.pdf implements it)."""
    def crop(self, pdf: Path, page: int, bbox, dest: Path, pad: float = 0.0) -> dict: ...
    def ink_fraction(self, png: Path) -> float: ...
    def paper_tone(self, png: Path) -> list: ...
    def gray_raster(self, pdf: Path, page: int, zoom: float): ...
    def ink_raster(self, pdf: Path, page: int, zoom: float): ...          # darkest channel: sees coloured print
    def text_metrics(self, png: Path, zoom: float) -> dict | None: ...    # typography measured on the image
    def render_zoom(self, pdf: Path, page: int, zoom: float) -> float: ...  # zoom crops really use (scan density)


# Renders formulas with the app's maths renderer: [{id, latex, display}] -> {id: error}, or None if unavailable.
LatexChecker = Callable[[list], 'dict | None']
