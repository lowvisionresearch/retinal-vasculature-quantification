#!/usr/bin/env python3
"""
Tkinter launcher for the OCT vascular refractive analysis pipeline.

Place this file at:
    oct-vascular-refract-analysis/launch_pipeline.py

Run with:
    conda activate sloctolyzer
    python launch_pipeline.py
"""

from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import platform
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parent

RAW_OCT_DIR = REPO_ROOT / "raw-oct-images"
UTILS_DIR = REPO_ROOT / "utils"

SLOCTOLYZER_ROOT = REPO_ROOT / "models" / "SLOctolyzer"
SLOCTOLYZER_DEMO_DIR = SLOCTOLYZER_ROOT / "analyze" / "demo"
SLOCTOLYZER_OUTPUT_DIR = SLOCTOLYZER_ROOT / "analyze" / "output"
SLOCTOLYZER_ANALYSIS_CSV = SLOCTOLYZER_OUTPUT_DIR / "analysis_output.csv"
SLOCTOLYZER_INPUT_FINGERPRINTS_JSON = SLOCTOLYZER_OUTPUT_DIR / "input_fingerprints.json"

METADATA_DIR = REPO_ROOT / "metadata"
SLOCTOLYZER_METADATA_CSV = METADATA_DIR / "sloctolyzer_case_metadata.csv"

SEGMENTED_DIR = REPO_ROOT / "segmented-images" / "Segmented Images"
VESSEL_METRICS_SCRIPT = REPO_ROOT / "scripts" / "vessel_metrics_python.py"
CONSOLIDATE_RESULTS_SCRIPT = REPO_ROOT / "scripts" / "consolidate_global_results.py"

ORGANIZER_PATH = UTILS_DIR / "organize_heidelbergfiles.py"
OCT_COPIER_PATH = UTILS_DIR / "oct_copier.py"
REVIEWER_PATH = UTILS_DIR / "review_segmentations.py"


def import_module_from_path(module_name: str, path: Path):
    """Import a Python module from an explicit file path."""
    if not path.exists():
        raise FileNotFoundError(f"Required script not found:\n{path}")

    spec = importlib.util.spec_from_file_location(module_name, path)

    if spec is None or spec.loader is None:
        raise ImportError(f"Could not import module from:\n{path}")

    module = importlib.util.module_from_spec(spec)

    # Required for dataclass decorators and module introspection.
    sys.modules[module_name] = module

    spec.loader.exec_module(module)

    return module


def open_in_file_browser(path: Path) -> None:
    """Open a file or folder in the system file browser."""
    path = path.resolve()

    if platform.system() == "Windows":
        os.startfile(path)  # type: ignore[attr-defined]
    elif platform.system() == "Darwin":
        subprocess.run(["open", str(path)], check=False)
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_active_input_fingerprints() -> dict[str, dict[str, str]]:
    """Bind each staged TIFF to its SLOctolyzer metadata (not research results)."""
    tiffs = sorted(p for p in SLOCTOLYZER_DEMO_DIR.iterdir()
                   if p.is_file() and p.suffix.lower() in (".tif", ".tiff"))
    if not tiffs:
        raise RuntimeError("There are no staged SLOctolyzer TIFF files.")
    workbook = SLOCTOLYZER_DEMO_DIR / "fname_resolution_location_eye.xlsx"
    if not workbook.is_file():
        raise FileNotFoundError(f"SLOctolyzer metadata workbook missing: {workbook}")
    metadata = pd.read_excel(workbook)
    required = {"Filename", "Scale", "Location", "Eye"}
    if not required.issubset(metadata.columns):
        raise ValueError(f"SLOctolyzer workbook lacks columns: {sorted(required - set(metadata.columns))}")
    fingerprints = {}
    for tif in tiffs:
        matching = metadata[metadata["Filename"].astype(str).str.casefold() == tif.name.casefold()]
        if len(matching) != 1:
            raise ValueError(f"Expected one metadata row for {tif.name}; found {len(matching)}")
        row = matching.iloc[0]
        scale = float(row["Scale"])
        if not 0 < scale < float("inf"):
            raise ValueError(f"Invalid image scale for {tif.name}: {scale}")
        case = tif.stem
        fingerprints[case] = {
            "filename": tif.name,
            "image_sha256": _sha256_file(tif),
            "scale_um_per_pixel": format(scale, ".12g"),
            "location": str(row["Location"]),
            "eye": str(row["Eye"]),
        }
    return fingerprints


