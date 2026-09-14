"""Rule R3 -- windowed COG reads.

Two rules are enforced here:

* Read only the AOI window. Sentinel-2 tiles are ~110 km squares and the region of
  interest is frequently a corner of one.
* Mask ``DN == 0`` on the *raw* DN. Because no offset is applied, ``DN 0`` maps to
  reflectance ``0.0``, which is a valid-looking value that would otherwise pollute every
  index and the forecast baseline.

Bands at different native resolutions (10 m and 20 m) are read onto the *same* grid by
deriving the output shape from geographic bounds rather than a fixed decimation factor.
A fixed factor gives 439x439 for a 10 m band and 457x457 for a 20 m band on the same
tile, which silently misaligns every index.
"""
from __future__ import annotations

import os

os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("GDAL_HTTP_MULTIPLEX", "YES")
os.environ.setdefault("VSI_CACHE", "TRUE")

import numpy as np  # noqa: E402
import rasterio  # noqa: E402
from rasterio.enums import Resampling  # noqa: E402
from rasterio.env import Env  # noqa: E402
from rasterio.warp import transform_bounds  # noqa: E402
from rasterio.windows import from_bounds  # noqa: E402

from .config import NODATA_DN, TARGET_RES_M  # noqa: E402
from .scaling import to_reflectance  # noqa: E402


def _aoi_window(ds: rasterio.DatasetReader, bbox):
    """Window covering `bbox` in the dataset's own CRS."""
    if ds.crs is not None and str(ds.crs) != "EPSG:4326":
        bbox = transform_bounds("EPSG:4326", ds.crs, *bbox)
    return from_bounds(*bbox, transform=ds.transform)


def read_aoi(scene: dict, asset: str, bbox,
             target_res: float = TARGET_RES_M,
             resampling: str = "nearest") -> tuple[np.ndarray, np.ndarray]:
    """Read one asset over `bbox` at ~`target_res`.

    Returns ``(raw_dn, valid_mask)`` where `valid_mask` excludes ``DN == 0``. The output
    grid is derived from geographic extent so that 10 m and 20 m assets align.

    ``resampling`` defaults to ``nearest`` because the only in-repo caller that passes a
    coarser ``target_res`` is a coverage probe, where a single class code is wanted. A
    *reflectance* read down to a much coarser cell must pass ``average``: nearest throws
    away all but one 10 m pixel, which is exactly the trap ``read_into_grid`` documents.
    """
    href = scene["assets"][asset]["href"]
    with Env():
        with rasterio.open(f"/vsicurl/{href}") as ds:
            window = _aoi_window(ds, bbox)
            geo_w = abs(window.width) * ds.res[0]
            geo_h = abs(window.height) * ds.res[1]
            out_w = max(1, int(round(geo_w / target_res)))
            out_h = max(1, int(round(geo_h / target_res)))
            raw = ds.read(1, window=window, out_shape=(out_h, out_w),
                          boundless=True, fill_value=NODATA_DN,
                          resampling=Resampling[resampling])
    return raw, raw > NODATA_DN


def read_aoi_reflectance(scene: dict, asset: str, bbox,
                         target_res: float = TARGET_RES_M
                         ) -> tuple[np.ndarray, np.ndarray]:
    """As :func:`read_aoi`, but converted to reflectance with nodata as NaN."""
    raw, valid = read_aoi(scene, asset, bbox, target_res)
    refl = to_reflectance(raw)
    refl[~valid] = np.nan
    return refl, valid


def read_band_stack(scene: dict, assets, bbox,
                    target_res: float = TARGET_RES_M
                    ) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray], np.ndarray]:
    """Read several optical assets onto one shared grid.

    Returns ``(reflectance, raw_dn, common_valid_mask)``. Reflectance values outside the
    common valid mask are NaN.
    """
    refl: dict[str, np.ndarray] = {}
    raw: dict[str, np.ndarray] = {}
    valid: np.ndarray | None = None

    for asset in assets:
        r, v = read_aoi(scene, asset, bbox, target_res)
        raw[asset] = r
        refl[asset] = to_reflectance(r)
        valid = v if valid is None else (valid & v)

    assert valid is not None, "read_band_stack requires at least one asset"
    for arr in refl.values():
        arr[~valid] = np.nan
    return refl, raw, valid
