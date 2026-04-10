from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
EXPECTED_OUTPUT_PATH = ROOT / "challenge" / "expected_output.json"
_WORKER_FLAG = "--_worker"


def _load_config():
    return importlib.import_module("config")


def _load_expected_output():
    with EXPECTED_OUTPUT_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _import_submission(submission_path: Path):
    module_name = f"_benchmark_submission_{submission_path.stem}_{int(time.time_ns())}"
    spec = importlib.util.spec_from_file_location(module_name, submission_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load submission module from {submission_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_worker_result(result_path: Path, payload: dict) -> None:
    with result_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle)


def _worker_run(submission_path: str, result_path: str) -> int:
    sys.path.insert(0, str(ROOT))
    result_file = Path(result_path)

    try:
        from challenge import task

        submission_module = _import_submission(Path(submission_path).resolve())
        start = time.perf_counter()
        output = task.run(submission_module)
        elapsed = time.perf_counter() - start
        _write_worker_result(
            result_file,
            {
                "elapsed": elapsed,
                "output": output,
                "error": None,
            },
        )
        return 0
    except Exception as exc:  # pragma: no cover - exercised through subprocess
        _write_worker_result(
            result_file,
            {
                "elapsed": None,
                "output": None,
                "error": f"{type(exc).__name__}: {exc}",
            },
        )
        return 1


def _invoke_worker(submission_path: Path, timeout_seconds: float) -> dict:
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".json",
        delete=False,
        dir=ROOT,
        encoding="utf-8",
    ) as handle:
        result_path = Path(handle.name)

    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "benchmark.runner",
                _WORKER_FLAG,
                str(submission_path.resolve()),
                str(result_path),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return {"elapsed": None, "output": None, "error": f"Timed out after {timeout_seconds} seconds"}

    try:
        with result_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        stderr = completed.stderr.strip()
        stdout = completed.stdout.strip()
        detail = stderr or stdout or f"Worker exited with code {completed.returncode}"
        return {"elapsed": None, "output": None, "error": detail}
    finally:
        result_path.unlink(missing_ok=True)

    if completed.returncode != 0 and not payload.get("error"):
        stderr = completed.stderr.strip()
        payload["error"] = stderr or f"Worker exited with code {completed.returncode}"

    return payload


def run_benchmark(submission_path: str | Path) -> dict:
    submission = Path(submission_path).resolve()

    try:
        config = _load_config()
        num_runs = int(getattr(config, "NUM_RUNS"))
        timeout_seconds = float(getattr(config, "TIMEOUT_SECONDS"))
        expected_output = _load_expected_output()
    except Exception as exc:
        return {
            "times": [],
            "median": 0.0,
            "output_valid": False,
            "error": f"{type(exc).__name__}: {exc}",
        }

    times: list[float] = []
    output_valid = True

    for _ in range(num_runs):
        payload = _invoke_worker(submission, timeout_seconds)
        if payload.get("error"):
            return {
                "times": times,
                "median": float(statistics.median(times)) if times else 0.0,
                "output_valid": False,
                "error": payload["error"],
            }

        elapsed = float(payload["elapsed"])
        times.append(elapsed)
        if payload.get("output") != expected_output:
            output_valid = False

    return {
        "times": times,
        "median": float(statistics.median(times)) if times else 0.0,
        "output_valid": output_valid,
        "error": None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a benchmarked challenge submission.")
    parser.add_argument("submission", nargs="?")
    parser.add_argument("result_path", nargs="?")
    parser.add_argument(_WORKER_FLAG, action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.__dict__[_WORKER_FLAG.lstrip("-")]:
        if not args.submission or not args.result_path:
            parser.error("worker mode requires submission and result_path")
        return _worker_run(args.submission, args.result_path)

    if not args.submission:
        parser.error("submission is required")

    result = run_benchmark(args.submission)
    result["submission_path"] = str(Path(args.submission).resolve())
    result["username"] = Path(args.submission).stem
    json.dump(result, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
