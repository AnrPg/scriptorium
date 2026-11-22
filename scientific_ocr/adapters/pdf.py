"""Source PDF handling: hashing, page parsing, rasters and crops from ORIGINAL pixels.

Coordinates everywhere: PDF points, origin TOPLEFT, bbox [x0, y0, x1, y1]. Pages are 1-based.
"""
from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

import pymupdf

from .. import config
from ..core.geometry import clamp as clamp_bbox


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def parse_pages(spec: str | None, page_count: int) -> list[int]:
    """'3,5-6' -> [3,5,6] (1-based). Rejects zero, negatives, out of range, empty."""
    if not spec:
        return list(range(1, page_count + 1))
    pages: set[int] = set()
    for part in spec.split(','):
        part = part.strip()
        if not part:
            continue
        if '-' in part:
            a, b = part.split('-', 1)
            lo, hi = int(a), int(b)
            if lo > hi:
                raise ValueError(f'bad range {part!r}')
            if lo < 1 or hi > page_count:                 # check before building: '1-9999999999' must not hang
                raise ValueError(f'pages {part} outside 1..{page_count}')
            pages.update(range(lo, hi + 1))
        else:
            pages.add(int(part))
    bad = [p for p in pages if p < 1 or p > page_count]
    if bad or not pages:
        raise ValueError(f'pages {sorted(bad)} outside 1..{page_count}' if bad else 'empty page spec')
    return sorted(pages)


def page_info(pdf: Path) -> list[dict]:
    with pymupdf.open(pdf) as doc:
        return [{'page': i + 1, 'width': p.rect.width, 'height': p.rect.height, 'rotation': p.rotation,
                 'images': len(p.get_images()), 'has_text_layer': bool(p.get_text().strip())}
                for i, p in enumerate(doc)]




def render_page(pdf: Path, page: int, out: Path, zoom: float = config.RENDER_ZOOM) -> dict:
    with pymupdf.open(pdf) as doc:
        pg = doc[page - 1]
        pix = pg.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_name(f'{out.stem}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp{out.suffix}')
        pix.save(tmp)
        tmp.replace(out)
        return {'path': str(out), 'zoom': zoom, 'px_per_pt': zoom, 'width_px': pix.width, 'height_px': pix.height,
                'sha256': sha256_file(out)}


def page_native_dpi(pg) -> float | None:
    """Pixel density of the scan on a PDF page: the dominant embedded image (>= 25% of the page), in pixels per
    inch of the page. Rotation-invariant (geometric mean). None for pages without such an image (born-digital)."""
    area = pg.rect.width * pg.rect.height
    best = None
    for info in pg.get_image_info():
        x0, y0, x1, y1 = info['bbox']
        bw, bh = x1 - x0, y1 - y0
        if bw <= 0 or bh <= 0 or bw * bh < 0.25 * area:
            continue
        dpi = 72 * ((info['width'] * info['height']) / (bw * bh)) ** 0.5
        best = max(best or 0.0, dpi)
    return best


def native_dpi(pdf: Path, page: int) -> float | None:
    with pymupdf.open(pdf) as doc:
        return page_native_dpi(doc[page - 1])


def faithful_zoom(pg, zoom: float) -> float:
    """`zoom`, raised to the scan's own pixel density (capped at OCR_MAX_DPI) so no scan pixel is discarded."""
    nd = page_native_dpi(pg)
    return max(zoom, min(nd, config.OCR_MAX_DPI) / 72) if nd else zoom


def crop(pdf: Path, page: int, bbox, out: Path, pad: float = config.CROP_PAD_PT,
         zoom: float = config.RENDER_ZOOM) -> dict:
    """Crop the original page pixels inside bbox (+pad), clamped to page. Returns provenance record."""
    with pymupdf.open(pdf) as doc:
        pg = doc[page - 1]
        w, h = pg.rect.width, pg.rect.height
        x0, y0, x1, y1 = bbox
        padded = clamp_bbox([x0 - pad, y0 - pad, x1 + pad, y1 + pad], w, h)
        if padded[2] - padded[0] < 1 or padded[3] - padded[1] < 1:
            raise ValueError(f'degenerate crop bbox {bbox} on page {page}')
        zoom = faithful_zoom(pg, zoom)              # excerpts keep every pixel of the scan
        pix = pg.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=pymupdf.Rect(*padded))
        out.parent.mkdir(parents=True, exist_ok=True)
        pix.save(out)
    return {'path': str(out), 'page': page, 'bbox': [round(v, 2) for v in bbox],
            'crop_bbox': [round(v, 2) for v in padded], 'pad_pt': pad, 'zoom': zoom,
            'width_px': pix.width, 'height_px': pix.height, 'sha256': sha256_file(out)}


