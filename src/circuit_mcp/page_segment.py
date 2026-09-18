"""Where the handwritten expressions are on a page, for the formula recognizer.

UniMERNet reads one tightly cropped expression, but a student's working arrives
as a whole scanned page. Rows of ink make lines; lines closer than half a
typical line height stay one expression (a fraction's numerator, bar and
denominator); and a horizontal gap several line heights wide splits a line into
separate columns, such as a side calculation next to the main working.

Only numpy and the standard library, and no relative imports: the OCR worker is
run as a script and imports this module by file name, outside the package.
"""
from __future__ import annotations

import numpy as np

MERGE_FRACTION = 0.5            # a vertical gap under this share of the median line height joins lines
SPLIT_LINE_HEIGHTS = 3.0        # a horizontal gap this many median line heights wide splits columns
SPLIT_MIN_WIDTH_FRACTION = 0.08  # ...and never narrower than this share of the page width
MIN_INK_PIXELS = 12             # fewer ink pixels than this is a speck, not writing
INK_CONTRAST = 96               # how far from the background a pixel must be to count as ink
PAD = 6                         # margin kept around each box, in pixels

Box = tuple[int, int, int, int]


def ink_mask(gray: np.ndarray) -> np.ndarray:
    """True where there is writing, on a light or a dark background."""
    image = np.asarray(gray)
    if image.ndim != 2 or image.size == 0:
        raise ValueError("expected a non-empty 2-D grayscale image")
    image = image.astype(np.int16)
    background = int(np.median(image))
    return np.abs(image - background) >= INK_CONTRAST


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Half-open [start, end) runs of True in a 1-D boolean array."""
    padded = np.concatenate(([False], mask, [False]))
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    return [(int(edges[i]), int(edges[i + 1])) for i in range(0, len(edges), 2)]


def _join(runs: list[tuple[int, int]], gap: int) -> list[tuple[int, int]]:
    """Merge consecutive runs separated by less than ``gap``."""
    joined: list[list[int]] = []
    for start, end in runs:
        if joined and start - joined[-1][1] < gap:
            joined[-1][1] = end
        else:
            joined.append([start, end])
    return [(start, end) for start, end in joined]


def expression_boxes(gray: np.ndarray) -> list[Box]:
    """``(x0, y0, x1, y1)`` boxes with exclusive ends, top to bottom then left to right."""
    ink = ink_mask(gray)
    height, width = ink.shape
    rows = _runs(ink.any(axis=1))
    if not rows:
        return []
    line_height = float(np.median([end - start for start, end in rows]))
    bands = _join(rows, max(2, int(MERGE_FRACTION * line_height)))
    split_gap = max(int(SPLIT_LINE_HEIGHTS * line_height), int(SPLIT_MIN_WIDTH_FRACTION * width), 1)
    boxes: list[Box] = []
    for top, bottom in bands:
        band = ink[top:bottom]
        for left, right in _join(_runs(band.any(axis=0)), split_gap):
            cell = band[:, left:right]
            if int(cell.sum()) < MIN_INK_PIXELS:
                continue
            ys = np.flatnonzero(cell.any(axis=1))
            y0, y1 = top + int(ys[0]), top + int(ys[-1]) + 1
            boxes.append((max(0, left - PAD), max(0, y0 - PAD), min(width, right + PAD), min(height, y1 + PAD)))
    return boxes
