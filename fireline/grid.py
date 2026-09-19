"""Shared raster grid in EPSG:25831. Everything that touches a raster imports this."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from pyproj import Transformer

_TO_25831 = Transformer.from_crs("EPSG:4326", "EPSG:25831", always_xy=True)
_TO_4326 = Transformer.from_crs("EPSG:25831", "EPSG:4326", always_xy=True)

GAVARRES_BBOX_4326 = (2.85, 41.80, 3.20, 42.05)  # lon_min, lat_min, lon_max, lat_max


def lonlat_to_xy(lon, lat):
    return _TO_25831.transform(lon, lat)


def xy_to_lonlat(x, y):
    return _TO_4326.transform(x, y)


@dataclass(frozen=True)
class Grid:
    xmin: float
    ymin: float
    xmax: float
    ymax: float
    cell: float = 100.0

    @classmethod
    def from_bbox_4326(cls, bbox, cell=100.0) -> "Grid":
        lon_min, lat_min, lon_max, lat_max = bbox
        xs, ys = zip(*[lonlat_to_xy(lo, la) for lo, la in
                       [(lon_min, lat_min), (lon_max, lat_min), (lon_min, lat_max), (lon_max, lat_max)]])
        return cls(min(xs), min(ys), max(xs), max(ys), cell)

    @classmethod
    def gavarres(cls, cell=100.0) -> "Grid":
        return cls.from_bbox_4326(GAVARRES_BBOX_4326, cell)

    @property
    def ncols(self) -> int:
        return int(np.ceil((self.xmax - self.xmin) / self.cell))

    @property
    def nrows(self) -> int:
        return int(np.ceil((self.ymax - self.ymin) / self.cell))

    @property
    def shape(self) -> tuple[int, int]:
        return (self.nrows, self.ncols)

    def zeros(self, dtype=float) -> np.ndarray:
        return np.zeros(self.shape, dtype=dtype)

    def to_rowcol(self, x, y):
        col = np.floor((np.asarray(x) - self.xmin) / self.cell).astype(int)
        row = np.floor((self.ymax - np.asarray(y)) / self.cell).astype(int)
        return row, col

    def to_xy(self, row, col):
        x = self.xmin + (np.asarray(col) + 0.5) * self.cell
        y = self.ymax - (np.asarray(row) + 0.5) * self.cell
        return x, y

    def inside(self, row, col) -> np.ndarray:
        return (row >= 0) & (row < self.nrows) & (col >= 0) & (col < self.ncols)

    def sample(self, arr: np.ndarray, lon, lat):
        """Nearest-cell sample at lon/lat (EPSG:4326). NaN outside the grid. Scalar or array."""
        x, y = lonlat_to_xy(lon, lat)
        row, col = self.to_rowcol(x, y)
        ok = self.inside(row, col)
        out = np.full(np.shape(row), np.nan, dtype=float)
        if np.ndim(row) == 0:
            return float(arr[row, col]) if ok else float("nan")
        out[ok] = arr[row[ok], col[ok]]
        return out

    def cell_lonlat(self):
        """Arrays (nrows, ncols) of lon and lat at cell centres."""
        rows, cols = np.mgrid[0:self.nrows, 0:self.ncols]
        x, y = self.to_xy(rows, cols)
        return xy_to_lonlat(x, y)
