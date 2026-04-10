"""Scoring wrapper for the challenge harness.

Runs a submission against all graphs and outputs a JSON score summary.
Expects harness.py, graph.py, reference.py, and a graphs/ directory to
exist in the repository root.

Usage:
    python score.py submissions/alice.py graphs/
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser(description="Score a submission and output JSON")
    parser.add_argument("submission", help="Path to the submission .py file")
    parser.add_argument("graph_path", help="Path to a graph JSON file or directory")
    parser.add_argument("--runs", type=int, default=3, help="Number of runs per graph (takes median)")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent

    # Import challenge modules from repo root
    sys.path.insert(0, str(root))
    from graph import BuildGraph
    from harness import run_one, _load_submission

    submission_module = _load_submission(args.submission)

    graph_path = Path(args.graph_path)
    if graph_path.is_dir():
        graph_files = sorted(graph_path.glob("*.json"))
    else:
        graph_files = [graph_path]

    if not graph_files:
        result = {"status": "dnf", "error": f"No graph files found in {graph_path}", "speedup": 0.0, "results": []}
        json.dump(result, sys.stdout, indent=2)
        print()
        return 1

    all_results = []
    total_ref = 0.0
    total_sub = 0.0
    all_passed = True

    for gf in graph_files:
        graph = BuildGraph.load(str(gf))

        # Run multiple times, take the best speedup per graph
        run_speedups = []
        best_result = None
        for _ in range(args.runs):
            # Reload graph for each run to reset state
            graph = BuildGraph.load(str(gf))
            result = run_one(submission_module, graph, gf.name)
            run_speedups.append(result["speedup"])
            if best_result is None or result["submission_time"] < best_result["submission_time"]:
                best_result = result

        # Use the run with the best (lowest) submission time
        total_ref += best_result["reference_time"]
        total_sub += best_result["submission_time"]
        if not best_result["passed"]:
            all_passed = False

        all_results.append({
            "graph": best_result["graph"],
            "num_targets": best_result["num_targets"],
            "reference_time": round(best_result["reference_time"], 4),
            "submission_time": round(best_result["submission_time"], 4),
            "speedup": round(best_result["speedup"], 2),
            "passed": best_result["passed"],
            "errors": best_result["errors"],
        })

    overall_speedup = total_ref / total_sub if total_sub > 0 else 0.0
    status = "valid" if all_passed else "dnf"

    output = {
        "status": status,
        "speedup": round(overall_speedup, 2),
        "all_passed": all_passed,
        "results": all_results,
    }

    if not all_passed:
        failed = [r for r in all_results if not r["passed"]]
        errors = []
        for r in failed[:5]:
            errors.extend(r["errors"][:3])
        output["error"] = "; ".join(errors) if errors else "Validation failed"

    json.dump(output, sys.stdout, indent=2)
    print()
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
