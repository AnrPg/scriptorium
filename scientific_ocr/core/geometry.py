"""Box geometry. Coordinates: PDF points, origin TOPLEFT, bbox = [x0, y0, x1, y1]."""
from __future__ import annotations

Box = list  # [x0, y0, x1, y1]


def unit(width: float, height: float) -> float:
    """Scale unit of a page: its diagonal / 1000 (~1 pt on an A4/Letter page, ~3 pt on a 1900-pt-wide scan).
    Every geometric threshold is written in these units so behaviour does not depend on scan resolution."""
    return (width ** 2 + height ** 2) ** 0.5 / 1000.0


def area(b: Box) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def intersection(a: Box, b: Box) -> float:
    return area([max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])])


def iou(a: Box, b: Box) -> float:
    i = intersection(a, b)
    u = area(a) + area(b) - i
    return i / u if u else 0.0


def containment(a: Box, b: Box) -> float:
    """Share of the smaller box covered by the other."""
    m = min(area(a), area(b))
    return intersection(a, b) / m if m else 0.0


def covered_by(inner: Box, outer: Box) -> float:
    """Share of `inner` lying inside `outer` (asymmetric)."""
    a = area(inner)
    return intersection(inner, outer) / a if a else 0.0


def union(boxes) -> Box:
    boxes = list(boxes)
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def clamp(bbox: Box, width: float, height: float) -> Box:
    x0, y0, x1, y1 = bbox
    x0, x1 = sorted((max(0.0, min(width, x0)), max(0.0, min(width, x1))))
    y0, y1 = sorted((max(0.0, min(height, y0)), max(0.0, min(height, y1))))
    return [x0, y0, x1, y1]


def same_column(a: Box, b: Box, frac: float = 0.3) -> bool:
    return min(a[2], b[2]) - max(a[0], b[0]) > frac * min(a[2] - a[0], b[2] - b[0])


def vertical_overlap(a: Box, b: Box) -> float:
    """Overlap of the y-ranges as a share of the shorter box height."""
    h = min(a[3] - a[1], b[3] - b[1])
    return max(0.0, min(a[3], b[3]) - max(a[1], b[1])) / h if h > 0 else 0.0


def side_by_side(a: Box, b: Box, max_gap: float, min_vertical_overlap: float = 0.6) -> bool:
    """Two boxes on the same text lines with a small horizontal gap: the signature of a layout split."""
    gap = max(a[0], b[0]) - min(a[2], b[2])
    return gap <= max_gap and vertical_overlap(a, b) >= min_vertical_overlap and not same_column(a, b, 0.5)


def insertion_index(boxes: list[Box], bbox: Box) -> int:
    """Index in a reading-ordered list of boxes at which a geometry-only box belongs: after the lowest
    preceding box in the same column band, else before the first box below it in that band, else end."""
    best, best_y = None, -1.0
    for i, g in enumerate(boxes):
        if same_column(g, bbox) and g[1] <= bbox[1] + 2 and g[3] > best_y:
            best, best_y = i, g[3]
    if best is not None:
        return best + 1
    for i, g in enumerate(boxes):
        if same_column(g, bbox) and g[1] > bbox[1]:
            return i
    return len(boxes)
