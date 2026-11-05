#!/usr/bin/env python3
"""
Python replacement for MATLAB vessel_map_analyzer.m.

Computes:
1. Vascular Area
2. Corrected Vascular Area
3. Fractal Dimension
4. Vascular Skeleton Length
5. Corrected Vascular Skeleton Length
6. Vascular Bifurcation Points

Input:
    segmented-images/Segmented Images/*.png
    metadata/axial_lengths.csv

Output:
    results/analysis_output_vascular_metrics.csv
"""

from __future__ import annotations

from pathlib import Path
import hashlib

import cv2
import numpy as np
import pandas as pd
import tkinter as tk
import matplotlib.pyplot as plt
import argparse
import re
from tkinter import messagebox, simpledialog
from PIL import Image
from scipy import ndimage


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

MASK_DIR = REPO_ROOT / "segmented-images" / "Segmented Images"
AXIAL_LENGTH_CSV = REPO_ROOT / "metadata" / "axial_lengths.csv"
RESULTS_DIR = REPO_ROOT / "results"
OUTPUT_CSV = RESULTS_DIR / "analysis_output_vascular_metrics.csv"
LUT_DIR = REPO_ROOT / "resources" / "luts"

C1 = 3.3944
C2 = 0.01306
C3 = 1.82

def sha256_input_mask(path: Path) -> str:
    """Checksum the accepted segmentation so stale results cannot be reused."""
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class StopSessionRequested(Exception):
    """Raised when the user stops the VBP review session."""

def load_binary_mask(path: Path) -> np.ndarray:
    img = np.array(Image.open(path))
    if img.ndim == 3:
        return (img[:, :, 0] > 0).astype(bool)
    return (img > 0).astype(bool)


def infer_eye_from_filename(filename: str) -> str:
    if "_OD_" in filename.upper():
        return "OD"
    if "_OS_" in filename.upper():
        return "OS"
    return "Unknown"


def extract_participant_id(filename: str) -> str:
    """Return the complete participant ID before the terminal OD/OS and H/V fields."""
    stem = Path(filename).stem
    stem = re.sub(r"_slo_binary_map$", "", stem, flags=re.I)
    match = re.fullmatch(r"(?P<subject>.+)_(?:OD|OS)_(?:H|V)_\d{2}", stem, flags=re.I)
    if match is None:
        raise ValueError(f"Unexpected segmentation filename format: {filename}")
    return match.group("subject")


def vascular_area(mask: np.ndarray) -> int:
    return int(np.count_nonzero(mask))


def fractal_dimension_matlab_style(mask: np.ndarray, num_scales: int = 20) -> float:
    rows, cols = mask.shape
    min_div = 3
    max_div = int(np.floor(min(rows, cols)))

    division_factors = np.unique(
        np.round(np.logspace(np.log10(min_div), np.log10(max_div), num_scales))
        .astype(int)
    )

    log_x = []
    log_y = []

    for n in division_factors:
        box_size = rows / n
        reciprocal_box_size = 1 / box_size

        block_size_row = rows // n
        block_size_col = cols // n

        count = 0

        for i in range(n):
            row_start = i * block_size_row
            row_end = min(rows, (i + 1) * block_size_row)

            for j in range(n):
                col_start = j * block_size_col
                col_end = min(cols, (j + 1) * block_size_col)

                if np.any(mask[row_start:row_end, col_start:col_end]):
                    count += 1

        log_x.append(np.log10(reciprocal_box_size))
        log_y.append(np.log10(count))

    coeffs = np.polyfit(np.array(log_x), np.array(log_y), 1)
    return float(coeffs[0])


