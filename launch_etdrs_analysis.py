#!/usr/bin/env python3
"""
Tkinter launcher for ETDRS sector analysis of SLOctolyzer outputs.

Place this file at:
    octa-vascular-age-analysis/launch_etdrs_analysis.py

Run with:
    conda activate sloctolyzer
    python launch_etdrs_analysis.py

Input:
    metadata/sloctolyzer_case_metadata.csv
    models/SLOctolyzer/analyze/output/<case_id>/*_slo_binary_map.png
    models/SLOctolyzer/analyze/output/<case_id>/*_slo_avod_map.png

Output:
    results/analysis_output_etdrs_vascular_metrics.csv
"""

from __future__ import annotations

import os
import platform
import subprocess
import threading
import tkinter as tk
from pathlib import Path
import matplotlib.pyplot as plt
from matplotlib.patches import Circle
from tkinter import messagebox, scrolledtext, simpledialog, ttk

import numpy as np
import pandas as pd
from PIL import Image

from scripts.etdrs_vascular_metrics_python import run_etdrs_analysis


REPO_ROOT = Path(__file__).resolve().parent

SLOCTOLYZER_ROOT = REPO_ROOT / "models" / "SLOctolyzer"
SLOCTOLYZER_OUTPUT_DIR = SLOCTOLYZER_ROOT / "analyze" / "output"

METADATA_DIR = REPO_ROOT / "metadata"
SLOCTOLYZER_METADATA_CSV = METADATA_DIR / "sloctolyzer_case_metadata.csv"
RESULTS_DIR = REPO_ROOT / "results"
ETDRS_OUTPUT_CSV = RESULTS_DIR / "analysis_output_etdrs_vascular_metrics.csv"

# Standard ETDRS radii in microns.
CENTRAL_RADIUS_UM = 500.0    # 1 mm diameter
INNER_RADIUS_UM = 1500.0     # 3 mm diameter
OUTER_RADIUS_UM = 3000.0     # 6 mm diameter

# SLOctolyzer AVOD grayscale labels.
ARTERY_LABEL = 191
VEIN_LABEL = 127
OPTIC_DISC_LABEL = 255


def open_in_file_browser(path: Path) -> None:
    """Open a file or folder in the system file browser."""
    path = path.resolve()

    if platform.system() == "Windows":
        os.startfile(path)  # type: ignore[attr-defined]
    elif platform.system() == "Darwin":
        subprocess.run(["open", str(path)], check=False)
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


def load_grayscale_png(path: Path) -> np.ndarray:
    """Load a PNG as a grayscale numpy array."""
    return np.array(Image.open(path).convert("L"))


def normalize_eye_label(eye_value: object) -> str:
    """Normalize eye labels from SLOctolyzer or filenames."""
    eye = str(eye_value).strip().lower()

    if eye in {"right", "od", "r"}:
        return "Right"

    if eye in {"left", "os", "l"}:
        return "Left"

    return "Unknown"


def safe_float(value: object) -> float | None:
    """Convert to float or return None."""
    try:
        val = float(value)
    except Exception:
        return None

    if np.isnan(val):
        return None

    return val


def find_case_files(case_dir: Path, case_id: str) -> tuple[Path, Path]:
    """Find binary and AVOD maps for one SLOctolyzer case folder."""
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



def load_case_record(case_id: str) -> pd.Series:
    """Load one cumulative metadata row by case_id."""
    if not SLOCTOLYZER_METADATA_CSV.exists():
        raise FileNotFoundError(
            f"Could not find cumulative metadata:\n{SLOCTOLYZER_METADATA_CSV}"
        )

    analysis_df = pd.read_csv(SLOCTOLYZER_METADATA_CSV)
    if "case_id" in analysis_df.columns:
        analysis_df["_case_id"] = analysis_df["case_id"].astype(str)
    elif "Filename" in analysis_df.columns:
        analysis_df["_case_id"] = analysis_df["Filename"].apply(
            lambda x: Path(str(x)).stem
        )
    else:
        raise ValueError(
            "Cumulative metadata contains neither case_id nor Filename."
        )

    matches = analysis_df[analysis_df["_case_id"] == case_id]

    if matches.empty:
        raise ValueError(f"Could not find {case_id} in cumulative metadata.")

    return matches.iloc[0]


