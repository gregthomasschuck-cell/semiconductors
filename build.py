#!/usr/bin/env python3
"""
build.py — dataset.json + template.html -> dashboard.html

Deterministic and offline. This is the only step that runs on every push and on
the weekly schedule; it fetches nothing, so it cannot fail for network reasons
and cannot silently change the data.

Refuses to build if tools/validate.py finds a problem. A dashboard that renders
wrong numbers is worse than one that did not rebuild.

Usage:
    python3 build.py                 # -> dashboard.html
    python3 build.py --out site/index.html
    python3 build.py --skip-validate # escape hatch, avoid in CI
"""

import argparse
import json
import pathlib
import subprocess
import sys
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).parent
PLACEHOLDER = '__DATA_JSON__'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='dashboard.html')
    ap.add_argument('--dataset', default='dataset.json')
    ap.add_argument('--template', default='template.html')
    ap.add_argument('--skip-validate', action='store_true')
    args = ap.parse_args()

    ds_path = ROOT / args.dataset
    tpl_path = ROOT / args.template
    out_path = ROOT / args.out

    for p in (ds_path, tpl_path):
        if not p.exists():
            sys.exit(f'missing required file: {p}')

    if not args.skip_validate:
        r = subprocess.run([sys.executable, str(ROOT / 'tools' / 'validate.py'),
                            '--dataset', str(ds_path)])
        if r.returncode != 0:
            sys.exit('validation failed — refusing to build')

    data = json.loads(ds_path.read_text())
    tpl = tpl_path.read_text()

    if tpl.count(PLACEHOLDER) != 1:
        sys.exit(f'template must contain exactly one {PLACEHOLDER}, '
                 f'found {tpl.count(PLACEHOLDER)}')

    # Stamp the build so the page can state honestly when it was last rebuilt,
    # which is a different thing from when the data was last refreshed. The
    # header renders both.
    data.setdefault('meta', {})['built_at'] = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(tpl.replace(PLACEHOLDER, json.dumps(data, indent=2)))

    kb = out_path.stat().st_size / 1024
    print(f'built {out_path} ({kb:,.0f} KB)')
    print(f'  companies      : {len(data.get("companies", []))}')
    print(f'  fab projects   : {len(data.get("fab_construction", {}).get("projects", []))}')
    print(f'  data as of     : {data["meta"].get("last_updated")}')
    print(f'  built at       : {data["meta"]["built_at"]}')


if __name__ == '__main__':
    main()
