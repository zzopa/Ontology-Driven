"""Keep generated data and offline scripts out of the project root."""
import unittest

from app.config import ROOT, settings
from scripts.paths import ARTIFACT_DIR, DATA_DIR, GRAPH_DIR, RENDERER_DIR


class LayoutTests(unittest.TestCase):
    def test_configured_inputs_live_in_data_directory(self):
        for path in (settings.ontology_path, settings.memory_path):
            self.assertEqual(path.parent, DATA_DIR)
            self.assertTrue(path.is_file(), path)
        for path in (settings.graph_path, settings.schema_path):
            self.assertEqual(path.parent, DATA_DIR / 'runtime')

    def test_generated_pages_have_dedicated_directories(self):
        self.assertEqual(DATA_DIR.parent, ROOT)
        self.assertEqual(GRAPH_DIR.parent, ARTIFACT_DIR)
        self.assertEqual(RENDERER_DIR.parent, ARTIFACT_DIR)
        self.assertTrue(GRAPH_DIR.is_dir())
        self.assertTrue(RENDERER_DIR.is_dir())

    def test_no_database_scripts_in_project_root(self):
        self.assertEqual(list(ROOT.glob('db_*.py')), [])

    def test_root_launch_scripts_and_docs_are_organized(self):
        root_launchers = sorted(p.name for ext in ('*.cmd', '*.bat', '*.ps1') for p in ROOT.glob(ext))
        self.assertEqual(root_launchers, ['restart.cmd', 'start-ops-ask.cmd', 'start-report-worker.cmd'])
        self.assertTrue((ROOT / 'scripts' / 'service' / 'launchers' / 'start.ps1').is_file())
        self.assertEqual(list((ROOT / 'docs').glob('verification-*.md')), [])
        self.assertTrue((ROOT / 'docs' / 'README.md').is_file())
        self.assertTrue((ROOT / 'docs' / 'verification').is_dir())


if __name__ == '__main__':
    unittest.main()
