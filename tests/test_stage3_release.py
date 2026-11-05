"""No-network tests for Stage 3 installation and model integrity."""
import csv
import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError
from retinal_setup import download_model_weights as mod

ROOT = Path(__file__).resolve().parents[1]
COPIER = ROOT / 'utils' / 'oct_copier.py'
spec = importlib.util.spec_from_file_location('copier_stage3', COPIER)
copier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(copier)

class Stage3Tests(unittest.TestCase):
    def test_sloctolyzer_model_urls_reference_organizational_release(self):
        for name in ('avo_inference.py','fov_inference.py','slo_inference.py'):
            path=ROOT/'models'/'SLOctolyzer'/'sloctolyzer'/'segment'/name
            text=path.read_text(encoding='utf-8')
            self.assertIn('lowvisionresearch/retinal-vasculature-quantification/releases/download/model-weights-v1.0.0',text)
            self.assertNotIn('jaburke166/SLOctolyzer/releases/download',text)

    def test_manifest_contains_verified_3_models(self):
        manifest = mod.load_manifest(mod.DEFAULT_MANIFEST)
        self.assertEqual(manifest['repository'], 'lowvisionresearch/retinal-vasculature-quantification')
        self.assertEqual(manifest['release_tag'], 'model-weights-v1.0.0')
        hashes = {e['filename']: e['sha256'] for e in manifest['models']}
        self.assertEqual(hashes, {
          'avosegmenter_weights.pth':'af434736f3178cc960501c8c4f165dafaeee8c2736355d5e2c0f4fb32d19c1e4',
          'fovsegmenter_weights.pth':'c0700e2acc7a1c92eeee327127a4a8f6bccea7b7760173d7fd03009a973cefcc',
          'slosegmenter_weights.pth':'f3b43ba5c17f1253c7970efff843ab0dc6c9ad2e30aacc55e0009b41ce8e4610'})

    def test_valid_cached_model_never_requests_network(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)/'test.pth'; data=b'synthetic checkpoint';p.write_bytes(data)
            digest=hashlib.sha256(data).hexdigest()
            with patch.object(mod.urllib.request, 'urlopen', side_effect=AssertionError('network used')):
                self.assertTrue(mod.ensure_model(p,digest,'https://example.invalid/test',False))
            self.assertEqual(p.read_bytes(),data)

    def test_invalid_cached_check_only_does_not_modify(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'test.pth';p.write_bytes(b'incorrect')
            with patch.object(mod.urllib.request,'urlopen',side_effect=AssertionError('network used')):
                self.assertFalse(mod.ensure_model(p,'0'*64,'https://example.invalid/test',True))
            self.assertEqual(p.read_bytes(),b'incorrect')

    def test_wrong_checksum_never_replaces_cached_checkpoint(self):
        class FakeResponse:
            def __enter__(self):return self
            def __exit__(self,*args):return None
            def read(self,size):return b'wrong synthetic weights' if not hasattr(self,'readonce') and setattr(self,'readonce',1) is None else b''
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'test.pth';p.write_bytes(b'old')
            with patch.object(mod.urllib.request,'urlopen',return_value=FakeResponse()):
                with self.assertRaises(RuntimeError):
                    mod.ensure_model(p,'0'*64,'https://example.invalid/test',False)
            self.assertEqual(p.read_bytes(),b'old')
            self.assertEqual(list(Path(temp).glob('*.part')),[])

    def test_sample_values_used_without_prompt_and_working_values_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            example=folder/'examples.csv'
            example.write_text('ParticipantID,OD_AL,OS_AL\nAG_02_001,23.10,24.20\n')
            working=folder/'working.csv'
            with patch.object(copier,'EXAMPLE_AXIAL_LENGTH_FILE',example),patch.object(copier,'AXIAL_LENGTH_FILE',working):
                self.assertEqual(copier.load_axial_lengths('AG_02_001'),(23.10,24.20))
                self.assertIsNone(copier.load_axial_lengths('ANOTHER_1'))
                working.write_text('ParticipantID,OD_AL,OS_AL\nAG_02_001,22.2,22.3\n')
                self.assertEqual(copier.load_axial_lengths('AG_02_001'),(22.2,22.3))

    def test_public_readme_does_not_print_example_axial_lengths(self):
        # The preassembly CSV template is intentionally omitted from the
        # public clone. Its approved two-row replacement is distributed instead.
        readme = (ROOT / 'README.md').read_text(encoding='utf-8')
        self.assertNotIn('25.73 mm', readme)
        self.assertNotIn('25.42 mm', readme)
        self.assertTrue((ROOT / 'examples' / 'axial_lengths_sample.csv').is_file())

if __name__ == '__main__':unittest.main()