def neighbourhood_index_3x3(binary_image: np.ndarray) -> np.ndarray:
    img = binary_image.astype(np.uint8)
    padded = np.pad(img, 1, mode="constant")

    weights = np.array(
        [
            [1, 8, 64],
            [2, 16, 128],
            [4, 32, 256],
        ],
        dtype=np.uint16,
    )

    out = np.zeros_like(img, dtype=np.uint16)

    for r in range(3):
        for c in range(3):
            out += (
                padded[r : r + img.shape[0], c : c + img.shape[1]].astype(np.uint16)
                * weights[r, c]
            )

    return out


def load_skeleton_luts() -> list[np.ndarray]:
    luts = []
    for i in range(1, 9):
        path = LUT_DIR / f"matlab_lut_skel_{i}.csv"
        if not path.exists():
            raise FileNotFoundError(f"Missing skeleton LUT: {path}")
        luts.append(pd.read_csv(path, header=None).values.squeeze().astype(bool))
    return luts


def skeletonize_matlab_lut(mask: np.ndarray, max_iter: int = 10_000) -> np.ndarray:
    luts = load_skeleton_luts()
    out = mask.astype(bool).copy()

    for _ in range(max_iter):
        previous = out.copy()

        for lut in luts:
            idx = neighbourhood_index_3x3(out)
            out = lut[idx].astype(bool)

        if np.array_equal(out, previous):
            break

    return out


def load_spur_lut() -> np.ndarray:
    path = LUT_DIR / "matlab_lut_spur.csv"
    if not path.exists():
        raise FileNotFoundError(f"Missing spur LUT: {path}")
    return pd.read_csv(path, header=None).values.squeeze().astype(bool)


def apply_lutspur_endpoint_detector(skel: np.ndarray, lut: np.ndarray) -> np.ndarray:
    complemented = ~skel.astype(bool)
    idx = neighbourhood_index_3x3(complemented)
    endpoint_mask = lut[idx].astype(bool)
    return endpoint_mask & skel.astype(bool)


def spur_matlab_lut(skel: np.ndarray, n_iter: int = 5) -> np.ndarray:
    lut = load_spur_lut()
    out = skel.astype(bool).copy()

    for _ in range(n_iter):
        endpoints_to_remove = apply_lutspur_endpoint_detector(out, lut)
        if not np.any(endpoints_to_remove):
            break
        out[endpoints_to_remove] = False

    return out


def neighbor_count_8(skel: np.ndarray) -> np.ndarray:
    kernel = np.ones((3, 3), dtype=np.uint8)
    count_with_center = cv2.filter2D(
        skel.astype(np.uint8),
        ddepth=-1,
        kernel=kernel,
        borderType=cv2.BORDER_CONSTANT,
    )
    return count_with_center - skel.astype(np.uint8)


def bresenham_line(r1: int, c1: int, r2: int, c2: int) -> tuple[list[int], list[int]]:
    rows, cols = [], []
    dx = abs(c2 - c1)
    dy = abs(r2 - r1)
    sx = 1 if c1 < c2 else -1
    sy = 1 if r1 < r2 else -1
    err = dx - dy
    r, c = r1, c1

    while True:
        rows.append(r)
        cols.append(c)

        if r == r2 and c == c2:
            break

        e2 = 2 * err

        if e2 > -dy:
            err -= dy
            c += sx

        if e2 < dx:
            err += dx
            r += sy

    return rows, cols


