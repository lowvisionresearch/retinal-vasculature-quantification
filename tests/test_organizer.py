"""Synthetic regression tests; no real research scans or participant metadata."""
from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from utils import organize_heidelbergfiles as org
from utils import oct_image_quality_summary as quality
from scripts import vessel_metrics_python as metrics
from scripts import consolidate_global_results as consolidate


ACQUISITIONS = [("OD", "H"), ("OD", "V"), ("OS", "H"), ("OS", "V")]


def append_image(images, typ, eye, orient, file_name, n=0):
    image = ET.SubElement(images, "Image")
    ET.SubElement(image, "Laterality").text = "R" if eye == "OD" else "L"
    ET.SubElement(ET.SubElement(image, "ImageType"), "Type").text = typ
    ET.SubElement(ET.SubElement(image, "ImageData"), "ExamURL").text = f"file:///C:/Heidelberg/{file_name}"
    acq = ET.SubElement(ET.SubElement(image, "AcquisitionTime"), "Time")
    for tag, value in [("Hour", "10"), ("Minute", "12"), ("Second", "13.25")]:
        ET.SubElement(acq, tag).text = value
    context = ET.SubElement(image, "OphthalmicAcquisitionContext")
    ET.SubElement(context, "ImageQuality").text = str(20 + n % 20)
    start = ET.SubElement(ET.SubElement(context, "Start"), "Coord")
    end = ET.SubElement(ET.SubElement(context, "End"), "Coord")
    for tag, val in [("X", "0"), ("Y", "0")]:
        ET.SubElement(start, tag).text = val
    for tag, val in [("X", "1" if orient == "H" else "0"),
                     ("Y", "0" if orient == "H" else "1")]:
        ET.SubElement(end, tag).text = val
    if typ == "LOCALIZER":
        ET.SubElement(ET.SubElement(image, "ImageType"), "LightSource").text = "IR"
        ET.SubElement(context, "ScaleX").text = "0.012"
        ET.SubElement(context, "ScaleY").text = "0.012"
    return image


def create_flat_participant(folder: Path):
    folder.mkdir(parents=True)
    for eye, orient in ACQUISITIONS:
        root = ET.Element("Export")
        images = ET.SubElement(root, "Images")
        prefix = f"{eye}_{orient}"
        loc = f"{prefix}_localizer.tif"
        append_image(images, "LOCALIZER", eye, orient, loc)
        (folder / loc).write_bytes(b"fake OCT localizer; not a patient image")
        for i in range(97):
            name = f"{prefix}_{i:03}.tif"
            append_image(images, "OCT", eye, orient, name, n=i)
            (folder / name).write_bytes(b"fake B-scan")
        ET.ElementTree(root).write(folder / f"{prefix}.xml", encoding="utf-8", xml_declaration=True)
    assert len(list(folder.iterdir())) == 396


