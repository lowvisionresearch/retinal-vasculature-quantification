#!/usr/bin/env python3
"""Organize Heidelberg Spectralis OCT exports for aging or refractive studies.

Supported participant-level protocols (97 B-scans per acquisition):
- Aging: 1 acquisition for each OD/OS x H/V group = 4 XMLs + 392 TIFFs
  = 396 flat-export files total.
- Refractive error: 3 repeated acquisitions for each group = 12 XMLs
  + 1176 TIFFs = 1188 flat-export files total.

Flat exports are organized into <participant>_<OD|OS>_<H|V>_<01..03>.
Repeated acquisitions within each eye/orientation are numbered by localizer
acquisition time, then XML filename to break time ties deterministically.
Already-organized acquisitions are validated and left untouched. No source
images are deleted; no image processing or numerical measurements occur here.
"""

from __future__ import annotations

import re
import shutil
import sys
import tkinter as tk
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox
import xml.etree.ElementTree as ET


TOL_COORD_COMPARE = 1e-6
EXPECTED_BSCAN_COUNT = 97
ACQUISITION_GROUPS = (("OD", "H"), ("OD", "V"), ("OS", "H"), ("OS", "V"))
SUPPORTED_REPEATS_PER_GROUP = (1, 3)
SUPPORTED_XML_COUNTS = tuple(4 * n for n in SUPPORTED_REPEATS_PER_GROUP)

PARTICIPANT_FOLDER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


@dataclass
class ScanRecord:
    """Parsed information for one XML / one OCT acquisition."""

    xml_path: Path
    laterality_raw: str
    eye_label: str
    orientation_label: str
    acquisition_time: datetime
    localizer_filename: str
    bscan_filenames: list[str]


def create_hidden_root() -> tk.Tk:
    """Create a hidden Tk root window for dialogs."""

    root = tk.Tk()
    root.withdraw()

    try:
        root.attributes("-topmost", True)
    except tk.TclError:
        pass

    return root


def choose_participant_folder() -> Path | None:
    """Open a GUI dialog for the user to select a participant folder."""

    root = create_hidden_root()
    folder = filedialog.askdirectory(
        title="Select an OCT participant folder"
    )
    root.destroy()

    if not folder:
        return None

    return Path(folder)


def validate_participant_folder_name(participant_dir: Path) -> str:
    """Validate a de-identified participant folder name for downstream case IDs."""
    if not participant_dir.is_dir():
        raise ValueError(f"Participant folder does not exist: {participant_dir}")

    participant_id = participant_dir.name
    if not PARTICIPANT_FOLDER_PATTERN.fullmatch(participant_id):
        raise ValueError(
            "Invalid participant folder name. Use 1–64 characters: letters, "
            "numbers, underscores, or hyphens, beginning with a letter or number. "
            "Examples: AG_02_001, SAMPLE_001. "
            f"Found: {participant_id!r}"
        )

    # A case suffix is reserved for scan metadata, not part of the participant ID.
    if re.search(r"_(?:OD|OS)_(?:H|V)_\d{2}$", participant_id, re.I):
        raise ValueError(
            "Participant name must not end with an acquisition suffix "
            "like _OD_H_01: " + participant_id
        )
    return participant_id



def find_files_by_extensions(
    folder: Path,
    extensions: tuple[str, ...],
) -> list[Path]:
    """Find top-level files with any of the given extensions without duplicates."""

    files_by_path: dict[str, Path] = {}

    for ext in extensions:
        for file_path in folder.glob(f"*{ext}"):
            # Normalize the path for case-insensitive Windows/macOS filesystems.
            key = str(file_path.resolve()).lower()
            files_by_path[key] = file_path

    return sorted(files_by_path.values())


def parse_examurl_filename(exam_url: str) -> str:
    """
    Extract the filename from a Heidelberg ExamURL.

    Example:
    file:///C:\\Users\\HE-ADMIN\\Desktop\\EXPORT\\2DA4DC80.tif
    -> 2DA4DC80.tif
    """

    exam_url = exam_url.strip().replace("\\", "/")
    return Path(exam_url).name


def safe_text(element: ET.Element | None, default: str = "") -> str:
    """Return stripped element text, or a default if it is missing."""

    if element is None or element.text is None:
        return default

    return element.text.strip()


