#!/usr/bin/env python3
"""
Copy and rename OCT.tif files from a selected participant folder,
then append scale, location, and eye metadata rows to
fname_resolution_location_eye.xlsx.

Cross-platform:
- Windows
- macOS

Destination folder is automatically inferred relative
to this script location:

YourProject/
├── utils/
│   └── oct_copier.py
├── models/
│   └── SLOctolyzer/
│       └── analyze/
│           └── demo/
"""

from pathlib import Path
import shutil
import csv
import math
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog
import xml.etree.ElementTree as ET

from openpyxl import Workbook, load_workbook


# ---------------------------------------------------------------------
# Auto-detect repo structure
# ---------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

METADATA_FOLDER = REPO_ROOT / "metadata"
AXIAL_LENGTH_FILE = METADATA_FOLDER / "axial_lengths.csv"
# Opt-in published example values; runtime metadata always takes precedence.
EXAMPLE_AXIAL_LENGTH_FILE = REPO_ROOT / "examples" / "axial_lengths_sample.csv"

DESTINATION_FOLDER = (
    REPO_ROOT /
    "models" /
    "SLOctolyzer" /
    "analyze" /
    "demo"
)

EXCEL_FILE = (
    DESTINATION_FOLDER /
    "fname_resolution_location_eye.xlsx"
)

METADATA_SHEET = "fname_resolution_location_eye"
METADATA_HEADERS = ("Filename", "Scale", "Location", "Eye")


# Littmann-Bennett constants
C1 = 3.3944
C2 = 0.01306
C3 = 1.82


def create_hidden_root() -> tk.Tk:
    """Create hidden Tk root for dialogs."""
    root = tk.Tk()
    root.withdraw()

    try:
        root.attributes("-topmost", True)
    except tk.TclError:
        pass

    return root


def select_participant_folder() -> Path | None:
    """Ask user to select participant folder."""
    root = create_hidden_root()

    selected_folder = filedialog.askdirectory(
        title="Select participant folder (e.g. MY_02_001)"
    )

    root.destroy()

    if not selected_folder:
        return None

    return Path(selected_folder)


def ask_axial_lengths() -> tuple[float, float] | None:
    """Ask user for OD and OS axial lengths."""
    root = create_hidden_root()

    od_al = simpledialog.askfloat(
        "OD Axial Length",
        "Enter axial length for OD / Right eye in mm:",
        parent=root,
    )

    if od_al is None:
        root.destroy()
        return None

    os_al = simpledialog.askfloat(
        "OS Axial Length",
        "Enter axial length for OS / Left eye in mm:",
        parent=root,
    )

    root.destroy()

    if os_al is None:
        return None

    return od_al, os_al

def save_axial_lengths(
    participant_id: str,
    od_al: float,
    os_al: float,
) -> None:
    """Save or update participant axial lengths in a shared CSV file."""
    if not (math.isfinite(od_al) and math.isfinite(os_al)) or min(od_al, os_al) <= C3:
        raise ValueError("Axial lengths must be finite and greater than 1.82 mm.")
    METADATA_FOLDER.mkdir(parents=True, exist_ok=True)

    rows: dict[str, dict[str, str]] = {}

    if AXIAL_LENGTH_FILE.exists():
        with open(AXIAL_LENGTH_FILE, "r", newline="", encoding="utf-8") as file_obj:
            reader = csv.DictReader(file_obj)

            for row in reader:
                rows[row["ParticipantID"]] = row

    rows[participant_id] = {
        "ParticipantID": participant_id,
        "OD_AL": f"{od_al:.6f}",
        "OS_AL": f"{os_al:.6f}",
    }

    with open(AXIAL_LENGTH_FILE, "w", newline="", encoding="utf-8") as file_obj:
        fieldnames = ["ParticipantID", "OD_AL", "OS_AL"]
        writer = csv.DictWriter(file_obj, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows.values())


