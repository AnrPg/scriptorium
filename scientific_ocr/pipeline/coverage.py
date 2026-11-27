"""Stage: printed ink that no engine box covers (heuristic; it can reveal omissions, never prove completeness)."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from ..core.geometry import clamp

CELL_UNITS = 2.0          # analysis cell size, in page units (geometry.unit): fine enough for small print
MIN_AREA_UNITS = 640.0    # smallest uncovered ink area reported, in square page units (scale-invariant)
BOX_PAD_UNITS = 6.0


def _sheet_mask(gray, ndimage, margin_cells: int = 4):
    """Cells belonging to the paper sheet: the largest bright connected area (photographed pages show table, hands
    or the facing page around the sheet), eroded by a margin so the sheet's own edge and shadow are not 'ink'."""
    bright = gray > max(120.0, float(np.percentile(gray, 50)) * 0.75)
    bright = ndimage.binary_opening(bright, iterations=2)
    lab, n = ndimage.label(bright)
    if n == 0:
        return np.ones_like(bright)
    sizes = ndimage.sum(bright, lab, range(1, n + 1))
    sheet = lab == (int(np.argmax(sizes)) + 1)
    sheet = ndimage.binary_fill_holes(sheet)            # print and figures inside the sheet belong to it
    return ndimage.binary_erosion(sheet, iterations=margin_cells)


BAND_ROW_FILL = 0.8       # a row of a region that is this dark across its width belongs to a solid band (fill)


def _band(dark_region, row0: int, cell_pt: float) -> dict:
    """How much of a region is a SOLID BAND (the dark or coloured bar behind a title, a shaded header): the share of
    its rows that are dark across >= BAND_ROW_FILL of the width, and their vertical extent in page points. Print is
    never that dense; filled backgrounds are (pipeline.fills decides what the band is)."""
    rows = dark_region.mean(axis=1) >= BAND_ROW_FILL if dark_region.size else np.zeros(0, bool)
    idx = np.where(rows)[0]
    if not len(idx):
        return {'band': 0.0}
    return {'band': round(float(rows.mean()), 3), 'band_y': [(row0 + int(idx.min())) * cell_pt, (row0 + int(idx.max()) + 1) * cell_pt]}


def uncovered_ink_regions(imaging, pdf: Path, page: int, covered: list, unit: float = 1.0) -> tuple[list[dict], dict]:
    from PIL import (
        ImageFilter,  # heavy imaging libraries: loaded only when coverage runs
    )
    from scipy import ndimage
    CELL_PT, BOX_PAD_PT = CELL_UNITS * unit, BOX_PAD_UNITS * unit
    # darkest colour channel: coloured print (teal titles, margin outlines, red labels) counts as ink; a grey
    # raster at analysis resolution washed light colours out to "paper" and hid whole margin columns
    raster = getattr(imaging, 'ink_raster', None) or imaging.gray_raster
    im, (W, H) = raster(pdf, page, 1 / CELL_PT)
    ink = np.asarray(im.filter(ImageFilter.MinFilter(3)), dtype=np.float32)
    bg = np.asarray(im.filter(ImageFilter.MedianFilter(31)), dtype=np.float32)
    paper = bg > 120                                    # excludes dark photo background / deep shadows
    sheet = _sheet_mask(np.asarray(im, dtype=np.float32), ndimage)   # the physical page inside the photo
    dark = (ink < bg * 0.72) & paper & sheet
    cov = np.zeros_like(dark)
    h_px, w_px = dark.shape
    for b in covered:
        x0, y0 = int(max(0, (b[0] - BOX_PAD_PT) / CELL_PT)), int(max(0, (b[1] - BOX_PAD_PT) / CELL_PT))
        x1, y1 = int(min(w_px, (b[2] + BOX_PAD_PT) / CELL_PT + 1)), int(min(h_px, (b[3] + BOX_PAD_PT) / CELL_PT + 1))
        cov[y0:y1, x0:x1] = True
    m = max(1, int(0.02 * w_px))                        # ignore the extreme edge of the image
    cand = dark & ~cov
    cand[:, :m] = cand[:, -m:] = False
    cand[:m, :] = cand[-m:, :] = False
    lab, _ = ndimage.label(ndimage.binary_dilation(cand, iterations=3))
    regions = []
    for sl in ndimage.find_objects(lab):
        if sl is None:
            continue
        cells = int(cand[sl].sum())
        if cells < MIN_AREA_UNITS / CELL_UNITS ** 2 or sl[0].stop - sl[0].start < 3:
            continue
        bbox = [sl[1].start * CELL_PT, sl[0].start * CELL_PT, sl[1].stop * CELL_PT, sl[0].stop * CELL_PT]
        regions.append({'bbox': clamp(bbox, W, H), 'ink_cells': cells, **_band(dark[sl], sl[0].start, CELL_PT)})
    rows = np.where(dark[:, m:-m].any(axis=1))[0]
    rows = rows[rows < h_px - m]
    info = {'uncovered_regions': len(regions), 'lowest_ink_y': float(rows.max() * CELL_PT) if len(rows) else None,
            'lowest_block_y': max((b[3] for b in covered), default=0.0), 'page_height': H,
            'unit': unit, 'method': 'ink (darkest colour channel) on the paper sheet (largest bright area, eroded) outside padded engine boxes; heuristic'}
    return regions, info
