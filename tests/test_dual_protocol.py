"""Synthetic tests for aging 4 scans and myopia 12 scans; no participant data."""
from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import xml.etree.ElementTree as ET

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from utils import organize_heidelbergfiles as organizer
from utils import oct_image_quality_summary as quality
from utils import oct_copier as copier
from scripts import vessel_metrics_python as metrics
from scripts import consolidate_global_results as consolidate

ACQUISITIONS = (("OD", "H"), ("OD", "V"), ("OS", "H"), ("OS", "V"))


def image_block(images, typ, eye, orient, file_name, repeat=1):
    im = ET.SubElement(images, "Image")
    ET.SubElement(im, "Laterality").text = "R" if eye == "OD" else "L"
    it = ET.SubElement(im, "ImageType")
    ET.SubElement(it, "Type").text = typ
    if typ == "LOCALIZER":
        ET.SubElement(it, "LightSource").text = "IR"
    ET.SubElement(ET.SubElement(im, "ImageData"), "ExamURL").text = (
        f"file:///C:/export/{file_name}"
    )
    tm = ET.SubElement(ET.SubElement(im, "AcquisitionTime"), "Time")
    ET.SubElement(tm, "Hour").text = "10"
    ET.SubElement(tm, "Minute").text = "12"
    ET.SubElement(tm, "Second").text = str(10.25 + repeat)
    ctx = ET.SubElement(im, "OphthalmicAcquisitionContext")
    ET.SubElement(ctx, "ImageQuality").text = "25"
    st = ET.SubElement(ET.SubElement(ctx, "Start"), "Coord")
    end = ET.SubElement(ET.SubElement(ctx, "End"), "Coord")
    for tag, val in (("X", "0"), ("Y", "0")):
        ET.SubElement(st, tag).text = val
    for tag, val in (("X", "1" if orient == "H" else "0"),
                     ("Y", "0" if orient == "H" else "1")):
        ET.SubElement(end, tag).text = val
    if typ == "LOCALIZER":
        ET.SubElement(ctx, "ScaleX").text = "0.012"
        ET.SubElement(ctx, "ScaleY").text = "0.012"


def make_flat(folder: Path, repeats: int) -> None:
    folder.mkdir(parents=True)
    # Reverse XML creation order, ensuring numbering comes from acquisition
    # time, not directory enumeration or filesystem insertion order.
    for eye, orient in ACQUISITIONS:
        for rep in range(repeats, 0, -1):
            prefix = f"{eye}_{orient}_rep{rep}"
            root = ET.Element("Export")
            images = ET.SubElement(root, "Images")
            localizer = f"{prefix}_localizer.tif"
            image_block(images, "LOCALIZER", eye, orient, localizer, rep)
            (folder / localizer).write_bytes(b"synthetic-localizer")
            for b in range(97):
                name = f"{prefix}_bscan_{b:03d}.tif"
                image_block(images, "OCT", eye, orient, name, rep)
                (folder / name).write_bytes(b"synthetic-bscan")
            ET.ElementTree(root).write(folder / f"{prefix}.xml", encoding="utf-8")


class NoGUI:
    def destroy(self):
        pass