def load_axial_lengths(
    participant_id: str,
) -> tuple[float, float] | None:
    """Load stored OD and OS axial lengths for a participant."""
    # First use the user's working registry, then the approved sample file.
    # Never load unrelated cohort-level measurements into the public release.
    for candidate in (AXIAL_LENGTH_FILE, EXAMPLE_AXIAL_LENGTH_FILE):
        if not candidate.exists():
            continue
        found = _load_axial_lengths_from_csv(candidate, participant_id)
        if found is not None:
            return found
    return None


def _load_axial_lengths_from_csv(
    csv_file: Path, participant_id: str
) -> tuple[float, float] | None:
    with open(
        csv_file,
        "r",
        newline="",
        encoding="utf-8-sig",
    ) as file_obj:
        reader = csv.DictReader(file_obj)

        required_columns = {
            "ParticipantID",
            "OD_AL",
            "OS_AL",
        }

        if reader.fieldnames is None:
            raise ValueError(
                f"Axial-length CSV has no header:\n{csv_file}"
            )

        missing_columns = required_columns - set(reader.fieldnames)

        if missing_columns:
            raise ValueError(
                "Axial-length CSV is missing required column(s): "
                + ", ".join(sorted(missing_columns))
                + f"\n\nFile:\n{csv_file}"
            )

        target_id = participant_id.strip().casefold()

        for row in reader:
            stored_id = str(row.get("ParticipantID", "")).strip()

            if stored_id.casefold() != target_id:
                continue

            try:
                od_al = float(str(row["OD_AL"]).strip())
                os_al = float(str(row["OS_AL"]).strip())
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"Invalid axial-length values for {participant_id} "
                    f"in:\n{csv_file}"
                ) from exc

            if not (math.isfinite(od_al) and math.isfinite(os_al)):
                raise ValueError(f"Non-finite axial length for {participant_id} in {csv_file}")
            if od_al <= C3 or os_al <= C3:
                raise ValueError(f"Axial lengths must exceed {C3} mm in {csv_file}")
            return od_al, os_al

    return None

def compute_axial_correction_factor(
    axial_length: float,
) -> float:
    """Compute Littmann-Bennett correction."""
    return C1 * C2 * (axial_length - C3)


def infer_eye_from_filename(
    filename: str,
) -> str | None:
    """Infer eye from filename."""
    filename_upper = filename.upper()

    if "_OD_" in filename_upper:
        return "Right"

    if "_OS_" in filename_upper:
        return "Left"

    return None


def find_xml_file(folder: Path) -> Path | None:
    """Find XML file in folder."""
    xml_files = sorted(
        list(folder.glob("*.xml"))
        + list(folder.glob("*.XML"))
    )

    if len(xml_files) == 0:
        return None

    if len(xml_files) > 1:
        print(
            f"[WARNING] More than one XML file found "
            f"in {folder}. Using first one: "
            f"{xml_files[0].name}"
        )

    return xml_files[0]


def find_oct_file(folder: Path) -> Path | None:
    """Find OCT file regardless of case."""
    candidates = [
        "OCT.tif",
        "OCT.TIF",
        "OCT.tiff",
        "OCT.TIFF",
    ]

    for name in candidates:
        candidate = folder / name

        if candidate.exists():
            return candidate

    return None


