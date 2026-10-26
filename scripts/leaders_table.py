"""Write docs/LEADERS.md: Leaders on Record as one table, from
config/leader_quotes.yaml (the list the site actually uses).

    python scripts/leaders_table.py
"""
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / 'config' / 'leader_quotes.yaml'
OUT = ROOT / 'docs' / 'LEADERS.md'


def render():
    leaders = yaml.safe_load(CONFIG.read_text(encoding='utf-8'))['leaders']
    regions = Counter(l.get('region', '') for l in leaders)
    lines = [
        '# Leaders on Record',
        '',
        f'{len(leaders)} leaders whose on-the-record quotes the site collects. '
        'Edit config/leader_quotes.yaml, then run `python scripts/leaders_table.py`.',
        '',
        'By region: ' + ', '.join(f'{r} {n}' for r, n in regions.most_common()) + '.',
        '',
        '| # | Leader | Company | Region | Domain |',
        '| --- | --- | --- | --- | --- |',
    ]
    lines += [f"| {i} | {l['name']} | {l['company']} | {l.get('region', '')} | {l.get('domain', '')} |"
              for i, l in enumerate(leaders, 1)]
    return '\n'.join(lines) + '\n'


if __name__ == '__main__':
    OUT.write_text(render(), encoding='utf-8')
    print(f'Wrote {OUT.relative_to(ROOT)}')
