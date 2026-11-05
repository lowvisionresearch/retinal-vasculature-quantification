"""Stage 6 cross-module safety checks (synthetic inputs, never clinical images)."""
from __future__ import annotations
import contextlib
import csv
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from openpyxl import Workbook

from utils import oct_copier as copier
import launch_pipeline as launcher

ROOT = Path(__file__).resolve().parents[1]


class NoGUI:
    def destroy(self): pass


class Stage6IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_examples_seed_working_registry_for_global_metrics(self):
        example = self.root / 'examples' / 'axial_lengths_sample.csv'
        example.parent.mkdir()
        example.write_text('ParticipantID,OD_AL,OS_AL\nMY_02_001,25.73,25.42\n')
        working = self.root / 'metadata' / 'axial_lengths.csv'
        demo = self.root / 'demo'; demo.mkdir()
        case = self.root / 'raw' / 'MY_02_001' / 'MY_02_001_OD_H_01';case.mkdir(parents=True)
        (case/'OCT.tif').write_bytes(b'example, not a real tiff')
        (case/'im.xml').write_text('''<Root><Image><ImageType><Type>LOCALIZER</Type><LightSource>IR</LightSource></ImageType>
        <OphthalmicAcquisitionContext><ScaleX>0.012</ScaleX><ScaleY>0.012</ScaleY></OphthalmicAcquisitionContext></Image></Root>''')
        with (mock.patch.object(copier,'METADATA_FOLDER',working.parent),
              mock.patch.object(copier,'AXIAL_LENGTH_FILE',working),
              mock.patch.object(copier,'EXAMPLE_AXIAL_LENGTH_FILE',example),
              mock.patch.object(copier,'EXCEL_FILE',demo/'fname_resolution_location_eye.xlsx'),
              mock.patch.object(copier,'ask_axial_lengths',side_effect=AssertionError('not needed')),
              mock.patch.object(copier,'create_hidden_root',return_value=NoGUI()),
              mock.patch.object(copier.messagebox,'showinfo'),
              contextlib.redirect_stdout(io.StringIO())):
            copier.copy_oct_files(case.parent, demo)
            self.assertTrue(working.exists())
            with working.open() as file:
                values = list(csv.DictReader(file))
            self.assertEqual(len(values),1)
            self.assertEqual(values[0]['ParticipantID'],'MY_02_001')
            self.assertEqual(float(values[0]['OD_AL']),25.73)
            self.assertEqual(float(values[0]['OS_AL']),25.42)
            copier.copy_oct_files(case.parent, demo)
            with working.open() as file:
                self.assertEqual(len(list(csv.DictReader(file))),1)

    def test_invalid_axial_lengths_rejected(self):
        with mock.patch.object(copier,'AXIAL_LENGTH_FILE',self.root/'working.csv'),\
             mock.patch.object(copier,'METADATA_FOLDER',self.root):
            for value in (float('nan'), float('inf'), 0., -2.):
                with self.assertRaises(ValueError):
                    copier.save_axial_lengths('MY_02_001', value,25.42)
            self.assertFalse((self.root/'working.csv').exists())

    def test_organizer_error_stops_pipeline_before_copier(self):
        class Dummy:
            def __init__(self): self.events=[]
            def log(self, msg): self.events.append(msg)
            def update_progress(self,msg): pass
            def clear_demo_tiffs(self): pass
        dummy = Dummy()
        bad = self.root/'MY_02_001';bad.mkdir()
        class BadOrganizer:
            def organize_participant_folder(self, p):
                raise ValueError('Incomplete 12-acquisition export')
        class NeverCopier:
            def copy_oct_files(self,*a): raise AssertionError('Copier must never run')
        with mock.patch.object(launcher,'import_module_from_path', side_effect=[BadOrganizer(),NeverCopier()]):
            with self.assertRaisesRegex(ValueError,'Incomplete'):
                launcher.PipelineLauncher.run_pipeline(dummy,[bad])

    def test_segmentation_cache_detects_changed_image_and_scale(self):
        demo=self.root/'demo';demo.mkdir()
        output=self.root/'output';output.mkdir()
        tiff=demo/'MY_02_001_OD_H_01.tif';tiff.write_bytes(b'first input')
        book=Workbook(); ws=book.active;ws.title='fname_resolution_location_eye';ws.append(['Filename','Scale','Location','Eye']);ws.append([tiff.name,12.345,'Macula','Right']);book.save(demo/'fname_resolution_location_eye.xlsx')
        with mock.patch.object(launcher,'SLOCTOLYZER_DEMO_DIR',demo),\
             mock.patch.object(launcher,'SLOCTOLYZER_OUTPUT_DIR',output),\
             mock.patch.object(launcher,'SLOCTOLYZER_INPUT_FINGERPRINTS_JSON',output/'input_fingerprints.json'):
            actual = launcher.build_active_input_fingerprints()
            self.assertEqual(set(actual),{tiff.stem})
            launcher.verify_existing_input_cache(actual)
            case_dir=output/tiff.stem;case_dir.mkdir()
            for suffix in ('_output.xlsx','_slo_binary_map.png','_slo_avod_map.png'):
                (case_dir/(tiff.stem+suffix)).write_bytes(b'synthetic output')
            launcher.require_complete_segmentation(actual)
            launcher.save_input_fingerprints(actual)
            launcher.verify_existing_input_cache(launcher.build_active_input_fingerprints())
            tiff.write_bytes(b'changed input')
            with self.assertRaisesRegex(RuntimeError,'changed or unverified'):
                launcher.verify_existing_input_cache(launcher.build_active_input_fingerprints())
            tiff.write_bytes(b'first input')
            book=Workbook();ws=book.active;ws.title='fname_resolution_location_eye';ws.append(['Filename','Scale','Location','Eye']);ws.append([tiff.name,13.0,'Macula','Right']);book.save(demo/'fname_resolution_location_eye.xlsx')
            with self.assertRaisesRegex(RuntimeError,'changed or unverified'):
                launcher.verify_existing_input_cache(launcher.build_active_input_fingerprints())

    def test_unverified_existing_sloctolyzer_cache_is_rejected(self):
        output=self.root/'output';output.mkdir()
        case='AG_02_001_OD_H_01'
        (output/case).mkdir()
        (output/case/(case+'_output.xlsx')).write_bytes(b'old output')
        with mock.patch.object(launcher,'SLOCTOLYZER_OUTPUT_DIR',output),\
             mock.patch.object(launcher,'SLOCTOLYZER_INPUT_FINGERPRINTS_JSON',output/'input_fingerprints.json'):
            with self.assertRaisesRegex(RuntimeError,'changed or unverified'):
                launcher.verify_existing_input_cache({case:{'image_sha256':'a'*64}})

    def test_gitignore_allows_two_examples_but_not_other_or_derived_files(self):
        if shutil.which('git') is None:
            self.skipTest('git executable not available')
        (self.root/'.gitignore').write_text((ROOT/'.gitignore').read_text())
        subprocess.run(['git','init','-q'],cwd=self.root,check=True)
        paths = {
            'raw-oct-images/AG_02_001/a.tif':False,
            'raw-oct-images/AG_02_001/a.xml':False,
            'raw-oct-images/MY_02_001/a.tif':False,
            'raw-oct-images/MY_02_001/a.xml':False,
            'raw-oct-images/AG_02_001/AG_02_001_OD_H_01/OCT.tif':True,
            'raw-oct-images/MY_02_001/MY_02_001_OD_H_03/OCT.tif':False,
            'raw-oct-images/MY_02_001/MY_02_001_OD_H_03/case.xml':False,
            'raw-oct-images/MY_02_001/MY_02_001_OD_H_03/bscans/0001.tif':False,
            'raw-oct-images/MY_02_001/MY_02_001_OD_H_03/log.txt':True,
            'raw-oct-images/MY_02_001/organization_summary.txt':True,
            'raw-oct-images/AG_02_001/organization_summary.txt':True,
            'raw-oct-images/AG_02_002/a.xml':True,
            'raw-oct-images/MY_02_002/a.tif':True,
            'metadata/axial_lengths.csv':True,
            'metadata/unexpected_backup.csv':True,
            'results/arbitrary.csv':True,
            'segmented-images/Segmented Images/abc.png':True,
            'models/SLOctolyzer/analyze/output/a.txt':True,
        }
        for path, should_ignore in paths.items():
            command=subprocess.run(['git','check-ignore','-q',path],cwd=self.root)
            self.assertEqual(command.returncode==0,should_ignore,path)

    def test_etdrs_does_not_document_fractal_dimension(self):
        doc=(ROOT/'scripts'/'etdrs_vascular_metrics_python.py').read_text()
        self.assertNotIn('    Fractal Dimension\n',doc)
        self.assertIn('    Vascular Skeleton Length',doc)

