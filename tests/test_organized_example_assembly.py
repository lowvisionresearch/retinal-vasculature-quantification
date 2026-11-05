"""Public tests for the already-organized myopia example and Git allowlist.

The private release-assembly script is deliberately not part of the clone.
Tests here are self-contained and work on Windows (core.ignorecase=true).
"""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class OrganizedExampleTests(unittest.TestCase):
    def test_organized_myopia_contains_12_complete_acquisitions(self):
        root = ROOT / 'raw-oct-images' / 'MY_02_001'
        self.assertTrue(root.is_dir())
        expected = {
            f'MY_02_001_{eye}_{orientation}_{repeat:02d}'
            for eye in ('OD', 'OS') for orientation in ('H', 'V')
            for repeat in (1, 2, 3)
        }
        actual = {p.name for p in root.iterdir() if p.is_dir()}
        self.assertEqual(actual, expected)
        total = 0
        for name in sorted(expected):
            folder = root / name
            xmls = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == '.xml']
            self.assertEqual(len(xmls), 1, name)
            self.assertTrue((folder / 'OCT.tif').is_file(), name)
            bscan_dir = folder / 'bscans'
            self.assertTrue(bscan_dir.is_dir(), name)
            bscans = [p for p in bscan_dir.iterdir() if p.is_file() and p.suffix.lower() in ('.tif', '.tiff')]
            self.assertEqual(len(bscans), 97, name)
            self.assertEqual(len(list(bscan_dir.iterdir())), 97, name)
            self.assertEqual(set(folder.iterdir()), {xmls[0], folder / 'OCT.tif', bscan_dir}, name)
            total += 99
        self.assertEqual(total, 1188)
        self.assertEqual(len(list(root.iterdir())), 12, 'No extra files should be distributed in the MY sample')

    def test_gitignore_accepts_nested_inputs_on_windows_and_unix(self):
        # A bare git check-ignore -v is misleading for negated rules; use
        # ls-files --others --exclude-standard in an isolated repository.
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            subprocess.run(['git', 'init', '-q'], cwd=repo, check=True)
            (repo / '.gitignore').write_bytes((ROOT / '.gitignore').read_bytes())
            sample = repo / 'raw-oct-images' / 'MY_02_001'
            good = sample / 'MY_02_001_OD_H_03'
            (good / 'bscans').mkdir(parents=True)
            (good / 'OCT.tif').write_bytes(b'sample')
            (good / 'example.xml').write_bytes(b'sample')
            (good / 'bscans' / 'scan.tif').write_bytes(b'sample')
            (good / 'log.txt').write_bytes(b'generated')
            bad = sample / 'MY_02_001_OD_H_04'
            bad.mkdir()
            (bad / 'OCT.tif').write_bytes(b'unsupported repeat')
            (sample / 'tree_generator.py').write_bytes(b'not an input')
            unrelated = repo / 'raw-oct-images' / 'MY_02_002'
            unrelated.mkdir()
            (unrelated / 'other.xml').write_bytes(b'private')
            for ignorecase in ('true', 'false'):
                with self.subTest(core_ignorecase=ignorecase):
                    subprocess.run(['git', 'config', 'core.ignorecase', ignorecase], cwd=repo, check=True)
                    res = subprocess.run(['git', 'ls-files', '--others', '--exclude-standard'],
                                         cwd=repo, check=True, capture_output=True, text=True)
                    files = set(res.stdout.splitlines())
                    self.assertIn('raw-oct-images/MY_02_001/MY_02_001_OD_H_03/OCT.tif', files)
                    self.assertIn('raw-oct-images/MY_02_001/MY_02_001_OD_H_03/example.xml', files)
                    self.assertIn('raw-oct-images/MY_02_001/MY_02_001_OD_H_03/bscans/scan.tif', files)
                    self.assertNotIn('raw-oct-images/MY_02_001/MY_02_001_OD_H_03/log.txt', files)
                    self.assertNotIn('raw-oct-images/MY_02_001/MY_02_001_OD_H_04/OCT.tif', files)
                    self.assertNotIn('raw-oct-images/MY_02_001/tree_generator.py', files)
                    self.assertNotIn('raw-oct-images/MY_02_002/other.xml', files)


if __name__ == '__main__':
    unittest.main()
