#!/usr/bin/env python3
"""
staleness.py — computes what in the dataset has gone stale, and writes it as
markdown for a GitHub Issue.

WHY THIS EXISTS, AND WHAT IT IS NOT
-----------------------------------
The build can run weekly. The DATA cannot refresh itself weekly. Nearly every
source behind this dataset resists scraping, and the refresh runbook documents
why in detail: MOPS is robots-disallowed, wearn.com has a path-level cache that
serves the wrong company unless you walk it sequentially and verify the name on
every page, stockanalysis hard-caps its free quarterly table at 20 columns,
Japanese tanshin PDFs need reading rather than parsing, and the fab construction
block comes from reading press releases and exercising judgment about whether a
date is disclosed or inferred. A cron job that pretended to do that would
produce confidently wrong numbers, which is worse than stale ones.

So this script does the part a machine genuinely can do: it measures how old
everything is against the publication calendar, and hands you a specific,
prioritised list. You bring that list to a refresh session; you do not go
hunting for what changed.

Emits GitHub Actions step-summary-compatible markdown on stdout.
Exit code 0 always — a stale dataset is not a build failure.
"""

import argparse
import json
import pathlib
from datetime import date, datetime

MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
          'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
MI = {m: i + 1 for i, m in enumerate(MONTHS)}


def parse_period(p):
    try:
        m, y = p.split()
        return date(int(y), MI[m], 1)
    except Exception:
        return None


def months_between(a, b):
    return (b.year - a.year) * 12 + (b.month - a.month)