def extract_localizer_scale_um(
    xml_file: Path,
) -> float:
    """
    Extract LOCALIZER IR ScaleX from XML.

    XML ScaleX is mm/pixel.
    Convert to microns/pixel.
    """
    tree = ET.parse(xml_file)
    root = tree.getroot()

    for image in root.findall(".//Image"):
        image_type = image.find("ImageType")

        if image_type is None:
            continue

        type_node = image_type.find("Type")
        light_node = image_type.find("LightSource")

        type_text = (
            type_node.text.strip()
            if type_node is not None
            and type_node.text
            else ""
        )

        light_text = (
            light_node.text.strip()
            if light_node is not None
            and light_node.text
            else ""
        )

        if (
            type_text == "LOCALIZER"
            and light_text == "IR"
        ):
            context = image.find(
                "OphthalmicAcquisitionContext"
            )

            if context is None:
                raise ValueError(
                    "Missing OphthalmicAcquisitionContext"
                )

            scalex_node = context.find("ScaleX")
            scaley_node = context.find("ScaleY")

            if (
                scalex_node is None
                or scalex_node.text is None
            ):
                raise ValueError(
                    f"ScaleX not found in {xml_file.name}"
                )

            scalex_mm = float(
                scalex_node.text.strip()
            )

            if (
                scaley_node is not None
                and scaley_node.text
            ):
                scaley_mm = float(
                    scaley_node.text.strip()
                )

                if abs(
                    scalex_mm - scaley_mm
                ) > 1e-6:
                    print(
                        "[WARNING] "
                        f"ScaleX != ScaleY "
                        f"in {xml_file.name}. "
                        "Using ScaleX."
                    )

            return scalex_mm * 1000.0

    raise ValueError(
        "No LOCALIZER / IR image block found "
        f"in {xml_file}"
    )


def ensure_metadata_workbook() -> Path:
    """Create the SLOctolyzer image metadata workbook when missing.

    SLOctolyzer reads the *first* worksheet with pandas.read_excel().
    Never replace an existing workbook or silently change its metadata.
    """
    EXCEL_FILE.parent.mkdir(parents=True, exist_ok=True)

    if not EXCEL_FILE.exists():
        wb = Workbook()
        ws = wb.active
        ws.title = METADATA_SHEET
        ws.append(list(METADATA_HEADERS))
        wb.save(EXCEL_FILE)
        wb.close()
        print(f"[CREATED] SLOctolyzer metadata workbook: {EXCEL_FILE}")
        return EXCEL_FILE

    wb = load_workbook(EXCEL_FILE)
    try:
        if not wb.worksheets or wb.worksheets[0].title != METADATA_SHEET:
            raise ValueError(
                f"SLOctolyzer expects its first worksheet to be "
                f"'{METADATA_SHEET}' in:\n{EXCEL_FILE}"
            )

        ws = wb[METADATA_SHEET]
        actual_headers = tuple(ws.cell(row=1, column=i).value for i in range(1, 5))
        if actual_headers != METADATA_HEADERS:
            raise ValueError(
                f"Unexpected columns in SLOctolyzer metadata workbook:\n"
                f"{EXCEL_FILE}\nExpected: {METADATA_HEADERS}\n"
                f"Found: {actual_headers}"
            )
    finally:
        wb.close()

    return EXCEL_FILE


def append_metadata_rows(rows: list[list]) -> None:
    """Insert or update metadata by filename; never append duplicate case rows."""
    ensure_metadata_workbook()

    if not rows:
        print("No metadata rows to update.")
        return

    wb = load_workbook(EXCEL_FILE)
    try:
        ws = wb[METADATA_SHEET]
        row_by_name: dict[str, int] = {}
        for row_number in range(2, ws.max_row + 1):
            name = ws.cell(row=row_number, column=1).value
            if name is not None and str(name).strip():
                row_by_name[str(name).strip().casefold()] = row_number

        for row in rows:
            if len(row) != len(METADATA_HEADERS):
                raise ValueError(f"Expected 4 metadata values, got: {row!r}")
            name = str(row[0]).strip()
            if not name:
                raise ValueError("SLOctolyzer metadata filename cannot be empty.")

            key = name.casefold()
            row_number = row_by_name.get(key)
            if row_number is None:
                row_number = ws.max_row + 1
                row_by_name[key] = row_number

            for col_number, value in enumerate(row, start=1):
                ws.cell(row=row_number, column=col_number, value=value)

        wb.save(EXCEL_FILE)
    finally:
        wb.close()