def ink_fraction(png: Path, threshold: int = 140) -> float:
    """Fraction of dark pixels; used to detect empty crops and uncovered printed areas."""
    from PIL import Image
    im = Image.open(png).convert('L')
    hist = im.histogram()
    dark = sum(hist[:threshold])
    return dark / max(1, im.width * im.height)


def gray_raster(pdf: Path, page: int, scale: float):
    """Grayscale page raster as a PIL image plus the page size in points."""
    from PIL import Image
    with pymupdf.open(pdf) as doc:
        pg = doc[page - 1]
        pix = pg.get_pixmap(matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csGRAY)
        return Image.frombytes('L', (pix.width, pix.height), pix.samples), (pg.rect.width, pg.rect.height)


def ink_raster(pdf: Path, page: int, scale: float):
    """'Ink' page raster: per pixel the DARKEST colour channel, so coloured print (teal headings, red labels,
    blue margin notes) is as visible as black print — a grey raster turns light colours into near-white."""
    from PIL import Image
    with pymupdf.open(pdf) as doc:
        pg = doc[page - 1]
        pix = pg.get_pixmap(matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csRGB)
        rgb = Image.frombytes('RGB', (pix.width, pix.height), pix.samples)
        r, g, b = rgb.split()
        from PIL import ImageChops
        return ImageChops.darker(ImageChops.darker(r, g), b), (pg.rect.width, pg.rect.height)


def _bands(rows) -> list[tuple[int, int]]:
    bands, start = [], None
    for y, on in enumerate(list(rows) + [False]):
        if on and start is None:
            start = y
        elif not on and start is not None:
            if y - start >= 2:
                bands.append((start, y))
            start = None
    return bands


