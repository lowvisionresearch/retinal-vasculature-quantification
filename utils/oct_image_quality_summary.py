#!/usr/bin/env python3
"""
Summarize Heidelberg Spectralis OCT B-scan image quality.

The utility searches every immediate participant folder under
raw-oct-images/, recursively finds XML exports, extracts <ImageQuality>
from OCT images only, and writes one row per acquisition to:

    results/oct_image_quality_summary.csv

Run from any location with:

    conda activate sloctolyzer
    python utils/oct_image_quality_summary.py
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from pathlib import Path
from math import ceil
from statistics import mean



SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DEFAULT_RAW_DIR = REPO_ROOT / "raw-oct-images"
DEFAULT_OUTPUT_CSV = REPO_ROOT / "results" / "oct_image_quality_summary.csv"

PARTICIPANT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$", re.IGNORECASE)
CASE_PATTERN = re.compile(
    r"^(?P<subject>[A-Za-z0-9][A-Za-z0-9_-]*)_"
    r"(?P<eye>OD|OS)_(?P<orientation>H|V)_\d{2}$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class QualitySummary:
    """Summary statistics for one OCT acquisition."""

    case_id: str
    subject: str
    scan_orientation: str
    average_oct_image_quality_db: float
    lowest_10_percent_mean_quality_db: float
    total_scans: int


def local_name(tag: str) -> str:
    """Return an XML tag name without its namespace."""
    return tag.rsplit("}", 1)[-1]


def direct_child(element: ET.Element, name: str) -> ET.Element | None:
    """Find a direct child by namespace-independent tag name."""
    for child in element:
        if local_name(child.tag) == name:
            return child
    return None


def descendant(element: ET.Element, path: tuple[str, ...]) -> ET.Element | None:
    """Follow a direct-child path using namespace-independent tag names."""
    current = element

    for name in path:
        next_element = direct_child(current, name)

        if next_element is None:
            return None

        current = next_element

    return current


def descendant_text(
    element: ET.Element,
    path: tuple[str, ...],
) -> str | None:
    """Return stripped text from a namespace-independent descendant path."""
    node = descendant(element, path)

    if node is None or node.text is None:
        return None

    value = node.text.strip()
    return value or None


def iter_elements(root: ET.Element, name: str):
    """Yield all elements whose local tag name matches name."""
    for element in root.iter():
        if local_name(element.tag) == name:
            yield element


def file_digest(path: Path) -> str:
    """Return SHA-256 digest for deduplicating copied XML exports."""
    digest = hashlib.sha256()

    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def infer_eye(root: ET.Element) -> str:
    """Infer OD/OS from the series or image laterality."""
    for series in iter_elements(root, "Series"):
        laterality = descendant_text(series, ("Laterality",))

        if laterality:
            value = laterality.upper()

            if value in {"R", "OD", "RIGHT"}:
                return "OD"

            if value in {"L", "OS", "LEFT"}:
                return "OS"

    for image in iter_elements(root, "Image"):
        laterality = descendant_text(image, ("Laterality",))

        if laterality:
            value = laterality.upper()

            if value in {"R", "OD", "RIGHT"}:
                return "OD"

            if value in {"L", "OS", "LEFT"}:
                return "OS"

    raise ValueError("Could not determine eye laterality from the XML.")


def oct_images(root: ET.Element) -> list[ET.Element]:
    """Return only Image elements whose ImageType/Type is OCT."""
    matches: list[ET.Element] = []

    for image in iter_elements(root, "Image"):
        image_type = descendant_text(image, ("ImageType", "Type"))

        if image_type and image_type.upper() == "OCT":
            matches.append(image)

    return matches


def infer_orientation(images: list[ET.Element]) -> str:
    """
    Infer acquisition orientation from OCT B-scan start/end coordinates.

    Horizontal B-scans vary primarily along X; vertical B-scans vary
    primarily along Y.
    """
    horizontal_votes = 0
    vertical_votes = 0

    for image in images:
        paths = {
            "start_x": ("OphthalmicAcquisitionContext", "Start", "Coord", "X"),
            "start_y": ("OphthalmicAcquisitionContext", "Start", "Coord", "Y"),
            "end_x": ("OphthalmicAcquisitionContext", "End", "Coord", "X"),
            "end_y": ("OphthalmicAcquisitionContext", "End", "Coord", "Y"),
        }

        values: dict[str, float] = {}

        try:
            for key, path in paths.items():
                text = descendant_text(image, path)

                if text is None:
                    raise ValueError

                values[key] = float(text)
        except ValueError:
            continue

        delta_x = abs(values["end_x"] - values["start_x"])
        delta_y = abs(values["end_y"] - values["start_y"])

        if delta_x > delta_y:
            horizontal_votes += 1
        elif delta_y > delta_x:
            vertical_votes += 1

    if horizontal_votes == 0 and vertical_votes == 0:
        raise ValueError(
            "Could not infer scan orientation from OCT start/end coordinates."
        )

    if horizontal_votes == vertical_votes:
        raise ValueError(
            "OCT scan orientation is ambiguous: equal horizontal and vertical votes."
        )

    return "Horizontal" if horizontal_votes > vertical_votes else "Vertical"


def quality_values(images: list[ET.Element]) -> list[float]:
    """Extract numeric ImageQuality values from OCT images."""
    values: list[float] = []

    for image in images:
        quality_text = descendant_text(
            image,
            ("OphthalmicAcquisitionContext", "ImageQuality"),
        )

        if quality_text is None:
            continue

        try:
            values.append(float(quality_text))
        except ValueError as exc:
            raise ValueError(
                f"Invalid ImageQuality value: {quality_text!r}"
            ) from exc

    return values


def case_id_from_parent(
    xml_path: Path,
    subject: str,
    eye: str,
    orientation: str,
) -> str:
    """Use organized-folder case IDs; flat exports are numbered in batches."""
    parent_match = CASE_PATTERN.fullmatch(xml_path.parent.name)

    if parent_match:
        return xml_path.parent.name

    orientation_code = "H" if orientation == "Horizontal" else "V"
    return f"{subject}_{eye}_{orientation_code}_01"

def localizer_acquisition_seconds(xml_path: Path) -> float:
    """Obtain time-of-day from LOCALIZER to order flat-export repeats.

    This uses the same localizer AcquisitionTime and tie-breaking convention
    as the Heidelberg organizer, without altering quality calculations.
    """
    root = ET.parse(xml_path).getroot()
    for image in iter_elements(root, "Image"):
        typ = descendant_text(image, ("ImageType", "Type"))
        if not typ or typ.upper() != "LOCALIZER":
            continue
        path = ("AcquisitionTime", "Time")
        try:
            h = int(descendant_text(image, path + ("Hour",)) or "0")
            m = int(descendant_text(image, path + ("Minute",)) or "0")
            sec = float(descendant_text(image, path + ("Second",)) or "0")
        except ValueError as exc:
            raise ValueError(f"Invalid localizer acquisition time: {xml_path.name}") from exc
        return h * 3600 + m * 60 + sec
    raise ValueError(f"Missing LOCALIZER acquisition time: {xml_path.name}")


def number_flat_acquisition_summaries(
    xml_summaries: list[tuple[Path, QualitySummary]],
) -> list[tuple[Path, QualitySummary]]:
    """Assign distinct IDs _01..._03 for flat 12-XML exports.

    Preserve already-organized acquisition IDs and their repeat indices.
    Same-eye-and-orientation flat XMLs are ordered chronologically, with the
    XML filename as a deterministic tie breaker, matching the organizer.
    """
    by_group: dict[tuple[str, str], list[tuple[Path, QualitySummary]]] = {}
    result: list[tuple[Path, QualitySummary]] = []
    for path, summary in xml_summaries:
        parent = CASE_PATTERN.fullmatch(path.parent.name)
        if parent is not None and parent['subject'].casefold() == summary.subject.casefold():
            result.append((path, summary))
        else:
            match = CASE_PATTERN.fullmatch(summary.case_id)
            if match is None:
                raise ValueError(f"Cannot infer acquisition group: {summary.case_id}")
            key = (match['eye'].upper(), match['orientation'].upper())
            by_group.setdefault(key, []).append((path, summary))

    for (eye, orient), items in by_group.items():
        ordered = sorted(items, key=lambda item: (
            localizer_acquisition_seconds(item[0]),
            item[0].name.casefold(),
        ))
        for repeat, (path, summary) in enumerate(ordered, start=1):
            name = f"{summary.subject}_{eye}_{orient}_{repeat:02d}"
            result.append((path, replace(summary, case_id=name)))
    return result


def lowest_fraction_mean(
    values: list[float],
    fraction: float = 0.10,
) -> float:
    """Return the mean of the lowest-quality fraction of values."""
    if not values:
        raise ValueError("Cannot summarize an empty quality list.")

    if not 0 < fraction <= 1:
        raise ValueError("fraction must be greater than 0 and at most 1.")

    count = max(1, ceil(len(values) * fraction))
    lowest_values = sorted(values)[:count]

    return mean(lowest_values)

def summarize_xml(xml_path: Path, subject: str) -> QualitySummary:
    """Parse one XML acquisition and calculate OCT quality statistics."""
    try:
        root = ET.parse(xml_path).getroot()
    except ET.ParseError as exc:
        raise ValueError(f"Invalid XML: {exc}") from exc

    images = oct_images(root)

    if not images:
        raise ValueError("No OCT Image elements were found.")

    eye = infer_eye(root)
    orientation = infer_orientation(images)
    qualities = quality_values(images)

    if not qualities:
        raise ValueError("No valid OCT ImageQuality values were found.")

    case_id = case_id_from_parent(
        xml_path=xml_path,
        subject=subject,
        eye=eye,
        orientation=orientation,
    )

    return QualitySummary(
        case_id=case_id,
        subject=subject,
        scan_orientation=orientation,
        average_oct_image_quality_db=round(mean(qualities), 2),
        lowest_10_percent_mean_quality_db=round(
            lowest_fraction_mean(qualities, fraction=0.10),
            2,
        ),
        total_scans=len(qualities),
    )


def participant_xml_files(participant_dir: Path) -> list[Path]:
    """
    Return unique XML exports for one participant.

    Exact copied XML files are deduplicated. This supports both:
    - the initial flat raw Heidelberg export; and
    - participant folders after organize_heidelbergfiles.py has created
      acquisition subfolders.
    """
    candidates = sorted(
        [
            *participant_dir.rglob("*.xml"),
            *participant_dir.rglob("*.XML"),
        ],
        key=lambda path: str(path).casefold(),
    )

    unique_files: list[Path] = []
    seen_digests: set[str] = set()

    for xml_path in candidates:
        digest = file_digest(xml_path)

        if digest in seen_digests:
            continue

        seen_digests.add(digest)
        unique_files.append(xml_path)

    return unique_files


def find_participant_dirs(raw_dir: Path) -> list[Path]:
    """Return non-hidden immediate participant directories."""
    if not raw_dir.exists():
        raise FileNotFoundError(f"Raw OCT directory not found:\n{raw_dir}")

    return sorted(
        [
            path
            for path in raw_dir.iterdir()
            if path.is_dir() and not path.name.startswith(".")
        ],
        key=lambda path: path.name.casefold(),
    )


def sort_key(summary: QualitySummary) -> tuple[str, int, int, str]:
    """Sort by subject, OD before OS, horizontal before vertical."""
    eye_order = 0 if "_OD_" in summary.case_id else 1
    orientation_order = 0 if summary.scan_orientation == "Horizontal" else 1

    return (
        summary.subject.casefold(),
        eye_order,
        orientation_order,
        summary.case_id.casefold(),
    )


def format_number(value: float) -> str | float:
    """Write integer-valued measurements without a trailing .0."""
    if value.is_integer():
        return int(value)

    return value


def write_csv(
    summaries: list[QualitySummary],
    output_csv: Path,
) -> None:
    """Write the acquisition summaries to CSV."""
    output_csv.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "case_id",
        "subject",
        "scan_orientation",
        "average_oct_image_quality_db",
        "lowest_10_percent_mean_quality_db",
        "total_scans",
    ]

    with output_csv.open("w", newline="", encoding="utf-8") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=fieldnames)
        writer.writeheader()

        for summary in sorted(summaries, key=sort_key):
            writer.writerow(
                {
                    "case_id": summary.case_id,
                    "subject": summary.subject,
                    "scan_orientation": summary.scan_orientation,
                    "average_oct_image_quality_db": (
                        summary.average_oct_image_quality_db
                    ),
                    "lowest_10_percent_mean_quality_db": format_number(
                        summary.lowest_10_percent_mean_quality_db
                    ),
                    "total_scans": summary.total_scans,
                }
            )


def parse_args() -> argparse.Namespace:
    """Parse command-line options."""
    parser = argparse.ArgumentParser(
        description=(
            "Extract overall mean and lowest-10%-mean OCT B-scan "
            "image quality from Heidelberg Spectralis XML exports."
        )
    )
    parser.add_argument(
        "--raw-dir",
        type=Path,
        default=DEFAULT_RAW_DIR,
        help=f"Participant root directory (default: {DEFAULT_RAW_DIR})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_CSV,
        help=f"Output CSV path (default: {DEFAULT_OUTPUT_CSV})",
    )
    return parser.parse_args()


def main() -> int:
    """Run the image-quality summary workflow."""
    args = parse_args()
    raw_dir = args.raw_dir.resolve()
    output_csv = args.output.resolve()

    try:
        participant_dirs = find_participant_dirs(raw_dir)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if not participant_dirs:
        print(f"No participant folders found in:\n{raw_dir}")
        return 0

    summaries: list[QualitySummary] = []
    seen_case_ids: dict[str, Path] = {}
    warnings: list[str] = []

    print(f"Searching for participant XML files in:\n{raw_dir}\n")

    for participant_dir in participant_dirs:
        subject = participant_dir.name

        if not PARTICIPANT_PATTERN.fullmatch(subject):
            warnings.append(
                f"Skipped participant folder with unexpected name: "
                f"{participant_dir.name}"
            )
            continue

        xml_files = participant_xml_files(participant_dir)

        if not xml_files:
            warnings.append(f"No XML files found for {subject}.")
            continue

        parsed_for_participant: list[tuple[Path, QualitySummary]] = []
        for xml_path in xml_files:
            try:
                summary = summarize_xml(xml_path, subject)
            except (OSError, ValueError) as exc:
                warnings.append(f"Skipped {xml_path}: {exc}")
                continue
            parsed_for_participant.append((xml_path, summary))

        try:
            numbered = number_flat_acquisition_summaries(parsed_for_participant)
        except (OSError, ValueError) as exc:
            warnings.append(f"Skipped {subject}: acquisition numbering failed: {exc}")
            continue

        for xml_path, summary in numbered:
            normalized_case_id = summary.case_id.casefold()
            if normalized_case_id in seen_case_ids:
                previous = seen_case_ids[normalized_case_id]
                warnings.append(
                    f"Duplicate case ID {summary.case_id}; kept {previous} "
                    f"and skipped {xml_path}."
                )
                continue
            seen_case_ids[normalized_case_id] = xml_path
            summaries.append(summary)
            print(
                f"[OK] {summary.case_id}: "
                f"mean={summary.average_oct_image_quality_db:.2f} dB, "
                f"lowest-10%-mean="
                f"{summary.lowest_10_percent_mean_quality_db:.2f} dB, "
                f"scans={summary.total_scans}"
            )

    if not summaries:
        print("\nNo valid OCT image-quality summaries were produced.")
        for warning in warnings:
            print(f"WARNING: {warning}", file=sys.stderr)
        return 1

    write_csv(summaries, output_csv)

    print(f"\nSaved {len(summaries)} acquisition row(s) to:\n{output_csv}")

    if warnings:
        print(f"\nCompleted with {len(warnings)} warning(s):")
        for warning in warnings:
            print(f"- {warning}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
