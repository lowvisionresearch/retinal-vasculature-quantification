#!/usr/bin/env python3
"""
ETDRS sector analysis using validated MATLAB-style vascular metrics.

Computes, per ETDRS sector:
    Vascular Area
    Vascular Skeleton Length
    Vascular Bifurcation Points

For:
    binary
    artery
    vein

Input:
    metadata/sloctolyzer_case_metadata.csv, or an in-memory pandas DataFrame
    models/SLOctolyzer/analyze/output/<case_id>/*_slo_binary_map.png
    models/SLOctolyzer/analyze/output/<case_id>/*_slo_avod_map.png

Output:
    results/analysis_output_etdrs_vascular_metrics.csv
"""

from __future__ import annotations

from pathlib import Path
from typing import Union

import numpy as np
import pandas as pd
from scipy import ndimage
from PIL import Image

try:
    from .vessel_metrics_python import (
        skeletonize_matlab_lut,
        spur_matlab_lut,
        compute_vbp,
    )
except ImportError:
    from vessel_metrics_python import (
        skeletonize_matlab_lut,
        spur_matlab_lut,
        compute_vbp,
    )


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

SLO_OUTPUT_DIR = REPO_ROOT / "models" / "SLOctolyzer" / "analyze" / "output"
DEFAULT_METADATA_CSV = REPO_ROOT / "metadata" / "sloctolyzer_case_metadata.csv"
RESULTS_DIR = REPO_ROOT / "results"
OUTPUT_CSV = RESULTS_DIR / "analysis_output_etdrs_vascular_metrics.csv"

CENTRAL_RADIUS_UM = 500.0
INNER_RADIUS_UM = 1500.0
OUTER_RADIUS_UM = 3000.0

ARTERY_LABEL = 191
VEIN_LABEL = 127
OPTIC_DISC_LABEL = 255


def load_grayscale_png(path: Path) -> np.ndarray:
    return np.array(Image.open(path).convert("L"))


def safe_float(value) -> float | None:
    try:
        val = float(value)
    except Exception:
        return None
    if np.isnan(val):
        return None
    return val


def normalize_eye_label(value) -> str:
    eye = str(value).strip().lower()

    if eye in {"right", "od", "r"}:
        return "Right"

    if eye in {"left", "os", "l"}:
        return "Left"

    return "Unknown"


def find_case_files(case_dir: Path, case_id: str) -> tuple[Path, Path]:
    binary_path = case_dir / f"{case_id}_slo_binary_map.png"
    avod_path = case_dir / f"{case_id}_slo_avod_map.png"

    if not binary_path.exists():
        matches = list(case_dir.glob("*_slo_binary_map.png"))
        if matches:
            binary_path = matches[0]

    if not avod_path.exists():
        matches = list(case_dir.glob("*_slo_avod_map.png"))
        if matches:
            avod_path = matches[0]

    if not binary_path.exists():
        raise FileNotFoundError(f"Missing binary map in {case_dir}")

    if not avod_path.exists():
        raise FileNotFoundError(f"Missing AVOD map in {case_dir}")

    return binary_path, avod_path


def create_etdrs_masks(
    image_shape: tuple[int, int],
    fovea_x: float,
    fovea_y: float,
    scale_um_per_px: float,
    eye: str,
) -> dict[str, np.ndarray]:
    h, w = image_shape
    y_grid, x_grid = np.ogrid[:h, :w]

    dx = x_grid - fovea_x
    dy = fovea_y - y_grid

    dist = np.sqrt(dx**2 + dy**2)
    theta = (np.degrees(np.arctan2(dy, dx)) + 360) % 360

    r1 = CENTRAL_RADIUS_UM / scale_um_per_px
    r3 = INNER_RADIUS_UM / scale_um_per_px
    r6 = OUTER_RADIUS_UM / scale_um_per_px

    central = dist <= r1
    inner_ring = (dist > r1) & (dist <= r3)
    outer_ring = (dist > r3) & (dist <= r6)

    superior = (theta >= 45) & (theta < 135)
    inferior = (theta >= 225) & (theta < 315)

    eye_norm = normalize_eye_label(eye)

    if eye_norm == "Right":
        nasal = (theta >= 315) | (theta < 45)
        temporal = (theta >= 135) & (theta < 225)
    elif eye_norm == "Left":
        nasal = (theta >= 135) & (theta < 225)
        temporal = (theta >= 315) | (theta < 45)
    else:
        nasal = (theta >= 315) | (theta < 45)
        temporal = (theta >= 135) & (theta < 225)

    return {
        "C1": central,
        "S3": inner_ring & superior,
        "I3": inner_ring & inferior,
        "N3": inner_ring & nasal,
        "T3": inner_ring & temporal,
        "S6": outer_ring & superior,
        "I6": outer_ring & inferior,
        "N6": outer_ring & nasal,
        "T6": outer_ring & temporal,
    }