def parse_acquisition_datetime(image_elem: ET.Element) -> datetime:
    """
    Parse the acquisition time from an Image block.

    A dummy date is used because only the time is relevant.
    """

    acq = image_elem.find("AcquisitionTime/Time")

    if acq is None:
        raise ValueError("Missing AcquisitionTime/Time in XML.")

    hour = int(safe_text(acq.find("Hour"), "0"))
    minute = int(safe_text(acq.find("Minute"), "0"))
    second_float = float(safe_text(acq.find("Second"), "0"))

    second = int(second_float)
    microsecond = int(round((second_float - second) * 1_000_000))

    # Protect against floating-point rounding to exactly 1,000,000 microseconds.
    if microsecond >= 1_000_000:
        second += 1
        microsecond = 0

    return datetime(2000, 1, 1, hour, minute, second, microsecond)


def infer_orientation_from_oct_images(oct_images: list[ET.Element]) -> str:
    """
    Infer OCT orientation using Start/End coordinate logic.

    Logic:
    - Use the first OCT B-scan:
        if |deltaX| > |deltaY| -> horizontal
        if |deltaY| > |deltaX| -> vertical
    - If nearly equal, compare variation across the stack:
        if rangeStartY > rangeStartX -> horizontal
        otherwise -> vertical
    """

    if not oct_images:
        raise ValueError("No OCT image blocks found in XML.")

    start_x_vals: list[float] = []
    start_y_vals: list[float] = []
    end_x_vals: list[float] = []
    end_y_vals: list[float] = []

    for image_elem in oct_images:
        context = image_elem.find("OphthalmicAcquisitionContext")

        if context is None:
            raise ValueError(
                "Missing OphthalmicAcquisitionContext for an OCT image."
            )

        start = context.find("Start/Coord")
        end = context.find("End/Coord")

        if start is None or end is None:
            raise ValueError("Missing Start/End coordinates for an OCT image.")

        start_x = float(safe_text(start.find("X")))
        start_y = float(safe_text(start.find("Y")))
        end_x = float(safe_text(end.find("X")))
        end_y = float(safe_text(end.find("Y")))

        start_x_vals.append(start_x)
        start_y_vals.append(start_y)
        end_x_vals.append(end_x)
        end_y_vals.append(end_y)

    delta_x = abs(end_x_vals[0] - start_x_vals[0])
    delta_y = abs(end_y_vals[0] - start_y_vals[0])

    if abs(delta_x - delta_y) < TOL_COORD_COMPARE:
        range_start_x = max(start_x_vals) - min(start_x_vals)
        range_start_y = max(start_y_vals) - min(start_y_vals)

        return "H" if range_start_y > range_start_x else "V"

    return "H" if delta_x > delta_y else "V"


def parse_xml_file(xml_path: Path) -> ScanRecord:
    """Parse one Heidelberg XML file into a ScanRecord."""

    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
    except ET.ParseError as exc:
        raise ValueError(f"Could not parse XML: {xml_path.name}") from exc

    image_elems = root.findall(".//Image")

    if not image_elems:
        raise ValueError(f"No <Image> blocks found in {xml_path.name}")

    localizer_elem: ET.Element | None = None
    oct_image_elems: list[ET.Element] = []

    for image_elem in image_elems:
        image_type = safe_text(image_elem.find("ImageType/Type")).upper()

        if image_type == "LOCALIZER" and localizer_elem is None:
            localizer_elem = image_elem
        elif image_type == "OCT":
            oct_image_elems.append(image_elem)

    if localizer_elem is None:
        raise ValueError(f"No LOCALIZER image found in {xml_path.name}")

    if len(oct_image_elems) != EXPECTED_BSCAN_COUNT:
        raise ValueError(
            f"{xml_path.name}: expected {EXPECTED_BSCAN_COUNT} OCT B-scans, "
            f"found {len(oct_image_elems)}"
        )

    laterality_raw = safe_text(localizer_elem.find("Laterality")).upper()

    if laterality_raw == "R":
        eye_label = "OD"
    elif laterality_raw == "L":
        eye_label = "OS"
    else:
        raise ValueError(
            f"{xml_path.name}: unexpected Laterality='{laterality_raw}'"
        )

    acquisition_time = parse_acquisition_datetime(localizer_elem)

    localizer_examurl = safe_text(localizer_elem.find("ImageData/ExamURL"))

    if not localizer_examurl:
        raise ValueError(f"{xml_path.name}: LOCALIZER ExamURL missing")

    localizer_filename = parse_examurl_filename(localizer_examurl)

    bscan_filenames: list[str] = []

    for image_elem in oct_image_elems:
        examurl = safe_text(image_elem.find("ImageData/ExamURL"))

        if not examurl:
            raise ValueError(f"{xml_path.name}: OCT ExamURL missing")

        bscan_filenames.append(parse_examurl_filename(examurl))

    orientation_label = infer_orientation_from_oct_images(oct_image_elems)

    return ScanRecord(
        xml_path=xml_path,
        laterality_raw=laterality_raw,
        eye_label=eye_label,
        orientation_label=orientation_label,
        acquisition_time=acquisition_time,
        localizer_filename=localizer_filename,
        bscan_filenames=bscan_filenames,
    )


