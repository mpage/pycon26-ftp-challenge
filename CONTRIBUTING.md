# Contributing

This repo contains the **PyCon 2026 Free-Threaded Python Challenge**: the challenge
code, evaluation harness, scoring pipeline, and leaderboard website.

---

## Repository structure

```
pycon26-ftp-challenge/
├── submission_template.py        # Skeleton solution to start from
├── challenge/
│   ├── graph.py                  # BuildGraph / Target classes
│   ├── reference.py              # Single-threaded reference implementation
│   ├── harness.py                # Test harness — runs & validates submissions
│   ├── generate.py               # Generates build graphs of various shapes
│   ├── test_graph.py             # Tests for graph module
│   ├── test_harness.py           # Tests for harness
│   ├── test_reference.py         # Tests for reference implementation
│   └── test_generate.py          # Tests for graph generation
├── graphs/                       # Build graph JSON files used for scoring
├── submissions/                  # Participant submissions (one .py per user)
├── leaderboard/                  # Static website (HTML/CSS/JS)
│   ├── index.html
│   ├── app.js
│   └── styles.css
├── scores/
│   ├── journal.txt               # Append-only log of all submission results
│   ├── format_journal_entry.py   # Formats a harness result into a journal line
│   ├── generate_leaderboard.py   # Builds leaderboard.json from journal.txt
│   └── leaderboard.json          # Live leaderboard data (updated by CI)
├── .github/
│   ├── PULL_REQUEST_TEMPLATE.md
│   └── workflows/
│       ├── evaluate.yml          # CI: validate PR, score, log, comment
│       └── update_leaderboard.yml # CI: regenerate leaderboard, deploy Pages
└── CONTRIBUTING.md               # You are here
```

---

## Running the leaderboard website locally

The leaderboard is a static site — no build step or dependencies required. You just
need a local HTTP server (browsers block `fetch()` on `file://` URLs).

```bash
# Clone the repo
git clone https://github.com/mpage/pycon26-ftp-challenge.git
cd pycon26-ftp-challenge

# Start a local server (Python is already installed if you're here)
python3 -m http.server 8080

# Open in your browser
open http://localhost:8080/leaderboard/
```

The page fetches `../scores/leaderboard.json` on load and auto-refreshes every 30
seconds. To test with sample data, edit `scores/leaderboard.json` directly.

### Leaderboard JSON format

```json
{
  "last_updated": "2026-05-15T14:30:00+00:00",
  "baseline_time": 12.345,
  "entries": [
    {
      "username": "alice",
      "display_name": "alice",
      "median_time_seconds": 3.86,
      "speedup": "3.20",
      "status": "valid",
      "merged_at": "2026-05-15T14:30:00+00:00",
      "num_runs": 2,
      "pr_number": 42,
      "pr_url": "https://github.com/mpage/pycon26-ftp-challenge/pull/42",
      "is_meta": false
    }
  ]
}
```

Each entry needs at minimum: `username`, `median_time_seconds`, `speedup`,
`status` (`"valid"` or `"invalid"`), and `merged_at`. The leaderboard UI filters
to valid entries and ranks by highest speedup.

---

## How CI works

There are two workflows that run in sequence.

### 1. Evaluate Submission (`evaluate.yml`)

Triggered on every PR open/synchronize:

1. **validate_pr** — Checks that the PR modifies exactly one file matching
   `submissions/<pr_author>.py`. Fails the PR if any other files are touched.
2. **run_harness** — Runs on a self-hosted runner. Installs Python 3.14t, then
   runs `challenge/harness.py --num-trials 3 --json` against all graphs in
   `graphs/`. Each graph is run 3 times and the median result is used.
3. **update_journal** — Checks whether the PR author is a member of the
   `facebook` GitHub org, then appends a JSON line to `scores/journal.txt`
   (via `scores/format_journal_entry.py`) and pushes to `main`.
4. **report_results** — Posts a score comment on the PR with per-graph speedups
   and pass/fail status.

### 2. Update Leaderboard (`update_leaderboard.yml`)

Triggered after the evaluate workflow completes successfully (or manually via
`workflow_dispatch`):

1. **update-leaderboard** — Runs `scores/generate_leaderboard.py` to rebuild
   `scores/leaderboard.json` from `scores/journal.txt`. Keeps only each user's
   best score. Commits and pushes if the file changed.
2. **deploy-pages** — Copies the `leaderboard/` directory and
   `scores/leaderboard.json` into a GitHub Pages artifact and deploys it.

### Scoring details

- Each graph is run 3 times; the **median** (by submission time) is used
- Overall speedup = `total_reference_time / total_submission_time`
- Status is `valid` only if all graphs pass correctness validation
- If your submission fails validation, it gets an `invalid` status
- Submissions are evaluated on a 24-core machine using Python 3.14t
- There is a 10-minute timeout; submissions that exceed it receive no score

---

## Modifying the leaderboard UI

The frontend is vanilla HTML/CSS/JS with no framework or build step:

- **`leaderboard/index.html`** — Page structure, table columns, CTA banner
- **`leaderboard/styles.css`** — All styling (Meta blue theme, responsive breakpoints)
- **`leaderboard/app.js`** — Fetch logic, sorting, rendering, auto-refresh timer

To test changes, run the local server and edit files — just refresh the browser.

---

## Deploying to GitHub Pages

The `deploy-pages` job runs automatically after a successful leaderboard update. It
copies the `leaderboard/` directory and `scores/leaderboard.json` into the Pages
artifact. No manual deployment needed.
