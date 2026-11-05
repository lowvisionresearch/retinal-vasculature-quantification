#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import re
import numpy as np
import pandas as pd


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

SLO_OUTPUT_CSV = REPO_ROOT / "models" / "SLOctolyzer" / "analyze" / "output" / "analysis_output.csv"
# The launcher merges each latest SLOctolyzer batch into this cumulative registry.
SLO_CUMULATIVE_CSV = REPO_ROOT / "metadata" / "sloctolyzer_case_metadata.csv"
RESULTS_DIR = REPO_ROOT / "results"

PYTHON_METRICS_CSV = RESULTS_DIR / "analysis_output_vascular_metrics.csv"
MATLAB_METRICS_CSV = RESULTS_DIR / "analysis_output_vascular_metrics_matlab.csv"
CONSOLIDATED_CSV = RESULTS_DIR / "consolidated_global_vascular_metrics.csv"


OUTPUT_COLUMNS = [
    "case_id",
    "subject",
    "scan_orientation",
    "eye",
    "scale",
    "metrics_source",
    "vascular_area",
    "corrected_vascular_area",
    "fractal_dimension",
    "vascular_skeleton_length",
    "corrected_vascular_skeleton_length",
    "vascular_bifurcation_points",
    "average_global_vessel_caliber_um",
    "average_local_vessel_caliber_um",
    "average_global_artery_caliber_um",
    "average_local_artery_caliber_um",
    "average_global_vein_caliber_um",
    "average_local_vein_caliber_um",
    "crae_knudtson_um",
    "crve_knudtson_um",
    "arteriole_to_venule_ratio",
    "tortuosity_density_whole_image",
    "tortuosity_density_artery",
    "tortuosity_density_veins",
]


def case_id_from_filename(filename: str) -> str:
    return Path(str(filename)).stem.replace("_slo_binary_map", "")


CASE_ID_PATTERN = re.compile(
    r"^(?P<subject>.+)_(?P<eye>OD|OS)_(?P<orientation>H|V)_\d{2}$",
    flags=re.I,
)


def parse_subject(case_id: str) -> str:
    match = CASE_ID_PATTERN.fullmatch(case_id)
    return match.group("subject") if match else ""


def parse_scan_orientation(case_id: str) -> str:
    match = CASE_ID_PATTERN.fullmatch(case_id)
    if not match:
        return ""
    return "Horizontal" if match.group("orientation").upper() == "H" else "Vertical"


def parse_eye(case_id: str) -> str:
    match = CASE_ID_PATTERN.fullmatch(case_id)
    return match.group("eye").upper() if match else ""



def first_existing_column(df: pd.DataFrame, candidates: list[str]):
    for col in candidates:
        if col in df.columns:
            return df[col]
    return np.nan


def load_sloctolyzer_table() -> pd.DataFrame:
    """Load all recorded SLOctolyzer cases, not only the latest input batch.

    The launcher maintains a cumulative copy of the full SLOctolyzer summary
    in metadata/sloctolyzer_case_metadata.csv. The upstream analysis_output.csv
    is batch-scoped and is retained as a fallback for standalone analysis.
    """
    if SLO_CUMULATIVE_CSV.is_file():
        source = SLO_CUMULATIVE_CSV
    elif SLO_OUTPUT_CSV.is_file():
        source = SLO_OUTPUT_CSV
    else:
        raise FileNotFoundError(
            "No SLOctolyzer summary found. Looked for the cumulative registry "
            f"at {SLO_CUMULATIVE_CSV} and latest batch at {SLO_OUTPUT_CSV}."
        )

    df = pd.read_csv(source)
    if "Filename" not in df.columns:
        raise ValueError(f"{source.name} must contain a Filename column.")
    if df.empty:
        raise ValueError(f"{source.name} contains no acquisition rows.")

    case_ids = df["Filename"].apply(case_id_from_filename)
    if case_ids.isna().any() or (case_ids.astype(str).str.strip() == "").any():
        raise ValueError(f"{source.name} contains a missing or empty Filename.")
    duplicates = case_ids[case_ids.duplicated(keep=False)].unique().tolist()
    if duplicates:
        raise ValueError(
            f"{source.name} contains duplicate case IDs: {duplicates[:8]}. "
            "Resolve duplicates rather than silently dropping results."
        )
    if "case_id" in df.columns:
        saved_ids = df["case_id"].astype(str)
        inconsistent = saved_ids != case_ids.astype(str)
        if inconsistent.any():
            raise ValueError(
                f"{source.name} has inconsistent Filename and case_id values; "
                "cannot safely combine measurements."
            )

    out = pd.DataFrame()
    out["case_id"] = df["Filename"].apply(case_id_from_filename)
    out["subject"] = out["case_id"].apply(parse_subject)
    out["scan_orientation"] = out["case_id"].apply(parse_scan_orientation)
    out["eye"] = out["case_id"].apply(parse_eye)

    out["scale"] = first_existing_column(df, ["scale", "Scale", "microns_per_pixel"])

    out["average_global_vessel_caliber_um"] = first_existing_column(
        df, ["average_global_calibre_binary_whole"]
    )
    out["average_local_vessel_caliber_um"] = first_existing_column(
        df, ["average_local_calibre_binary_whole"]
    )
    out["average_global_artery_caliber_um"] = first_existing_column(
        df, ["average_global_calibre_artery_whole"]
    )
    out["average_local_artery_caliber_um"] = first_existing_column(
        df, ["average_local_calibre_artery_whole"]
    )
    out["average_global_vein_caliber_um"] = first_existing_column(
        df, ["average_global_calibre_vein_whole"]
    )
    out["average_local_vein_caliber_um"] = first_existing_column(
        df, ["average_local_calibre_vein_whole"]
    )
    out["crae_knudtson_um"] = first_existing_column(
        df, ["CRAE_Knudtson_artery_whole"]
    )
    out["crve_knudtson_um"] = first_existing_column(
        df, ["CRVE_Knudtson_vein_whole"]
    )
    out["arteriole_to_venule_ratio"] = first_existing_column(
        df, ["AVR_whole"]
    )
    out["tortuosity_density_whole_image"] = first_existing_column(
        df, ["tortuosity_density_binary_whole"]
    )
    out["tortuosity_density_artery"] = first_existing_column(
        df, ["tortuosity_density_artery_whole"]
    )
    out["tortuosity_density_veins"] = first_existing_column(
        df, ["tortuosity_density_vein_whole"]
    )

    return out


