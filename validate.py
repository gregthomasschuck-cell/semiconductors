#!/usr/bin/env python3
"""
validate.py — the runbook's consistency audit, as code.

The refresh runbook says this audit "has caught real bugs on every pass" — a
rounding drift on v5, a wrong Vanguard 2025 series on v7, dropped Jun-2024
quarters on v8 that silently shifted every later YoY comparison onto the wrong
base. Running it by hand means it gets run when someone remembers. Running it in
CI means a bad dataset cannot reach the site.

ERRORS block the build. WARNINGS do not — several are expected and documented
(reporting-perimeter breaks, acquisitions, delistings), so failing on them would
train everyone to ignore the check.

Usage:
    python3 tools/validate.py [--dataset dataset.json] [--strict]
"""

import argparse
import json
import pathlib
import re
import sys
from collections import Counter

MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
          'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
MI = {m: i for i, m in enumerate(MONTHS)}

errors, warnings = [], []
KNOWN = {}


def load_known(path):
    """Documented, deliberately-tolerated deviations. Anything not listed fails.
    The file is meant to shrink over time; entries are removed as they are fixed."""
    global KNOWN
    p = pathlib.Path(path)
    KNOWN = json.loads(p.read_text()) if p.exists() else {}


def err(msg):
    errors.append(msg)


def warn(msg):
    warnings.append(msg)


def norm_period(s):
    """Normalise fiscal-label wording so 'Q1 FY2027/3 (Apr-Jun 2026)' and
    '1Q FY2027/3 (Apr-Jun 2026)' compare equal. Wording variants are noise;
    a genuinely different quarter is not."""
    if not s:
        return s
    s = re.sub(r'\s*\([^)]*\)', '', s)          # drop the month gloss
    s = re.sub(r'\b([1-4])Q\b', r'Q\1', s)      # 1Q -> Q1
    return re.sub(r'\s+', ' ', s).strip().lower()


def mstamp(period):
    """'Jul 2026' -> absolute month index. None if unparseable."""
    try:
        m, y = period.split()
        return int(y) * 12 + MI[m]
    except Exception:
        return None