def validate_source_files(
    participant_dir: Path,
    record: ScanRecord,
) -> list[str]:
    """Check whether every file referenced by one XML exists."""

    missing: list[str] = []

    localizer_path = participant_dir / record.localizer_filename

    if not localizer_path.exists():
        missing.append(record.localizer_filename)

    for filename in record.bscan_filenames:
        if not (participant_dir / filename).exists():
            missing.append(filename)

    return missing


def build_output_folder_name(
    participant_id: str,
    eye_label: str,
    orientation_label: str,
    repeat_index: int = 1,
) -> str:
    """Build a unique case ID such as MY_02_001_OD_H_03."""
    if not 1 <= repeat_index <= 99:
        raise ValueError(f"Invalid acquisition repeat index: {repeat_index}")
    return f"{participant_id}_{eye_label}_{orientation_label}_{repeat_index:02d}"


def group_acquisitions(
    records: list[ScanRecord],
) -> tuple[dict[tuple[str, str], list[ScanRecord]], int]:
    """Validate complete 4- or 12-acquisition protocol and assign stable order."""
    grouped: dict[tuple[str, str], list[ScanRecord]] = defaultdict(list)
    for record in records:
        group = (record.eye_label, record.orientation_label)
        if group not in ACQUISITION_GROUPS:
            raise ValueError(f"Unexpected eye/orientation group: {group}")
        grouped[group].append(record)

    per_group = {group: len(grouped[group]) for group in ACQUISITION_GROUPS}
    sizes = set(per_group.values())
    if len(sizes) != 1 or next(iter(sizes)) not in SUPPORTED_REPEATS_PER_GROUP:
        summary = ", ".join(f"{eye}_{orient}={per_group[eye, orient]}"
                            for eye, orient in ACQUISITION_GROUPS)
        raise ValueError(
            "Expected exactly 1 or 3 acquisitions in EACH OD/OS x H/V "
            f"group (4 or 12 total); found {summary}. No files changed."
        )

    repeat_count = next(iter(sizes))
    for group in ACQUISITION_GROUPS:
        grouped[group].sort(key=lambda record: (
            record.acquisition_time,
            record.xml_path.name.casefold(),
        ))
    return grouped, repeat_count


def write_summary_file(
    participant_dir: Path,
    participant_id: str,
    grouped: dict[tuple[str, str], list[ScanRecord]],
) -> Path:
    """Write an auditable acquisition-to-folder mapping for all scan repeats."""
    summary_path = participant_dir / f"{participant_id}_organization_summary.txt"
    lines = [f"Participant: {participant_id}", "=" * 60, ""]

    for eye, orient in ACQUISITION_GROUPS:
        for repeat, record in enumerate(grouped[(eye, orient)], start=1):
            name = build_output_folder_name(participant_id, eye, orient, repeat)
            lines += [
                f"{eye}_{orient}_{repeat:02d}",
                "-" * 40,
                f"  Output Folder : {name}",
                f"  XML File      : {record.xml_path.name}",
                f"  Laterality    : {record.laterality_raw}",
                f"  Orientation   : {record.orientation_label}",
                f"  Localizer     : {record.localizer_filename}",
                f"  B-scans       : {len(record.bscan_filenames)} files",
                "  Acquisition   : "
                + record.acquisition_time.strftime('%H:%M:%S.%f')[:-3],
                "",
            ]
    with summary_path.open("w", encoding="utf-8") as file_obj:
        file_obj.write("\n".join(lines))
    return summary_path