def load_metrics_table(path: Path, source_name: str) -> pd.DataFrame | None:
    if not path.exists():
        return None

    df = pd.read_csv(path)

    if "Filename" not in df.columns:
        raise ValueError(f"{path.name} must contain a Filename column.")

    out = pd.DataFrame()
    out["case_id"] = df["Filename"].apply(case_id_from_filename)
    out["metrics_source"] = source_name
    out["vascular_area"] = df.get("Vascular_Area", np.nan)
    out["corrected_vascular_area"] = df.get("Corrected_Vascular_Area", np.nan)
    out["fractal_dimension"] = df.get("Fractal_Dimension", np.nan)
    out["vascular_skeleton_length"] = df.get("Vascular_Skeleton_Length", np.nan)
    out["corrected_vascular_skeleton_length"] = df.get(
        "Corrected_Vascular_Skeleton_Length", np.nan
    )
    out["vascular_bifurcation_points"] = df.get(
        "Vascular_Bifurcation_Points", np.nan
    )

    return out


def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    base = load_sloctolyzer_table()

    python_metrics = load_metrics_table(PYTHON_METRICS_CSV, "Python")
    matlab_metrics = load_metrics_table(MATLAB_METRICS_CSV, "MATLAB")

    metric_tables = []

    if matlab_metrics is not None:
        metric_tables.append(matlab_metrics)

    if python_metrics is not None:
        metric_tables.append(python_metrics)

    if metric_tables:
        metrics = pd.concat(metric_tables, ignore_index=True)

        # Priority: Python overrides MATLAB for the same case_id.
        priority = {"MATLAB": 1, "Python": 2}
        metrics["_priority"] = metrics["metrics_source"].map(priority).fillna(0)

        metrics = (
            metrics.sort_values(["case_id", "_priority"])
            .drop_duplicates(subset=["case_id"], keep="last")
            .drop(columns=["_priority"])
        )
    else:
        metrics = pd.DataFrame({"case_id": base["case_id"]})
        metrics["metrics_source"] = ""


    out = base.merge(metrics, on="case_id", how="left")
    out["metrics_source"] = out["metrics_source"].fillna("Not Computed")

    for col in OUTPUT_COLUMNS:
        if col not in out.columns:
            out[col] = np.nan

    out = out[OUTPUT_COLUMNS].sort_values("case_id")

    # Validate one row per acquisition before replacing any existing CSV.
    if out["case_id"].duplicated().any():
        raise ValueError("Consolidation produced duplicate acquisition IDs.")
    temporary = CONSOLIDATED_CSV.with_name(CONSOLIDATED_CSV.name + ".tmp")
    try:
        out.to_csv(temporary, index=False)
        temporary.replace(CONSOLIDATED_CSV)
    finally:
        temporary.unlink(missing_ok=True)

    print(f"Saved consolidated global results to:\n{CONSOLIDATED_CSV}")
    print(f"Rows: {len(out)}")


if __name__ == "__main__":
    main()