def verify_existing_input_cache(fingerprints: dict[str, dict[str, str]]) -> None:
    """Fail safely if SLOctolyzer would reuse results from mismatched inputs."""
    previous = {}
    if SLOCTOLYZER_INPUT_FINGERPRINTS_JSON.exists():
        previous = json.loads(SLOCTOLYZER_INPUT_FINGERPRINTS_JSON.read_text(encoding="utf-8"))
    for case, active in fingerprints.items():
        output = SLOCTOLYZER_OUTPUT_DIR / case / f"{case}_output.xlsx"
        if output.is_file() and previous.get(case) != active:
            raise RuntimeError(
                f"Existing SLOctolyzer results for {case} have changed or unverified "
                "inputs. Back up and remove only this case's output directory, "
                "then rerun; no files were automatically deleted."
            )


def require_complete_segmentation(fingerprints: dict[str, dict[str, str]]) -> None:
    for case in fingerprints:
        case_dir = SLOCTOLYZER_OUTPUT_DIR / case
        required = [case_dir / f"{case}_output.xlsx", case_dir / f"{case}_slo_binary_map.png",
                    case_dir / f"{case}_slo_avod_map.png"]
        missing = [str(p) for p in required if not p.is_file()]
        if missing:
            raise RuntimeError(f"SLOctolyzer did not produce expected files for {case}: {missing}")


