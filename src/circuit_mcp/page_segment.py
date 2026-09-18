"""Where the handwritten expressions are on a page, for the formula recognizer.

UniMERNet reads one tightly cropped expression, but a student's working arrives
as a whole scanned page. Rows of ink make lines; lines closer than 0.3 of the
median line height stay one expression (a fraction's numerator, bar and
denominator); and a horizontal gap several line heights wide splits a line into
separate columns, such as a side calculation next to the main working. Each
column is then split into its own lines again, because ink in one column can
fill the gap between two lines of the other. Boxes come in reading order: bands
top to bottom, columns left to right within a band, and lines top to bottom
within a column, so a side calculation stays together.

Known limitation: a hand-drawn frame around an answer joins that answer to the
line above it, because the frame's edges read as ink that bridges the gap.
Coloured ink is not filtered out to prevent this: students often write in blue
or other coloured ink, so treating coloured pixels as background would erase
whole pages.

Only numpy and the standard library, and no relative imports: the OCR worker is
run as a script and imports this module by file name, outside the package.
"""
from __future__ import annotations

import numpy as np

# A vertical gap under MERGE_FRACTION of the median line height joins lines. On four
# real pages, gaps inside a fraction measured 0.05-0.23 of it and gaps between lines
# 0.22-0.47, so 0.3 separates most lines while keeping fractions whole; the ranges
# overlap, so a tightly stacked pair of lines can still merge.
MERGE_FRACTION = 0.3
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
    """``(x0, y0, x1, y1)`` boxes with exclusive ends, in reading order.

    Bands top to bottom; within a band, columns left to right; within a column,
    lines top to bottom, so a side calculation stays together.
    """
    ink = ink_mask(gray)
    height, width = ink.shape
    rows = _runs(ink.any(axis=1))
    # Specks in blank rows are dropped as boxes below; they must not drag the line height down.
    heights = [end - start for start, end in rows if int(ink[start:end].sum()) >= MIN_INK_PIXELS]
    if not heights:
        return []
    line_height = float(np.median(heights))
    merge_gap = max(2, int(MERGE_FRACTION * line_height))
    split_gap = max(int(SPLIT_LINE_HEIGHTS * line_height), int(SPLIT_MIN_WIDTH_FRACTION * width), 1)
    boxes: list[Box] = []
    for top, bottom in _join(rows, merge_gap):
        band = ink[top:bottom]
        for left, right in _join(_runs(band.any(axis=0)), split_gap):
            column = band[:, left:right]
            for line_top, line_bottom in _join(_runs(column.any(axis=1)), merge_gap):
                line = column[line_top:line_bottom]
                if int(line.sum()) < MIN_INK_PIXELS:
                    continue
                xs = np.flatnonzero(line.any(axis=0))
                x0, x1 = left + int(xs[0]), left + int(xs[-1]) + 1
                y0, y1 = top + line_top, top + line_bottom
                boxes.append((max(0, x0 - PAD), max(0, y0 - PAD), min(width, x1 + PAD), min(height, y1 + PAD)))
    return boxes
