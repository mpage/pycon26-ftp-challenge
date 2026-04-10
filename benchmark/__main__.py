from __future__ import annotations

import argparse

from . import runner, scorer


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark utilities")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("runner", help="Run a benchmarked submission")
    run_parser.add_argument("submission", help="Path to the submission file")

    score_parser = subparsers.add_parser("scorer", help="Score a benchmark result")
    score_parser.add_argument("--result", required=True, help="Path to benchmark result JSON")
    score_parser.add_argument("--merged-at", required=True, help="Merge timestamp in ISO 8601 format")

    args = parser.parse_args(argv)

    if args.command == "runner":
        return runner.main([args.submission])
    return scorer.main(["--result", args.result, "--merged-at", args.merged_at])


if __name__ == "__main__":
    raise SystemExit(main())
