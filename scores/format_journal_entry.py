import argparse
import json
import time


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("github_username")
    parser.add_argument("pr_url")
    parser.add_argument("pr_number")
    parser.add_argument("results_path")
    parser.add_argument("--is-meta", action="store_true", default=False)
    args = parser.parse_args()

    with open(args.results_path) as f:
        results = json.load(f)

    entry = {
        "github_username": args.github_username,
        "pr_url": args.pr_url,
        "pr_number": args.pr_number,
        "results": results,
        "is_meta": args.is_meta,
        "timestamp": int(time.time()),
    }
    print(json.dumps(entry))
