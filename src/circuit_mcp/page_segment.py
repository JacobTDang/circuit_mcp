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

A hand-drawn frame around an answer is found and dropped before the row
profile: its edges would otherwise bridge the gap to the line above, and each
framed region would count as one tall row, dragging the median line height up
until ordinary lines merged as well. A frame the writing touches is left alone,
because erasing the answer with it would be worse than merging it. Coloured ink
is not filtered: students often write in blue, so treating coloured pixels as
background would erase whole pages.

Known limitation: on a page of nothing but tightly stacked fractions, the median
line height is itself a fraction's height, and no scale separates the gaps inside
a fraction from the gaps between lines -- measured over 218 labelled gaps, the
two ranges overlap. Such a page can still merge.

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
FRAME_MIN_ASPECT = 2.0          # a frame around an answer is at least this much wider than tall
FRAME_MIN_HEIGHT = 8            # ...and tall enough to hold a line of writing
FRAME_MAX_FILL = 0.5            # a frame is hollow; a fraction bar of the same shape is solid
FRAME_EDGE_ROWS = 0.6           # this share of its middle rows show exactly two edges, left and right
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


def _components(mask: np.ndarray) -> tuple[np.ndarray, int]:
    """8-connected component labels, background 0.

    Union-find over row runs rather than a per-pixel flood fill: a page is
    thousands of rows but only a handful of runs each, and scipy is not
    importable here (the OCR worker runs this file as a script).
    """
    height, _ = mask.shape
    parent = [0]

    def find(node: int) -> int:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(left: int, right: int) -> None:
        a, b = find(left), find(right)
        if a != b:
            parent[max(a, b)] = min(a, b)

    labels = np.zeros(mask.shape, np.int32)
    previous: list[tuple[int, int, int]] = []
    for y in range(height):
        current: list[tuple[int, int, int]] = []
        for start, end in _runs(mask[y]):
            parent.append(len(parent))
            label = len(parent) - 1
            for above_start, above_end, above_label in previous:
                # Touching or diagonally adjacent, which is what 8-connected means here.
                if above_start - 1 < end and start - 1 < above_end:
                    union(label, above_label)
            labels[y, start:end] = label
            current.append((start, end, label))
        previous = current

    roots = np.array([find(node) for node in range(len(parent))], np.int32)
    return roots[labels], len(parent)


def frame_mask(ink: np.ndarray) -> np.ndarray:
    """True where a hand-drawn frame is, so it can be dropped before row profiles.

    A frame is a wide, hollow, closed loop. Wide alone would also describe a
    fraction bar, so hollowness is what separates them: a bar fills its box and a
    frame's middle rows hold only its two side edges. The two-edge test is taken
    per row rather than from the bounding box, so a frame on a slightly rotated
    scan still reads as one.

    A frame the writing touches is left alone. Its component would carry the
    answer along with it, and erasing the answer is far worse than merging it.
    """
    labels, count = _components(ink)
    found = np.zeros(ink.shape, bool)
    if count <= 1:
        return found
    ys, xs = np.nonzero(labels)
    if ys.size == 0:
        return found
    of_label = labels[ys, xs]
    pixels = np.bincount(of_label, minlength=count)
    left = np.full(count, ink.shape[1], np.int64)
    right = np.full(count, -1, np.int64)
    top = np.full(count, ink.shape[0], np.int64)
    bottom = np.full(count, -1, np.int64)
    np.minimum.at(left, of_label, xs)
    np.maximum.at(right, of_label, xs)
    np.minimum.at(top, of_label, ys)
    np.maximum.at(bottom, of_label, ys)

    for label in range(1, count):
        if pixels[label] == 0:
            continue
        width = int(right[label] - left[label]) + 1
        height = int(bottom[label] - top[label]) + 1
        if height < FRAME_MIN_HEIGHT or width < FRAME_MIN_ASPECT * height:
            continue
        if pixels[label] > FRAME_MAX_FILL * width * height:
            continue
        component = labels[top[label]:bottom[label] + 1, left[label]:right[label] + 1] == label
        middle = component[height // 4:height - height // 4]
        if middle.shape[0] == 0:
            continue
        two_edges = sum(1 for row in middle if len(_runs(row)) == 2)
        if two_edges >= FRAME_EDGE_ROWS * middle.shape[0]:
            found |= labels == label
    return found


def expression_boxes(gray: np.ndarray) -> list[Box]:
    """``(x0, y0, x1, y1)`` boxes with exclusive ends, in reading order.

    Bands top to bottom; within a band, columns left to right; within a column,
    lines top to bottom, so a side calculation stays together.
    """
    ink = ink_mask(gray)
    # A frame bridges the gaps around whatever it encloses, so it has to go
    # before the row profile is taken, not after the boxes are built.
    ink = ink & ~frame_mask(ink)
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
