"""Load optional manual lake-outlet overrides from CSV (shared by raster and vector steps)."""

from __future__ import annotations

import os

import geopandas as gpd
import pandas as pd


def load_overrides(overrides_csv_path: str, target_crs) -> gpd.GeoDataFrame:
    """Load optional manual outlet overrides (lat/lon per lake_id) and reproject."""
    if not os.path.exists(overrides_csv_path):
        return gpd.GeoDataFrame(columns=["lake_id", "lat", "lon", "geometry"], crs="EPSG:4326")

    overrides_df = pd.read_csv(overrides_csv_path)
    overrides_df["lake_id"] = overrides_df["lake_id"].astype(str).str.strip()
    geometry = gpd.points_from_xy(overrides_df["lon"], overrides_df["lat"])
    return gpd.GeoDataFrame(overrides_df, geometry=geometry, crs="EPSG:4326").to_crs(target_crs)
