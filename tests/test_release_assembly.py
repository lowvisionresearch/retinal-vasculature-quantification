"""Check public examples while allowing ignored, local pipeline working directories.

The aging source example is flat in Git. Running the workflow may create
organized acquisition subdirectories under that source folder. These local
working directories are deliberately ignored by .gitignore; their presence
must not make the public-distribution regression test fail.
"""
from __future__ import annotations

import csv
import math
import os
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / 'raw-oct-images'


class PublicDistributionTests(unittest.TestCase):
    def test_public_axial_length_csv_contains_only_two_examples(self):
        path = ROOT / 'examples' / 'axial_lengths_sample.csv'
        with path.open(newline='', encoding='utf-8-sig') as fp:
            records = list(csv.DictReader(fp))
        self.assertEqual(len(records), 2)
        self.assertEqual({r['ParticipantID'] for r in records}, {'AG_02_001', 'MY_02_001'})
        for record in records:
            for eye in ('OD_AL', 'OS_AL'):
                self.assertTrue(math.isfinite(float(record[eye])))
                self.assertGreater(float(record[eye]), 1.82)
        my = next(record for record in records if record['ParticipantID'] == 'MY_02_001')
        self.assertAlmostEqual(float(my['OD_AL']), 25.73)
        self.assertAlmostEqual(float(my['OS_AL']), 25.42)

    def test_aging_example_has_only_flat_acquisition_inputs(self):
        folder = EXAMPLES / 'AG_02_001'
        self.assertTrue(folder.is_dir())
        # Check the source-export files at the sample root only. Organizer-
        # produced folders are local intermediates, never public inputs.
        input_files = [p for p in folder.iterdir()
                       if p.is_file() and p.suffix.lower() in {'.xml', '.tif', '.tiff'}]
        self.assertEqual(len(input_files), 396)
        self.assertEqual(sum(p.suffix.lower() == '.xml' for p in input_files), 4)
        self.assertEqual(sum(p.suffix.lower() in ('.tif', '.tiff') for p in input_files), 392)
        # Local pipeline runs can also leave ignored summary/log files next to
        # the flat inputs. The separate Git-distribution test below is what
        # checks that none of those files or generated folders can be published.

    @unittest.skipUnless((ROOT / '.git').exists() and shutil.which('git'),
                         'Git tracking audit requires a Git working tree and executable')
    def test_aging_git_distribution_excludes_working_directories(self):
        """What Git would include must contain exactly the 396 flat source files."""
        root = ROOT / 'raw-oct-images' / 'AG_02_001'
        expected = {p.relative_to(ROOT).as_posix() for p in root.iterdir()
                    if p.is_file() and p.suffix.lower() in {'.xml', '.tif', '.tiff'}}
        cmd = ['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z',
               '--', 'raw-oct-images/AG_02_001']
        completed = subprocess.run(cmd, cwd=ROOT, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, check=False)
        self.assertEqual(completed.returncode, 0,
                         completed.stderr.decode('utf-8', errors='replace'))
        actual = {os.fsdecode(part).replace('\\', '/') for part in completed.stdout.split(b'\0')
                  if part}
        self.assertEqual(actual, expected,
                         'Git selection must contain only the 396 intended flat aging inputs')


if __name__ == '__main__':
    unittest.main()
