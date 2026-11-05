"""Cumulative global-results regression tests (no real participant data)."""
from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

from scripts import consolidate_global_results as consolidated


class CumulativeConsolidationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.output = self.root / 'slo' / 'analysis_output.csv'
        self.registry = self.root / 'metadata' / 'sloctolyzer_case_metadata.csv'
        self.matlab = self.root / 'results' / 'analysis_output_vascular_metrics_matlab.csv'
        self.python = self.root / 'results' / 'analysis_output_vascular_metrics.csv'
        self.final = self.root / 'results' / 'consolidated_global_vascular_metrics.csv'
        self.final.parent.mkdir(parents=True, exist_ok=True)
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.registry.parent.mkdir(parents=True, exist_ok=True)
        patches = [
            mock.patch.object(consolidated, 'SLO_OUTPUT_CSV', self.output),
            mock.patch.object(consolidated, 'SLO_CUMULATIVE_CSV', self.registry),
            mock.patch.object(consolidated, 'MATLAB_METRICS_CSV', self.matlab),
            mock.patch.object(consolidated, 'PYTHON_METRICS_CSV', self.python),
            mock.patch.object(consolidated, 'RESULTS_DIR', self.final.parent),
            mock.patch.object(consolidated, 'CONSOLIDATED_CSV', self.final),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    @staticmethod
    def cases():
        aging = [f'AG_02_001_{eye}_{direction}_01' for eye in ('OD','OS') for direction in ('H','V')]
        myopia = [f'MY_02_001_{eye}_{direction}_{repeat:02d}' for eye in ('OD','OS') for direction in ('H','V') for repeat in (1,2,3)]
        return aging, myopia

    def write_inputs(self):
        aging, myopia = self.cases()
        # Mimic SLOctolyzer latest-batch CSV after processing myopia only.
        pd.DataFrame({'Filename': [f'{case}.tif' for case in myopia],
                      'average_global_calibre_binary_whole': [55.0] * len(myopia),
                      'Scale': [12.0] * len(myopia)}).to_csv(self.output, index=False)
        # The launcher has separately maintained the full, cumulative registry.
        pd.DataFrame({'Filename': [f'{case}.tif' for case in aging + myopia],
                      'case_id': aging + myopia,
                      'participant': ['AG_02_001']*4 + ['MY_02_001']*12,
                      'average_global_calibre_binary_whole': [44.0]*4 + [55.0]*12,
                      'Scale': [11.5]*4 + [12.0]*12}).to_csv(self.registry, index=False)
        # Only MATLAB global vascular metrics have been calculated.
        pd.DataFrame({'Filename': [f'{case}_slo_binary_map.png' for case in aging + myopia],
                      'Vascular_Area': list(range(100,116)),
                      'Corrected_Vascular_Area': list(range(90,106)),
                      'Fractal_Dimension': [1.45]*16,
                      'Vascular_Skeleton_Length': [1000]*16,
                      'Corrected_Vascular_Skeleton_Length': [950]*16,
                      'Vascular_Bifurcation_Points': [100]*16}).to_csv(self.matlab, index=False)
        return aging, myopia

    def test_latest_12_plus_cumulative_16_yields_all_16_with_matlab(self):
        aging, myopia = self.write_inputs()
        with contextlib.redirect_stdout(io.StringIO()):
            consolidated.main()
        actual = pd.read_csv(self.final).set_index('case_id')
        self.assertEqual(len(actual),16)
        self.assertEqual(set(actual.index),set(aging + myopia))
        self.assertEqual(set(actual['metrics_source']),{'MATLAB'})
        self.assertEqual(actual.loc[aging[0], 'vascular_area'],100)
        self.assertEqual(actual.loc[myopia[-1], 'vascular_area'],115)
        self.assertEqual(actual.loc[aging[0], 'average_global_vessel_caliber_um'],44.0)
        self.assertEqual(actual.loc[myopia[0], 'average_global_vessel_caliber_um'],55.0)
        with contextlib.redirect_stdout(io.StringIO()):
            consolidated.main()
        self.assertEqual(len(pd.read_csv(self.final)),16)

    def test_absent_registry_uses_latest_batch_as_fallback(self):
        _, myopia = self.write_inputs()
        self.registry.unlink()
        with contextlib.redirect_stdout(io.StringIO()):
            consolidated.main()
        actual = pd.read_csv(self.final)
        self.assertEqual(len(actual),12)
        self.assertEqual(set(actual['case_id']),set(myopia))

    def test_duplicate_registry_fails_before_overwriting_existing_results(self):
        self.write_inputs()
        bad = pd.read_csv(self.registry)
        pd.concat([bad, bad.iloc[[0]]], ignore_index=True).to_csv(self.registry,index=False)
        self.final.write_text('previous good result\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'duplicate case IDs'):
            consolidated.main()
        self.assertEqual(self.final.read_text(encoding='utf-8'), 'previous good result\n')

    def test_inconsistent_case_identifier_fails_safely(self):
        self.write_inputs()
        bad = pd.read_csv(self.registry)
        bad.loc[0, 'case_id'] = 'AG_02_001_OS_H_03'
        bad.to_csv(self.registry,index=False)
        with self.assertRaisesRegex(ValueError, 'inconsistent'):
            consolidated.main()
        self.assertFalse(self.final.exists())


if __name__ == '__main__':
    unittest.main()