def preview_etdrs_overlay(case_id: str) -> None:
    """Display ETDRS overlay for one case without saving."""
    record = load_case_record(case_id)

    scale = safe_float(record["scale"])
    fovea_x = safe_float(record["fovea_x"])
    fovea_y = safe_float(record["fovea_y"])
    eye = normalize_eye_label(record["eye"])

    if scale is None or scale <= 0:
        raise ValueError(f"Invalid scale for {case_id}.")

    if fovea_x is None or fovea_y is None:
        raise ValueError(f"Missing fovea coordinates for {case_id}.")

    case_dir = SLOCTOLYZER_OUTPUT_DIR / case_id
    if not case_dir.exists():
        raise FileNotFoundError(f"Missing case folder:\n{case_dir}")

    slo_path = case_dir / f"{case_id}_slo.png"
    binary_path, avod_path = find_case_files(case_dir, case_id)

    if not slo_path.exists():
        matches = list(case_dir.glob("*_slo.png"))
        if not matches:
            raise FileNotFoundError(f"Missing SLO image in {case_dir}")
        slo_path = matches[0]

    slo_img = load_grayscale_png(slo_path)
    binary_img = load_grayscale_png(binary_path)
    avod_img = load_grayscale_png(avod_path)

    binary_mask = binary_img > 0
    od_mask = avod_img == OPTIC_DISC_LABEL
    binary_mask = binary_mask & ~od_mask

    r1 = CENTRAL_RADIUS_UM / scale
    r3 = INNER_RADIUS_UM / scale
    r6 = OUTER_RADIUS_UM / scale

    fig, ax = plt.subplots(figsize=(8, 8))
    ax.imshow(slo_img, cmap="gray", interpolation="none")

    overlay = np.zeros((*binary_mask.shape, 4), dtype=float)
    overlay[..., 1] = binary_mask.astype(float)
    overlay[..., 3] = binary_mask.astype(float) * 0.35
    ax.imshow(overlay, interpolation="none")

    # Draw ETDRS rings.
    for radius in [r1, r3, r6]:
        ax.add_patch(
            Circle(
                (fovea_x, fovea_y),
                radius,
                fill=False,
                linewidth=2,
                edgecolor="black",
            )
        )

    # Draw ETDRS sector boundaries at 45, 135, 225, and 315 degrees.
    # These divide the rings into superior, inferior, nasal, and temporal sectors.
    for angle_deg in [45, 135, 225, 315]:
        angle_rad = np.deg2rad(angle_deg)

        x1 = fovea_x + r1 * np.cos(angle_rad)
        y1 = fovea_y - r1 * np.sin(angle_rad)

        x2 = fovea_x + r6 * np.cos(angle_rad)
        y2 = fovea_y - r6 * np.sin(angle_rad)

        ax.plot([x1, x2], [y1, y2], "k-", linewidth=1.5)

    # Fovea marker.
    ax.scatter(
        [fovea_x],
        [fovea_y],
        marker="x",
        s=100,
        linewidths=2,
        color="cyan",
        zorder=5,
    )

    # Determine nasal/temporal side using SLOctolyzer eye convention.
    # Right eye: nasal is image-right.
    # Left eye: nasal is image-left.
    if eye == "Right":
        nasal_sign = 1
        temporal_sign = -1
    elif eye == "Left":
        nasal_sign = -1
        temporal_sign = 1
    else:
        nasal_sign = 1
        temporal_sign = -1

    # Label positions.
    label_style = {
        "fontsize": 11,
        "fontweight": "bold",
        "ha": "center",
        "va": "center",
        "bbox": {
            "boxstyle": "round,pad=0.2",
            "facecolor": "white",
            "edgecolor": "black",
            "alpha": 0.75,
        },
    }

    # Central 1 mm.
    ax.text(fovea_x, fovea_y, "C1", **label_style)

    # Inner 3 mm ring labels.
    ax.text(fovea_x, fovea_y - 0.55 * r3, "S3", **label_style)
    ax.text(fovea_x, fovea_y + 0.55 * r3, "I3", **label_style)
    ax.text(fovea_x + nasal_sign * 0.55 * r3, fovea_y, "N3", **label_style)
    ax.text(fovea_x + temporal_sign * 0.55 * r3, fovea_y, "T3", **label_style)

    # Outer 6 mm ring labels.
    ax.text(fovea_x, fovea_y - 0.72 * r6, "S6", **label_style)
    ax.text(fovea_x, fovea_y + 0.72 * r6, "I6", **label_style)
    ax.text(fovea_x + nasal_sign * 0.72 * r6, fovea_y, "N6", **label_style)
    ax.text(fovea_x + temporal_sign * 0.72 * r6, fovea_y, "T6", **label_style)

    ax.set_title(
        f"{case_id}\nETDRS preview | eye={eye} | scale={scale:.3f} µm/px"
    )
    ax.set_axis_off()
    fig.tight_layout()
    plt.show()