def validate_organized_acquisitions(
    participant_dir: Path,
    participant_id: str,
) -> bool:
    """Validate complete organized 4/12-case folders without altering them."""
    pattern = re.compile(
        rf"^{re.escape(participant_id)}_(?P<eye>OD|OS)_"
        r"(?P<orient>H|V)_(?P<repeat>\d{2})$", re.I,
    )
    folders: dict[tuple[str, str, int], Path] = {}
    for folder in participant_dir.iterdir():
        if not folder.is_dir():
            continue
        matched = pattern.fullmatch(folder.name)
        if matched is None:
            # Never silently ignore a malformed attempted scan directory.
            prefix = participant_id.casefold() + "_"
            suffix = folder.name.casefold()[len(prefix):] if folder.name.casefold().startswith(prefix) else ""
            if suffix.startswith(("od_", "os_")):
                raise ValueError(f"Unexpected acquisition folder name: {folder.name}")
            continue
        key = (matched['eye'].upper(), matched['orient'].upper(), int(matched['repeat']))
        if key in folders:
            raise ValueError(f"Duplicate acquisition folder: {folder.name}")
        folders[key] = folder

    if not folders:
        return False

    repeats = {index for _, _, index in folders}
    expected_repeats = len(repeats)
    if expected_repeats not in SUPPORTED_REPEATS_PER_GROUP:
        raise ValueError(
            f"Partially or incorrectly organized acquisitions: repeat indices {sorted(repeats)}. "
            "Expected 01, or 01–03; no files were changed."
        )
    expected_indices = set(range(1, expected_repeats + 1))
    if repeats != expected_indices:
        raise ValueError(
            f"Invalid repeat indices {sorted(repeats)}; expected "
            f"{sorted(expected_indices)}. No files were changed."
        )
    expected_keys = {(eye, orient, repeat)
                     for eye, orient in ACQUISITION_GROUPS
                     for repeat in expected_indices}
    if set(folders) != expected_keys:
        missing = sorted(expected_keys - set(folders))
        raise ValueError(
            "Participant appears partially organized. Missing acquisition "
            f"folder(s): {missing}. No files were changed."
        )

    for (eye, orient, repeat), folder in sorted(folders.items()):
        xml_files = find_files_by_extensions(folder, (".xml", ".XML"))
        if len(xml_files) != 1:
            raise ValueError(f"{folder.name}: expected 1 XML file, found {len(xml_files)}")
        localizers = [p for p in folder.iterdir() if p.is_file()
                      and p.name.lower() in {"oct.tif", "oct.tiff"}]
        if len(localizers) != 1:
            raise ValueError(f"{folder.name}: expected one OCT.tif localizer")
        bscans_dir = folder / "bscans"
        if not bscans_dir.is_dir():
            raise ValueError(f"{folder.name}: missing bscans/ directory")
        bscans = find_files_by_extensions(bscans_dir, (".tif", ".TIF", ".tiff", ".TIFF"))
        if len(bscans) != EXPECTED_BSCAN_COUNT:
            raise ValueError(
                f"{folder.name}: expected {EXPECTED_BSCAN_COUNT} B-scans, "
                f"found {len(bscans)}"
            )
        record = parse_xml_file(xml_files[0])
        if (record.eye_label, record.orientation_label) != (eye, orient):
            raise ValueError(
                f"{folder.name}: XML describes {record.eye_label}_{record.orientation_label}, "
                f"expected {eye}_{orient}"
            )
        actual_names = {f.name.casefold() for f in bscans}
        expected_names = {name.casefold() for name in record.bscan_filenames}
        if len(expected_names) != EXPECTED_BSCAN_COUNT or actual_names != expected_names:
            raise ValueError(f"{folder.name}: B-scans do not match XML filenames")

    # Guard against a stale 4-scan organized set alongside a newly supplied
    # 12-scan raw export (or vice versa) being silently treated as complete.
    raw_xmls = find_files_by_extensions(participant_dir, (".xml", ".XML"))
    if raw_xmls and len(raw_xmls) != len(folders):
        raise ValueError(
            f"Organized participant has {len(folders)} acquisitions but "
            f"{len(raw_xmls)} top-level XMLs. Mixed protocol or stale "
            "organized folders; review before continuing."
        )
    return True