def split_sector(sector: str) -> tuple[str, str]:
    if sector == "C1":
        return "central_1mm", "central"

    ring = "inner_3mm" if sector.endswith("3") else "outer_6mm"

    region_map = {
        "S": "superior",
        "I": "inferior",
        "N": "nasal",
        "T": "temporal",
    }

    return ring, region_map.get(sector[0], "unknown")


def compute_matlab_style_metrics(mask: np.ndarray) -> dict[str, float | int]:
    mask = mask.astype(bool)

    va = int(np.count_nonzero(mask))

    if va == 0:
        return {
            "Vascular_Area": 0,
            "Vascular_Skeleton_Length": 0,
            "Vascular_Bifurcation_Points": 0,
        }

    skeleton = skeletonize_matlab_lut(mask)
    skeleton_pruned = spur_matlab_lut(skeleton, n_iter=5)

    vsl = int(np.count_nonzero(skeleton_pruned))
    vbp = compute_vbp(skeleton_pruned)

    return {
        "Vascular_Area": va,
        "Vascular_Skeleton_Length": vsl,
        "Vascular_Bifurcation_Points": vbp,
    }

def complete_av_masks_from_binary(
    binary_mask: np.ndarray,
    artery_mask: np.ndarray,
    vein_mask: np.ndarray,
    od_mask: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, int, float]:
    binary_mask = binary_mask.astype(bool) & ~od_mask.astype(bool)
    artery_mask = artery_mask.astype(bool) & binary_mask
    vein_mask = vein_mask.astype(bool) & binary_mask

    overlap = artery_mask & vein_mask
    if np.any(overlap):
        vein_mask[overlap] = False

    classified = artery_mask | vein_mask
    unclassified = binary_mask & ~classified

    binary_pixels = int(np.count_nonzero(binary_mask))
    unclassified_pixels = int(np.count_nonzero(unclassified))
    unclassified_fraction = (
        unclassified_pixels / binary_pixels
        if binary_pixels > 0
        else np.nan
    )

    if not np.any(unclassified) or not np.any(classified):
        return artery_mask, vein_mask, unclassified_pixels, unclassified_fraction

    label_source = np.zeros(binary_mask.shape, dtype=np.uint8)
    label_source[artery_mask] = 1
    label_source[vein_mask] = 2

    _, indices = ndimage.distance_transform_edt(
        label_source == 0,
        return_indices=True,
    )

    nearest_labels = label_source[tuple(indices)]

    artery_mask = artery_mask | (unclassified & (nearest_labels == 1))
    vein_mask = vein_mask | (unclassified & (nearest_labels == 2))

    return artery_mask, vein_mask, unclassified_pixels, unclassified_fraction

MetadataInput = Union[pd.DataFrame, str, Path]