def skel_to_graph_image(skel: np.ndarray) -> np.ndarray:
    skel = skel.astype(bool)
    h, w = skel.shape

    n_count = neighbor_count_8(skel)

    node_pixels = skel & (n_count > 2)
    endpoint_pixels = skel & (n_count == 1)

    structure = np.ones((3, 3), dtype=bool)

    node_labels, num_node_clusters = ndimage.label(node_pixels, structure=structure)
    endpoint_labels, num_endpoint_clusters = ndimage.label(endpoint_pixels, structure=structure)

    graph_node_labels = np.zeros_like(skel, dtype=np.int32)
    graph_nodes: dict[int, tuple[float, float]] = {}

    node_id = 1

    for lab in range(1, num_node_clusters + 1):
        coords = np.argwhere(node_labels == lab)
        if coords.size == 0:
            continue
        graph_node_labels[node_labels == lab] = node_id
        graph_nodes[node_id] = (float(coords[:, 0].mean()), float(coords[:, 1].mean()))
        node_id += 1

    for lab in range(1, num_endpoint_clusters + 1):
        coords = np.argwhere(endpoint_labels == lab)
        if coords.size == 0:
            continue
        graph_node_labels[endpoint_labels == lab] = node_id
        graph_nodes[node_id] = (float(coords[:, 0].mean()), float(coords[:, 1].mean()))
        node_id += 1

    graph_img = np.zeros_like(skel, dtype=bool)
    visited_edges: set[tuple[int, int]] = set()

    offsets = [
        (-1, -1), (-1, 0), (-1, 1),
        (0, -1),           (0, 1),
        (1, -1),  (1, 0),  (1, 1),
    ]

    def in_bounds(rr: int, cc: int) -> bool:
        return 0 <= rr < h and 0 <= cc < w

    for start_node_id in graph_nodes:
        start_pixels = np.argwhere(graph_node_labels == start_node_id)

        for sr, sc in start_pixels:
            for dr, dc in offsets:
                nr, nc = int(sr + dr), int(sc + dc)

                if not in_bounds(nr, nc):
                    continue
                if not skel[nr, nc]:
                    continue
                if graph_node_labels[nr, nc] == start_node_id:
                    continue

                prev = (int(sr), int(sc))
                cur = (nr, nc)
                end_node_id = None

                for _ in range(h * w):
                    cur_node_label = graph_node_labels[cur]

                    if cur_node_label > 0 and cur_node_label != start_node_id:
                        end_node_id = int(cur_node_label)
                        break

                    candidates = []

                    for rr_off, cc_off in offsets:
                        rr = cur[0] + rr_off
                        cc = cur[1] + cc_off

                        if not in_bounds(rr, cc):
                            continue
                        if (rr, cc) == prev:
                            continue
                        if skel[rr, cc]:
                            candidates.append((rr, cc))

                    if not candidates:
                        break

                    prev = cur
                    cur = candidates[0]

                if end_node_id is None:
                    continue

                edge_key = tuple(sorted((start_node_id, end_node_id)))

                if edge_key in visited_edges:
                    continue

                visited_edges.add(edge_key)

                r1, c1 = graph_nodes[start_node_id]
                r2, c2 = graph_nodes[end_node_id]

                rows, cols = bresenham_line(
                    int(round(r1)),
                    int(round(c1)),
                    int(round(r2)),
                    int(round(c2)),
                )

                rows_arr = np.array(rows)
                cols_arr = np.array(cols)

                valid = (
                    (rows_arr >= 0)
                    & (rows_arr < h)
                    & (cols_arr >= 0)
                    & (cols_arr < w)
                )

                graph_img[rows_arr[valid], cols_arr[valid]] = True

    return graph_img


def detect_branchpoints_lut(skel: np.ndarray) -> np.ndarray:
    matlab_to_py = {
        1: (0, 0), 2: (1, 0), 3: (2, 0),
        4: (0, 1), 5: (1, 1), 6: (2, 1),
        7: (0, 2), 8: (1, 2), 9: (2, 2),
    }

    patterns = [
        [1, 3, 7],
        [1, 3, 9],
        [1, 7, 9],
        [3, 7, 9],
        [2, 4, 6],
        [2, 4, 8],
        [2, 6, 8],
        [4, 6, 8],
        [1, 6, 8],
        [3, 4, 8],
        [2, 6, 7],
        [2, 4, 9],
        [1, 3, 8],
        [1, 6, 7],
        [3, 4, 9],
        [2, 7, 9],
    ]

    padded = np.pad(skel.astype(bool), 1, mode="constant")
    out = np.zeros_like(skel, dtype=bool)

    rows, cols = skel.shape

    for r in range(rows):
        for c in range(cols):
            patch = padded[r : r + 3, c : c + 3]

            if not patch[1, 1]:
                continue

            if np.count_nonzero(patch) != 4:
                continue

            for pat in patterns:
                if all(patch[matlab_to_py[idx]] for idx in pat):
                    out[r, c] = True
                    break

    return out