def text_metrics(png: Path, zoom: float) -> dict | None:
    """Typography of the print in a crop, measured on the image (engines report no font sizes):
      lines      text lines (horizontal ink bands)
      text_h     median height of a text line's ink band, in page points (glyph size incl. ascenders)
      band_px    the same in pixels of the crop (resolution check: too few pixels = unreliable measurement)
      density    ink coverage INSIDE the text lines (heavier for bold)
      ink_rgb    median colour of the ink; sat = its saturation (coloured headings vs black)
    All are absolute: lighting, scan resolution and the distance of a photo change them. pipeline.sections uses
    them RELATIVE to the body text of the same page (pipeline.page -> page typography reference).
    Photographed pages are slightly rotated, so lines overlap in a full-width projection: bands are measured in
    narrow vertical strips and the median is taken. None when the crop holds no measurable print."""
    import numpy as np
    from PIL import Image
    im = np.asarray(Image.open(png).convert('RGB'), dtype=np.float32)
    if im.size == 0:
        return None
    mins = im.min(axis=2)
    paper = float(np.percentile(mins, 90))
    ink = mins < paper - max(45.0, 0.3 * paper)
    if ink.mean() < 0.002:
        return None
    h, w = ink.shape
    n = max(1, min(12, w // max(24, h // 2)))          # strips about as wide as a line is tall
    heights, counts, inside, area = [], [], 0, 0
    for k in range(n):
        strip = ink[:, k * w // n:(k + 1) * w // n]
        if strip.mean() < 0.01:
            continue
        bands = _bands(strip.mean(axis=1) > 0.02)
        if not bands:
            continue
        hs = sorted(b - a for a, b in bands)
        med = hs[len(hs) // 2]
        good = [(a, b) for a, b in bands if b - a >= 0.5 * med]
        heights += [b - a for a, b in good]
        counts.append(len(good))
        for a, b in good:                               # stroke weight INSIDE the text lines (bold is heavier)
            inside += int(strip[a:b].sum())
            area += (b - a) * strip.shape[1]
    if not heights:
        return None
    heights.sort()
    med = heights[len(heights) // 2]
    rgb = np.median(im[ink], axis=0)
    return {'lines': int(np.median(counts)), 'text_h': round(med / zoom, 2), 'band_px': int(med),
            'density': round(inside / area, 3) if area else 0.0,
            'ink_rgb': [round(float(v)) for v in rgb], 'sat': round(float(rgb.max() - rgb.min()))}


def thumbnail(pdf: Path, page: int, width_px: int, dest: Path) -> Path:
    """JPEG preview of a page at a given pixel width (cached by caller-chosen path)."""
    if dest.exists():
        return dest
    with pymupdf.open(pdf) as doc:
        pg = doc[page - 1]
        z = width_px / pg.rect.width
        pix = pg.get_pixmap(matrix=pymupdf.Matrix(z, z))
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f'{dest.stem}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp.jpg')   # concurrent requests never collide
    pix.save(tmp, jpg_quality=82)
    tmp.replace(dest)
    return dest


IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.tif', '.tiff', '.bmp', '.webp', '.heic', '.heif'}


class UnsupportedImage(ValueError):
    pass


@contextlib.contextmanager
def decodable(img: Path):
    """Yield a path MuPDF can decode for `img`. iPhone/macOS HEIC/HEIF (also HEIC saved under a .png/.jpg name)
    is converted LOSSLESSLY to PNG with macOS's built-in `sips`, offline, into a temporary file."""
    try:
        pymupdf.Pixmap(str(img))
        yield img
        return
    except Exception as exc:
        first = exc
    sips = shutil.which('sips')
    if not sips:
        raise UnsupportedImage(f'{img.name}: unsupported image format ({first})')
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / (img.stem + '.png')
        r = subprocess.run([sips, '-s', 'format', 'png', str(img), '--out', str(out)], capture_output=True, text=True)
        if r.returncode or not out.exists():
            raise UnsupportedImage(f'{img.name}: unsupported image format ({first}; sips: {r.stderr.strip()[:200]})')
        yield out


def image_thumbnail(img: Path, width_px: int, dest: Path) -> Path:
    """JPEG preview of a page IMAGE file (any IMAGE_SUFFIXES format; RGB, no alpha), cached at `dest`."""
    if dest.exists():
        return dest
    with decodable(img) as src:
        pix = pymupdf.Pixmap(str(src))
    if pix.alpha:
        pix = pymupdf.Pixmap(pix, 0)
    if pix.colorspace is None or pix.colorspace.n != 3:
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
    if pix.width > width_px:
        pix = pymupdf.Pixmap(pix, width_px, max(1, round(pix.height * width_px / pix.width)))
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f'{dest.stem}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp.jpg')
    pix.save(tmp, jpg_quality=80)
    tmp.replace(dest)
    return dest


# A page image says how big the paper was only through its DPI. Phone photos and many scan apps declare none or a
# screen default (72/96), which would make a 2200x3100 px photo a 31x43-inch page: every later raster (rendered at
# RENDER_ZOOM per point) and every engine would then work on huge upsampled images. Such pages are given a paper size
# instead: the long side becomes PAPER_LONG_SIDE_IN. The pixels themselves are embedded unchanged (JPEG passthrough).
TRUSTED_MIN_DPI = 150
PAPER_LONG_SIDE_IN = 11.69                 # A4


def page_dpi(px_w: int, px_h: int, declared: float | None) -> float:
    """Resolution used to size the PDF page of a page image (see TRUSTED_MIN_DPI). Never below 72, so a small
    image is never blown up beyond its pixels."""
    if declared and declared >= TRUSTED_MIN_DPI:
        return float(declared)
    return max(72.0, max(px_w, px_h) / PAPER_LONG_SIDE_IN)


def images_to_pdf(images: list[Path], dest: Path) -> list[dict]:
    """Scanned page images (sorted by caller) -> one PDF whose pages keep the original pixels, each on a page of
    plausible paper size (page_dpi). EXIF rotation (phone photos) is applied. Returns per page {file, px, dpi}."""
    from PIL import Image, ImageOps
    out, info = pymupdf.open(), []
    for img in images:
        with decodable(img) as src, Image.open(src) as im:
            w, h = im.size
            dpi = (im.info.get('dpi') or (None,))[0]
            rotated = (im.getexif() or {}).get(0x0112, 1) not in (None, 1)
            with tempfile.TemporaryDirectory() as td:
                if rotated:                        # bake the orientation in, losslessly (PNG of the decoded pixels)
                    up = ImageOps.exif_transpose(im)
                    w, h = up.size
                    src = Path(td) / 'upright.png'
                    up.save(src)
                d = page_dpi(w, h, float(dpi) if dpi else None)
                page = out.new_page(width=w * 72 / d, height=h * 72 / d)
                page.insert_image(page.rect, filename=str(src))
            info.append({'file': img.name, 'px': [w, h], 'dpi': round(d, 1)})
    dest.parent.mkdir(parents=True, exist_ok=True)
    out.save(dest, garbage=1, deflate=True)
    return info



def paper_tone(png: Path) -> list[float]:
    """Background colour of a crop as [R, G, B]: the median colour of its brightest 40% of pixels, i.e. the
    paper or box shading behind the print. Grey AND coloured textbook boxes differ from plain paper."""
    import numpy as np
    from PIL import Image
    im = np.asarray(Image.open(png).convert('RGB'), dtype=np.float32).reshape(-1, 3)
    lum = im @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    bright = im[lum >= np.percentile(lum, 60)]
    return [round(float(v), 1) for v in np.median(bright, axis=0)]