def quarter_of(d):
    return (d.month - 1) // 3 + 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dataset', default='dataset.json')
    ap.add_argument('--today', default=None, help='YYYY-MM-DD, for testing')
    args = ap.parse_args()

    today = date.fromisoformat(args.today) if args.today else date.today()
    data = json.loads(pathlib.Path(args.dataset).read_text())
    out = []
    actions = []

    out.append(f'# Refresh check — {today.isoformat()}')
    out.append('')
    out.append(f'Dataset last updated **{data["meta"].get("last_updated", "?")}**.')
    out.append('')

    # ---------------- monthly revenue (Taiwan, 49 companies) --------------
    # Taiwan mandates monthly disclosure by the 10th of the following month, so
    # by mid-month the prior month should be in. Anything older than that is a
    # missed refresh, not a publication lag.
    monthly = [c for c in data['companies'] if c.get('monthly_history')]
    if monthly:
        latest = max((parse_period(c['monthly_history'][-1]['period']) for c in monthly),
                     default=None)
        expected = date(today.year, today.month, 1)
        if today.day < 12:                       # this month's filings not in yet
            expected = date(expected.year - (expected.month == 1),
                            12 if expected.month == 1 else expected.month - 1, 1)
        expected = date(expected.year - (expected.month == 1),
                        12 if expected.month == 1 else expected.month - 1, 1)
        behind = months_between(latest, expected)
        state = '🔴' if behind >= 2 else ('🟡' if behind == 1 else '🟢')
        out.append(f'## {state} Monthly revenue — {len(monthly)} Taiwan companies')
        out.append('')
        out.append(f'- Latest in dataset: **{latest.strftime("%b %Y")}**')
        out.append(f'- Expected available: **{expected.strftime("%b %Y")}**')
        if behind > 0:
            out.append(f'- **{behind} month(s) behind.**')
            actions.append(f'Pull {behind} month(s) of monthly revenue for {len(monthly)} '
                           f'Taiwan companies (wantgoo; see runbook for the wearn.com traps)')
        else:
            out.append('- Up to date.')
        lagging = sorted(
            (c for c in monthly
             if months_between(parse_period(c['monthly_history'][-1]['period']), latest) >= 1),
            key=lambda c: c['monthly_history'][-1]['period'])
        if lagging:
            out.append(f'- {len(lagging)} company/companies trail the leader: '
                       + ', '.join(f'{c["name"]} ({c["monthly_history"][-1]["period"]})'
                                   for c in lagging[:8]))
        out.append('')

    # ---------------- quarterly KPIs --------------------------------------
    # Most reporters file 4-8 weeks after quarter end; several China A-share
    # names file H1 in late August, which is why coverage is judged by share
    # rather than by the single newest quarter.
    quarterly = [c for c in data['companies'] if c.get('quarterly_history')]
    if quarterly:
        tails = [parse_period(c['quarterly_history'][-1]['period']) for c in quarterly]
        tails = [t for t in tails if t]
        newest = max(tails)
        cur_q_end = date(today.year, quarter_of(today) * 3, 1)
        prev_q_end = date(cur_q_end.year - (cur_q_end.month <= 3),
                          12 if cur_q_end.month <= 3 else cur_q_end.month - 3, 1)
        # A quarter is fairly reportable ~7 weeks after it ends.
        target = prev_q_end if (today - date(prev_q_end.year, prev_q_end.month, 28)).days > 49 \
            else date(prev_q_end.year - (prev_q_end.month <= 3),
                      12 if prev_q_end.month <= 3 else prev_q_end.month - 3, 1)
        at_newest = sum(1 for t in tails if t == newest)
        behind = months_between(newest, target) // 3
        state = '🔴' if behind >= 2 else ('🟡' if behind == 1 else '🟢')
        out.append(f'## {state} Quarterly KPIs — {len(quarterly)} companies')
        out.append('')
        out.append(f'- Newest quarter in dataset: **{newest.strftime("%b %Y")}** '
                   f'({at_newest} of {len(quarterly)} companies, '
                   f'{at_newest * 100 // len(quarterly)}% coverage)')
        out.append(f'- Should now be reportable through: **{target.strftime("%b %Y")}**')
        if behind > 0:
            out.append(f'- **{behind} quarter(s) behind.**')
            actions.append(f'Add {behind} quarter(s) of KPIs; regenerate the 49 derived '
                           f'Taiwan series with derive_quarterly() AFTER the monthly pull')
        else:
            out.append('- Up to date.')
        if today.month == 8 and today.day > 20:
            out.append('- 📌 Late August: the China A-share names (NAURA, AMEC, Will Semi, '
                       'Luxshare, BOE, Goertek, JCET, ACM Shanghai, Piotech, GigaDevice, '
                       'SG Micro, Hygon) file H1 now.')
        out.append('')

    # ---------------- fab construction ------------------------------------
    fc = data.get('fab_construction')
    if fc:
        as_of = datetime.strptime(fc['as_of'], '%Y-%m-%d').date()
        age_d = (today - as_of).days
        today_dec = today.year + (today.month - 1) / 12
        drift = abs(today_dec - fc['axis'].get('today', today_dec))

        due, low_conf = [], []
        for p in fc['projects']:
            prod = next((f for f in p['phases'] if f['k'] == 'production'), None)
            if prod and p['status'] not in ('producing', 'cancelled') and prod['a'] <= today_dec:
                due.append(p)
            if p['confidence'] == 'low':
                low_conf.append(p)

        state = '🔴' if age_d > 120 else ('🟡' if age_d > 60 else '🟢')
        out.append(f'## {state} Fab construction — {len(fc["projects"])} projects')
        out.append('')
        out.append(f'- Audited **{fc["as_of"]}** ({age_d} days ago)')
        if drift > 0.12:
            out.append(f'- ⚠️ `axis.today` is {drift:.2f}y off the real date — move it '
                       f'to `{today_dec:.2f}`.')
            actions.append(f'Move fab_construction.axis.today to {today_dec:.2f}')
        if due:
            out.append(f'- **{len(due)} project(s) whose production date has now passed but '
                       f'are not marked `producing`** — confirm or slip each:')
            for p in sorted(due, key=lambda x: x['company']):
                out.append(f'  - `{p["id"]}` — {p["company"]} {p["fab"]} '
                           f'(first output: {p["first_output"]}, status: `{p["status"]}`)')
            actions.append(f'Verify {len(due)} fab project(s) past their stated first-output date')
        else:
            out.append('- No project has passed its stated first-output date unconfirmed.')
        out.append(f'- {len(low_conf)} project(s) carry `low` confidence (inferred dates). '
                   f'Promote any that have since been disclosed.')
        out.append('')
        out.append('  <details><summary>Low-confidence rows</summary>')
        out.append('')
        for p in sorted(low_conf, key=lambda x: x['company']):
            out.append(f'  - `{p["id"]}` — {p["company"]} {p["fab"]} ({p["region"]})')
        out.append('')
        out.append('  </details>')
        out.append('')

    # ---------------- industry blocks -------------------------------------
    ind = data.get('industry', {})
    if ind:
        out.append('## 🟡 Industry volumes')
        out.append('')
        out.append('- SIA/WSTS global sales — monthly, published ~first week for two months prior')
        out.append('- SEMI silicon wafer area (MSI) — quarterly, ~6-8 weeks after quarter end')
        out.append('- IDC PC vendor units — quarterly, ~2 weeks after quarter end '
                   '(IDC restates; keep the as-published YoY)')
        out.append('- SEMI World Fab Forecast — new editions go in `capacity_by_segment.editions` '
                   'as a NEW entry, never appended to an existing segment')
        out.append('')

    # ---------------- action list -----------------------------------------
    out.append('---')
    out.append('')
    if actions:
        out.append('## Do this refresh')
        out.append('')
        for a in actions:
            out.append(f'- [ ] {a}')
        out.append('- [ ] Update `meta.last_updated` and `meta.data_as_of`')
        out.append('- [ ] Run `python3 tools/validate.py` and clear any new ERROR')
        out.append('- [ ] Shrink `tools/known_issues.json` if anything got fixed')
    else:
        out.append('Nothing is overdue. No action needed this week.')
    out.append('')
    out.append('<sub>Generated by `tools/staleness.py`. This job measures age; it does not '
               'fetch data — see the header comment for why.</sub>')

    try:
        print('\n'.join(out))
    except BrokenPipeError:   # piped into head/sed
        pass
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