class Stage2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def organize(self, folder):
        # Never show a Tk window from the organizer during tests.
        with mock.patch.object(org, 'create_hidden_root') as mkroot, \
             mock.patch.object(org.messagebox, 'showinfo'), \
             contextlib.redirect_stdout(io.StringIO()):
            org.organize_participant_folder(folder)
            mkroot.return_value.destroy.assert_called_once()

    def test_original_396_flat_case(self):
        participant = self.root / "AG_02_001"
        create_flat_participant(participant)
        self.organize(participant)
        for eye, orient in ACQUISITIONS:
            folder = participant / f"AG_02_001_{eye}_{orient}_01"
            self.assertTrue((folder / "OCT.tif").is_file())
            self.assertEqual(len(list((folder / 'bscans').glob('*.tif'))), 97)
            self.assertTrue((folder / f"{eye}_{orient}.xml").exists())
        self.assertEqual(len(list(participant.glob('*.tif'))), 392)
        self.assertEqual(len(list(participant.glob('*.xml'))), 4)
        self.assertTrue((participant / 'AG_02_001_organization_summary.txt').exists())

    def test_flexible_id_and_rerun_no_copy(self):
        participant = self.root / "STUDY_2030_ALPHA"
        create_flat_participant(participant)
        self.organize(participant)
        with mock.patch.object(org.shutil, 'copy2', side_effect=AssertionError('unexpected copy')):
            with contextlib.redirect_stdout(io.StringIO()) as out:
                org.organize_participant_folder(participant)
        self.assertIn('already organized', out.getvalue())

    def test_preorganized_only_does_not_require_flat_files(self):
        participant = self.root / "CASE-007"
        create_flat_participant(participant)
        self.organize(participant)
        for file in participant.glob('*.tif'):
            file.unlink()
        for file in participant.glob('*.xml'):
            file.unlink()
        with mock.patch.object(org.shutil, 'copy2', side_effect=AssertionError('unexpected copy')):
            with contextlib.redirect_stdout(io.StringIO()):
                org.organize_participant_folder(participant)
        self.assertEqual(len(list(participant.iterdir())), 5)  # folders + summary

    def test_partial_organized_rejected_untouched(self):
        participant = self.root / 'SAMPLE_001'
        create_flat_participant(participant)
        (participant / 'SAMPLE_001_OD_H_01').mkdir()
        with self.assertRaisesRegex(ValueError, 'partially organized'):
            org.organize_participant_folder(participant)
        self.assertEqual(len([p for p in participant.iterdir() if p.is_dir()]), 1)

    def test_missing_referenced_file_rejected_before_copy(self):
        participant = self.root / 'SAMPLE_001'
        create_flat_participant(participant)
        (participant / 'OD_H_003.tif').unlink()
        with self.assertRaisesRegex(ValueError, 'Expected exactly'):
            org.organize_participant_folder(participant)
        self.assertEqual(len(list(participant.iterdir())), 395)
        self.assertFalse(list(participant.glob('SAMPLE_001_*_01')))

    def test_existing_xml_mismatch_rejected(self):
        participant = self.root / 'SAMPLE_001'
        create_flat_participant(participant)
        self.organize(participant)
        bad_dir = participant / 'SAMPLE_001_OD_V_01'
        (bad_dir / 'OD_V.xml').unlink()
        (bad_dir / 'OD_H.xml').write_bytes((participant / 'OD_H.xml').read_bytes())
        with self.assertRaisesRegex(ValueError, 'XML describes'):
            org.organize_participant_folder(participant)

    def test_invalid_folder_names(self):
        for value in ['Jane Smith', '.hidden', 'CASE_1_OD_H_01']:
            participant = self.root / value
            participant.mkdir()
            with self.assertRaises(ValueError):
                org.validate_participant_folder_name(participant)

    def test_metric_filename_extraction_legacy_and_new(self):
        for fname, expected in [
            ('AG_02_001_OD_H_01_slo_binary_map.png', 'AG_02_001'),
            ('STUDY_2030_ALPHA_OS_V_01_slo_binary_map.png', 'STUDY_2030_ALPHA'),
            ('CASE-007_OD_H_01_slo_binary_map.png', 'CASE-007')
        ]:
            self.assertEqual(metrics.extract_participant_id(fname), expected)
        with self.assertRaises(ValueError):
            metrics.extract_participant_id('nonsense.png')

    def test_consolidation_uses_suffix_not_first_three_fields(self):
        case = 'STUDY_2030_ALPHA_OS_V_01'
        self.assertEqual(consolidate.parse_subject(case), 'STUDY_2030_ALPHA')
        self.assertEqual(consolidate.parse_eye(case), 'OS')
        self.assertEqual(consolidate.parse_scan_orientation(case), 'Vertical')
        self.assertEqual(consolidate.parse_subject('AG_02_001_OD_H_01'), 'AG_02_001')

    def test_matlab_filename_parser_is_suffix_aware(self):
        matlab = (ROOT / 'scripts' / 'vessel_map_analyzer.m').read_text()
        self.assertIn('regexp(char(fname), pattern', matlab)
        self.assertNotIn('strjoin(parts(1:3)', matlab)

    def test_quality_utility_accepts_new_id(self):
        participant = self.root / 'STUDY_2030_ALPHA'
        create_flat_participant(participant)
        self.organize(participant)
        files = quality.participant_xml_files(participant)
        self.assertEqual(len(files), 4)
        records = [quality.summarize_xml(f, participant.name) for f in files]
        self.assertEqual(len(records), 4)
        self.assertTrue(all(r.subject == participant.name for r in records))
        self.assertEqual(len({r.case_id for r in records}), 4)

if __name__ == '__main__':
    unittest.main()
