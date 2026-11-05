"""Offline regression tests for source release workbook initialization.

Run from patch root: python -m unittest discover -s tests -v
"""
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook, load_workbook

COPIER = Path(__file__).resolve().parents[1] / 'utils' / 'oct_copier.py'
spec = importlib.util.spec_from_file_location('retinal_oct_copier_stage1', COPIER)
copier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(copier)

class TestMetadataWorkbook(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.workbook = self.root / 'demo' / 'fname_resolution_location_eye.xlsx'
        self.patcher = patch.object(copier, 'EXCEL_FILE', self.workbook)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.metadata_patch = patch.object(copier, 'METADATA_FOLDER', self.root / 'metadata')
        self.metadata_patch.start()
        self.addCleanup(self.metadata_patch.stop)
        self.axial_patch = patch.object(copier, 'AXIAL_LENGTH_FILE', self.root / 'metadata' / 'axial_lengths.csv')
        self.axial_patch.start()
        self.addCleanup(self.axial_patch.stop)

    def sheet_rows(self):
        wb = load_workbook(self.workbook, read_only=True, data_only=True)
        try:
            self.assertEqual(wb.sheetnames[0], 'fname_resolution_location_eye')
            return list(wb.active.values)
        finally:
            wb.close()

    def test_create_empty_workbook_and_preserve(self):
        self.assertEqual(copier.ensure_metadata_workbook(), self.workbook)
        self.assertEqual(self.sheet_rows(), [('Filename','Scale','Location','Eye')])
        stamp = self.workbook.stat().st_mtime_ns
        self.assertEqual(copier.ensure_metadata_workbook(), self.workbook)
        self.assertEqual(self.workbook.stat().st_mtime_ns, stamp)

    def test_upsert_and_preserve_other_participants(self):
        copier.append_metadata_rows([
            ['SAMPLE_001_OD_H_01.tif', 11.5, 'Macula', 'Right'],
            ['ANOTHER_001_OD_H_01.tif', 10.0, 'Macula', 'Right']
        ])
        copier.append_metadata_rows([['sample_001_od_h_01.tif', 12.0, 'Macula', 'Right']])
        rows = self.sheet_rows()
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[1][0], 'sample_001_od_h_01.tif')
        self.assertEqual(rows[1][1], 12.0)
        self.assertEqual(rows[2][0], 'ANOTHER_001_OD_H_01.tif')

    def test_existing_malformed_workbook_is_not_overwritten(self):
        self.workbook.parent.mkdir(parents=True)
        wb = Workbook()
        wb.active['A1'] = 'Important unrelated data'
        wb.save(self.workbook)
        wb.close()
        with self.assertRaises(ValueError):
            copier.ensure_metadata_workbook()
        wb = load_workbook(self.workbook)
        try:
            self.assertEqual(wb.active['A1'].value, 'Important unrelated data')
        finally:
            wb.close()

    def test_copy_twice_preserves_row_and_axial_input(self):
        source = self.root / 'raw-oct-images' / 'SAMPLE_001'
        acquisition = source / 'SAMPLE_001_OD_H_01'
        acquisition.mkdir(parents=True)
        (acquisition / 'OCT.tif').write_bytes(b'placeholder-localizer')
        (acquisition / 'export.xml').write_text('''<Root><Image>
            <ImageType><Type>LOCALIZER</Type><LightSource>IR</LightSource></ImageType>
            <OphthalmicAcquisitionContext><ScaleX>0.011</ScaleX><ScaleY>0.011</ScaleY></OphthalmicAcquisitionContext>
            </Image></Root>''')
        copier.save_axial_lengths('SAMPLE_001', 24.25, 24.5)
        class DummyRoot:
            def destroy(self): pass
        with patch.object(copier, 'create_hidden_root', lambda: DummyRoot()), \
             patch.object(copier.messagebox,'showinfo',lambda *args, **kwargs:None):
            copier.copy_oct_files(source, self.workbook.parent)
            copier.copy_oct_files(source, self.workbook.parent)
        rows = self.sheet_rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][0], 'SAMPLE_001_OD_H_01.tif')
        self.assertEqual(rows[1][2:], ('Macula', 'Right'))
        expected_scale = 11.0 * copier.compute_axial_correction_factor(24.25)
        self.assertAlmostEqual(rows[1][1], round(expected_scale,6), places=6)
        self.assertEqual((self.workbook.parent/'SAMPLE_001_OD_H_01.tif').read_bytes(),b'placeholder-localizer')

if __name__=='__main__':
    unittest.main()