# ----------------------------------------------------------------- checks
def check_histories(data):
    for c in data.get('companies', []):
        cid = c.get('id', c.get('name', '?'))

        mh = c.get('monthly_history') or []
        if mh:
            stamps = [mstamp(p['period']) for p in mh]
            if any(s is None for s in stamps):
                err(f'{cid}: unparseable month in monthly_history')
            else:
                if stamps != sorted(stamps):
                    err(f'{cid}: monthly_history not chronological')
                if len(set(stamps)) != len(stamps):
                    dup = [p for p, n in Counter(stamps).items() if n > 1]
                    err(f'{cid}: duplicate months in monthly_history ({len(dup)})')
                # Gap check — this has caught the same class of bug three times.
                expected = stamps[-1] - stamps[0] + 1
                if expected != len(stamps):
                    missing = sorted(set(range(stamps[0], stamps[-1] + 1)) - set(stamps))
                    names = [f'{MONTHS[s % 12]} {s // 12}' for s in missing[:6]]
                    err(f'{cid}: {len(missing)} missing month(s) inside monthly_history '
                        f'span: {", ".join(names)}{" ..." if len(missing) > 6 else ""}')

            mr = c.get('monthly_revenue')
            if mr:
                if mr.get('period') != mh[-1]['period']:
                    err(f'{cid}: monthly_revenue.period {mr.get("period")!r} != '
                        f'monthly_history tail {mh[-1]["period"]!r}')
                if mr.get('value') is not None and mh[-1].get('value') is not None:
                    if abs(mr['value'] - mh[-1]['value']) > 0.01:
                        err(f'{cid}: monthly_revenue.value != monthly_history tail value')

        qh = c.get('quarterly_history') or []
        if qh:
            stamps = [mstamp(p['period']) for p in qh]
            if any(s is None for s in stamps):
                err(f'{cid}: unparseable period in quarterly_history')
            else:
                if stamps != sorted(stamps):
                    err(f'{cid}: quarterly_history not chronological')
                if len(set(stamps)) != len(stamps):
                    err(f'{cid}: duplicate quarters in quarterly_history')
                if any((s % 12) % 3 != 2 for s in stamps):
                    bad = [p['period'] for p, s in zip(qh, stamps) if (s % 12) % 3 != 2]
                    err(f'{cid}: quarter period(s) not a quarter-end month: {bad[:4]}')
                expected = (stamps[-1] - stamps[0]) // 3 + 1
                if expected != len(stamps):
                    missing = sorted(set(range(stamps[0], stamps[-1] + 1, 3)) - set(stamps))
                    names = [f'{MONTHS[s % 12]} {s // 12}' for s in missing]
                    known = KNOWN.get('quarterly_gaps', {}).get('companies', {}).get(cid, [])
                    new = [n for n in names if n not in known]
                    if new:
                        err(f'{cid}: {len(new)} NEW missing quarter(s) inside span: '
                            f'{", ".join(new[:6])} — a dropped quarter shifts every later '
                            f'YoY onto the wrong base')
                    else:
                        warn(f'{cid}: known quarterly gap ({", ".join(names)}) — '
                             f'documented seam, still unfilled')

            units = {p.get('revenue_unit') for p in qh if p.get('revenue_unit')}
            if len(units) > 1:
                err(f'{cid}: mixed revenue_unit in quarterly_history: {sorted(units)}')

            q = c.get('quarterly')
            if q:
                # `quarterly.period` carries the calendar quarter-end month for most
                # companies but the fiscal label for others ("Q1 FY2027 (Apr-Jun 2026)"),
                # so accept a match against either the tail's period or its label.
                tail_forms = {norm_period(x) for x in
                              (qh[-1].get('period'), qh[-1].get('label')) if x}
                if norm_period(q.get('period')) not in tail_forms:
                    stale = KNOWN.get('stale_headline_quarter', {}).get('companies', [])
                    msg = (f'{cid}: quarterly.period {q.get("period")!r} is not the '
                           f'quarterly_history tail ({qh[-1].get("period")!r}) — the headline '
                           f'tile is reporting an older period than the chart')
                    (warn if cid in stale else err)(msg)
                qu, hu = q.get('revenue_unit'), qh[-1].get('revenue_unit')
                if qu and hu and qu != hu:
                    allowed = KNOWN.get('headline_unit_differs', {}).get('companies', [])
                    msg = f'{cid}: quarterly unit {qu!r} != history unit {hu!r}'
                    (warn if cid in allowed else err)(msg)

            # Stated YoY vs computed. Divergence is usually a mis-mapped quarter
            # or a restated base, not rounding — so it warns and names the pair.
            for i, cur in enumerate(qh):
                if i < 4 or cur.get('yoy_pct') is None:
                    continue
                prev = qh[i - 4]
                if mstamp(cur['period']) - mstamp(prev['period']) != 12:
                    continue
                if not prev.get('revenue'):
                    continue
                computed = (cur['revenue'] / prev['revenue'] - 1) * 100
                if abs(computed - cur['yoy_pct']) > 6:
                    warn(f'{cid}: {cur["period"]} yoy_pct {cur["yoy_pct"]:.1f} vs '
                         f'{computed:.1f} computed off {prev["period"]}'
                         f'{" [has data_gap_note]" if c.get("data_gap_note") else ""}')


