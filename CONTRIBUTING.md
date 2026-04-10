# Contributing

This repo contains the **leaderboard website** and **GitHub Actions workflow** for
the PyCon 2026 Free-Threaded Python Challenge. The actual challenge code and grading
harness live in a separate repository maintained by the challenge author.

---

## Repository structure

```
pycon26-ft-challenge/
├── graph.py              # BuildGraph / Target classes (from challenge author)
├── reference.py          # Single-threaded reference implementation
├── harness.py            # Test harness — runs & validates submissions
├── score.py              # Scoring wrapper — runs harness, outputs JSON
├── graphs/               # Build graph JSON files used for scoring
├── submissions/          # Participant submissions (one .py per user)
│   └── _example.py
├── leaderboard/          # Static website (HTML/CSS/JS)
│   ├── index.html
│   ├── app.js
│   └── styles.css
├── scores/
│   └── leaderboard.json  # Live leaderboard data (updated by CI on merge)
├── .github/workflows/
│   └── benchmark.yml     # CI: score on PR, update leaderboard on merge
└── CONTRIBUTING.md       # You are here
```

---

## Running the leaderboard website locally

The leaderboard is a static site — no build step or dependencies required. You just
need a local HTTP server (browsers block `fetch()` on `file://` URLs).

```bash
# Clone the repo
git clone https://github.com/meta/pycon26-ft-challenge.git
cd pycon26-ft-challenge

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
  "entries": [
    {
      "username": "alice",
      "display_name": "Alice",
      "speedup": "3.20",
      "status": "valid",
      "merged_at": "2026-05-15T14:30:00+00:00",
      "pr_number": 42,
      "pr_url": "https://github.com/meta/pycon26-ft-challenge/pull/42"
    }
  ]
}
```

Each entry needs at minimum: `username`, `speedup`, `status` (`"valid"` or `"dnf"`),
and `merged_at`. The leaderboard UI ranks entries by highest speedup.

---

## How CI works

When you open a PR that adds/modifies a file in `submissions/`, the **score** job
runs automatically:

1. Detects which submission file changed
2. Installs free-threaded Python 3.13t
3. Runs `score.py` (wraps `harness.py`) against all graphs in `graphs/`
4. Posts a score comment on the PR with per-graph speedups and pass/fail status

When the PR is **merged to main**, two more jobs run:

5. **update-leaderboard** — upserts the entry into `scores/leaderboard.json` (keeps
   the better score if the user already has one) and pushes to `main`
6. **deploy-pages** — deploys the updated leaderboard to GitHub Pages

### Scoring details

- Each graph is run 3 times; the best (lowest submission time) run is used
- Overall speedup = `total_reference_time / total_submission_time`
- Status is `valid` only if all graphs pass correctness validation
- If your submission fails validation, it gets a `dnf` status and is not added to
  the leaderboard

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
