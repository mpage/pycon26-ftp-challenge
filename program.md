# autoresearch

This is an experiment to have the LLM do its own research.

## Setup

To set up a new experiment, work with the user to:

1. **Agree on a run tag**: propose a tag based on today's date (e.g. `may15`). The branch `autoresearch/<tag>` must not already exist — this is a fresh run.
2. **Create the branch**: `git checkout -b autoresearch/<tag>` from current main.
3. **Read the in-scope files**: Read these files for full context:
   - `README.md` — challenge context and rules.
   - `challenge/graph.py` — `BuildGraph` and `Target` data structures. Do not modify.
   - `challenge/harness.py` — evaluation harness. Do not modify.
   - `challenge/reference.py` — the single-threaded reference solution (the baseline to beat). Do not modify.
   - `submissions/sadrasabouri.py` — **the only file you modify**.
4. **Initialize results.tsv**: Create `results.tsv` with just the header row. The baseline will be recorded after the first run.
5. **Confirm and go**: Confirm setup looks good.

Once you get confirmation, kick off the experimentation.

## The challenge

Build a scheduler that executes all targets in a `BuildGraph` as fast as possible, obeying:
1. Every target is built exactly once (via `target.build(dep_results)`).
2. A target must not start until all its dependencies have finished.

The reference solution (`challenge/reference.py`) builds targets in single-threaded topological order. Your job is to beat it using parallelism. The evaluation machine has **24 cores** and runs **Python 3.14t** (the free-threading build — the GIL is disabled).

**The metric is TOTAL speedup** (aggregate across all graph shapes), printed at the bottom of the harness summary. Higher is better.

## Experimentation

Each experiment runs the harness against all graphs in `graphs/` with 3 trials per graph (median is taken). You run it as:

```bash
python challenge/harness.py submissions/sadrasabouri.py graphs/ --num-trials 3 > run.log 2>&1
```

**What you CAN do:**
- Modify `submissions/sadrasabouri.py` — this is the only file you edit. Everything is fair game: scheduling strategy, thread pool sizing, task prioritization, batching, use of `threading` or `concurrent.futures`, etc.

**What you CANNOT do:**
- Modify any file in `challenge/` — harness, graph, reference, generate are all read-only.
- Modify files in `graphs/`.
- Use third-party libraries. Only the Python standard library is allowed.
- Skip or replace `target.build()` calls — correctness is validated by the harness.

**The goal is simple: get the highest TOTAL speedup.** The submission must also pass all correctness checks (status must be PASS, not FAIL).

**Simplicity criterion**: All else being equal, simpler is better. A tiny improvement that adds ugly complexity is not worth it. Conversely, removing something and getting equal or better results is a great outcome. When evaluating whether to keep a change, weigh complexity cost against improvement magnitude. A 0.01x speedup gain that adds 30 lines of hacky code? Probably not worth it. A 0.01x gain from deleting code? Definitely keep.

**The first run**: Your very first run should always be to establish the baseline with the current `submissions/sadrasabouri.py` as-is.

## Output format

Once the script finishes it prints a per-graph table and a summary like this:

```
============================================================
SUMMARY
============================================================
Graph                          Time  Speedup   Status
------------------------------ -------- -------- --------
chain.json                      0.123s    3.21x     PASS
diamond.json                    0.045s    5.67x     PASS
realistic.json                  1.234s    8.90x     PASS
tree.json                       0.234s    4.56x     PASS
wide.json                       0.567s    6.78x     PASS
------------------------------ -------- -------- --------
TOTAL                           2.203s    6.82x     PASS
============================================================
```

Extract the key metric:

```bash
grep "TOTAL" run.log
```

If the run crashed or produced a FAIL, run `tail -n 50 run.log` to read the traceback.

## Logging results

When an experiment is done, log it to `results.tsv` (tab-separated, NOT comma-separated — commas break in descriptions).

The TSV has a header row and 4 columns:

```
commit	total_speedup	status	description
```

1. git commit hash (short, 7 chars)
2. total_speedup achieved (e.g. 6.82) — use 0.00 for crashes or FAILs
3. status: `keep`, `discard`, or `crash`
4. short text description of what this experiment tried

Example:

```
commit	total_speedup	status	description
a1b2c3d	6.82	keep	baseline
b2c3d4e	7.15	keep	increase thread pool to 32 workers
c3d4e5f	6.50	discard	process pool instead of threads
d4e5f6g	0.00	crash	use multiprocessing (pickling failure)
```

## The experiment loop

The experiment runs on a dedicated branch (e.g. `autoresearch/may15`).

LOOP FOREVER:

1. Look at the git state: the current branch/commit we're on.
2. Tune `submissions/sadrasabouri.py` with an experimental idea by directly hacking the code.
3. git commit
4. Run the experiment: `python challenge/harness.py submissions/sadrasabouri.py graphs/ --num-trials 3 > run.log 2>&1` (redirect everything — do NOT use tee or let output flood your context)
5. Read out the results: `grep "TOTAL" run.log`
6. If the grep output is empty or shows FAIL, the run crashed or is invalid. Run `tail -n 50 run.log` to read the Python traceback and attempt a fix. If you can't get things to work after more than a few attempts, give up on the idea.
7. Record the results in the tsv (NOTE: do not commit the results.tsv file, leave it untracked by git)
8. If total_speedup improved (higher), you "advance" the branch, keeping the git commit
9. If total_speedup is equal or worse, you `git reset --hard HEAD~1` back to where you started

The idea is that you are a completely autonomous researcher trying things out. If they work, keep. If they don't, discard. You're advancing the branch so that you can iterate on top of wins.

**Timeout**: Each evaluation run should complete in well under 10 minutes. If a run exceeds 10 minutes, kill it with Ctrl-C and treat it as a failure (discard and revert).

**Crashes/FAILs**: If a run crashes (exception, or correctness FAIL), use your judgment: If it's something dumb and easy to fix (e.g. a typo, a missing import), fix it and re-run. If the idea itself is fundamentally broken, skip it, log "crash" as the status, and move on.

**NEVER STOP**: Once the experiment loop has begun (after the initial setup), do NOT pause to ask the human if you should continue. Do NOT ask "should I keep going?" or "is this a good stopping point?". The human might be asleep, or gone from a computer and expects you to continue working *indefinitely* until you are manually stopped. You are autonomous. If you run out of ideas, think harder — consider different scheduling strategies, thread pool sizes, work-stealing, priority queues based on critical-path length, batching small tasks, reducing overhead, etc. The loop runs until the human interrupts you, period.

As an example use case, a user might leave you running while they sleep. If each experiment takes you ~2 minutes then you can run approx 30/hour. The user then wakes up to experimental results, all completed by you while they slept!
