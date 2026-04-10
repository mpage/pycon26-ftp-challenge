"""Placeholder task harness for loading input and running a submission."""

from __future__ import annotations

from typing import Any


def load_input_data() -> dict:
    """Return placeholder challenge input data."""
    # PLACEHOLDER: load the real dataset from challenge/data/ when specified.
    return {
        "items": [],
        "note": "Replace with real challenge input.",
    }


def run(submission_module: Any) -> object:
    """Execute a submission module that exposes solve(input_data)."""
    if not hasattr(submission_module, "solve"):
        raise AttributeError("Submission module must define solve(input_data)")

    input_data = load_input_data()
    return submission_module.solve(input_data)
