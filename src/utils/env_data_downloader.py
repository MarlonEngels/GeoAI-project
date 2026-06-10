"""
Download Copernicus Marine environmental data for VISIR-2 route computation.

Downloads wave and/or current data from the Arctic WAM model and saves
NetCDF files in the format expected by VISIR-2 under __data/envFields/.

Wave data (VHM0, VMDR) is used directly.
Current data (Current, Currentdir) is converted to uo/vo components.
"""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import copernicusmarine
import numpy as np
import xarray as xr

# ---------------------------------------------------------------------------
# Copernicus Marine configuration (matches copernicus_module.ipynb)
# ---------------------------------------------------------------------------
DATASET_ID = "dataset-wam-arctic-1hr3km-be"

WAVE_VARIABLES = ["VMDR", "VHM0"]
CURRENT_VARIABLES = ["Currentdir", "Current"]

# Download bounding box — must be wider than the graph extremes in
# tyrr_graph.yaml so the downloaded NetCDF fully covers the edge grid.
# Graph yaml has lat 58.5–59.95, lon 9.5–11.2 but VISIR-2 computes
# edge coordinates that extend ~0.045 deg beyond those bounds.
# Copernicus snaps to its own grid (~0.03 deg), so we request extra margin.
MIN_LON = 9.0
MAX_LON = 11.7
MIN_LAT = 58.0
MAX_LAT = 60.5

# Fixed filenames — VISIR-2 Campi YAML references these
WAVE_FILENAME = "tyrr_wave.nc"
CURRENT_FILENAME = "tyrr_current.nc"
WIND_FILENAME = "tyrr_wind.nc"

# ---------------------------------------------------------------------------
# Output directories
# ---------------------------------------------------------------------------
_BASE_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "VISIR-2_v6", "__data", "envFields")
)
WAVE_DIR = os.path.join(_BASE_DIR, "wave")
CURRENT_DIR = os.path.join(_BASE_DIR, "current")


# ---------------------------------------------------------------------------
# Individual downloaders
# ---------------------------------------------------------------------------

def download_wave_data(start_dt: str, end_dt: str, filename: str) -> str:
    """Download wave data (VHM0, VMDR) from Copernicus Marine.

    Returns the absolute path to the written NetCDF file.
    """
    os.makedirs(WAVE_DIR, exist_ok=True)

    copernicusmarine.subset(
        dataset_id=DATASET_ID,
        variables=WAVE_VARIABLES,
        minimum_longitude=MIN_LON,
        maximum_longitude=MAX_LON,
        minimum_latitude=MIN_LAT,
        maximum_latitude=MAX_LAT,
        start_datetime=start_dt,
        end_datetime=end_dt,
        output_directory=WAVE_DIR,
        output_filename=filename,
        overwrite=True,
    )

    return os.path.join(WAVE_DIR, filename)


def download_current_data(start_dt: str, end_dt: str, filename: str) -> str:
    """Download current data and convert magnitude/direction to uo/vo.

    Returns the absolute path to the written NetCDF file.
    """
    os.makedirs(CURRENT_DIR, exist_ok=True)

    raw_filename = f"_raw_{filename}"

    copernicusmarine.subset(
        dataset_id=DATASET_ID,
        variables=CURRENT_VARIABLES,
        minimum_longitude=MIN_LON,
        maximum_longitude=MAX_LON,
        minimum_latitude=MIN_LAT,
        maximum_latitude=MAX_LAT,
        start_datetime=start_dt,
        end_datetime=end_dt,
        output_directory=CURRENT_DIR,
        output_filename=raw_filename,
        overwrite=True,
    )

    raw_path = os.path.join(CURRENT_DIR, raw_filename)
    output_path = os.path.join(CURRENT_DIR, filename)

    # load_dataset reads everything into memory and releases the file handle,
    # which avoids Windows PermissionError when we delete the raw file.
    ds = xr.load_dataset(raw_path)

    magnitude = ds["Current"]
    direction_rad = np.deg2rad(ds["Currentdir"])

    # Oceanographic convention: direction current flows TO
    ds["uo"] = magnitude * np.sin(direction_rad)
    ds["vo"] = magnitude * np.cos(direction_rad)
    ds["uo"].attrs = {"units": "m/s", "long_name": "Eastward sea water velocity"}
    ds["vo"].attrs = {"units": "m/s", "long_name": "Northward sea water velocity"}

    ds = ds.drop_vars(["Current", "Currentdir"])
    ds.to_netcdf(output_path)
    ds.close()

    os.remove(raw_path)
    return output_path


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def floor_hour(dt_str: str) -> str:
    """Floor an ISO datetime string to the previous whole hour."""
    dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    floored = dt.replace(minute=0, second=0, microsecond=0)
    return floored.strftime("%Y-%m-%dT%H:%M:%SZ")


def compute_time_range(departure_dt: str, n_days: int = 3) -> tuple[str, str]:
    """Derive start/end datetime strings from departure + n_days."""
    dt = datetime.fromisoformat(departure_dt.replace("Z", "+00:00"))
    dt = dt.replace(minute=0, second=0, microsecond=0)
    end = dt + timedelta(days=n_days)
    fmt = "%Y-%m-%dT%H:%M:%S"
    return dt.strftime(fmt), end.strftime(fmt)


def download_env_data(
    start_dt: str,
    end_dt: str,
    forcing: dict,
) -> dict:
    """Download environmental data concurrently for the requested forcing.

    Returns a dict mapping forcing type to the filename written,
    e.g. ``{"wave": "tyrr_wave.nc", "current": "tyrr_current.nc"}``.
    """
    tasks = {}
    filenames = {}

    with ThreadPoolExecutor(max_workers=2) as executor:
        if int(forcing.get("wave", 0)):
            tasks["wave"] = executor.submit(
                download_wave_data, start_dt, end_dt, WAVE_FILENAME,
            )
            filenames["wave"] = WAVE_FILENAME

        if int(forcing.get("current", 0)):
            tasks["current"] = executor.submit(
                download_current_data, start_dt, end_dt, CURRENT_FILENAME,
            )
            filenames["current"] = CURRENT_FILENAME

        for key, future in tasks.items():
            future.result()

    return filenames


def get_env_file_paths(env_filenames: dict) -> list[str]:
    """Return absolute paths for the downloaded environmental data files."""
    paths = []
    if "wave" in env_filenames:
        paths.append(os.path.join(WAVE_DIR, env_filenames["wave"]))
    if "current" in env_filenames:
        paths.append(os.path.join(CURRENT_DIR, env_filenames["current"]))
    return paths
