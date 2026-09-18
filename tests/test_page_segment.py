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