class TestDualProtocol(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def organize(self, folder: Path) -> None:
        with mock.patch.object(organizer, "create_hidden_root", return_value=NoGUI()), \
             mock.patch.object(organizer.messagebox, "showinfo"), \
             contextlib.redirect_stdout(io.StringIO()):
            organizer.organize_participant_folder(folder)

    def test_aging_396_files_4_distinct_cases(self):
        p = self.root / "AG_02_001"
        make_flat(p, 1)
        self.assertEqual(len(list(p.iterdir())), 396)
        self.organize(p)
        for eye, orient in ACQUISITIONS:
            folder = p / f"AG_02_001_{eye}_{orient}_01"
            self.assertTrue((folder / "OCT.tif").is_file())
            self.assertEqual(len(list((folder / "bscans").glob("*.tif"))), 97)

    def test_myopia_1188_files_12_distinct_cases(self):
        p = self.root / "MY_02_001"
        make_flat(p, 3)
        self.assertEqual(len(list(p.iterdir())), 1188)
        self.organize(p)
        for eye, orient in ACQUISITIONS:
            for rep in range(1, 4):
                name = f"MY_02_001_{eye}_{orient}_{rep:02d}"
                folder = p / name
                self.assertTrue((folder / "OCT.tif").exists())
                self.assertTrue((folder / f"{eye}_{orient}_rep{rep}.xml").is_file())
                self.assertEqual(len(list((folder / "bscans").glob("*.tif"))), 97)
        self.assertEqual(len([v for v in p.iterdir() if v.is_dir()]), 12)

    def test_myopia_preorganized_repeat_run_no_copies(self):
        p = self.root / "MY_02_001"
        make_flat(p, 3)
        self.organize(p)
        for f in p.glob("*.xml"):
            f.unlink()
        for f in p.glob("*.tif"):
            f.unlink()
        with mock.patch.object(organizer.shutil, "copy2", side_effect=AssertionError("recopy")):
            with contextlib.redirect_stdout(io.StringIO()):
                organizer.organize_participant_folder(p)

    def test_partial_12_folders_fail(self):
        p = self.root / "MY_02_001"
        make_flat(p, 3)
        self.organize(p)
        import shutil
        shutil.rmtree(p / "MY_02_001_OD_H_03")
        with self.assertRaisesRegex(ValueError, "partially organized"):
            organizer.organize_participant_folder(p)

    def test_mixed_organized_4_and_flat_12_rejected(self):
        p = self.root / "MY_02_001"
        make_flat(p, 1)
        self.organize(p)
        # Remove only the original top-level files; then replace with 12.
        for f in p.glob("*.xml"):
            f.unlink()
        for f in p.glob("*.tif"):
            f.unlink()
        for f in p.glob("*.txt"):
            f.unlink()
        # Build 12 new top-level files while 4 organized dirs are still there.
        # make_flat creates folder with exist_ok=True in this test.
        p2 = self.root / "source"
        make_flat(p2, 3)
        import shutil
        for file in p2.iterdir():
            shutil.copy2(file, p / file.name)
        with self.assertRaisesRegex(ValueError, "stale organized"):
            organizer.organize_participant_folder(p)

    def test_unsupported_eight_xml_fails(self):
        p = self.root / "MY_02_001"
        make_flat(p, 3)
        for xml in sorted(p.glob("*.xml"))[:4]:
            xml.unlink()
        with self.assertRaisesRegex(ValueError, "4 or 12 XML"):
            organizer.organize_participant_folder(p)
        self.assertEqual(len([f for f in p.iterdir() if f.is_dir()]), 0)

    def test_myopia_flat_image_quality_ids_match_organizer(self):
        p = self.root / "MY_02_001"
        make_flat(p, 3)
        files = quality.participant_xml_files(p)
        original = [(f, quality.summarize_xml(f, p.name)) for f in files]
        flat = quality.number_flat_acquisition_summaries(original)
        got = {summary.case_id for _, summary in flat}
        want = {f"MY_02_001_{e}_{o}_{r:02d}"
                for e, o in ACQUISITIONS for r in range(1,4)}
        self.assertEqual(got, want)
        for f, summary in flat:
            rep = int(f.stem.rsplit("rep", 1)[1])
            self.assertTrue(summary.case_id.endswith(f"_{rep:02d}"))
        self.organize(p)
        files2 = quality.participant_xml_files(p)
        organized = quality.number_flat_acquisition_summaries(
            [(f, quality.summarize_xml(f,p.name)) for f in files2])
        self.assertEqual({r.case_id for _,r in organized}, want)

    def test_myopia_copier_and_metadata_all_12(self):
        p = self.root / "MY_02_001"
        make_flat(p, 3)
        self.organize(p)
        meta_dir = self.root / "metadata"
        meta_dir.mkdir()
        (meta_dir / "axial_lengths.csv").write_text(
            "ParticipantID,OD_AL,OS_AL\nMY_02_001,24.3,25.1\n"
        )
        dest = self.root / "demo"
        patches = [
            mock.patch.object(copier,"AXIAL_LENGTH_FILE",meta_dir/"axial_lengths.csv"),
            mock.patch.object(copier,"EXAMPLE_AXIAL_LENGTH_FILE",self.root/"no_sample.csv"),
            mock.patch.object(copier,"EXCEL_FILE",dest/"fname_resolution_location_eye.xlsx"),
            mock.patch.object(copier,"create_hidden_root",return_value=NoGUI()),
            mock.patch.object(copier.messagebox,"showinfo"),
            mock.patch.object(copier,"ask_axial_lengths",side_effect=AssertionError("prompted")),
        ]
        with contextlib.ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)
            with contextlib.redirect_stdout(io.StringIO()):
                copier.copy_oct_files(p,dest)
                copier.copy_oct_files(p,dest)
        self.assertEqual(len(list(dest.glob("*.tif"))),12)
        book = load_workbook(dest/"fname_resolution_location_eye.xlsx", read_only=True)
        try:
            rows = list(book.active.values)
        finally:
            book.close()
        self.assertEqual(len(rows),13)  # header + 12 unique cases
        self.assertEqual(len({row[0] for row in rows[1:]}),12)
        for eye,orient in ACQUISITIONS:
            for rep in range(1,4):
                case_id=f"MY_02_001_{eye}_{orient}_{rep:02d}"
                self.assertIn(case_id+".tif",{r[0] for r in rows[1:]})
                self.assertEqual(metrics.extract_participant_id(case_id+"_slo_binary_map.png"),"MY_02_001")
                self.assertEqual(consolidate.parse_subject(case_id),"MY_02_001")


if __name__ == "__main__":
    unittest.main()

class AdditionalProtocolGuards(unittest.TestCase):
    def test_no_mislabeled_study_in_global_metrics(self):
        code = (ROOT / 'scripts' / 'vessel_metrics_python.py').read_text()
        self.assertIn('"Category": "Unspecified"',code)
        matlab = (ROOT / 'scripts' / 'vessel_map_analyzer.m').read_text()
        self.assertIn('category = "Unspecified"', matlab)

    def test_quality_cli_assigns_all_12_from_flat_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = root / 'raw-oct-images' / 'MY_02_001'
            make_flat(p,3)
            output = root / 'results' / 'quality.csv'
            with mock.patch.object(sys, 'argv', ['quality', '--raw-dir', str(p.parent), '--output',str(output)]), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(quality.main(), 0)
            import csv
            with output.open(newline='') as f:
                rows=list(csv.DictReader(f))
            self.assertEqual(len(rows),12)
            self.assertEqual(len({r['case_id'] for r in rows}),12)

    def test_quality_cli_assigns_all_4_from_flat_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = root / 'raw-oct-images' / 'AG_02_001'
            make_flat(p,1)
            output = root / 'results' / 'quality.csv'
            with mock.patch.object(sys, 'argv', ['quality', '--raw-dir', str(p.parent), '--output',str(output)]), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(quality.main(), 0)
            import csv
            with output.open(newline='') as f:
                rows=list(csv.DictReader(f))
            self.assertEqual(len(rows),4)
            self.assertEqual(len({r['case_id'] for r in rows}),4)
