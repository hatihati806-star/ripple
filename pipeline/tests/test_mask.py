import numpy as np

from ripple_pipeline.mask import combine_water_mask, scl_usable, scl_water


def test_scl_usable_excludes_cloud_shadow_snow():
    scl = np.array([[6, 8, 3, 11, 9, 10, 1, 0, 7]], dtype="uint8")
    assert scl_usable(scl).tolist() == \
        [[True, False, False, False, False, False, False, False, True]]


def test_scl_water_only_class_6():
    scl = np.array([[6, 7, 5, 4]], dtype="uint8")
    assert scl_water(scl).tolist() == [[True, False, False, False]]


def test_combine_without_jrc_uses_scl_water():
    scl = np.array([[6, 7, 8, 6]], dtype="uint8")
    assert combine_water_mask(scl, None).tolist() == [[True, False, False, True]]


def test_combine_with_jrc_lets_unclassified_through_only_where_jrc_says_water():
    scl = np.array([[6, 7, 7, 5]], dtype="uint8")
    jrc = np.array([[True, True, False, False]])
    assert combine_water_mask(scl, jrc).tolist() == [[True, True, False, False]]


def test_jrc_removes_scl_water_false_positives():
    """SCL occasionally tags wet soil as water; the static backbone overrules it."""
    scl = np.array([[6, 6]], dtype="uint8")
    jrc = np.array([[True, False]])
    assert combine_water_mask(scl, jrc).tolist() == [[True, False]]


def test_combine_never_returns_usable_cloud_pixels():
    """A cloud pixel must not become water just because JRC says water is under it."""
    scl = np.array([[8, 3, 11]], dtype="uint8")
    jrc = np.array([[True, True, True]])
    assert combine_water_mask(scl, jrc).tolist() == [[False, False, False]]
