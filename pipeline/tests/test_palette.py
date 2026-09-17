import numpy as np
import pytest

from ripple_pipeline.palette import (
    OVERLAY_ALPHA,
    PALETTE_STOPS,
    ramp,
    write_png_tile,
    write_tile,
    write_webp_tile,
)


def test_palette_runs_clean_green_to_polluted_red():
    assert PALETTE_STOPS[0][0] == 0.0
    assert PALETTE_STOPS[-1][0] == 1.0
    assert PALETTE_STOPS[0][1] == (34, 197, 94)     # #22C55E
    assert PALETTE_STOPS[-1][1] == (220, 38, 38)    # #DC2626


def test_palette_stop_values_ascend():
    values = [stop for stop, _ in PALETTE_STOPS]
    assert values == sorted(values)


def test_ramp_output_is_rgba_uint8():
    values = np.array([[0.0, 0.5], [1.0, 0.25]], dtype="float32")
    out = ramp(values, np.ones((2, 2), dtype=bool))
    assert out.shape == (2, 2, 4)
    assert out.dtype == np.uint8


def test_ramp_endpoints_match_palette():
    out = ramp(np.array([[0.0, 1.0]], dtype="float32"), np.ones((1, 2), dtype=bool))
    assert tuple(out[0, 0, :3]) == (34, 197, 94)
    assert tuple(out[0, 1, :3]) == (220, 38, 38)


def test_ramp_is_opaque_only_where_valid():
    out = ramp(np.array([[0.5, 0.5]], dtype="float32"),
               np.array([[True, False]]))
    assert out[0, 0, 3] == OVERLAY_ALPHA
    assert out[0, 1, 3] == 0


def test_ramp_clamps_out_of_range_values():
    out = ramp(np.array([[-5.0, 5.0]], dtype="float32"), np.ones((1, 2), dtype=bool))
    assert tuple(out[0, 0, :3]) == (34, 197, 94)
    assert tuple(out[0, 1, :3]) == (220, 38, 38)


def test_ramp_makes_nan_transparent_even_when_masked_valid():
    out = ramp(np.array([[np.nan]], dtype="float32"), np.array([[True]]))
    assert out[0, 0, 3] == 0


def test_risk_interpolates_between_stops():
    low = ramp(np.array([[0.10]], dtype="float32"), np.array([[True]]))[0, 0, :3]
    high = ramp(np.array([[0.90]], dtype="float32"), np.array([[True]]))[0, 0, :3]
    assert int(high[0]) > int(low[0])      # more red
    assert int(high[1]) < int(low[1])      # less green


def test_write_png_tile_creates_a_readable_png(tmp_path):
    import rasterio

    rgba = ramp(np.linspace(0, 1, 16, dtype="float32").reshape(4, 4),
                np.ones((4, 4), dtype=bool))
    path = write_png_tile(tmp_path / "nested" / "tile.png", rgba)
    assert path.exists()
    with rasterio.open(path) as ds:
        assert ds.count == 4
        assert (ds.width, ds.height) == (4, 4)


def test_write_png_tile_leaves_no_aux_sidecar(tmp_path):
    """GDAL PAM sidecars must not ship to the browser as dead weight."""
    rgba = ramp(np.zeros((4, 4), dtype="float32"), np.ones((4, 4), dtype=bool))
    path = write_png_tile(tmp_path / "tile.png", rgba)
    assert path.exists()
    assert not (tmp_path / "tile.png.aux.xml").exists()
    assert list(tmp_path.glob("*.aux.xml")) == []


def test_webp_tile_round_trips_pixels_exactly(tmp_path):
    """WebP must be lossless: the colours *are* the data.

    Alpha is preserved everywhere. RGB is preserved wherever the pixel is visible; libwebp
    zeroes the RGB of fully transparent pixels, which is invisible by definition and exactly
    what the probe sampler assumes when it skips alpha < 8.
    """
    from PIL import Image

    rgba = ramp(np.linspace(0, 1, 64, dtype="float32").reshape(8, 8),
                np.ones((8, 8), dtype=bool))
    rgba[0, 0] = (34, 197, 94, 0)  # a transparent pixel keeps its zero alpha
    path = write_webp_tile(tmp_path / "nested" / "tile.webp", rgba)
    assert path.exists()
    with Image.open(path) as image:
        assert image.mode == "RGBA"
        assert (image.width, image.height) == (8, 8)
        decoded = np.array(image)

    np.testing.assert_array_equal(decoded[..., 3], rgba[..., 3])
    visible = rgba[..., 3] > 0
    np.testing.assert_array_equal(decoded[..., :3][visible], rgba[..., :3][visible])
    assert (decoded[..., 3][~visible] == 0).all()


def test_webp_is_smaller_than_png_for_a_realistic_tile(tmp_path):
    rng = np.random.default_rng(0)
    values = np.zeros((256, 256), dtype="float32")
    values[40:200, 30:220] = rng.random((160, 190)) * 0.6
    rgba = ramp(values, values > 0)
    png = write_png_tile(tmp_path / "tile.png", rgba)
    webp = write_webp_tile(tmp_path / "tile.webp", rgba)
    assert webp.stat().st_size < png.stat().st_size


def test_write_tile_dispatches_on_format(tmp_path):
    rgba = ramp(np.zeros((4, 4), dtype="float32"), np.ones((4, 4), dtype=bool))
    assert write_tile(tmp_path / "a.webp", rgba, "webp").suffix == ".webp"
    assert write_tile(tmp_path / "a.png", rgba, "png").suffix == ".png"
    assert write_tile(tmp_path / "b.png", rgba).suffix == ".png"