def save_input_fingerprints(fingerprints: dict[str, dict[str, str]]) -> None:
    SLOCTOLYZER_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    previous = {}
    if SLOCTOLYZER_INPUT_FINGERPRINTS_JSON.exists():
        previous = json.loads(SLOCTOLYZER_INPUT_FINGERPRINTS_JSON.read_text(encoding="utf-8"))
    previous.update(fingerprints)
    temporary = SLOCTOLYZER_INPUT_FINGERPRINTS_JSON.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(previous, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(SLOCTOLYZER_INPUT_FINGERPRINTS_JSON)


class PipelineLauncher(tk.Tk):
    """Main Tkinter launcher application."""

    def __init__(self) -> None:
        super().__init__()

        self.title("OCT Vascular Analysis Pipeline")
        self.geometry("1050x820")

        self.participant_vars: dict[str, tk.BooleanVar] = {}
        self.is_running = False

        self._build_gui()
        self.refresh_participants()
        self.check_required_paths()

    def _build_gui(self) -> None:
        """Create GUI layout."""
        title = tk.Label(
            self,
            text="OCT Vascular Analysis Pipeline",
            font=("Arial", 18, "bold"),
        )
        title.pack(pady=(12, 6))

        path_frame = tk.LabelFrame(self, text="Project paths")
        path_frame.pack(fill=tk.X, padx=12, pady=6)

        self.path_label = tk.Label(
            path_frame,
            text=(
                f"Project root: {REPO_ROOT}\n"
                f"Raw OCT folder: {RAW_OCT_DIR}\n"
                f"Deep-learning segmentation input: {SLOCTOLYZER_DEMO_DIR}\n"
                f"Deep-learning segmentation output: {SLOCTOLYZER_OUTPUT_DIR}\n"
                f"Cumulative SLOctolyzer metadata: {SLOCTOLYZER_METADATA_CSV}\n"
                f"Accepted segmentations: {SEGMENTED_DIR}"
            ),
            anchor="w",
            justify="left",
        )
        self.path_label.pack(fill=tk.X, padx=8, pady=6)

        main_frame = tk.Frame(self)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=6)

        participant_frame = tk.LabelFrame(
            main_frame,
            text="Available participants",
        )
        participant_frame.pack(
            side=tk.LEFT,
            fill=tk.BOTH,
            expand=False,
            padx=(0, 8),
        )

        self.participant_canvas = tk.Canvas(participant_frame, width=300)
        self.participant_scrollbar = tk.Scrollbar(
            participant_frame,
            orient="vertical",
            command=self.participant_canvas.yview,
        )
        self.participant_inner = tk.Frame(self.participant_canvas)

        self.participant_inner.bind(
            "<Configure>",
            lambda event: self.participant_canvas.configure(
                scrollregion=self.participant_canvas.bbox("all")
            ),
        )

        self.participant_canvas.create_window(
            (0, 0),
            window=self.participant_inner,
            anchor="nw",
        )
        self.participant_canvas.configure(
            yscrollcommand=self.participant_scrollbar.set
        )

        self.participant_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.participant_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        control_frame = tk.LabelFrame(main_frame, text="Pipeline controls")
        control_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        button_row = tk.Frame(control_frame)
        button_row.pack(fill=tk.X, padx=8, pady=8)

        tk.Button(
            button_row,
            text="Refresh participants",
            command=self.refresh_participants,
            width=20,
        ).pack(side=tk.LEFT, padx=4)

        tk.Button(
            button_row,
            text="Select all",
            command=self.select_all,
            width=12,
        ).pack(side=tk.LEFT, padx=4)

        tk.Button(
            button_row,
            text="Clear selection",
            command=self.clear_selection,
            width=14,
        ).pack(side=tk.LEFT, padx=4)

        self.run_button = tk.Button(
            control_frame,
            text="Run pipeline for selected participants",
            font=("Arial", 12, "bold"),
            command=self.run_pipeline_threaded,
            height=2,
        )
        self.run_button.pack(fill=tk.X, padx=12, pady=(8, 4))

        tk.Button(
            control_frame,
            text="Review segmentations only",
            command=self.launch_reviewer_threaded,
            height=2,
        ).pack(fill=tk.X, padx=12, pady=4)

        tk.Button(
            control_frame,
            text="Run Python vascular metrics",
            command=self.run_vascular_metrics_threaded,
            height=2,
        ).pack(fill=tk.X, padx=12, pady=4)

        tk.Button(
            control_frame,
            text="Open segmentation outputs",
            command=lambda: open_in_file_browser(SLOCTOLYZER_OUTPUT_DIR),
        ).pack(fill=tk.X, padx=12, pady=4)

        tk.Button(
            control_frame,
            text="Open accepted segmentations folder",
            command=lambda: open_in_file_browser(SEGMENTED_DIR),
        ).pack(fill=tk.X, padx=12, pady=4)

        progress_frame = tk.LabelFrame(self, text="Progress")
        progress_frame.pack(fill=tk.X, padx=12, pady=(6, 0))

        self.progress_label = tk.Label(
            progress_frame,
            text="Idle",
            anchor="w",
            justify="left",
        )
        self.progress_label.pack(fill=tk.X, padx=8, pady=(6, 2))

        self.progress_bar = ttk.Progressbar(
            progress_frame,
            mode="indeterminate",
        )
        self.progress_bar.pack(fill=tk.X, padx=8, pady=(0, 8))

        log_frame = tk.LabelFrame(self, text="Status log")
        log_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=(6, 12))

        self.log_box = scrolledtext.ScrolledText(log_frame, height=14)
        self.log_box.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

    def log(self, message: str) -> None:
        """Append a message to the GUI log."""
        self.log_box.insert(tk.END, message + "\n")
        self.log_box.see(tk.END)
        self.update_idletasks()

    def start_progress(self, message: str) -> None:
        """Start indeterminate progress display."""
        self.progress_label.config(text=message)
        self.progress_bar.start(10)
        self.log(message)

    def update_progress(self, message: str) -> None:
        """Update progress message."""
        self.progress_label.config(text=message)
        self.log(message)

    def stop_progress(self, message: str = "Idle") -> None:
        """Stop progress display."""
        self.progress_bar.stop()
        self.progress_label.config(text=message)
        self.log(message)

    def check_required_paths(self) -> None:
        """Check expected project paths and create safe output folders."""
        RAW_OCT_DIR.mkdir(exist_ok=True)
        SEGMENTED_DIR.mkdir(parents=True, exist_ok=True)
        SLOCTOLYZER_DEMO_DIR.mkdir(parents=True, exist_ok=True)
        SLOCTOLYZER_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        METADATA_DIR.mkdir(parents=True, exist_ok=True)

        required_files = [
            ORGANIZER_PATH,
            OCT_COPIER_PATH,
            REVIEWER_PATH,
            VESSEL_METRICS_SCRIPT,
            CONSOLIDATE_RESULTS_SCRIPT,
        ]

        missing = [path for path in required_files if not path.exists()]

        if missing:
            msg = "Missing required files:\n\n" + "\n".join(
                str(p) for p in missing
            )
            messagebox.showwarning("Missing files", msg)
            self.log(msg)
            return

        # The input workbook is generated locally; no populated research
        # metadata file should be required in a fresh public checkout.
        try:
            copier = import_module_from_path("oct_copier", OCT_COPIER_PATH)
            workbook = copier.ensure_metadata_workbook()
            self.log(f"SLOctolyzer metadata workbook ready: {workbook}")
        except Exception as exc:
            msg = f"Could not initialize SLOctolyzer metadata workbook:\n{exc}"
            messagebox.showwarning("Metadata initialization", msg)
            self.log(msg)

    def refresh_participants(self) -> None:
        """Refresh participant folder list from raw-oct-images."""
        for child in self.participant_inner.winfo_children():
            child.destroy()

        self.participant_vars.clear()
        RAW_OCT_DIR.mkdir(exist_ok=True)

        participant_dirs = sorted(
            p for p in RAW_OCT_DIR.iterdir()
            if p.is_dir() and not p.name.startswith(".")
        )

        if not participant_dirs:
            tk.Label(
                self.participant_inner,
                text=(
                    "No participant folders found.\n"
                    "Place raw folders in raw-oct-images/"
                ),
                justify="left",
                fg="gray25",
            ).pack(anchor="w", padx=8, pady=8)
            return

        for participant_dir in participant_dirs:
            var = tk.BooleanVar(value=False)
            self.participant_vars[participant_dir.name] = var

            tk.Checkbutton(
                self.participant_inner,
                text=participant_dir.name,
                variable=var,
                anchor="w",
            ).pack(anchor="w", padx=8, pady=2)

    def selected_participants(self) -> list[Path]:
        """Return selected participant folder paths."""
        selected = []

        for name, var in self.participant_vars.items():
            if var.get():
                selected.append(RAW_OCT_DIR / name)

        return selected

    def select_all(self) -> None:
        """Select all participants."""
        for var in self.participant_vars.values():
            var.set(True)

    def clear_selection(self) -> None:
        """Clear participant selection."""
        for var in self.participant_vars.values():
            var.set(False)

    def run_pipeline_threaded(self) -> None:
        """Run pipeline in a background thread."""
        if self.is_running:
            messagebox.showinfo("Pipeline running", "The pipeline is already running.")
            return

        participants = self.selected_participants()

        if not participants:
            messagebox.showwarning(
                "No participants selected",
                "Please select at least one participant.",
            )
            return

        self.is_running = True
        self.run_button.config(state=tk.DISABLED)

        thread = threading.Thread(
            target=self._run_pipeline_safe,
            args=(participants,),
            daemon=True,
        )
        thread.start()

    def _run_pipeline_safe(self, participants: list[Path]) -> None:
        """Wrapper with error handling for threaded pipeline."""
        try:
            self.start_progress("Processing pipeline...")
            self.run_pipeline(participants)
            self.stop_progress("Pipeline completed.")
            messagebox.showinfo("Done", "Pipeline completed.")

        except Exception as exc:
            self.stop_progress("Pipeline stopped because of an error.")
            self.log(f"ERROR: {exc}")
            messagebox.showerror("Pipeline error", str(exc))

        finally:
            self.is_running = False
            self.run_button.config(state=tk.NORMAL)

    def run_pipeline(self, participants: list[Path]) -> None:
        """Run selected pipeline steps."""
        self.log("=" * 80)
        self.log("Starting pipeline.")
        self.log(f"Selected participants: {', '.join(p.name for p in participants)}")

        organizer = import_module_from_path("organize_heidelbergfiles", ORGANIZER_PATH)
        oct_copier = import_module_from_path("oct_copier", OCT_COPIER_PATH)

        # Validate/organize EVERY participant before clearing the temporary
        # inference inputs. A bad 12-acquisition export must not partly run.
        for participant_dir in participants:
            self.log("-" * 80)
            self.log(f"Validating/organizing: {participant_dir.name}")
            self.update_progress(f"Validating Heidelberg files for {participant_dir.name}...")
            organizer.organize_participant_folder(participant_dir)
            self.log("  Organization validated successfully.")

        self.update_progress("Clearing prior current-batch TIFF files...")
        self.clear_demo_tiffs()

        for participant_dir in participants:
            self.update_progress(f"Preparing OCT images for {participant_dir.name}...")
            staged = oct_copier.copy_oct_files(participant_dir, SLOCTOLYZER_DEMO_DIR)
            expected = sum(
                p.is_dir() and p.name.startswith(participant_dir.name + "_") and
                ("_OD_" in p.name or "_OS_" in p.name)
                for p in participant_dir.iterdir()
            )
            if staged != expected or expected not in (4, 12):
                raise RuntimeError(
                    f"Only {staged} of {expected} required acquisitions were prepared "
                    f"for {participant_dir.name}; stopping before segmentation."
                )
            self.log(f"  Prepared {staged} acquisition image(s) for {participant_dir.name}.")

        self.log("-" * 80)
        self.update_progress(
            "Running SLOctolyzer segmentation. This may take several minutes..."
        )
        self.run_sloctolyzer_segmentation()
        self.log("  Segmentation step completed.")

        self.update_progress("Updating cumulative SLOctolyzer metadata...")
        self.update_sloctolyzer_metadata()

        self.log("-" * 80)
        self.update_progress("Launching segmentation reviewer...")
        self.launch_reviewer()
        self.log("  Review step closed/completed.")

        self.log("-" * 80)
        run_python_metrics = messagebox.askyesno(
            "Run Python vascular metrics?",
            "SLOctolyzer segmentation review is complete.\n\n"
            "Do you want to run the Python vascular metrics now?\n\n"
            "Choose Yes to compute Python vascular metrics.\n"
            "Choose No to skip this step and run the MATLAB implementation separately.",
        )

        if run_python_metrics:
            self.update_progress("Running Python vascular metrics...")
            self.run_vascular_metrics(participants)
            self.log("  Python vascular metrics step completed.")
        else:
            self.update_progress("Skipped Python vascular metrics.")
            self.log(
                "  User skipped Python vascular metrics. "
                "Run the MATLAB implementation separately if needed."
            )

        self.log("-" * 80)
        self.update_progress("Updating consolidated global results...")
        self.run_consolidate_results()
        self.log("  Consolidated global results step completed.")
        self.update_progress("Pipeline completed.")

    def get_demo_tiffs(self) -> list[Path]:
        """Return TIFF files currently present in SLOctolyzer analyze/demo."""
        tiff_files = (
            list(SLOCTOLYZER_DEMO_DIR.glob("*.tif"))
            + list(SLOCTOLYZER_DEMO_DIR.glob("*.TIF"))
            + list(SLOCTOLYZER_DEMO_DIR.glob("*.tiff"))
            + list(SLOCTOLYZER_DEMO_DIR.glob("*.TIFF"))
        )

        unique_files: dict[str, Path] = {}

        for file_path in tiff_files:
            unique_files[str(file_path.resolve()).lower()] = file_path

        return sorted(unique_files.values())

    def clear_demo_tiffs(self) -> None:
        """Clear only current-batch TIFF files from SLOctolyzer analyze/demo."""
        self.log("Clearing prior current-batch TIFF files from analyze/demo...")

        deleted = 0

        for file_path in self.get_demo_tiffs():
            file_path.unlink()
            deleted += 1

        self.log(f"  Removed {deleted} TIFF file(s) from demo folder.")
        self.log("  Preserved fname_resolution_location_eye.xlsx and previous outputs.")

    def run_sloctolyzer_segmentation(self) -> None:
        """Run SLOctolyzer batch processing on analyze/demo."""
        # Robust fallback if the environment activation hook was bypassed.
        # The standard-library-only downloader checks the three SHA-256 hashes
        # and retrieves missing/invalid weights from our GitHub Release.
        from retinal_setup.download_model_weights import main as ensure_weights
        if ensure_weights([]) != 0:
            raise RuntimeError(
                "Required model weights are unavailable or failed SHA-256 "
                "verification. See the model download messages above. "
                "The organizational model-weights GitHub Release must be "
                "published and an internet connection is needed on first use."
            )
        if not self.get_demo_tiffs():
            raise RuntimeError(
                "No TIFF files found in analyze/demo. "
                "Stopping before SLOctolyzer segmentation."
            )

        # SLOctolyzer otherwise reuses existing XLSX measurements by filename.
        # Refuse to reuse results after any TIFF or image-scale change.
        fingerprints = build_active_input_fingerprints()
        verify_existing_input_cache(fingerprints)

        if str(SLOCTOLYZER_ROOT) not in sys.path:
            sys.path.insert(0, str(SLOCTOLYZER_ROOT))

        try:
            from sloctolyzer import main  # type: ignore
        except Exception as exc:
            raise ImportError(
                "Could not import SLOctolyzer. Make sure the launcher is run "
                "from the correct conda environment and that models/SLOctolyzer "
                "contains the sloctolyzer package."
            ) from exc

        args = {
            "image_directory": str(SLOCTOLYZER_DEMO_DIR),
            "output_directory": str(SLOCTOLYZER_OUTPUT_DIR),
            "robust_run": 1,
            "save_individual_segmentations": 1,
            "save_av_overlay": 1,
        }

        self.log(f"  Input:  {SLOCTOLYZER_DEMO_DIR}")
        self.log(f"  Output: {SLOCTOLYZER_OUTPUT_DIR}")

        main.run(args)
        # Persist cache identity only after EVERY case completed successfully.
        require_complete_segmentation(fingerprints)
        save_input_fingerprints(fingerprints)

    @staticmethod
    def participant_from_case_id(case_id: str) -> str:
        """Extract participant ID from a standard case ID."""
        for eye_marker in ("_OD_", "_OS_"):
            if eye_marker in case_id:
                return case_id.split(eye_marker, 1)[0]

        parts = case_id.split("_")
        if len(parts) >= 3:
            return "_".join(parts[:3])

        return case_id

    def update_sloctolyzer_metadata(self) -> None:
        """Merge the latest SLOctolyzer summary into the cumulative registry."""
        if not SLOCTOLYZER_ANALYSIS_CSV.exists():
            raise FileNotFoundError(
                "SLOctolyzer completed, but its analysis summary was not found:\n"
                f"{SLOCTOLYZER_ANALYSIS_CSV}"
            )

        latest_df = pd.read_csv(SLOCTOLYZER_ANALYSIS_CSV)

        if "Filename" not in latest_df.columns:
            raise ValueError(
                "SLOctolyzer analysis_output.csv does not contain a Filename column."
            )

        latest_df = latest_df.copy()
        latest_df["case_id"] = latest_df["Filename"].apply(
            lambda value: Path(str(value)).stem
        )
        latest_df["participant"] = latest_df["case_id"].apply(
            self.participant_from_case_id
        )

        # Keep only the newest row if the current batch itself contains duplicates.
        latest_df = latest_df.drop_duplicates(subset=["case_id"], keep="last")

        METADATA_DIR.mkdir(parents=True, exist_ok=True)

        if SLOCTOLYZER_METADATA_CSV.exists():
            existing_df = pd.read_csv(SLOCTOLYZER_METADATA_CSV)

            if "case_id" not in existing_df.columns:
                if "Filename" not in existing_df.columns:
                    raise ValueError(
                        "Existing cumulative metadata has neither case_id nor "
                        "Filename columns."
                    )
                existing_df["case_id"] = existing_df["Filename"].apply(
                    lambda value: Path(str(value)).stem
                )

            if "participant" not in existing_df.columns:
                existing_df["participant"] = existing_df["case_id"].apply(
                    self.participant_from_case_id
                )

            # Remove older versions of cases present in the latest batch.
            existing_df = existing_df[
                ~existing_df["case_id"].isin(set(latest_df["case_id"]))
            ]
            cumulative_df = pd.concat(
                [existing_df, latest_df],
                ignore_index=True,
                sort=False,
            )
        else:
            cumulative_df = latest_df

        cumulative_df = cumulative_df.drop_duplicates(
            subset=["case_id"],
            keep="last",
        ).sort_values("case_id", key=lambda col: col.astype(str).str.casefold())

        cumulative_df.to_csv(SLOCTOLYZER_METADATA_CSV, index=False)
        self.log(
            f"  Updated cumulative metadata: {SLOCTOLYZER_METADATA_CSV}"
        )
        self.log(
            f"  Registry now contains {len(cumulative_df)} case(s) from "
            f"{cumulative_df['participant'].nunique()} participant(s)."
        )

    def launch_reviewer_threaded(self) -> None:
        """Launch reviewer only in a background thread."""
        thread = threading.Thread(
            target=self.launch_reviewer,
            daemon=True,
        )
        thread.start()

    def launch_reviewer(self) -> None:
        """Run segmentation reviewer utility."""
        self.log("Launching segmentation reviewer...")

        result = subprocess.run(
            [sys.executable, str(REVIEWER_PATH)],
            cwd=str(REPO_ROOT),
            check=False,
        )

        if result.returncode != 0:
            self.log(f"Reviewer exited with code {result.returncode}.")

    def run_vascular_metrics_threaded(self) -> None:
        """Run Python vascular metrics only in a background thread."""
        if self.is_running:
            messagebox.showinfo(
                 "Pipeline running",
                 "The pipeline is already running.",
            )
            return

        participants = self.selected_participants()

        if participants:
            participant_names = ", ".join(p.name for p in participants)

            run_selected = messagebox.askyesno(
                "Run selected participants?",
                "Run Python vascular metrics only for the selected participant(s)?\n\n"
                f"Selected:\n{participant_names}\n\n"
                "Choose Yes to process only selected participants.\n"
                "Choose No to process all reviewed segmentation masks.",
            )

            if not run_selected:
                participants = None

        else:
            run_all = messagebox.askyesno(
                "Run all masks?",
                "No participants are selected.\n\n"
                "Do you want to run Python vascular metrics for ALL reviewed "
                "segmentation masks?",
            )

            if not run_all:
                return

            participants = None

        self.is_running = True
        self.run_button.config(state=tk.DISABLED)

        thread = threading.Thread(
            target=self._run_vascular_metrics_safe,
            args=(participants,),
            daemon=True,
        )
        thread.start()

    def _run_vascular_metrics_safe(
        self,
        participants: list[Path] | None = None,
    ) -> None:
        """Run Python vascular metrics with GUI error handling."""
        try:
            self.start_progress("Running Python vascular metrics...")
            self.run_vascular_metrics(participants)

            self.update_progress("Updating consolidated global results...")
            self.run_consolidate_results()

            self.stop_progress("Python vascular metrics completed.")
            messagebox.showinfo("Done", "Python vascular metrics completed.")

        except Exception as exc:
            self.stop_progress("Python vascular metrics stopped because of an error.")
            self.log(f"ERROR: {exc}")
            messagebox.showerror("Python vascular metrics error", str(exc))

        finally:
            self.is_running = False
            self.run_button.config(state=tk.NORMAL)

    def run_vascular_metrics(self, participants: list[Path] | None = None) -> None:
        """Run Python replacement for MATLAB vessel_map_analyzer.m."""
        if not VESSEL_METRICS_SCRIPT.exists():
            raise FileNotFoundError(
                f"Could not find Python vascular metrics script:\n"
                f"{VESSEL_METRICS_SCRIPT}"
            )

        self.log("Running Python vascular metrics...")
        self.log(f"  Script: {VESSEL_METRICS_SCRIPT}")
        self.log(f"  Input:  {SEGMENTED_DIR}")

        command = [sys.executable, str(VESSEL_METRICS_SCRIPT)]

        if participants:
            participant_names = [p.name for p in participants]
            command += ["--participants", *participant_names]
            self.log(
                "  Restricting Python vascular metrics to: "
                + ", ".join(participant_names)
            )

        process = subprocess.Popen(
            command,
            cwd=str(REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True,
        )

        if process.stdout is None:
            raise RuntimeError(
                "Failed to capture output from vascular metrics script."
            )

        # Stream output live into GUI log.
        for line in process.stdout:
            line = line.strip()

            if not line: 
                continue

            self.log(f"  {line}")

            if line.startswith("[VBP REVIEW REQUIRED]"):
                self.progress_label.config(
                    text="ACTION REQUIRED: Answer the VBP Review dialog (Alt+Tab if hidden)."
                )
            elif line.startswith("[VBP REVIEW CHOICE]"):
                self.progress_label.config(text="Running Python vascular metrics...")

            # Update progress label if script prints case names.
            if (
                "Processing" in line
                or "Already processed" in line
            ):
                self.progress_label.config(
                    text=f"Python vascular metrics: {line}"
                )

        process.wait()

        if process.returncode != 0:
            raise RuntimeError(
                f"Python vascular metrics script exited with code "
                f"{process.returncode}."
            )


    def run_consolidate_results(self) -> None:
        """Update consolidated user-facing results CSV."""
        if not CONSOLIDATE_RESULTS_SCRIPT.exists():
            raise FileNotFoundError(
                f"Could not find consolidator script:\n"
                f"{CONSOLIDATE_RESULTS_SCRIPT}"
            )

        self.log("Updating consolidated global results...")
        self.log(f"  Script: {CONSOLIDATE_RESULTS_SCRIPT}")

        result = subprocess.run(
            [sys.executable, str(CONSOLIDATE_RESULTS_SCRIPT)],
            cwd=str(REPO_ROOT),
            check=False,
            capture_output=True,
            text=True,
        )

        if result.stdout:
            for line in result.stdout.splitlines():
                self.log(f"  {line}")

        if result.stderr:
            for line in result.stderr.splitlines():
                self.log(f"  STDERR: {line}")

        if result.returncode != 0:
            raise RuntimeError(
                f"Consolidated results script exited with code {result.returncode}."
            )

def main() -> None:
    """Launch GUI."""
    app = PipelineLauncher()
    app.mainloop()


if __name__ == "__main__":
    main()