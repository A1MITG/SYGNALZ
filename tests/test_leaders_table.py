"""docs/LEADERS.md is the leader list as one table; it must match the config."""
import importlib.util
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('leaders_table', ROOT / 'scripts' / 'leaders_table.py')
leaders_table = importlib.util.module_from_spec(spec)
spec.loader.exec_module(leaders_table)


class LeadersTableTest(unittest.TestCase):
    def test_table_matches_config(self):
        self.assertEqual((ROOT / 'docs' / 'LEADERS.md').read_text(encoding='utf-8'), leaders_table.render(),
                         'Run python scripts/leaders_table.py after editing config/leader_quotes.yaml')

    def test_every_leader_has_company_region_and_domain(self):
        leaders = yaml.safe_load((ROOT / 'config' / 'leader_quotes.yaml').read_text(encoding='utf-8'))['leaders']
        names = [l['name'] for l in leaders]
        self.assertEqual(len(names), len(set(names)))
        for l in leaders:
            self.assertTrue(l.get('company') and l.get('region') and l.get('domain'), l)


if __name__ == '__main__':
    unittest.main()