def compute_vbp_details(skel_pruned: np.ndarray) -> tuple[int, np.ndarray, np.ndarray]:
    """Return VBP count, graph image, and branchpoint mask."""
    graph_img = skel_to_graph_image(skel_pruned)
    branchpoints = detect_branchpoints_lut(graph_img)
    return int(np.count_nonzero(branchpoints)), graph_img, branchpoints


def compute_vbp(skel_pruned: np.ndarray) -> int:
    """Return only VBP count."""
    count, _, _ = compute_vbp_details(skel_pruned)
    return count


def correction_factor(al: float) -> float:
    return C1 * C2 * (al - C3)


def load_axial_lengths() -> pd.DataFrame:
    if not AXIAL_LENGTH_CSV.exists():
        raise FileNotFoundError(f"Missing axial length CSV: {AXIAL_LENGTH_CSV}")

    df = pd.read_csv(AXIAL_LENGTH_CSV)

    required = {"ParticipantID", "OD_AL", "OS_AL"}
    missing = required - set(df.columns)

    if missing:
        raise ValueError(f"Missing columns in axial_lengths.csv: {missing}")

    return df


def get_axial_length(axial_df: pd.DataFrame, participant_id: str, eye: str) -> float:
    matches = axial_df[axial_df["ParticipantID"].astype(str) == participant_id]

    if matches.empty:
        raise ValueError(f"No axial length found for participant {participant_id}")

    row = matches.iloc[0]

    if eye == "OD":
        return float(row["OD_AL"])

    if eye == "OS":
        return float(row["OS_AL"])

    raise ValueError(f"Unknown eye label: {eye}")

def ask_vbp_review_mode() -> bool:
    """Ask whether user wants manual VBP review."""
    root = tk.Tk()
    root.withdraw()

    review = messagebox.askyesno(
        "VBP Review",
        "Vascular bifurcation point (VBP) estimation can be noisy.\n\n"
        "Do you want to review VBP detections case-by-case?\n\n"
        "Yes = review VBP detections\n"
        "No = compute automatically without review",
        parent=root,
    )

    root.destroy()
    return review


def ask_vbp_action(filename: str, auto_count: int) -> str:
    """Ask user whether to accept, correct, skip, or stop."""
    root = tk.Tk()
    root.withdraw()

    dialog = tk.Toplevel(root)
    dialog.title("Review VBP Detection")
    dialog.geometry("430x230")
    dialog.resizable(False, False)

    action_var = tk.StringVar(value="")

    tk.Label(
        dialog,
        text=(
            f"File:\n{filename}\n\n"
            f"Automatic VBP count = {auto_count}\n\n"
            "Choose an action:"
        ),
        justify="left",
        wraplength=390,
    ).pack(padx=18, pady=(18, 12))

    button_frame = tk.Frame(dialog)
    button_frame.pack(pady=8)

    def set_action(action: str) -> None:
        action_var.set(action)
        dialog.destroy()

    tk.Button(
        button_frame,
        text="Accept",
        width=12,
        command=lambda: set_action("accept"),
    ).grid(row=0, column=0, padx=5, pady=5)

    tk.Button(
        button_frame,
        text="Correct count",
        width=12,
        command=lambda: set_action("correct"),
    ).grid(row=0, column=1, padx=5, pady=5)

    tk.Button(
        button_frame,
        text="Skip file",
        width=12,
        command=lambda: set_action("skip"),
    ).grid(row=1, column=0, padx=5, pady=5)

    tk.Button(
        button_frame,
        text="Stop session",
        width=12,
        command=lambda: set_action("stop"),
    ).grid(row=1, column=1, padx=5, pady=5)

    dialog.protocol("WM_DELETE_WINDOW", lambda: set_action("stop"))
    dialog.grab_set()
    root.wait_window(dialog)

    action = action_var.get()
    root.destroy()

    if not action:
        return "stop"

    return action