def copy_oct_files(
    participant_folder: Path,
    destination_folder: Path,
) -> int:
    """Copy OCT files and metadata; return number of successfully staged cases."""

    if not participant_folder.is_dir():
        raise FileNotFoundError(f"Participant folder does not exist: {participant_folder}")

    participant_id = participant_folder.name

    axial_lengths = load_axial_lengths(
        participant_id
    )

    if axial_lengths is None:
        print(
            f"No stored axial lengths found for "
            f"{participant_id}."
        )

        axial_lengths = ask_axial_lengths()

        if axial_lengths is None:
            raise RuntimeError(f"Axial-length entry cancelled for {participant_id}; no processing performed.")

        od_al, os_al = axial_lengths

        save_axial_lengths(
            participant_id,
            od_al,
            os_al,
        )

        print(
            f"Saved axial lengths for "
            f"{participant_id}."
        )
    else:
        od_al, os_al = axial_lengths

        # The bundled examples CSV is a read-only distribution artifact.
        # The downstream vascular-metrics module reads ONLY the working CSV,
        # so copy the chosen example's values into that registry on first use.
        working_values = (
            _load_axial_lengths_from_csv(AXIAL_LENGTH_FILE, participant_id)
            if AXIAL_LENGTH_FILE.exists() else None
        )
        if working_values is None:
            save_axial_lengths(participant_id, od_al, os_al)
            print(f"Initialized working axial lengths for {participant_id}.")

        print(
            f"Using stored axial lengths for "
            f"{participant_id}: "
            f"OD={od_al:.6f} mm, "
            f"OS={os_al:.6f} mm"
        )

    od_factor = compute_axial_correction_factor(
        od_al
    )
    os_factor = compute_axial_correction_factor(
        os_al
    )

    destination_folder.mkdir(
        parents=True,
        exist_ok=True,
    )
    ensure_metadata_workbook()

    copied_count = 0
    skipped_count = 0
    metadata_rows = []

    print(
        f"\nDestination:\n"
        f"{destination_folder}\n"
    )

    for item in sorted(
        participant_folder.iterdir()
    ):
        if not item.is_dir():
            continue

        subfolder_name = item.name

        source_file = find_oct_file(item)

        if source_file is None:
            print(
                f"[MISSING] OCT file "
                f"not found in {item}"
            )
            continue

        destination_file = (
            destination_folder /
            f"{subfolder_name}.tif"
        )

        # Even when a localizer is already staged, its metadata must be
        # registered (e.g., when re-creating a missing workbook).
        already_copied = destination_file.exists()

        xml_file = find_xml_file(item)

        if xml_file is None:
            print(
                f"[MISSING XML] {item}"
            )
            continue

        eye = infer_eye_from_filename(
            destination_file.name
        )

        if eye is None:
            print(
                f"[WARNING] Could not infer eye "
                f"from filename"
            )
            continue

        device_scale_um = (
            extract_localizer_scale_um(
                xml_file
            )
        )

        final_scale = (
            device_scale_um * od_factor
            if eye == "Right"
            else device_scale_um * os_factor
        )

        if already_copied:
            print(f"[SKIPPED] Already staged: {destination_file.name}")
            skipped_count += 1
        else:
            shutil.copy2(source_file, destination_file)
            copied_count += 1
            print(f"[COPIED] {destination_file.name}")

        metadata_rows.append([
            destination_file.name,
            round(final_scale, 6),
            "Macula",
            eye,
        ])

    append_metadata_rows(
        metadata_rows
    )

    summary = (
        f"Finished.\n\n"
        f"Destination:\n"
        f"{destination_folder}\n\n"
        f"Copied: {copied_count}\n"
        f"Skipped existing: "
        f"{skipped_count}\n"
        f"Metadata rows added: "
        f"{len(metadata_rows)}"
    )

    print(summary)

    root = create_hidden_root()
    messagebox.showinfo(
        "Copy complete",
        summary,
    )
    root.destroy()
    return copied_count + skipped_count


def main() -> None:
    """Run program."""
    participant_folder = (
        select_participant_folder()
    )

    if participant_folder is None:
        print(
            "No folder selected."
        )
        return

    copy_oct_files(
        participant_folder,
        DESTINATION_FOLDER,
    )


if __name__ == "__main__":
    main()