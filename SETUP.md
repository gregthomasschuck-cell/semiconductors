# Repo setup — weekly rebuild and refresh checks

Drop these files into your GitHub repo, enable Pages, done. Two workflows, split
along the one line that matters: **the build can be automated, the data cannot.**

## Files

```
dataset.json                        the data
template.html                       the shell, with one __DATA_JSON__ placeholder
build.py                            dataset + template -> dashboard.html
tools/validate.py                   the runbook's consistency audit, as a CI gate
tools/known_issues.json             documented deviations that are allowed to pass
tools/staleness.py                  computes what has gone stale
.github/workflows/deploy.yml        build + validate + publish to Pages
.github/workflows/refresh-check.yml weekly staleness issue
```

`dashboard.html` no longer needs committing — Actions builds it into `site/` and
publishes from there. Add it to `.gitignore` if it is currently tracked, so you
never have a stale copy competing with the live one.

## One-time setup

1. Commit the files above to `main`.
2. **Settings → Pages → Source: GitHub Actions.** Not "Deploy from a branch" —
   the workflow uses the Pages deployment API and will fail against the branch
   source.
3. **Settings → Actions → General → Workflow permissions:** allow read/write, or
   the refresh-check job cannot open its issue.
4. Create a label called `refresh-check` (the workflow reuses one rolling issue
   keyed on that label; without it you get a new issue every Monday).
5. Run **Actions → Build and deploy → Run workflow** once by hand to confirm the
   pipeline before trusting the schedule.

Your site lands at `https://<user>.github.io/<repo>/`.

## What runs, and when

| Workflow | Trigger | What it does |
|---|---|---|
| Build and deploy | push to `main` touching data/template/build, **plus Mondays 06:00 UTC**, plus manual | validates, builds, checks the output is self-contained, publishes to Pages |
| Weekly refresh check | Mondays 07:00 UTC, plus manual | measures staleness, files/updates one issue |

The weekly rebuild is a safety net, not a data refresh — it guarantees the live
site always matches what is committed. On a week where nothing was committed it
produces a byte-identical page apart from the build timestamp.

## Why the refresh is not automated

This is the honest part, and it is worth being clear about because "updates
weekly" could reasonably be read as "the numbers refresh themselves."

Every source behind this dataset resists scraping, in ways your refresh runbook
already documents from experience:

- **MOPS** (Taiwan's official filing system) is robots-disallowed.
- **wearn.com**, the only free source deeper than 5 years, has a path-level
  cache that serves *the wrong company's data* unless you walk it strictly
  sequentially and verify the company name on every single page. Polling
  refreshes the cache TTL, so a retry loop makes it worse. That trap cost hours
  to find and would be invisible in an unattended job — it fails by returning
  plausible numbers for the wrong firm.
- **stockanalysis.com** hard-caps its free quarterly table at 20 columns.
- **Japanese tanshin filings** are PDFs that need reading, and the fiscal-year
  conventions differ per company (Apr–Mar named by ending March for TDK and
  Tokyo Electron, by starting year for Advantest and Disco, calendar for
  Renesas, June year-end for Lasertec).
- **Fab construction** comes from press releases and earnings calls, and the
  judgment that matters — is this date *disclosed* or *inferred*? — is exactly
  what a scraper cannot make. That judgment is what the `confidence` field
  encodes, and it is why the chart can be trusted.

A cron job papering over all that would produce confidently wrong numbers.
Openly stale beats quietly wrong, so the automation measures staleness precisely
and tells you what to go and fix.

## The weekly loop

Monday morning you get one issue, updated in place. It tells you:

- how many months behind the Taiwan monthly spine is, and which companies trail
- whether a new quarter is now broadly reportable, and current coverage %
- **which fab projects have sailed past their stated first-output date without
  being confirmed** — this is the one that will catch real changes, because a
  fab either started producing or slipped, and both are news
- whether `axis.today` has drifted off the real date
- which `low` confidence rows might now have public dates
- a reminder in late August that the China A-share names file H1 then

Most weeks it will say nothing is overdue. When it does have items, bring the
issue into a refresh session, update `dataset.json`, and push — the deploy
workflow takes it from there.

## The validation gate

`tools/validate.py` runs before every build and **refuses to publish on error**.
It ports the audit from the refresh runbook, which caught a rounding drift on
v5, a wrong Vanguard 2025 series on v7, and dropped Jun-2024 quarters on v8 that
had silently shifted every later YoY comparison onto the wrong base quarter.

It currently reports **0 errors, 23 warnings**. The warnings are real and
tracked in `tools/known_issues.json` rather than suppressed:

- **6 known quarterly gaps** — SMIC, Hua Hong, AMEC and Cambricon missing
  Jun 2024; LG Display and LG Innotek missing Dec 2021. Seams where a backfill
  batch met an existing batch. Fillable in a normal refresh.
- **9 headline unit deviations** — TSMC, ASE and others report the latest-quarter
  tile in US$ or NT$ billion while the history carries NT$ million. Deliberate.
- **7 stale headline tiles** — Asustek, Acer, Novatek, Accton, BizLink, Nuvoton
  and Wistron have a `quarterly` snapshot older than their own derived history,
  because the roll-up ran but the snapshot block was not rewritten. Worth fixing
  next refresh.
- **1 YoY divergence** — Lasertec Sep 2024, which is its June fiscal year-end
  interacting with the comparison base.

`known_issues.json` is an allowlist, not a mute button: anything **not** listed
fails the build. If a new quarter goes missing next refresh, CI stops it before
it reaches the site. The file is meant to shrink — delete entries as they are
fixed.

## If you want the monthly pull automated anyway

It is possible for the one machine-readable slice — the 49 Taiwan monthly
disclosers via wantgoo — and that is roughly 40% of the refresh effort. It needs
a scraper with a per-page company-name assertion (non-negotiable, given the
wearn cache behaviour), a NT$ thousand → million conversion, and a 12-month sum
cross-checked against an independently sourced annual figure before anything is
written. It should open a **pull request**, never commit to `main`, so the diff
gets looked at.

I did not build it here because I could not test it against the live endpoints
from this environment, and an untested scraper writing to your dataset is worse
than no scraper. Say the word and it is a contained piece of work.

## Local development

```bash
python3 tools/validate.py            # audit only
python3 build.py                     # -> dashboard.html
python3 tools/staleness.py           # preview this week's report
python3 tools/staleness.py --today 2026-11-20   # simulate a future date
```

`build.py --skip-validate` exists as an escape hatch. Do not use it in CI.
