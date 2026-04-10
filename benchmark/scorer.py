from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
LEADERBOARD_PATH = ROOT / "scores" / "leaderboard.json"
LOCK_PATH = LEADERBOARD_PATH.with_suffix(".json.lock")
MAX_RETRIES = 10


def _load_config():
    return importlib.import_module("config")


def _parse_iso8601(timestamp: str) -> str:
    normalized = timestamp.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized).isoformat()


def _load_result(result_path: Path) -> dict:
    with result_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _load_leaderboard() -> dict:
    if not LEADERBOARD_PATH.exists():
        return {"entries": []}

    with LEADERBOARD_PATH.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    if isinstance(payload, list):
        return {"entries": payload}

    if "entries" not in payload or not isinstance(payload["entries"], list):
        raise ValueError(f"Invalid leaderboard format in {LEADERBOARD_PATH}")

    return payload


def _extract_username(result: dict) -> str:
    username = result.get("username")
    if username:
        return str(username)

    submission_path = result.get("submission_path")
    if submission_path:
        return Path(submission_path).stem

    raise ValueError("Benchmark result is missing username/submission_path")


def _entry_sort_key(entry: dict):
    return (float(entry["median_time"]), entry["merged_at"])


def _build_entry(result: dict, merged_at: str) -> dict:
    error = result.get("error")
    output_valid = bool(result.get("output_valid"))
    median_time = float(result.get("median", 0.0))

    if error:
        raise ValueError(f"Benchmark result is not scoreable: {error}")
    if not output_valid:
        raise ValueError("Benchmark result is not scoreable: output validation failed")
    if median_time <= 0:
        raise ValueError("Benchmark result is not scoreable: median time must be positive")

    config = _load_config()
    baseline_time = float(getattr(config, "BASELINE_TIME"))

    return {
        "username": _extract_username(result),
        "median_time": median_time,
        "speedup": baseline_time / median_time,
        "merged_at": _parse_iso8601(merged_at),
        "times": [float(value) for value in result.get("times", [])],
        "output_valid": output_valid,
        "error": None,
    }


def _should_replace(existing: dict, candidate: dict) -> bool:
    existing_key = _entry_sort_key(existing)
    candidate_key = _entry_sort_key(candidate)
    return candidate_key < existing_key


def _acquire_lock() -> int:
    return os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o644)


def _release_lock(lock_fd: int) -> None:
    os.close(lock_fd)
    Path(LOCK_PATH).unlink(missing_ok=True)


def update_leaderboard(result: dict, merged_at: str) -> dict:
    entry = _build_entry(result, merged_at)
    LEADERBOARD_PATH.parent.mkdir(parents=True, exist_ok=True)

    for attempt in range(MAX_RETRIES):
        try:
            lock_fd = _acquire_lock()
        except FileExistsError:
            time.sleep(0.05 * (attempt + 1))
            continue

        try:
            leaderboard = _load_leaderboard()
            entries = [dict(item) for item in leaderboard["entries"]]

            existing_index = next(
                (index for index, item in enumerate(entries) if item.get("username") == entry["username"]),
                None,
            )

            if existing_index is None:
                entries.append(entry)
            elif _should_replace(entries[existing_index], entry):
                entries[existing_index] = entry

            entries.sort(key=_entry_sort_key)
            updated = {
                "updated_at": datetime.now().astimezone().isoformat(),
                "entries": entries,
            }

            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".json",
                delete=False,
                dir=LEADERBOARD_PATH.parent,
                encoding="utf-8",
            ) as handle:
                json.dump(updated, handle, indent=2, sort_keys=True)
                handle.write("\n")
                temp_path = Path(handle.name)

            os.replace(temp_path, LEADERBOARD_PATH)
            return updated
        finally:
            _release_lock(lock_fd)

    raise RuntimeError(f"Could not update leaderboard after {MAX_RETRIES} retries")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score a benchmark result and update the leaderboard.")
    parser.add_argument("--result", required=True, help="Path to benchmark result JSON")
    parser.add_argument("--merged-at", required=True, help="Merge timestamp in ISO 8601 format")
    args = parser.parse_args(argv)

    try:
        result = _load_result(Path(args.result).resolve())
        leaderboard = update_leaderboard(result, args.merged_at)
        json.dump(leaderboard, sys.stdout)
        sys.stdout.write("\n")
        return 0
    except Exception as exc:
        json.dump({"error": f"{type(exc).__name__}: {exc}"}, sys.stdout)
        sys.stdout.write("\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