def enable_mouse_pan_zoom(fig, ax):
    """
    Enable intuitive image navigation:

    Left mouse drag -> pan
    Mouse wheel     -> zoom in/out
    """
    pan_state = {
        "pressed": False,
        "x": None,
        "y": None,
        "xlim": None,
        "ylim": None,
    }

    def on_press(event):
        if event.inaxes != ax:
            return

        if event.button == 1:  # left click
            pan_state["pressed"] = True
            pan_state["x"] = event.xdata
            pan_state["y"] = event.ydata
            pan_state["xlim"] = ax.get_xlim()
            pan_state["ylim"] = ax.get_ylim()

    def on_release(event):
        pan_state["pressed"] = False

    def on_motion(event):
        if not pan_state["pressed"]:
            return

        if event.inaxes != ax:
            return

        if event.xdata is None or event.ydata is None:
            return

        dx = event.xdata - pan_state["x"]
        dy = event.ydata - pan_state["y"]

        x0, x1 = pan_state["xlim"]
        y0, y1 = pan_state["ylim"]

        ax.set_xlim(x0 - dx, x1 - dx)
        ax.set_ylim(y0 - dy, y1 - dy)

        fig.canvas.draw_idle()

    def on_scroll(event):
        if event.inaxes != ax:
            return

        base_scale = 1.2

        if event.button == "up":
            scale_factor = 1 / base_scale
        elif event.button == "down":
            scale_factor = base_scale
        else:
            return

        cur_xlim = ax.get_xlim()
        cur_ylim = ax.get_ylim()

        xdata = event.xdata
        ydata = event.ydata

        if xdata is None or ydata is None:
            return

        new_width = (cur_xlim[1] - cur_xlim[0]) * scale_factor
        new_height = (cur_ylim[1] - cur_ylim[0]) * scale_factor

        relx = (cur_xlim[1] - xdata) / (cur_xlim[1] - cur_xlim[0])
        rely = (cur_ylim[1] - ydata) / (cur_ylim[1] - cur_ylim[0])

        ax.set_xlim(
            [xdata - new_width * (1 - relx),
             xdata + new_width * relx]
        )

        ax.set_ylim(
            [ydata - new_height * (1 - rely),
             ydata + new_height * rely]
        )

        fig.canvas.draw_idle()

    fig.canvas.mpl_connect("button_press_event", on_press)
    fig.canvas.mpl_connect("button_release_event", on_release)
    fig.canvas.mpl_connect("motion_notify_event", on_motion)
    fig.canvas.mpl_connect("scroll_event", on_scroll)

def show_vbp_overlay(filename: str, graph_img: np.ndarray, branchpoints: np.ndarray) -> None:
    """Show VBP overlay on graph/skeleton image."""
    fig, ax = plt.subplots(figsize=(8, 8))
    ax.imshow(graph_img, cmap="gray", interpolation="none")

    rows, cols = np.where(branchpoints)
    ax.scatter(cols, rows, s=45, facecolors="none", edgecolors="lime", linewidths=1.5)

    ax.set_title(
        f"{filename}\nDetected VBP = {len(rows)}",
        fontsize=11,
    )
    ax.set_axis_off()
    fig.tight_layout()
    enable_mouse_pan_zoom(fig, ax)
    plt.show(block=False)


