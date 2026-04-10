"""Placeholder baseline implementation for the PyCon 2026 challenge."""

from __future__ import annotations

from challenge.task import load_input_data


def solve(input_data: dict) -> object:
    """Naive sequential baseline for comparison against participant submissions."""
    # PLACEHOLDER: replace with the real sequential implementation once the task
    # definition and expected output are finalized.
    return {
        "status": "placeholder",
        "items_processed": len(input_data.get("items", [])),
    }


if __name__ == "__main__":
    print(solve(load_input_data()))