class ETDRSLauncher(tk.Tk):
    """Tkinter launcher for ETDRS sector analysis."""

    def __init__(self) -> None:
        super().__init__()

        self.title("ETDRS Sector Analysis Launcher")
        self.geometry("1000x900")

        self.is_running = False
        self.participant_case_ids: dict[str, list[str]] = {}
        self.participant_vars: dict[str, tk.BooleanVar] = {}

        self._build_gui()
        self.check_required_paths()
        self.refresh_participants()

    def _build_gui(self) -> None:
        """Create GUI layout."""
        title = tk.Label(
            self,
            text="ETDRS Sector Analysis Launcher",
            font=("Arial", 18, "bold"),
        )
        title.pack(pady=(12, 6))

        path_frame = tk.LabelFrame(self, text="Project paths")
        path_frame.pack(fill=tk.X, padx=12, pady=6)

        self.path_label = tk.Label(
            path_frame,
            text=(
                f"Project root: {REPO_ROOT}\n"
                f"SLOctolyzer output folder: {SLOCTOLYZER_OUTPUT_DIR}\n"
                f"Cumulative metadata: {SLOCTOLYZER_METADATA_CSV}\n"
                f"Output CSV: {ETDRS_OUTPUT_CSV}"
            ),
            anchor="w",
            justify="left",
        )
        self.path_label.pack(fill=tk.X, padx=8, pady=6)

        selection_frame = tk.LabelFrame(self, text="Participants to analyze")
        selection_frame.pack(fill=tk.BOTH, padx=12, pady=6)

        selection_toolbar = tk.Frame(selection_frame)
        selection_toolbar.pack(fill=tk.X, padx=8, pady=(6, 4))

        tk.Button(
            selection_toolbar,
            text="Select all",
            command=self.select_all_participants,
        ).pack(side=tk.LEFT, padx=(0, 4))

        tk.Button(
            selection_toolbar,
            text="Clear all",
            command=self.clear_participant_selection,
        ).pack(side=tk.LEFT, padx=4)

        tk.Button(
            selection_toolbar,
            text="Refresh",
            command=self.refresh_participants,
        ).pack(side=tk.LEFT, padx=4)

        self.selection_label = tk.Label(
            selection_toolbar,
            text="0 participants selected",
            anchor="e",
        )
        self.selection_label.pack(side=tk.RIGHT)

        list_frame = tk.Frame(selection_frame)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        self.participant_canvas = tk.Canvas(
            list_frame,
            height=180,
            highlightthickness=1,
            highlightbackground="#b0b0b0",
        )
        self.participant_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        participant_scrollbar = ttk.Scrollbar(
            list_frame,
            orient=tk.VERTICAL,
            command=self.participant_canvas.yview,
        )
        participant_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.participant_canvas.configure(
            yscrollcommand=participant_scrollbar.set
        )

        self.participant_checkbox_frame = tk.Frame(self.participant_canvas)
        self.participant_canvas_window = self.participant_canvas.create_window(
            (0, 0),
            window=self.participant_checkbox_frame,
            anchor="nw",
        )

        self.participant_checkbox_frame.bind(
            "<Configure>",
            self._update_participant_scrollregion,
        )
        self.participant_canvas.bind(
            "<Configure>",
            self._resize_participant_checkbox_frame,
        )

        control_frame = tk.LabelFrame(self, text="Controls")
        control_frame.pack(fill=tk.X, padx=12, pady=6)

        self.run_button = tk.Button(
            control_frame,
            text="Run ETDRS sector analysis",
            font=("Arial", 12, "bold"),
            command=self.run_threaded,
            height=2,
        )
        self.run_button.pack(fill=tk.X, padx=12, pady=(10, 6))

        tk.Button(
            control_frame,
            text="Preview ETDRS overlay",
            command=self.preview_overlay,
            height=2,
        ).pack(fill=tk.X, padx=12, pady=4)

        tk.Button(
            control_frame,
            text="Open SLOctolyzer output folder",
            command=lambda: open_in_file_browser(SLOCTOLYZER_OUTPUT_DIR),
            height=2,
        ).pack(fill=tk.X, padx=12, pady=4)

        tk.Button(
            control_frame,
            text="Open ETDRS output CSV",
            command=self.open_output_csv,
            height=2,
        ).pack(fill=tk.X, padx=12, pady=(4, 10))

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

        self.log_box = scrolledtext.ScrolledText(log_frame, height=20)
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

    def _update_participant_scrollregion(self, _event: tk.Event) -> None:
        """Update the checkbox canvas scrollable region."""
        self.participant_canvas.configure(
            scrollregion=self.participant_canvas.bbox("all")
        )

    def _resize_participant_checkbox_frame(self, event: tk.Event) -> None:
        """Keep the checkbox frame the same width as its canvas."""
        self.participant_canvas.itemconfigure(
            self.participant_canvas_window,
            width=event.width,
        )

    def refresh_participants(self) -> None:
        """Reload participant checkboxes from cumulative metadata."""
        previous_selection = {
            participant
            for participant, variable in self.participant_vars.items()
            if variable.get()
        }

        for child in self.participant_checkbox_frame.winfo_children():
            child.destroy()

        self.participant_case_ids.clear()
        self.participant_vars.clear()

        if not SLOCTOLYZER_METADATA_CSV.exists():
            self.log(f"Cumulative metadata not found: {SLOCTOLYZER_METADATA_CSV}")
            self.update_selection_label()
            return

        try:
            analysis_df = pd.read_csv(SLOCTOLYZER_METADATA_CSV)
        except Exception as exc:
            self.log(f"Could not read participant list: {exc}")
            self.update_selection_label()
            return

        if "Filename" not in analysis_df.columns:
            if "case_id" not in analysis_df.columns:
                self.log(
                    "Cumulative metadata contains neither Filename nor case_id."
                )
                self.update_selection_label()
                return

        if "case_id" in analysis_df.columns:
            case_ids = analysis_df["case_id"].dropna().astype(str)
        else:
            case_ids = analysis_df["Filename"].dropna().apply(
                lambda value: Path(str(value)).stem
            )

        for case_id in case_ids:
            participant = self.participant_from_case_id(case_id)
            self.participant_case_ids.setdefault(participant, []).append(case_id)

        participants = sorted(self.participant_case_ids, key=str.casefold)
        select_all_by_default = not previous_selection

        for row_index, participant in enumerate(participants):
            case_count = len(self.participant_case_ids[participant])
            selected = (
                participant in previous_selection
                if previous_selection
                else select_all_by_default
            )
            variable = tk.BooleanVar(value=selected)
            self.participant_vars[participant] = variable

            checkbox = tk.Checkbutton(
                self.participant_checkbox_frame,
                text=(
                    f"{participant}  "
                    f"({case_count} case{'s' if case_count != 1 else ''})"
                ),
                variable=variable,
                command=self.update_selection_label,
                anchor="w",
                justify="left",
            )
            checkbox.grid(
                row=row_index,
                column=0,
                sticky="ew",
                padx=6,
                pady=1,
            )

        self.participant_checkbox_frame.grid_columnconfigure(0, weight=1)
        self.update_selection_label()
        self.log(
            f"Loaded {len(self.participant_case_ids)} participant(s) "
            "from cumulative SLOctolyzer metadata."
        )

    def select_all_participants(self) -> None:
        """Check every participant checkbox."""
        for variable in self.participant_vars.values():
            variable.set(True)
        self.update_selection_label()

    def clear_participant_selection(self) -> None:
        """Clear every participant checkbox."""
        for variable in self.participant_vars.values():
            variable.set(False)
        self.update_selection_label()

    def selected_participants(self) -> list[str]:
        """Return checked participant IDs in display order."""
        return [
            participant
            for participant in sorted(self.participant_case_ids, key=str.casefold)
            if self.participant_vars[participant].get()
        ]

    def update_selection_label(self) -> None:
        """Update the checkbox-selection count label."""
        selected_count = sum(
            variable.get() for variable in self.participant_vars.values()
        )
        total_count = len(self.participant_vars)
        self.selection_label.config(
            text=f"{selected_count} of {total_count} participants selected"
        )

    def create_filtered_metadata(
        self,
        selected_participants: list[str],
    ) -> pd.DataFrame:
        """Return cumulative metadata rows for selected participants."""
        analysis_df = pd.read_csv(SLOCTOLYZER_METADATA_CSV)
        if "Filename" not in analysis_df.columns:
            raise ValueError("Cumulative metadata does not contain a Filename column.")
        selected_set = set(selected_participants)
        if "participant" in analysis_df.columns:
            participant_series = analysis_df["participant"].astype(str)
        else:
            participant_series = analysis_df["Filename"].apply(
                lambda value: self.participant_from_case_id(Path(str(value)).stem)
            )
        filtered_df = analysis_df[participant_series.isin(selected_set)].copy()
        if filtered_df.empty:
            raise ValueError("No cases matched the selected participants.")
        return filtered_df.reset_index(drop=True)

    def check_required_paths(self) -> None:
        """Check required input paths."""
        SLOCTOLYZER_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        missing = []

        if not SLOCTOLYZER_METADATA_CSV.exists():
            missing.append(SLOCTOLYZER_METADATA_CSV)

        if not SLOCTOLYZER_ROOT.exists():
            missing.append(SLOCTOLYZER_ROOT)

        if missing:
            msg = "Missing required files/folders:\n\n" + "\n".join(
                str(path) for path in missing
            )
            messagebox.showwarning("Missing input", msg)
            self.log(msg)

    def run_threaded(self) -> None:
        """Run ETDRS analysis in a background thread."""
        if self.is_running:
            messagebox.showinfo("Already running", "ETDRS analysis is already running.")
            return

        if not SLOCTOLYZER_METADATA_CSV.exists():
            messagebox.showerror(
                "Missing cumulative metadata",
                f"Could not find:\n{SLOCTOLYZER_METADATA_CSV}",
            )
            return

        selected_participants = self.selected_participants()

        if not selected_participants:
            messagebox.showwarning(
                "No participants selected",
                "Select at least one participant before running the analysis.",
            )
            return

        self.is_running = True
        self.run_button.config(state=tk.DISABLED)

        selected_case_count = sum(
            len(self.participant_case_ids[participant])
            for participant in selected_participants
        )
        self.log("Starting ETDRS vascular metrics...")
        self.log(
            f"  Selected: {len(selected_participants)} participant(s), "
            f"{selected_case_count} case(s)"
        )
        self.log(f"  Output: {ETDRS_OUTPUT_CSV}")


        thread = threading.Thread(
            target=self._run_safe,
            args=(selected_participants,),
            daemon=True,
        )
        thread.start()

    def _run_safe(self, selected_participants: list[str]) -> None:
        """Run ETDRS metrics for the selected participants only."""
        try:
            self.start_progress("Preparing selected participant metadata...")
            filtered_df = self.create_filtered_metadata(selected_participants)
            self.update_progress(
                f"Running ETDRS vascular metrics for {len(filtered_df)} case(s)..."
            )
            run_etdrs_analysis(metadata=filtered_df, output_csv=ETDRS_OUTPUT_CSV)
            self.stop_progress("ETDRS analysis completed.")
            messagebox.showinfo(
                "Done",
                "ETDRS analysis completed.\n\nSaved to:\n"
                f"{ETDRS_OUTPUT_CSV}",
            )
        except Exception as exc:
            self.stop_progress("ETDRS analysis stopped because of an error.")
            self.log(f"ERROR: {exc}")
            messagebox.showerror("ETDRS analysis error", str(exc))
        finally:
            self.is_running = False
            self.run_button.config(state=tk.NORMAL)

    def preview_overlay(self) -> None:
        """Ask user for case ID and show ETDRS overlay."""
        if not SLOCTOLYZER_METADATA_CSV.exists():
            messagebox.showerror(
                "Missing cumulative metadata",
                f"Could not find:\n{SLOCTOLYZER_METADATA_CSV}",
            )
            return

        analysis_df = pd.read_csv(SLOCTOLYZER_METADATA_CSV)

        if "Filename" not in analysis_df.columns:
            messagebox.showerror(
                "Invalid CSV",
                "Cumulative metadata does not contain a Filename column.",
            )
            return

        case_ids = [
            Path(str(fname)).stem
            for fname in analysis_df["Filename"].dropna().tolist()
        ]

        if not case_ids:
            messagebox.showerror(
                "No cases found",
                "No cases were found in cumulative metadata.",
            )
            return

        default_case = case_ids[0]

        case_id = simpledialog.askstring(
            "Preview ETDRS overlay",
            "Enter case ID to preview:\n\n"
            f"Example: {default_case}",
            initialvalue=default_case,
            parent=self,
        )

        if case_id is None:
            return

        case_id = case_id.strip()

        if not case_id:
            return

        try:
            preview_etdrs_overlay(case_id)
            self.log(f"Displayed ETDRS overlay for {case_id}.")

        except Exception as exc:
            self.log(f"Overlay preview error: {exc}")
            messagebox.showerror("Overlay preview error", str(exc))

    def open_output_csv(self) -> None:
        """Open output CSV if it exists."""
        if ETDRS_OUTPUT_CSV.exists():
            open_in_file_browser(ETDRS_OUTPUT_CSV)
        else:
            messagebox.showinfo(
                "Output not found",
                f"Output CSV does not exist yet:\n{ETDRS_OUTPUT_CSV}",
            )


def main() -> None:
    """Launch GUI."""
    app = ETDRSLauncher()
    app.mainloop()


if __name__ == "__main__":
    main()