#!/usr/bin/env python3
"""
Persistent GUI reviewer for SLO segmentation images.

Workflow
--------
1. For each case subfolder:
   - Find the ground-truth image: *_slo.png
   - Find the segmented image: *_slo_binary_map.png
   - Show one persistent GUI window
   - Toggle the two images back-and-forth like a GIF
   - User chooses:
       * Copy  -> copy segmented image to destination
       * Skip  -> move to next case
       * Stop  -> terminate session cleanly
2. If the destination file already exists, copying is skipped.

Designed for Windows and macOS with Anaconda Python.
"""

from __future__ import annotations

import shutil
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

from PIL import Image, ImageTk


# ---------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

SOURCE_FOLDER = (
    REPO_ROOT /
    "models" /
    "SLOctolyzer" /
    "analyze" /
    "output"
)

DESTINATION_FOLDER = (
    REPO_ROOT /
    "segmented-images" /
    "Segmented Images"
)

TOGGLE_INTERVAL_MS = 500
WINDOW_TITLE = "Segmentation Reviewer"
MAX_DISPLAY_SIZE = 900


class SegmentationReviewerApp:
    """Persistent GUI application for reviewing segmentation results."""

    def __init__(
        self,
        root: tk.Tk,
        source_root: Path,
        destination_folder: Path,
    ) -> None:
        self.root = root
        self.source_root = source_root
        self.destination_folder = destination_folder

        self.case_folders = sorted(
            [p for p in self.source_root.iterdir() if p.is_dir()]
        )
        self.case_index = 0

        self.current_case_folder: Path | None = None
        self.current_ground_truth_path: Path | None = None
        self.current_segmented_path: Path | None = None

        self.current_ground_truth_photo: ImageTk.PhotoImage | None = None
        self.current_segmented_photo: ImageTk.PhotoImage | None = None

        self.showing_ground_truth = True
        self.toggle_job: str | None = None

        self.copied_count = 0
        self.skipped_exists_count = 0
        self.declined_count = 0
        self.missing_pair_count = 0
        self.finished = False

        self._build_gui()

        if not self.case_folders:
            messagebox.showinfo(
                "No subfolders found",
                f"No participant subfolders were found in:\n{self.source_root}",
            )
            self.finished = True
            self.root.after(100, self.root.destroy)
            return

        self.load_next_valid_case()

    def _build_gui(self) -> None:
        """Build the persistent GUI."""
        self.root.title(WINDOW_TITLE)
        self.root.geometry("1100x1000")
        self.root.protocol("WM_DELETE_WINDOW", self.on_stop)

        info_frame = tk.Frame(self.root)
        info_frame.pack(side=tk.TOP, fill=tk.X, padx=10, pady=10)

        self.case_label = tk.Label(
            info_frame,
            text="Case: ",
            font=("Arial", 14, "bold"),
            anchor="w",
            justify="left",
        )
        self.case_label.pack(fill=tk.X)

        self.image_type_label = tk.Label(
            info_frame,
            text="Showing: ",
            font=("Arial", 12),
            anchor="w",
            justify="left",
        )
        self.image_type_label.pack(fill=tk.X, pady=(5, 0))

        self.status_label = tk.Label(
            info_frame,
            text="Status: ",
            font=("Arial", 11),
            fg="blue",
            anchor="w",
            justify="left",
        )
        self.status_label.pack(fill=tk.X, pady=(5, 0))

        image_frame = tk.Frame(self.root, bd=2, relief=tk.SUNKEN)
        image_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        self.image_label = tk.Label(image_frame, bg="black")
        self.image_label.pack(fill=tk.BOTH, expand=True)

        button_frame = tk.Frame(self.root)
        button_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=10)

        self.copy_button = tk.Button(
            button_frame,
            text="Copy",
            width=18,
            font=("Arial", 12),
            command=self.on_copy,
        )
        self.copy_button.pack(side=tk.LEFT, padx=5)

        self.skip_button = tk.Button(
            button_frame,
            text="Skip",
            width=18,
            font=("Arial", 12),
            command=self.on_skip,
        )
        self.skip_button.pack(side=tk.LEFT, padx=5)

        self.stop_button = tk.Button(
            button_frame,
            text="Stop reviewing for now",
            width=22,
            font=("Arial", 12),
            command=self.on_stop,
        )
        self.stop_button.pack(side=tk.RIGHT, padx=5)

        instruction_text = (
            "Review workflow: the display alternates between the ground-truth "
            "image and the segmented image.\n"
            "If the segmentation looks good, click Copy. Otherwise click Skip. "
            "Click Stop reviewing for now to end the session cleanly."
        )

        self.instructions_label = tk.Label(
            self.root,
            text=instruction_text,
            font=("Arial", 10),
            justify="left",
            anchor="w",
        )
        self.instructions_label.pack(
            side=tk.BOTTOM,
            fill=tk.X,
            padx=10,
            pady=(0, 10),
        )

    @staticmethod
    def find_image_pair(case_folder: Path) -> tuple[Path | None, Path | None]:
        """Find ground-truth and segmented image in the case folder."""
        png_files = (
            list(case_folder.glob("*.png")) +
            list(case_folder.glob("*.PNG"))
        )

        segmented_candidates = sorted(
            p for p in png_files
            if p.name.lower().endswith("_slo_binary_map.png")
        )

        ground_truth_candidates = sorted(
            p for p in png_files
            if p.name.lower().endswith("_slo.png")
            and not p.name.lower().endswith("_slo_binary_map.png")
        )

        ground_truth_path = (
            ground_truth_candidates[0]
            if ground_truth_candidates
            else None
        )

        segmented_path = (
            segmented_candidates[0]
            if segmented_candidates
            else None
        )

        return ground_truth_path, segmented_path

    @staticmethod
    def prepare_photoimage(
        image_path: Path,
        max_size: int = MAX_DISPLAY_SIZE,
    ) -> ImageTk.PhotoImage:
        """Load and resize image for display while preserving aspect ratio."""
        with Image.open(image_path) as image:
            if image.mode not in ("L", "RGB", "RGBA"):
                image = image.convert("RGB")

            width, height = image.size
            scale = min(max_size / width, max_size / height, 1.0)

            new_width = int(width * scale)
            new_height = int(height * scale)

            if (new_width, new_height) != (width, height):
                image = image.resize(
                    (new_width, new_height),
                    Image.Resampling.LANCZOS,
                )

            return ImageTk.PhotoImage(image)

    def stop_toggle(self) -> None:
        """Stop scheduled toggle callback if active."""
        if self.toggle_job is not None:
            self.root.after_cancel(self.toggle_job)
            self.toggle_job = None

    def start_toggle(self) -> None:
        """Start image toggling."""
        self.stop_toggle()
        self.showing_ground_truth = True
        self.update_display()
        self.schedule_toggle()

    def schedule_toggle(self) -> None:
        """Schedule next toggle."""
        self.toggle_job = self.root.after(
            TOGGLE_INTERVAL_MS,
            self.toggle_image,
        )

    def toggle_image(self) -> None:
        """Toggle between ground-truth and segmented image."""
        self.showing_ground_truth = not self.showing_ground_truth
        self.update_display()
        self.schedule_toggle()

    def update_display(self) -> None:
        """Update image display."""
        if self.showing_ground_truth:
            self.image_label.config(
                image=self.current_ground_truth_photo
            )
            self.image_type_label.config(
                text=(
                    "Showing: Ground truth "
                    f"({self.current_ground_truth_path.name})"
                )
            )
        else:
            self.image_label.config(
                image=self.current_segmented_photo
            )
            self.image_type_label.config(
                text=(
                    "Showing: Segmented "
                    f"({self.current_segmented_path.name})"
                )
            )

    def load_next_valid_case(self) -> None:
        """Load next valid image pair not already copied."""
        self.stop_toggle()

        while self.case_index < len(self.case_folders):
            case_folder = self.case_folders[self.case_index]
            ground_truth_path, segmented_path = self.find_image_pair(
                case_folder
            )

            if ground_truth_path is None or segmented_path is None:
                self.missing_pair_count += 1
                self.case_index += 1
                continue

            destination_path = self.destination_folder / segmented_path.name

            if destination_path.exists():
                self.skipped_exists_count += 1
                self.case_index += 1
                continue

            self.current_case_folder = case_folder
            self.current_ground_truth_path = ground_truth_path
            self.current_segmented_path = segmented_path

            try:
                self.current_ground_truth_photo = self.prepare_photoimage(
                    ground_truth_path
                )
                self.current_segmented_photo = self.prepare_photoimage(
                    segmented_path
                )
            except Exception as exc:
                self.missing_pair_count += 1
                self.case_index += 1
                self.status_label.config(
                    text=(
                        "Status: Error loading images in "
                        f"{case_folder.name}: {exc}"
                    ),
                    fg="red",
                )
                continue

            self.case_label.config(
                text=(
                    f"Case {self.case_index + 1} of "
                    f"{len(self.case_folders)}: {case_folder.name}"
                )
            )

            self.status_label.config(
                text="Status: Ready for review.",
                fg="blue",
            )

            self.start_toggle()
            return

        self.finish_session()

    def copy_segmented_image(self) -> str:
        """Copy segmented image unless it already exists."""
        assert self.current_segmented_path is not None

        self.destination_folder.mkdir(parents=True, exist_ok=True)
        destination_path = (
            self.destination_folder /
            self.current_segmented_path.name
        )

        if destination_path.exists():
            return "skipped_exists"

        shutil.copy2(self.current_segmented_path, destination_path)
        return "copied"

    def on_copy(self) -> None:
        """Handle Copy button."""
        if self.current_segmented_path is None:
            return

        status = self.copy_segmented_image()

        if status == "copied":
            self.copied_count += 1
            self.status_label.config(
                text=f"Status: Copied {self.current_segmented_path.name}",
                fg="green",
            )
        else:
            self.skipped_exists_count += 1
            self.status_label.config(
                text=(
                    "Status: Skipped copy because destination already has "
                    f"{self.current_segmented_path.name}"
                ),
                fg="dark orange",
            )

        self.case_index += 1
        self.root.after(300, self.load_next_valid_case)

    def on_skip(self) -> None:
        """Handle Skip button."""
        self.declined_count += 1
        self.status_label.config(
            text="Status: Skipped by user.",
            fg="gray25",
        )
        self.case_index += 1
        self.root.after(300, self.load_next_valid_case)

    def on_stop(self) -> None:
        """Handle Stop button or window close."""
        self.stop_toggle()
        self.finish_session()

    def finish_session(self) -> None:
        """Finish session and show summary."""
        if self.finished:
            return

        self.finished = True
        self.stop_toggle()

        self.copy_button.config(state=tk.DISABLED)
        self.skip_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.DISABLED)

        self.case_label.config(text="All eligible cases processed.")
        self.image_type_label.config(text="Showing: None")
        self.status_label.config(
            text="Status: Review session complete. Closing...",
            fg="green",
        )
        self.image_label.config(image="")

        summary = (
            "Review session finished.\n\n"
            f"Source folder:\n{self.source_root}\n\n"
            f"Destination folder:\n{self.destination_folder}\n\n"
            f"Copied: {self.copied_count}\n"
            f"Skipped because already existed: {self.skipped_exists_count}\n"
            f"Skipped by user: {self.declined_count}\n"
            "Skipped because image pair missing/unreadable: "
            f"{self.missing_pair_count}"
        )

        messagebox.showinfo("Session summary", summary)
        self.root.after(100, self.root.destroy)


def main() -> None:
    """Main entry point."""
    if not SOURCE_FOLDER.exists():
        root = tk.Tk()
        root.withdraw()

        messagebox.showerror(
            "Source folder not found",
            "SLOctolyzer output folder was not found:\n\n"
            f"{SOURCE_FOLDER}\n\n"
            "Please run SLOctolyzer first."
        )

        root.destroy()
        return

    root = tk.Tk()

    SegmentationReviewerApp(
        root=root,
        source_root=SOURCE_FOLDER,
        destination_folder=DESTINATION_FOLDER,
    )

    root.mainloop()


if __name__ == "__main__":
    main()