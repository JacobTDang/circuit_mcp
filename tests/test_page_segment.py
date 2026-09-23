"""Finding handwritten expressions on a page, on synthetic pages with known layout."""
from __future__ import annotations

import numpy as np
import pytest

from circuit_mcp.page_segment import PAD, expression_boxes


def page(height=300, width=400):
    return np.full((height, width), 255, np.uint8)


def ink(image, x0, y0, x1, y1):
    image[y0:y1, x0:x1] = 0


def contains(box, x0, y0, x1, y1):
    bx0, by0, bx1, by1 = box
    return bx0 <= x0 and by0 <= y0 and bx1 >= x1 and by1 >= y1


def test_a_blank_page_has_no_expressions():
    assert expression_boxes(page()) == []


def test_two_separate_lines_are_two_boxes_top_first():
    image = page()
    ink(image, 50, 40, 150, 70)
    ink(image, 60, 130, 200, 160)
    boxes = expression_boxes(image)
    assert len(boxes) == 2
    assert contains(boxes[0], 50, 40, 150, 70)
    assert contains(boxes[1], 60, 130, 200, 160)
    assert boxes[0] == (50 - PAD, 40 - PAD, 150 + PAD, 70 + PAD)


def test_a_fraction_stays_one_expression():
    image = page()
    ink(image, 100, 50, 200, 70)    # numerator
    ink(image, 90, 75, 210, 78)     # fraction bar
    ink(image, 100, 82, 200, 102)   # denominator
    boxes = expression_boxes(image)
    assert len(boxes) == 1
    assert contains(boxes[0], 90, 50, 210, 102)


def test_lines_a_third_of_a_line_height_apart_are_two_boxes():
    image = page()
    ink(image, 50, 40, 150, 70)
    ink(image, 60, 81, 200, 111)    # 11 px below: about 0.35 of the 30 px line height
    boxes = expression_boxes(image)
    assert len(boxes) == 2
    assert contains(boxes[0], 50, 40, 150, 70)
    assert contains(boxes[1], 60, 81, 200, 111)


def test_a_fraction_with_gaps_a_fifth_of_a_line_height_stays_one_box():
    image = page()
    ink(image, 100, 50, 200, 80)    # numerator, 30 px tall
    ink(image, 90, 86, 210, 89)     # fraction bar, 6 px below
    ink(image, 100, 95, 200, 125)   # denominator, 6 px below
    boxes = expression_boxes(image)
    assert len(boxes) == 1
    assert contains(boxes[0], 90, 50, 210, 125)


def test_a_side_column_does_not_bridge_the_gap_between_main_lines():
    image = page()
    ink(image, 20, 40, 120, 70)     # main line 1
    ink(image, 40, 110, 100, 140)   # main line 2, narrower, 40 px below
    ink(image, 300, 55, 380, 125)   # side block whose rows span the gap between them
    ink(image, 20, 180, 150, 210)   # two ordinary lines, so the median
    ink(image, 20, 240, 150, 270)   # line height stays 30 px
    boxes = expression_boxes(image)
    assert len(boxes) == 5
    assert boxes[:3] == [
        (20 - PAD, 40 - PAD, 120 + PAD, 70 + PAD),
        (40 - PAD, 110 - PAD, 100 + PAD, 140 + PAD),
        (300 - PAD, 55 - PAD, 380 + PAD, 125 + PAD),
    ]


def test_gaps_between_words_do_not_split_a_line():
    image = page()
    for x0, x1 in ((20, 80), (100, 160), (185, 260)):
        ink(image, x0, 40, x1, 70)
    assert len(expression_boxes(image)) == 1


def test_a_wide_gap_splits_a_side_column_left_first():
    image = page()
    ink(image, 20, 40, 120, 70)
    ink(image, 300, 40, 380, 70)
    boxes = expression_boxes(image)
    assert len(boxes) == 2
    assert contains(boxes[0], 20, 40, 120, 70)
    assert contains(boxes[1], 300, 40, 380, 70)


def test_reading_order_is_band_then_column_then_line():
    image = page()
    ink(image, 300, 40, 380, 70)    # upper line, right side
    ink(image, 20, 130, 120, 160)   # lower line, left side
    boxes = expression_boxes(image)
    assert boxes[0][1] < boxes[1][1]


def test_a_speck_is_not_writing():
    image = page()
    ink(image, 50, 40, 150, 70)
    ink(image, 300, 250, 302, 252)
    assert len(expression_boxes(image)) == 1


def test_specks_in_blank_rows_do_not_shrink_the_line_height():
    image = page()
    ink(image, 100, 50, 200, 70)    # numerator
    ink(image, 90, 75, 210, 78)     # fraction bar
    ink(image, 100, 82, 200, 102)   # denominator
    for y in range(150, 270, 20):   # six 2x2 specks, each in its own blank rows
        ink(image, 300, y, 302, y + 2)
    boxes = expression_boxes(image)
    assert len(boxes) == 1
    assert contains(boxes[0], 90, 50, 210, 102)