def run_etdrs_analysis(
    metadata: MetadataInput,
    output_csv: str | Path = OUTPUT_CSV,
) -> pd.DataFrame:
    """Run ETDRS vascular analysis from a DataFrame or metadata CSV."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    if isinstance(metadata, pd.DataFrame):
        analysis_df = metadata.copy()
        metadata_description = "in-memory metadata"
    else:
        metadata_path = Path(metadata)
        if not metadata_path.exists():
            raise FileNotFoundError(f"Missing metadata CSV:\n{metadata_path}")
        analysis_df = pd.read_csv(metadata_path)
        metadata_description = str(metadata_path)

    required = {"Filename", "eye", "scale", "fovea_x", "fovea_y"}
    missing = required - set(analysis_df.columns)
    if missing:
        raise ValueError(f"Metadata is missing required columns: {sorted(missing)}")
    if analysis_df.empty:
        raise ValueError("Metadata contains no cases to analyze.")

    print(f"Loaded {len(analysis_df)} case(s) from {metadata_description}")

    rows = []

    for idx, record in analysis_df.iterrows():
        filename = str(record["Filename"])
        case_id = Path(filename).stem
        case_dir = SLO_OUTPUT_DIR / case_id

        print(f"[{idx + 1}/{len(analysis_df)}] Processing {case_id}")

        scale = safe_float(record["scale"])
        fovea_x = safe_float(record["fovea_x"])
        fovea_y = safe_float(record["fovea_y"])
        eye = normalize_eye_label(record["eye"])

        if scale is None or scale <= 0 or fovea_x is None or fovea_y is None:
            print("  Skipped: invalid scale or fovea.")
            continue

        try:
            binary_path, avod_path = find_case_files(case_dir, case_id)

            binary_img = load_grayscale_png(binary_path)
            avod_img = load_grayscale_png(avod_path)

            binary_mask = binary_img > 0
            artery_mask = avod_img == ARTERY_LABEL
            vein_mask = avod_img == VEIN_LABEL
            od_mask = avod_img == OPTIC_DISC_LABEL

            binary_mask = binary_mask & ~od_mask

            (
                artery_mask,
                vein_mask,
                unclassified_pixels_global,
                unclassified_fraction_global,
            ) = complete_av_masks_from_binary(
                binary_mask=binary_mask,
                artery_mask=artery_mask,
                vein_mask=vein_mask,
                od_mask=od_mask,
            )

            etdrs_masks = create_etdrs_masks(
                image_shape=binary_mask.shape,
                fovea_x=fovea_x,
                fovea_y=fovea_y,
                scale_um_per_px=scale,
                eye=eye,
            )

            vessel_maps = {
                "binary": binary_mask,
                "artery": artery_mask,
                "vein": vein_mask,
            }

            for sector, sector_mask in etdrs_masks.items():
                sector_ring, sector_region = split_sector(sector)

                for vessel_type, vessel_mask in vessel_maps.items():
                    sector_vessel_mask = vessel_mask & sector_mask
                    metrics = compute_matlab_style_metrics(sector_vessel_mask)

                    rows.append(
                        {
                            "Filename": filename,
                            "case_id": case_id,
                            "eye": eye,
                            "scale": scale,
                            "scale_units": "microns-per-pixel",
                            "fovea_x": fovea_x,
                            "fovea_y": fovea_y,
                            "sector": sector,
                            "sector_ring": sector_ring,
                            "sector_region": sector_region,
                            "vessel_type": vessel_type,
                            "sector_pixel_count": int(np.count_nonzero(sector_mask)),
                            "vessel_pixel_count": int(
                                np.count_nonzero(sector_vessel_mask)
                            ),
                            "optic_disc_masked_pixels": int(
                                np.count_nonzero(od_mask & sector_mask)
                            ),
                            "unclassified_pixels_global": unclassified_pixels_global,
                            "unclassified_fraction_global": unclassified_fraction_global,
                            **metrics,
                            "status": "ok",
                        }
                    )

            print("  Done.")

        except Exception as exc:
            print(f"  Error: {exc}")
            rows.append(
                {
                    "Filename": filename,
                    "case_id": case_id,
                    "eye": eye,
                    "scale": scale,
                    "scale_units": "microns-per-pixel",
                    "fovea_x": fovea_x,
                    "fovea_y": fovea_y,
                    "sector": "",
                    "sector_ring": "",
                    "sector_region": "",
                    "vessel_type": "",
                    "sector_pixel_count": np.nan,
                    "vessel_pixel_count": np.nan,
                    "optic_disc_masked_pixels": np.nan,
                    "Vascular_Area": np.nan,
                    "Vascular_Skeleton_Length": np.nan,
                    "Vascular_Bifurcation_Points": np.nan,
                    "unclassified_pixels_global": np.nan,
                    "unclassified_fraction_global": np.nan,
                    "status": f"error: {exc}",
                }
            )

    out_df = pd.DataFrame(rows)
    output_path = Path(output_csv)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(output_path, index=False)
    print(f"\nSaved ETDRS vascular metrics to:\n{output_path}")
    return out_df


def main() -> None:
    """Run directly using the cumulative metadata registry."""
    run_etdrs_analysis(DEFAULT_METADATA_CSV, OUTPUT_CSV)


if __name__ == "__main__":
    main()