def show_vbp_correction_view(
    filename: str,
    graph_img: np.ndarray,
    branchpoints: np.ndarray,
    auto_count: int,
) -> int | None:
    """
    Show side-by-side correction view and ask for corrected count.

    Matplotlib toolbar allows pan/zoom.
    """
    fig, axes = plt.subplots(1, 2, figsize=(14, 7))

    rows, cols = np.where(branchpoints)

    axes[0].imshow(graph_img, cmap="gray", interpolation="none")
    axes[0].scatter(cols, rows, s=45, facecolors="none", edgecolors="lime", linewidths=1.5)
    axes[0].set_title(f"Skeleton/graph + VBP overlay\nDetected = {auto_count}")
    axes[0].set_axis_off()

    axes[1].imshow(branchpoints, cmap="gray", interpolation="none")
    axes[1].set_title("Branchpoint mask")
    axes[1].set_axis_off()

    fig.suptitle(filename)
    fig.tight_layout()
    enable_mouse_pan_zoom(fig, axes[0])
    enable_mouse_pan_zoom(fig, axes[1])
    plt.show(block=False)

    root = tk.Tk()
    root.withdraw()

    corrected = simpledialog.askinteger(
        "Correct VBP Count",
        f"File: {filename}\n\n"
        f"Automatic VBP count = {auto_count}\n\n"
        "Enter corrected vascular bifurcation point count:",
        minvalue=0,
        parent=root,
    )

    root.destroy()

    return corrected

def process_mask(
    path: Path,
    axial_df: pd.DataFrame,
    review_vbp: bool = False,
) -> dict[str, object] | None:
    filename = path.name
    eye = infer_eye_from_filename(filename)
    participant_id = extract_participant_id(filename)

    if eye == "Unknown":
        raise ValueError(f"Could not infer eye from filename: {filename}")

    al = get_axial_length(axial_df, participant_id, eye)

    mask = load_binary_mask(path)

    va = vascular_area(mask)
    fd = fractal_dimension_matlab_style(mask)

    skeleton = skeletonize_matlab_lut(mask)
    skeleton_pruned = spur_matlab_lut(skeleton, n_iter=5)

    vsl = int(np.count_nonzero(skeleton_pruned))
    vbp_auto, graph_img, branchpoints = compute_vbp_details(skeleton_pruned)

    vbp_review_status = "automatic_no_review"
    vbp_final = vbp_auto

    if review_vbp:
        show_vbp_overlay(filename, graph_img, branchpoints)
        action = ask_vbp_action(filename, vbp_auto)

        if action == "accept":
            vbp_review_status = "accepted"

        elif action == "correct":
            corrected = show_vbp_correction_view(
                filename,
                graph_img,
                branchpoints,
                vbp_auto,
            )

            if corrected is None:
                vbp_review_status = "correction_cancelled_used_auto"
                vbp_final = vbp_auto
            else:
                vbp_review_status = "corrected"
                vbp_final = corrected

        elif action == "skip":
            plt.close("all")
            return None

        elif action == "stop":
            plt.close("all")
            raise StopSessionRequested("User stopped VBP review session.")

        plt.close("all")

    sf = correction_factor(al)

    return {
        "Category": "Unspecified",  # Study/cohort label is not inferred from scan count.
        "Filename": filename,
        "Eye": eye,
        "OD_OS_AL_Average": al,
        "Vascular_Area": va,
        "Corrected_Vascular_Area": sf * va,
        "Fractal_Dimension": fd,
        "Vascular_Skeleton_Length": vsl,
        "Corrected_Vascular_Skeleton_Length": sf * vsl,
        "Vascular_Bifurcation_Points": vbp_final,
        "VBP_Automatic_Count": vbp_auto,
        "VBP_Final_Count": vbp_final,
        "VBP_Review_Status": vbp_review_status,
    }

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--participants",
        nargs="*",
        default=None,
        help="Restrict processing to specific participants",
    )

    return parser.parse_args()