def check_categories(data):
    companies = {c['id'] for c in data.get('companies', [])}
    stages = set(data.get('stages', []))
    seen = {}
    for cat in data.get('categories', []):
        if cat.get('stage') and stages and cat['stage'] not in stages:
            err(f'category {cat["id"]}: stage {cat["stage"]!r} not in data.stages')
        for mid in cat.get('members', []):
            if mid not in companies:
                err(f'category {cat["id"]}: member {mid!r} is not a company id')
            if mid in seen:
                err(f'company {mid!r} in two categories: {seen[mid]} and {cat["id"]}')
            seen[mid] = cat['id']
    orphan = companies - set(seen)
    if orphan:
        warn(f'{len(orphan)} company/companies in no category: {sorted(orphan)[:6]}')

    # The category grid emits a stage heading whenever cat.stage changes, so an
    # unsorted list produces repeated headings.
    order = [c.get('stage') for c in data.get('categories', []) if c.get('stage')]
    if order:
        first_seen, dupe = [], []
        for s in order:
            if s not in first_seen:
                first_seen.append(s)
            elif first_seen[-1] != s:
                dupe.append(s)
        if dupe:
            err(f'categories not sorted by stage — these stages recur: {sorted(set(dupe))}')


def check_fabs(data):
    fc = data.get('fab_construction')
    if not fc:
        warn('no fab_construction block')
        return
    regions = set(fc.get('regions') or [])
    axis = fc.get('axis', {})
    lo, hi = axis.get('min'), axis.get('max')
    valid_k = {'start', 'build', 'fitout', 'paused', 'production'}
    ids = set()

    for p in fc.get('projects', []):
        pid = p.get('id', '?')
        if pid in ids:
            err(f'fab {pid}: duplicate id')
        ids.add(pid)
        if regions and p.get('region') not in regions:
            err(f'fab {pid}: region {p.get("region")!r} not in fab_construction.regions')
        if p.get('confidence') not in ('high', 'medium', 'low'):
            err(f'fab {pid}: bad confidence {p.get("confidence")!r}')
        rev = p.get('revision', {})
        if rev.get('direction') not in ('later', 'earlier', 'unchanged', 'new', 'cancelled'):
            err(f'fab {pid}: bad revision.direction {rev.get("direction")!r}')
        if rev.get('direction') in ('later', 'earlier') and 'was_year' not in rev:
            err(f'fab {pid}: moved schedule with no revision.was_year — '
                f'the audit overlay cannot draw it')
        last = None
        for f in p.get('phases', []):
            if f.get('k') not in valid_k:
                err(f'fab {pid}: unknown phase {f.get("k")!r}')
            if f.get('b', 0) < f.get('a', 0):
                err(f'fab {pid}: phase {f.get("k")} ends before it starts')
            if lo is not None and not (lo <= f.get('a', lo) <= hi):
                err(f'fab {pid}: phase {f.get("k")} starts outside the axis')
            if last is not None and f.get('a', 0) < last - 0.01:
                err(f'fab {pid}: phases overlap or are out of order at {f.get("k")}')
            last = f.get('b')
        if not p.get('source', {}).get('url'):
            warn(f'fab {pid}: no source url')


def check_meta(data):
    for k in ('title', 'subtitle', 'last_updated', 'data_as_of'):
        if not data.get('meta', {}).get(k):
            err(f'meta.{k} is missing or empty')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dataset', default='dataset.json')
    ap.add_argument('--strict', action='store_true',
                    help='treat warnings as errors')
    ap.add_argument('--known', default=str(pathlib.Path(__file__).parent / 'known_issues.json'))
    args = ap.parse_args()
    load_known(args.known)

    path = pathlib.Path(args.dataset)
    try:
        data = json.loads(path.read_text())
    except Exception as e:
        print(f'FAIL: {path} is not valid JSON — {e}')
        return 1

    check_meta(data)
    check_histories(data)
    check_categories(data)
    check_fabs(data)

    for w in warnings:
        print(f'WARN  {w}')
    for e in errors:
        print(f'ERROR {e}')

    print(f'\n{len(errors)} error(s), {len(warnings)} warning(s)')
    if errors or (args.strict and warnings):
        return 1
    print('validation passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