def organize_participant_folder(participant_dir: Path) -> None:
    """Validate organized acquisitions, or organize a flat 4/12-case export."""
    participant_id = validate_participant_folder_name(participant_dir)
    if validate_organized_acquisitions(participant_dir, participant_id):
        print(f"[SKIPPED] {participant_id}: already organized and validated.")
        return

    xml_files = find_files_by_extensions(participant_dir, (".xml", ".XML"))
    tif_files = find_files_by_extensions(participant_dir, (".tif", ".TIF", ".tiff", ".TIFF"))

    if len(xml_files) not in SUPPORTED_XML_COUNTS:
        raise ValueError(
            f"Expected exactly 4 or 12 XML files, found {len(xml_files)} "
            f"in {participant_dir}. No files changed."
        )
    expected_tiffs = len(xml_files) * (EXPECTED_BSCAN_COUNT + 1)
    if len(tif_files) != expected_tiffs:
        raise ValueError(
            f"Expected exactly {expected_tiffs} TIF images "
            f"({len(xml_files)} localizers + "
            f"{len(xml_files) * EXPECTED_BSCAN_COUNT} B-scans), "
            f"found {len(tif_files)} in {participant_dir}. No files changed."
        )

    records: list[ScanRecord] = []
    all_missing: list[str] = []
    for xml_path in xml_files:
        record = parse_xml_file(xml_path)
        records.append(record)
        missing = validate_source_files(participant_dir, record)
        if missing:
            preview = ", ".join(missing[:10])
            suffix = " ..." if len(missing) > 10 else ""
            all_missing.append(
                f"{xml_path.name}: missing {len(missing)} referenced file(s): "
                f"{preview}{suffix}"
            )
    if all_missing:
        raise FileNotFoundError(
            "Some files referenced by the XML files were not found.\n\n"
            + "\n".join(all_missing)
        )

    all_references = [
        name.casefold()
        for record in records
        for name in [record.localizer_filename, *record.bscan_filenames]
    ]
    if len(set(all_references)) != expected_tiffs:
        raise ValueError(
            "Duplicate image filenames referenced across acquisitions; "
            f"expected {expected_tiffs} unique TIF references. No files changed."
        )

    grouped, repeats = group_acquisitions(records)

    created_folders: list[str] = []
    for eye, orient in ACQUISITION_GROUPS:
        for repeat, record in enumerate(grouped[(eye, orient)], start=1):
            name = build_output_folder_name(participant_id, eye, orient, repeat)
            out_dir = participant_dir / name
            if out_dir.exists():
                raise ValueError(
                    f"Refusing to overwrite existing acquisition folder {out_dir}"
                )
            bscans_dir = out_dir / "bscans"
            bscans_dir.mkdir(parents=True, exist_ok=False)
            shutil.copy2(record.xml_path, out_dir / record.xml_path.name)
            shutil.copy2(participant_dir / record.localizer_filename, out_dir / "OCT.tif")
            for bscan_name in record.bscan_filenames:
                shutil.copy2(participant_dir / bscan_name, bscans_dir / bscan_name)
            created_folders.append(name)

    summary_path = write_summary_file(participant_dir, participant_id, grouped)
    summary_lines = [
        f"Participant: {participant_id}",
        f"Protocol: {repeats} repeat(s) per eye/orientation",
        f"XML files found: {len(xml_files)}",
        f"TIF images found: {len(tif_files)}",
        "Created folders:",
        *[f"  - {name}" for name in created_folders],
        "", "Summary file written to:", f"  {summary_path.name}",
    ]
    summary_text = "\n".join(summary_lines)
    print(summary_text)
    root = create_hidden_root()
    messagebox.showinfo("Done", summary_text)
    root.destroy()


def main() -> None:
    """Run the GUI-based Heidelberg participant organizer."""

    participant_dir = choose_participant_folder()

    if participant_dir is None:
        print("Folder selection canceled.")
        return

    try:
        organize_participant_folder(participant_dir)
    except Exception as exc:
        error_msg = f"Error:\n\n{exc}"
        print(error_msg, file=sys.stderr)

        root = create_hidden_root()
        messagebox.showerror("Error", error_msg)
        root.destroy()


if __name__ == "__main__":
    main()