def main() -> None:
    args = parse_args()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    selected_participants = (
        set(args.participants)
        if args.participants
        else None
    )

    if not MASK_DIR.exists():
        raise FileNotFoundError(f"Missing mask folder: {MASK_DIR}")

    axial_df = load_axial_lengths()
    review_vbp = ask_vbp_review_mode()

    mask_files_all = list(MASK_DIR.glob("*.png")) + list(MASK_DIR.glob("*.PNG"))

    mask_files = sorted(
        {path.resolve(): path for path in mask_files_all}.values(),
        key=lambda p: p.name.lower(),
    )

    if selected_participants is not None:
        mask_files = [
            path
            for path in mask_files
            if extract_participant_id(path.name) in selected_participants
        ]

        print(
            "Restricting vascular metrics to participants:",
            ", ".join(sorted(selected_participants)),
            flush=True,
        )

        print(
            f"Matching mask files found: {len(mask_files)}\n",
            flush=True,
        )
    if OUTPUT_CSV.exists():
        existing_df = pd.read_csv(OUTPUT_CSV)

        if "Filename" not in existing_df.columns:
            raise ValueError(
                f"Existing output CSV does not contain a Filename column:\n"
                f"{OUTPUT_CSV}"
            )

        # Keep existing cases not in the selected batch, but allow a selected
        # case to be replaced when its mask or axial length has changed.
        rows_by_filename = {
            str(row["Filename"]): row for row in existing_df.to_dict("records")
        }
        print(f"Loaded existing output CSV:")
        print(f"  {OUTPUT_CSV}")
        print(f"Previously recorded files: {len(rows_by_filename)}\n")

    else:
        rows_by_filename = {}

    num_processed_this_run = 0
    num_skipped_existing = 0
    num_errors = 0
    stopped_early = False

    for idx, path in enumerate(mask_files, start=1):
        try:
            input_digest = sha256_input_mask(path)
            eye = infer_eye_from_filename(path.name)
            current_al = get_axial_length(axial_df, extract_participant_id(path.name), eye)
            existing = rows_by_filename.get(path.name)
            previous_digest = str(existing.get("Input_Mask_SHA256", "")) if existing else ""
            try:
                previous_al = float(existing.get("OD_OS_AL_Average")) if existing else float("nan")
            except (TypeError, ValueError):
                previous_al = float("nan")
            prior_review = str(existing.get("VBP_Review_Status", "")) if existing else ""
            needs_vbp_review = review_vbp and prior_review == "automatic_no_review"
            if (existing and previous_digest == input_digest
                    and np.isclose(previous_al, current_al, rtol=0, atol=1e-9)
                    and not needs_vbp_review):
                print(f"[{idx}/{len(mask_files)}] Inputs unchanged. Skipping {path.name}", flush=True)
                num_skipped_existing += 1
                continue
            if existing:
                print(f"[{idx}/{len(mask_files)}] Input or review status changed; recomputing {path.name}", flush=True)
            else:
                print(f"[{idx}/{len(mask_files)}] Processing {path.name}...", flush=True)
            row = process_mask(path, axial_df, review_vbp=review_vbp)

            if row is None:
                print("  Skipped by user.")
                num_errors += 1
                continue

            row["Input_Mask_SHA256"] = input_digest
            rows_by_filename[path.name] = row
            num_processed_this_run += 1
            print("  Done.")

        except StopSessionRequested as exc:
            print(f"  {exc}")
            stopped_early = True
            break

        except Exception as exc:
            print(f"  Skipped/error: {exc}")
            num_errors += 1

    out_df = pd.DataFrame(list(rows_by_filename.values()))
    out_df.to_csv(OUTPUT_CSV, index=False)

    print("\n============================================================")
    print("Python vascular metrics complete.")
    print(f"Newly processed this run: {num_processed_this_run}")
    print(f"Already existing/skipped: {num_skipped_existing}")
    print(f"Errors/skipped:           {num_errors}")
    print(f"Total rows in output CSV: {len(out_df)}")
    print(f"Saved Python vascular metrics to:\n{OUTPUT_CSV}")
    print("============================================================")


if __name__ == "__main__":
    main()