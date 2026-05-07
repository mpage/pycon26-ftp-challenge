# PyCon 2026 Free-Threading Challenge

Welcome to the Free-Threading Challenge, hosted at the Meta booth at Pycon US 2026. This challenge will run from Friday, May 15, 8:00am to Saturday, May 16, 4:00pm PST.
Participants will implement a solution in `submissions/` and compete on correctness and
runtime under Python `3.14t` (the free-threading build of Python). The top 25 valid solutions each day will receive a swag prize!

## The Challenge

Your challenge, should you choose to accept it, is to implement a scheduler for a build system that builds all
targets a quickly as possible while obeying the following constraints:

1) Each target is built exactly once.
2) Each target is not built until all of its dependencies have been built.

Your solution should perform well on a variety of graph shapes and
sizes. Solutions will be evaluated on a 24 core machine; winning solutions will
likely take advantage of this fact.

Valid solutions will be shared on the leaderboard [here](https://mpage.github.io/pycon26-ftp-challenge/leaderboard/).

## Quick Start

1. Fork the repository.
2. Read `submission_template.py` to get started.
3. Add your solution at `submissions/<github_username>.py`.
4. Evaluate your solution locally and iterate:

   ```bash
   python challenge/harness.py submissions/<github_username>.py graphs
   ```

5. Once you are happy with your solution open a pull request using the provided template.

## Rules

1. This challenge will run from Friday, May 15, 8:00am to Saturday, May 16,
   4:00pm PST. The top 25 finishers at the end of each conference day win swag!
   Swing by the Meta booth at 5:45pm Friday and 4:00pm Saturday to pick up your
   swag and have the opportunity to demo your solution if you wish.
2. You can enter as many submissions as you like; only your highest score will
   count towards the leaderboard.
3. One prize per contestant.
4. Your PRs should only add or modify `submissions/<github_username>.py`. Do not
   modify any of the supporting code or rely on third-party libraries.
5. AI use is fine, but please make sure that you understand your submission. You
   must be able to answer questions about how it works.
6. There is a maximum execution time of 10 minutes. Submissions that run longer
   than this will be cancelled and receive no score.
7. All solutions must be pure Python solutions, and runnable using Python 3.14t.
8. Use of third party dependencies is not allowed.


## Evaluation

Submissions are evaluated using the provided harness (see
`challenge/harness.py`) and the build graphs generated using the
`challenge/generate.py` script in the `graphs` directory.  We'll run each graph
three times, take the median, and compute the speed-up as a factor relative to
the reference solution (see `challenge/reference.py`). Each submission's final
score is the average speed-up across all graphs.

Submissions are all evaluated on the same 24 core machine using Python 3.14t.

## Python 3.14t Installation

Install a free-threading build of Python 3.14 before benchmarking your solution.
Feel free to use your favorite method of installing Python. Some popular options
are:

`uv`:

```bash
uv run --python 3.14t python
```

`pyenv`:

```bash
pyenv install 3.14.4t
pyenv local 3.14.4t
```

To learn more about Python free threading see [the Python Free Threading Guide](https://py-free-threading.github.io/)

## File Reference

* `challenge/generate.py` - Generates sample build graphs of varying topologies.
* `challenge/graph.py` - Contains the definitions of build graphs and targets.
* `challenge/harness.py` - The evaluation harness used to score submissions.
* `challenge/reference.py` - A singly threaded reference solution.
* `graphs/` - The graphs used to evaluate submissions.
* `submission_template.py` - A skeleton solution to start from.