def test_a_page_of_only_specks_has_no_expressions():
    image = page()
    for y in range(50, 250, 40):
        ink(image, 100, y, 102, y + 2)
    assert expression_boxes(image) == []


def test_a_speck_inside_a_bridged_band_is_dropped():
    image = page()
    ink(image, 20, 40, 120, 70)     # main line 1
    ink(image, 40, 110, 100, 140)   # main line 2
    ink(image, 300, 55, 380, 125)   # side block spanning the gap between them
    ink(image, 20, 180, 150, 210)
    ink(image, 20, 240, 150, 270)
    clean = expression_boxes(image)
    ink(image, 60, 88, 62, 90)      # speck in the main column, between its two lines
    assert expression_boxes(image) == clean
    assert len(clean) == 5


def test_a_dark_page_with_light_ink_segments_the_same():
    image = page()
    ink(image, 50, 40, 150, 70)
    ink(image, 60, 130, 200, 160)
    assert expression_boxes(255 - image) == expression_boxes(image)


def test_boxes_are_clipped_to_the_page():
    image = page()
    ink(image, 0, 0, 50, 20)
    (box,) = expression_boxes(image)
    assert box[0] == 0 and box[1] == 0
    assert box[2] <= image.shape[1] and box[3] <= image.shape[0]


def test_ink_at_the_bottom_right_corner_is_clipped_to_the_page():
    image = page()
    ink(image, 300, 270, 400, 300)
    assert expression_boxes(image) == [(300 - PAD, 270 - PAD, image.shape[1], image.shape[0])]


@pytest.mark.parametrize("bad", [np.zeros((10, 10, 3), np.uint8), np.zeros((0, 5), np.uint8)])
def test_only_a_non_empty_grayscale_image_is_accepted(bad):
    with pytest.raises(ValueError, match="2-D grayscale"):
        expression_boxes(bad)


# --- hand-drawn frames -------------------------------------------------------

def frame(image, x0, y0, x1, y1, thickness=3, slope=0):
    """A hollow rectangle, optionally drifting down to the right like a scanned page."""
    for column in range(x0, x1):
        drift = int(slope * (column - x0))
        image[y0 + drift:y0 + drift + thickness, column] = 0
        image[y1 + drift - thickness:y1 + drift, column] = 0
    for row in range(y0, y1):
        image[row:row + 1, x0:x0 + thickness] = 0
        image[row + int(slope * (x1 - x0 - 1)):row + int(slope * (x1 - x0 - 1)) + 1, x1 - thickness:x1] = 0


def test_a_framed_answer_is_its_own_box():
    """The frame's edges used to bridge the gap, joining the answer to the line above."""
    image = page(height=260, width=400)
    ink(image, 40, 30, 200, 60)        # the working
    frame(image, 30, 90, 260, 160)     # a box drawn around the answer
    ink(image, 60, 115, 180, 140)      # the answer inside it

    boxes = expression_boxes(image)

    assert len(boxes) == 2
    assert contains(boxes[0], 40, 30, 200, 60)
    assert contains(boxes[1], 60, 115, 180, 140)


def test_a_sloped_frame_is_removed_too():
    image = page(height=260, width=400)
    ink(image, 40, 30, 200, 60)
    frame(image, 30, 90, 260, 160, slope=0.05)
    ink(image, 60, 118, 180, 140)

    assert len(expression_boxes(image)) == 2


def test_frames_do_not_merge_tightly_stacked_lines():
    """Each framed answer is one tall row run, which drags the median line height up.

    The merge gap is a fraction of that median, so on a page with several framed
    answers it grows wide enough to swallow the gaps between ordinary lines.
    """
    image = page(height=700, width=400)
    for index in range(4):                       # four framed answers
        top = 30 + index * 100
        frame(image, 40, top, 300, top + 80)
        ink(image, 70, top + 25, 220, top + 55)
    for index in range(3):                       # three ordinary lines below them
        top = 450 + index * 24
        ink(image, 60, top, 200, top + 12)

    boxes = expression_boxes(image)

    assert len(boxes) == 7


def test_a_closed_digit_is_not_a_frame():
    """A 0 is a closed loop, but it is taller than it is wide."""
    image = page()
    frame(image, 100, 60, 130, 120, thickness=2)

    boxes = expression_boxes(image)

    assert len(boxes) == 1
    assert contains(boxes[0], 100, 60, 130, 120)


def test_a_fraction_bar_is_not_a_frame():
    """A bar is wide and short like a frame, but solid rather than hollow."""
    image = page()
    ink(image, 100, 50, 200, 70)
    ink(image, 95, 73, 205, 77)
    ink(image, 100, 80, 200, 100)

    assert len(expression_boxes(image)) == 1
