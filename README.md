# PyCon 2026 Free-Threading Challenge

This repository hosts a Python free-threading performance challenge for PyCon 2026.
Participants implement a solution in `submissions/` and compete on correctness and
runtime under Python `3.13t`.

## Challenge Owner

[PLACEHOLDER: challenge owner name, handle, and contact information]

## Challenge Description

[PLACEHOLDER: describe the task, dataset, scoring criteria, and what "winning"
means for this challenge]

## Quick Start

1. Clone the repository:

   ```bash
   git clone [PLACEHOLDER: repository URL]
   cd pycon26-ft-challenge
   ```

2. Create a branch for your submission:

   ```bash
   git checkout -b submit/<github-username>
   ```

3. Add your solution at `submissions/<github_username>.py`.

4. Commit and submit your work:

   ```bash
   git add submissions/<github_username>.py
   git commit -m "Add challenge submission for <github_username>"
   git push origin submit/<github-username>
   ```

5. Open a pull request using the provided template.

## Rules

- One submission per person.
- Do not hardcode outputs.
- [PLACEHOLDER: add any resource, dependency, or time-limit constraints]

## Leaderboard

[PLACEHOLDER: leaderboard URL]

## Python 3.13t Installation

Install a free-threading build of Python 3.13 before benchmarking your solution.

Example with `pyenv`:

```bash
PYTHON_CONFIGURE_OPTS="--disable-gil" pyenv install 3.13.0
pyenv local 3.13.0
python -VV
```

[PLACEHOLDER: replace with the canonical install steps your event wants to
support, including package manager or uv instructions if applicable]