if __name__ == '__main__': unittest.main()

# Keep this test as a separate TestCase: it mocks numerical processing rather
# than using synthetic TIFF data as if those bytes were retinal images.
class ReprocessTests(unittest.TestCase):
    def test_global_metrics_recompute_only_when_mask_or_axial_length_changes(self):
        from scripts import vessel_metrics_python as metrics
        from types import SimpleNamespace
        import pandas as pd
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); masks=root/'masks';masks.mkdir()
            mask=masks/'MY_02_001_OD_H_03_slo_binary_map.png';mask.write_bytes(b'first')
            output=root/'results'/'metrics.csv'
            counter=[]
            axial=pd.DataFrame([{'ParticipantID':'MY_02_001','OD_AL':25.73,'OS_AL':25.42}])
            def fake_process(path, axial_df, review_vbp=False):
                counter.append('processed')
                return {'Filename':path.name,'OD_OS_AL_Average':float(axial_df.iloc[0]['OD_AL']),
                        'Vascular_Area':42,'VBP_Review_Status':'automatic_no_review'}
            with mock.patch.object(metrics,'MASK_DIR',masks),\
                 mock.patch.object(metrics,'OUTPUT_CSV',output),\
                 mock.patch.object(metrics,'RESULTS_DIR',output.parent),\
                 mock.patch.object(metrics,'parse_args',return_value=SimpleNamespace(participants=None)),\
                 mock.patch.object(metrics,'load_axial_lengths',return_value=axial),\
                 mock.patch.object(metrics,'ask_vbp_review_mode',return_value=False),\
                 mock.patch.object(metrics,'process_mask',side_effect=fake_process),\
                 contextlib.redirect_stdout(io.StringIO()):
                metrics.main()
                self.assertEqual(len(counter),1)
                metrics.main()  # matching mask and AL: skip
                self.assertEqual(len(counter),1)
                mask.write_bytes(b'changed mask')
                metrics.main()  # edited segmentation: recompute
                self.assertEqual(len(counter),2)
                axial.loc[0,'OD_AL']=25.80
                metrics.main()  # edited axial length: recompute
                self.assertEqual(len(counter),3)
            results=pd.read_csv(output)
            self.assertEqual(len(results),1)
            self.assertEqual(float(results.loc[0,'OD_OS_AL_Average']),25.80)
            self.assertRegex(results.loc[0,'Input_Mask_SHA256'],r'^[0-9a-f]{64}$')
