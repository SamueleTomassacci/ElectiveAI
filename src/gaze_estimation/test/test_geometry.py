import numpy as np

from gaze_estimation.geometry import (
    bbox_area,
    heatmap_mass_inside_bbox,
    point_in_bbox,
    select_largest_face,
)


def test_point_in_bbox_inside_and_outside():

    bbox = [10,20, 50,80]
    assert point_in_bbox([20,30],bbox)
    assert not point_in_bbox([5,30],bbox)
    assert not point_in_bbox([20,90],bbox)


def test_point_in_bbox_with_margin():
    bbox = [10,20, 50,80]
    assert not point_in_bbox([5,20],bbox,margin_px=0)
    assert point_in_bbox([5,20], bbox, margin_px=5)


def test_select_largest_face():
    results = [
        {"face_bbox": [0.1,0.1,0.2, 0.2]},
        {"face_bbox": [0.1, 0.1,0.5, 0.6]},
        {"face_bbox": [0.1,0.1, 0.3,0.3]},
    ]
    assert select_largest_face(results) == 1
    assert bbox_area([0, 0,4,3]) == 12


def test_heatmap_mass_inside_bbox():
    heatmap = np.zeros((10,10), dtype=np.float32)
    heatmap[2:6, 2:6] = 1.0
    ratio = heatmap_mass_inside_bbox(heatmap,[20,20, 60,60],image_width=100, image_height=100)
    assert ratio == 1.0

    partial_ratio = heatmap_mass_inside_bbox(
        heatmap, [20,20, 40, 40], image_width=100, image_height=100
    )
    assert partial_ratio == 0.25
