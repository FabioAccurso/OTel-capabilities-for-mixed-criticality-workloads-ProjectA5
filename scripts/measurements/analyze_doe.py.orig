#!/usr/bin/env python3
"""
Walk every run directory produced by run_doe.sh, extract response variables
from rt-app's per-thread log for HI_task, and merge them back into a results
CSV joined with the input factor levels from data_table.csv.

rt-app log columns (see rt-app_utils.cpp:log_timing), whitespace separated:
  ind  perf  duration  period  start_time  end_time  rel_start_time  slack
  c_duration  c_period  wu_latency
All time fields are in microseconds. slack < 0 means the iteration
overran its budget for that phase (a "deadline miss" from rt-app's point
of view).

Usage:
  ./analyze_doe.py --data-table /path/2-DoE/data_table.csv --out results.csv
"""
import argparse
import csv
import glob
import os
import statistics as st


def find_hi_log(run_dir):
    candidates = glob.glob(os.path.join(run_dir, "*HI*log")) or \
                 glob.glob(os.path.join(run_dir, "*HI*.log")) or \
                 glob.glob(os.path.join(run_dir, "*.log"))
    for c in candidates:
        if "hi" in os.path.basename(c).lower():
            return c
    return candidates[0] if candidates else None


def parse_log(path):
    rows = []
    with open(path) as f:
        for line in f:
            parts = line.split()
            if len(parts) < 11:
                continue
            try:
                ind = int(parts[0])
            except ValueError:
                continue  # header / comment line
            perf, duration, period = int(parts[1]), int(parts[2]), int(parts[3])
            slack = int(parts[7])
            c_duration, c_period, wu_latency = int(parts[8]), int(parts[9]), int(parts[10])
            rows.append(dict(ind=ind, duration=duration, period=period,
                              slack=slack, c_duration=c_duration,
                              c_period=c_period, wu_latency=wu_latency))
    return rows


def summarize(rows):
    if not rows:
        return None
    periods = [r["period"] for r in rows if r["period"] > 0]
    durations = [r["duration"] for r in rows]
    slacks = [r["slack"] for r in rows]
    return {
        "n_iters": len(rows),
        "deadline_miss_ratio": sum(1 for s in slacks if s < 0) / len(slacks),
        "max_duration_us": max(durations),
        "mean_duration_us": st.mean(durations),
        "period_jitter_std_us": st.pstdev(periods) if len(periods) > 1 else 0.0,
        "mean_wu_latency_us": st.mean(r["wu_latency"] for r in rows),
    }


def count_exported_spans(run_dir, name_substr):
    """Count occurrences of a span/task name in stdout.log (ostream exporter).
    Only meaningful for Block 2 runs where InitTracer (ostream) was used."""
    stdout_path = os.path.join(run_dir, "stdout.log")
    if not os.path.exists(stdout_path):
        return None
    with open(stdout_path, errors="ignore") as f:
        content = f.read()
    return content.count(name_substr)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-table", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    with open(args.data_table) as f:
        reader = csv.DictReader(f)
        input_rows = list(reader)

    out_fields = list(input_rows[0].keys()) + [
        "n_iters", "deadline_miss_ratio", "max_duration_us", "mean_duration_us",
        "period_jitter_std_us", "mean_wu_latency_us",
        "hi_spans_exported", "lo_spans_exported",
    ]

    with open(args.out, "w", newline="") as out_f:
        writer = csv.DictWriter(out_f, fieldnames=out_fields)
        writer.writeheader()
        for row in input_rows:
            run_dir = row["run_dir"]
            log_path = find_hi_log(run_dir)
            summary = summarize(parse_log(log_path)) if log_path else None
            merged = dict(row)
            if summary:
                merged.update(summary)
            else:
                merged.update({k: "" for k in
                               ["n_iters", "deadline_miss_ratio", "max_duration_us",
                                "mean_duration_us", "period_jitter_std_us",
                                "mean_wu_latency_us"]})
            merged["hi_spans_exported"] = count_exported_spans(run_dir, "HI_task")
            merged["lo_spans_exported"] = count_exported_spans(run_dir, "LO_noise")
            writer.writerow(merged)

    print(f"wrote {args.out}  ({len(input_rows)} runs)")


if __name__ == "__main__":
    main()
