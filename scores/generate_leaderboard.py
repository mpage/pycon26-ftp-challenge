import argparse
import json
from datetime import datetime, timezone


def parse_journal(path):
    entries = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


def build_leaderboard(entries):
    best_by_user = {}
    run_counts = {}
    for entry in entries:
        user = entry["github_username"]
        run_counts[user] = run_counts.get(user, 0) + 1
        speedup = entry["results"]["summary"]["overall_speedup"]
        if user not in best_by_user or speedup > best_by_user[user]["results"]["summary"]["overall_speedup"]:
            best_by_user[user] = entry

    sorted_users = sorted(best_by_user, key=lambda u: best_by_user[u]["results"]["summary"]["overall_speedup"], reverse=True)

    leaderboard_entries = []
    for user in sorted_users:
        entry = best_by_user[user]
        summary = entry["results"]["summary"]
        leaderboard_entries.append({
            "username": user,
            "display_name": user,
            "median_time_seconds": summary["total_submission_time"],
            "speedup": f"{summary['overall_speedup']:.2f}",
            "status": "valid" if summary["overall_passed"] else "invalid",
            "merged_at": datetime.fromtimestamp(entry["timestamp"], tz=timezone.utc).isoformat(),
            "num_runs": run_counts[user],
            "pr_number": int(entry["pr_number"]),
            "pr_url": entry["pr_url"],
            "is_meta": entry.get("is_meta", False),
        })

    top_entry = best_by_user[sorted_users[0]] if sorted_users else None
    baseline_time = top_entry["results"]["summary"]["total_reference_time"] if top_entry else 0.0

    return {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "baseline_time": baseline_time,
        "entries": leaderboard_entries,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("journal_path")
    args = parser.parse_args()

    entries = parse_journal(args.journal_path)
    leaderboard = build_leaderboard(entries)
    print(json.dumps(leaderboard, indent=2))
